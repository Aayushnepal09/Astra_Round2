import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class ClientFile(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft · needs setup"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creation_key = models.UUIDField(default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="client_files",
        editable=False,
    )
    reference = models.CharField(max_length=128)
    title = models.CharField(max_length=200, blank=True)
    client_name = models.CharField(max_length=200)
    client_email = models.EmailField(blank=True)
    signer_name = models.CharField(max_length=200, blank=True)
    signer_email = models.EmailField(blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "creation_key"], name="clientfile_company_key_uniq"
            ),
            models.UniqueConstraint(
                "company", Lower("reference"), name="clientfile_company_ref_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft"), name="clientfile_draft_status"
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "-updated_at", "-id"],
                name="clientfile_company_updated",
            ),
        ]

    def clean_fields(self, exclude=None):
        for name in (
            "reference", "title", "client_name", "client_email",
            "signer_name", "signer_email",
        ):
            value = getattr(self, name)
            if isinstance(value, str):
                value = value.strip()
                setattr(self, name, value.lower() if name.endswith("_email") else value)
        super().clean_fields(exclude=exclude)

    def save(self, *args, **kwargs):
        # Model writes must validate even when they do not come from a form.
        self.full_clean()
        return super().save(*args, **kwargs)
