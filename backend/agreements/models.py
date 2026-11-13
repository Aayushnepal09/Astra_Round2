import math
import uuid

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.validators import RegexValidator
from django.db import models, router, transaction

from .storage import PrivateDocumentStorage


class Agreement(models.Model):
    class PreparationState(models.TextChoices):
        UNPREPARED = "unprepared", "Unprepared agreement"

    class ProcessingState(models.TextChoices):
        PENDING = "pending", "Pending validation"
        PROCESSING = "processing", "Processing document"
        READY = "ready", "Document validated"
        FAILED = "failed", "Processing failed"

    class Format(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"

    hash_validator = RegexValidator(
        r"\A[0-9a-f]{64}\Z", "Use a SHA-256 hexadecimal digest."
    )
    ORIGINAL_FIELDS = (
        "original_filename", "original_key", "original_sha256", "original_size_bytes",
    )
    PDF_FIELDS = (
        "pdf_key", "pdf_sha256", "pdf_size_bytes", "page_count", "page_geometry",
        "conversion_engine", "conversion_version", "validated_at",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creation_key = models.UUIDField(default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="agreements", editable=False,
    )
    title = models.CharField(max_length=200, blank=True)
    original_filename = models.CharField(max_length=255)
    detected_format = models.CharField(
        max_length=8, choices=Format.choices, blank=True, editable=False,
    )
    preparation_state = models.CharField(
        max_length=16, choices=PreparationState.choices,
        default=PreparationState.UNPREPARED, editable=False,
    )
    processing_state = models.CharField(
        max_length=16, choices=ProcessingState.choices,
        default=ProcessingState.PENDING, editable=False,
    )
    processing_error = models.CharField(max_length=500, blank=True, editable=False)
    original_key = models.CharField(max_length=200, blank=True, editable=False)
    original_sha256 = models.CharField(
        max_length=64, blank=True, validators=[hash_validator], editable=False,
    )
    original_size_bytes = models.PositiveBigIntegerField(
        null=True, blank=True, editable=False,
    )
    pdf_key = models.CharField(max_length=200, blank=True, editable=False)
    pdf_sha256 = models.CharField(
        max_length=64, blank=True, validators=[hash_validator], editable=False,
    )
    pdf_size_bytes = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    page_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    page_geometry = models.JSONField(default=list, blank=True, editable=False)
    conversion_engine = models.CharField(max_length=100, blank=True, editable=False)
    conversion_version = models.CharField(max_length=100, blank=True, editable=False)
    validated_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "creation_key"], name="agreement_company_key_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(preparation_state="unprepared"),
                name="agreement_unprepared_only",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    processing_state__in=("pending", "processing", "ready", "failed"),
                ),
                name="agreement_processing_state",
            ),
            models.CheckConstraint(
                condition=models.Q(detected_format__in=("", "pdf", "docx")),
                name="agreement_detected_format",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        original_key="", original_sha256="",
                        original_size_bytes__isnull=True,
                    )
                    | (
                        ~models.Q(original_key="")
                        & ~models.Q(original_sha256="")
                        & models.Q(
                            original_size_bytes__isnull=False,
                            original_size_bytes__gt=0,
                        )
                    )
                ),
                name="agreement_original_metadata",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(processing_state="ready")
                    | (
                        ~models.Q(original_key="")
                        & ~models.Q(pdf_key="")
                        & ~models.Q(pdf_sha256="")
                        & models.Q(
                            pdf_size_bytes__isnull=False, pdf_size_bytes__gt=0,
                            page_count__isnull=False, page_count__gt=0,
                            validated_at__isnull=False,
                            detected_format__in=("pdf", "docx"), processing_error="",
                        )
                    )
                ),
                name="agreement_ready_metadata",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(processing_state="ready")
                    | models.Q(
                        pdf_key="", pdf_sha256="", pdf_size_bytes__isnull=True,
                        page_count__isnull=True, page_geometry=[],
                        validated_at__isnull=True,
                    )
                ),
                name="agreement_pending_pdf_empty",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(processing_state="failed") & ~models.Q(processing_error=""))
                    | (~models.Q(processing_state="failed") & models.Q(processing_error=""))
                ),
                name="agreement_failure_detail",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "-created_at", "-id"],
                name="agreement_company_created",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if isinstance(self.title, str):
            self.title = self.title.strip()
        if isinstance(self.original_filename, str):
            self.original_filename = self.original_filename.strip()
        if (
            not isinstance(self.original_filename, str)
            or not self.original_filename
            or any(
                character in "/\\" or ord(character) < 32 or ord(character) == 127
                for character in self.original_filename
            )
        ):
            errors["original_filename"] = "Use a filename without directories or control characters."
        for field in ("original_key", "pdf_key"):
            key = getattr(self, field)
            if key and self.company_id:
                try:
                    PrivateDocumentStorage.validate_key(key, self.company_id, self.pk)
                except (ValueError, SuspiciousFileOperation):
                    errors[field] = "The artifact must belong to this company and agreement."
        if self.pdf_key and self.pdf_key == self.original_key:
            if (
                self.detected_format != self.Format.PDF
                or (self.pdf_sha256, self.pdf_size_bytes)
                != (self.original_sha256, self.original_size_bytes)
            ):
                errors["pdf_key"] = "Only unchanged PDF input can reuse its original artifact."
        if self.processing_state == self.ProcessingState.READY:
            if (
                not isinstance(self.page_geometry, list)
                or len(self.page_geometry) != self.page_count
                or not self.page_geometry
            ):
                errors["page_geometry"] = "Include geometry for every validated PDF page."
            elif any(not self._valid_page(page) for page in self.page_geometry):
                errors["page_geometry"] = "Each page needs positive width/height in PDF points and a valid rotation."
            if self.detected_format == self.Format.DOCX and not all(
                isinstance(value, str) and value.strip()
                for value in (self.conversion_engine, self.conversion_version)
            ):
                errors["conversion_engine"] = "Record the DOCX conversion engine and version."
        elif (
            self.pdf_key or self.pdf_sha256 or self.pdf_size_bytes is not None
            or self.page_count is not None or self.page_geometry != []
            or self.validated_at is not None
        ):
            errors["processing_state"] = "Publish PDF metadata only after document validation succeeds."
        if self.processing_state == self.ProcessingState.FAILED and not (
            isinstance(self.processing_error, str) and self.processing_error.strip()
        ):
            errors["processing_error"] = "Include a useful, non-sensitive processing error."
        if errors:
            raise ValidationError(errors)

    @staticmethod
    def _valid_page(page):
        try:
            return (
                isinstance(page, dict)
                and set(page) == {"width", "height", "rotation"}
                and all(
                    type(page[key]) in (int, float)
                    and math.isfinite(page[key]) and page[key] > 0
                    for key in ("width", "height")
                )
                and type(page["rotation"]) is int
                and page["rotation"] in (0, 90, 180, 270)
            )
        except OverflowError:
            return False

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if not self._state.adding:
                previous = type(self).objects.using(using).select_for_update().get(pk=self.pk)
                frozen = ["company_id", "creation_key", "created_at"]
                if previous.original_key:
                    frozen.extend(self.ORIGINAL_FIELDS)
                if previous.detected_format:
                    frozen.append("detected_format")
                if previous.processing_state == self.ProcessingState.READY:
                    frozen.extend((
                        *self.ORIGINAL_FIELDS, *self.PDF_FIELDS,
                        "detected_format", "processing_state",
                    ))
                if any(getattr(self, field) != getattr(previous, field) for field in frozen):
                    raise ValidationError("Saved agreement artifacts and ownership cannot be replaced. Create another agreement.")
            # Bulk writes bypass this guard; future processing must use model saves.
            self.full_clean()
            return super().save(
                force_insert=force_insert or self._state.adding,
                force_update=force_update, using=using, update_fields=update_fields,
            )
