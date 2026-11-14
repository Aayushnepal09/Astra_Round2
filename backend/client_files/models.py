import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower


class ClientFile(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft · needs setup"
        AWAITING_CLIENT = "awaiting_client", "Awaiting client"
        AWAITING_COMPANY = "awaiting_company", "Awaiting company"
        FINALIZING = "finalizing", "Finalizing"
        COMPLETED = "completed", "Ready to start"

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
    template_version = models.ForeignKey(
        "agreements.TemplateVersion", on_delete=models.PROTECT,
        null=True, blank=True, related_name="client_files",
    )
    company_signer_name = models.CharField(max_length=200, blank=True)
    company_signer_email = models.EmailField(blank=True)
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
                condition=models.Q(status__in=("draft", "awaiting_client", "awaiting_company", "finalizing", "completed")), name="clientfile_draft_status"
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
            "company_signer_name", "company_signer_email",
        ):
            value = getattr(self, name)
            if isinstance(value, str):
                value = value.strip()
                setattr(self, name, value.lower() if name.endswith("_email") else value)
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.template_version_id and self.template_version.agreement.company_id != self.company_id:
            raise ValidationError({"template_version": "Choose a published version from this workspace."})
        if self.status == self.Status.COMPLETED and not SigningCompletion.objects.filter(
                signing_request__client_file_id=self.pk, completed_at__isnull=False).exists():
            raise ValidationError("Save the completed PDF and signing history before marking this file ready.")

    def setup_issues(self):
        if not self.template_version_id:
            return ["Select a published template version."]
        missing = []
        if not self.signer_name:
            missing.append("Add the client signer's name.")
        if not self.signer_email:
            missing.append("Add the client signer's email.")
        if "company" in self.template_version.roles:
            if not self.company_signer_name:
                missing.append("Add the company countersigner's name.")
            if not self.company_signer_email:
                missing.append("Add the company countersigner's email.")
        return missing

    def save(self, *args, **kwargs):
        # Model writes must validate even when they do not come from a form.
        self.full_clean()
        return super().save(*args, **kwargs)


class SigningRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_file = models.OneToOneField(ClientFile, on_delete=models.PROTECT, related_name="signing_request")
    template_version = models.ForeignKey("agreements.TemplateVersion", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    @property
    def status(self):
        return self.client_file.status

    def clean(self):
        super().clean()
        if (self.client_file.company_id != self.template_version.agreement.company_id
                or self.client_file.template_version_id != self.template_version_id):
            raise ValidationError("Requests must use the client file's published version in its own workspace.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Issued request bindings cannot be changed.")
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)


class SigningRecipient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    signing_request = models.ForeignKey(SigningRequest, on_delete=models.PROTECT, related_name="recipients")
    role = models.CharField(max_length=8, choices=(("client", "Client"), ("company", "Company")))
    name = models.CharField(max_length=200)
    email = models.EmailField()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Issued recipient details cannot be changed.")
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["signing_request", "role"], name="request_recipient_role_unique"),
            models.CheckConstraint(condition=models.Q(role__in=("client", "company")), name="request_recipient_role_valid"),
        ]


class SigningAccess(models.Model):
    invitation = models.OneToOneField("login.ClientInvitation", on_delete=models.PROTECT, related_name="signing_access")
    recipient = models.ForeignKey(SigningRecipient, on_delete=models.PROTECT, related_name="access_codes")


class SigningDraft(models.Model):
    recipient = models.OneToOneField(SigningRecipient, on_delete=models.PROTECT, related_name="draft")
    values = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class SigningSubmissionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Accepted signing submissions cannot be edited.")

    def delete(self):
        raise ValidationError("Accepted signing submissions must be preserved.")


class SigningSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.OneToOneField(SigningRecipient, on_delete=models.PROTECT, related_name="submission")
    signing_request = models.ForeignKey(SigningRequest, on_delete=models.PROTECT, related_name="submissions")
    template_version = models.ForeignKey("agreements.TemplateVersion", on_delete=models.PROTECT)
    invitation = models.ForeignKey("login.ClientInvitation", on_delete=models.PROTECT, null=True, blank=True)
    company_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    submission_key = models.UUIDField()
    pdf_sha256 = models.CharField(max_length=64)
    values = models.JSONField()
    consent_text = models.TextField()
    signature_method = models.CharField(max_length=16, default="typed")
    submitted_at = models.DateTimeField(auto_now_add=True)
    objects = SigningSubmissionQuerySet.as_manager()

    def clean(self):
        super().clean()
        issued = self.recipient.signing_request
        if (self.signing_request_id != issued.pk or self.template_version_id != issued.template_version_id
                or self.pdf_sha256 != issued.template_version.pdf_sha256
                or issued.client_file.company_id != issued.template_version.agreement.company_id
                or issued.client_file.template_version_id != self.template_version_id
                or self.signature_method != "typed"):
            raise ValidationError("The submission must belong to this verified signer and exact issued agreement.")
        if self.recipient.role == "client":
            if (self.company_user_id or not self.invitation_id
                    or not SigningAccess.objects.filter(invitation_id=self.invitation_id, recipient=self.recipient).exists()):
                raise ValidationError("Client submissions require their verified invitation.")
        elif self.recipient.role == "company":
            if self.invitation_id or self.company_user_id != issued.client_file.company_id:
                raise ValidationError("Company submissions require the authenticated workspace owner.")
        else:
            raise ValidationError("Unsupported signer role.")

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(invitation__isnull=False, company_user__isnull=True)
                       | models.Q(invitation__isnull=True, company_user__isnull=False)),
            name="submission_auth_source",
        )]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Accepted signing submissions cannot be edited.")
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Accepted signing submissions must be preserved.")


class SigningCompletionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Use the finalization service to update completion records.")

    def delete(self):
        raise ValidationError("Completion records and signing history must be preserved.")


class SigningCompletion(models.Model):
    signing_request = models.OneToOneField(SigningRequest, on_delete=models.PROTECT, related_name="completion")
    pdf_key = models.CharField(max_length=255)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    pdf_size_bytes = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    history = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    objects = SigningCompletionQuerySet.as_manager()

    def save(self, *args, **kwargs):
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.completed_at:
            raise ValidationError("Completed artifacts and signing history cannot be edited.")
        if previous and (previous.signing_request_id != self.signing_request_id or previous.pdf_key != self.pdf_key):
            raise ValidationError("Finalization bindings cannot be changed.")
        issued = self.signing_request
        from agreements.storage import PrivateDocumentStorage
        PrivateDocumentStorage.validate_key(self.pdf_key, issued.client_file.company_id, issued.template_version.agreement_id)
        if issued.client_file.company_id != issued.template_version.agreement.company_id:
            raise ValidationError("Finalization must belong to its agreement workspace.")
        if self.completed_at and not (self.history and self.pdf_sha256 and self.pdf_size_bytes and self.page_count):
            raise ValidationError("Save the completed PDF and signing history before completion.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Completion records must be preserved.")
