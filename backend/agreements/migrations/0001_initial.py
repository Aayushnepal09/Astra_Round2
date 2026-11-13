import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Agreement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("creation_key", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("title", models.CharField(blank=True, max_length=200)),
                ("original_filename", models.CharField(max_length=255)),
                ("detected_format", models.CharField(blank=True, choices=[("pdf", "PDF"), ("docx", "DOCX")], editable=False, max_length=8)),
                ("preparation_state", models.CharField(choices=[("unprepared", "Unprepared agreement")], default="unprepared", editable=False, max_length=16)),
                ("processing_state", models.CharField(choices=[("pending", "Pending validation"), ("processing", "Processing document"), ("ready", "Document validated"), ("failed", "Processing failed")], default="pending", editable=False, max_length=16)),
                ("processing_error", models.CharField(blank=True, editable=False, max_length=500)),
                ("original_key", models.CharField(blank=True, editable=False, max_length=200)),
                ("original_sha256", models.CharField(blank=True, editable=False, max_length=64, validators=[django.core.validators.RegexValidator("\\A[0-9a-f]{64}\\Z", "Use a SHA-256 hexadecimal digest.")])),
                ("original_size_bytes", models.PositiveBigIntegerField(blank=True, editable=False, null=True)),
                ("pdf_key", models.CharField(blank=True, editable=False, max_length=200)),
                ("pdf_sha256", models.CharField(blank=True, editable=False, max_length=64, validators=[django.core.validators.RegexValidator("\\A[0-9a-f]{64}\\Z", "Use a SHA-256 hexadecimal digest.")])),
                ("pdf_size_bytes", models.PositiveBigIntegerField(blank=True, editable=False, null=True)),
                ("page_count", models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ("page_geometry", models.JSONField(blank=True, default=list, editable=False)),
                ("conversion_engine", models.CharField(blank=True, editable=False, max_length=100)),
                ("conversion_version", models.CharField(blank=True, editable=False, max_length=100)),
                ("validated_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="agreements", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["company", "-created_at", "-id"], name="agreement_company_created")],
                "constraints": [
                    models.UniqueConstraint(fields=["company", "creation_key"], name="agreement_company_key_uniq"),
                    models.CheckConstraint(condition=models.Q(preparation_state="unprepared"), name="agreement_unprepared_only"),
                    models.CheckConstraint(condition=models.Q(processing_state__in=("pending", "processing", "ready", "failed")), name="agreement_processing_state"),
                    models.CheckConstraint(condition=models.Q(detected_format__in=("", "pdf", "docx")), name="agreement_detected_format"),
                    models.CheckConstraint(
                        condition=(models.Q(original_key="", original_sha256="", original_size_bytes__isnull=True) | (~models.Q(original_key="") & ~models.Q(original_sha256="") & models.Q(original_size_bytes__isnull=False, original_size_bytes__gt=0))),
                        name="agreement_original_metadata",
                    ),
                    models.CheckConstraint(
                        condition=(~models.Q(processing_state="ready") | (~models.Q(original_key="") & ~models.Q(pdf_key="") & ~models.Q(pdf_sha256="") & models.Q(pdf_size_bytes__isnull=False, pdf_size_bytes__gt=0, page_count__isnull=False, page_count__gt=0, validated_at__isnull=False, detected_format__in=("pdf", "docx"), processing_error=""))),
                        name="agreement_ready_metadata",
                    ),
                    models.CheckConstraint(
                        condition=(models.Q(processing_state="ready") | models.Q(pdf_key="", pdf_sha256="", pdf_size_bytes__isnull=True, page_count__isnull=True, page_geometry=[], validated_at__isnull=True)),
                        name="agreement_pending_pdf_empty",
                    ),
                    models.CheckConstraint(
                        condition=((models.Q(processing_state="failed") & ~models.Q(processing_error="")) | (~models.Q(processing_state="failed") & models.Q(processing_error=""))),
                        name="agreement_failure_detail",
                    ),
                ],
            },
        ),
    ]
