import logging
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.utils import timezone

from .models import Agreement
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
                        raise DocumentError(f"This file is too large. Choose a PDF up to {limit:g} MiB.", "oversized")
                    destination.write(chunk)
            if not size:
                raise DocumentError("This file is empty. Choose a PDF containing at least one page.", "empty")

            geometry = self.processor.validate(path)
            agreement = Agreement(
                company=self.company, creation_key=creation_key,
                original_filename=filename, detected_format=Agreement.Format.PDF,
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
            agreement.original_key = agreement.pdf_key = artifact.key
            agreement.original_sha256 = agreement.pdf_sha256 = artifact.sha256
            agreement.original_size_bytes = agreement.pdf_size_bytes = artifact.size_bytes
            try:
                agreement.save()
            except (ValidationError, DatabaseError):
                # A lost database acknowledgement may still have committed.
                # Keep the artifact if the database cannot resolve that outcome.
                winner = self._existing(creation_key)
                if winner is None or winner.original_key != artifact.key:
                    self._cleanup(storage, artifact.key)
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
