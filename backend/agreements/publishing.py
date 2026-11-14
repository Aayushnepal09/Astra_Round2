import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max

from .models import Agreement, TemplateVersion
from .storage import PrivateDocumentStorage


class TemplatePublisher:
    def __init__(self, company):
        self.company = company

    def publish(self, agreement_id, *, revision, name, pdf_sha256):
        with transaction.atomic():
            # A write lock serializes both publishing and field saves on SQLite too.
            locked = Agreement.objects.filter(pk=agreement_id, company=self.company).update(
                fields_revision=F("fields_revision"),
            )
            if not locked:
                raise Agreement.DoesNotExist
            agreement = Agreement.objects.get(pk=agreement_id, company=self.company)
            existing = agreement.versions.filter(source_revision=revision).first()
            if existing is not None:
                if existing.name != name or existing.pdf_sha256 != pdf_sha256:
                    raise ValidationError("This revision was already published with different details. Reopen the saved draft.")
                return existing, False
            if agreement.fields_revision != revision or agreement.title != name or agreement.pdf_sha256 != pdf_sha256:
                raise ValidationError(
                    "The saved draft changed. Reopen it to load the current name and fields before publishing.",
                    code="draft_conflict",
                )
            fields = list(agreement.fields.all())
            errors = []
            for index, field in enumerate(fields, 1):
                try:
                    field.full_clean(validate_unique=False, validate_constraints=False)
                except ValidationError as error:
                    errors.append(f"Field {index}: {' '.join(error.messages)}")
            if errors:
                raise ValidationError(errors)
            version = TemplateVersion(
                agreement=agreement, name=name, source_revision=revision, pdf_sha256=pdf_sha256,
                number=(agreement.versions.aggregate(number=Max("number"))["number"] or 0) + 1,
                fields=[field.snapshot() for field in fields],
            )
            version.full_clean()
            storage = PrivateDocumentStorage(self.company.pk, agreement.pk)
            digest = hashlib.sha256()
            size = 0
            with storage.open(agreement.pdf_key) as source:
                for chunk in iter(lambda: source.read(64 * 1024), b""):
                    size += len(chunk)
                    if size > agreement.pdf_size_bytes:
                        break
                    digest.update(chunk)
            if size != agreement.pdf_size_bytes or digest.hexdigest() != pdf_sha256:
                raise ValidationError("The saved PDF does not match its validated identity. Upload the document again and prepare a new draft.")
            version.save()
            return version, True
