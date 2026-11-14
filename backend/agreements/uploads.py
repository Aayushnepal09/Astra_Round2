import logging
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.utils import timezone

from .models import Agreement
from .docx import DocxConverter
from .processing import DocumentError, PdfProcessor
from .storage import PrivateDocumentStorage


logger = logging.getLogger(__name__)


class AgreementUpload:
    """Validate an original before publishing its immutable private artifact."""

    def __init__(self, company):
        self.company = company
        self.processor = PdfProcessor()

    def save(self, source, *, filename, creation_key):
        existing = self._existing(creation_key)
        if existing is not None:
            return existing, False

        temporary_root = settings.DATA_DIR / "agreement-processing"
        temporary_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            path = Path(directory) / "original.bin"
            size = 0
            with path.open("xb") as destination:
                for chunk in source:
                    size += len(chunk)
                    if size > settings.AGREEMENT_MAX_BYTES:
                        limit = settings.AGREEMENT_MAX_BYTES / (1024 * 1024)
                        raise DocumentError(f"This file is too large. Choose a PDF or DOCX up to {limit:g} MiB.", "oversized")
                    destination.write(chunk)
            if not size:
                raise DocumentError("This file is empty. Choose a PDF or DOCX containing at least one page.", "empty")

            with path.open("rb") as source_file:
                is_docx = source_file.read(4) == b"PK\x03\x04"
            pdf_path = path
            engine = version = ""
            if is_docx:
                pdf_path = Path(directory) / "converted.pdf"
                engine, version = DocxConverter().convert(path, pdf_path)
            geometry = self.processor.validate(pdf_path)
            agreement = Agreement(
                company=self.company, creation_key=creation_key,
                original_filename=filename,
                detected_format=Agreement.Format.DOCX if is_docx else Agreement.Format.PDF,
                conversion_engine=engine, conversion_version=version,
                processing_state=Agreement.ProcessingState.READY,
                page_count=len(geometry), page_geometry=geometry,
                validated_at=timezone.now(),
            )
            storage = PrivateDocumentStorage(self.company.pk, agreement.pk)
            with path.open("rb") as original:
                artifact = storage.write(
                    iter(lambda: original.read(64 * 1024), b""),
                    max_bytes=settings.AGREEMENT_MAX_BYTES,
                )
            agreement.original_key = artifact.key
            agreement.original_sha256 = artifact.sha256
            agreement.original_size_bytes = artifact.size_bytes
            pdf_artifact = artifact
            try:
                if is_docx:
                    with pdf_path.open("rb") as converted:
                        pdf_artifact = storage.write(
                            iter(lambda: converted.read(64 * 1024), b""),
                            max_bytes=settings.AGREEMENT_MAX_BYTES,
                        )
                agreement.pdf_key = pdf_artifact.key
                agreement.pdf_sha256 = pdf_artifact.sha256
                agreement.pdf_size_bytes = pdf_artifact.size_bytes
                agreement.save()
            except (ValidationError, DatabaseError, OSError, ValueError):
                # A lost database acknowledgement may still have committed.
                # Keep the artifact if the database cannot resolve that outcome.
                winner = self._existing(creation_key)
                if winner is None or winner.original_key != artifact.key:
                    self._cleanup(storage, artifact.key)
                    if pdf_artifact.key != artifact.key:
                        self._cleanup(storage, pdf_artifact.key)
                if winner is not None:
                    return winner, False
                raise
            return agreement, True

    def _existing(self, creation_key):
        return Agreement.objects.filter(
            company=self.company, creation_key=creation_key,
        ).first()

    @staticmethod
    def _cleanup(storage, key):
        try:
            storage.delete(key)
        except OSError:
            logger.warning("Could not clean up an unsaved agreement artifact: %s", storage.agreement_id)
