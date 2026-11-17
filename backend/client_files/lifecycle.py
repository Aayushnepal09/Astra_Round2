"""Serialize request lifecycle decisions on the owning client file."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from login.models import ClientInvitation
from .models import ClientFile, SigningCompletion, SigningRequest


class RequestLifecycleError(Exception):
    def __init__(self, message, *, code="request_changed", status=409):
        super().__init__(message)
        self.code, self.status = code, status


def lock_file(file_id, company_id=None):
    files = ClientFile.objects.filter(pk=file_id)
    if company_id is not None:
        files = files.filter(company_id=company_id)
    # UPDATE acquires a writer on SQLite and a row lock on other supported DBs.
    if not files.update(updated_at=F("updated_at")):
        raise ClientFile.DoesNotExist
    file = files.select_related("template_version__agreement", "current_request").get()
    expire_current(file)
    return file


def expire_current(file):
    """Reconcile a due request while holding its owning file's writer lock."""
    issued = file.current_request
    if (not issued or issued.status not in ("awaiting_client", "awaiting_company")
            or not issued.expires_at or issued.expires_at > timezone.now()):
        return
    issued.client_file = file
    recipients = set(issued.recipients.values_list("pk", flat=True))
    accepted = set(issued.submissions.values_list("recipient_id", flat=True))
    if recipients and recipients == accepted:
        from .finalization import AgreementFinalizer
        from .submissions import SigningError
        try:
            AgreementFinalizer.accepted(issued)
        except (ValidationError, SigningError):
            pass
        else:
            set_request_status(issued, SigningRequest.Status.FINALIZING)
            return
    set_request_status(issued, SigningRequest.Status.EXPIRED)
    ClientInvitation.objects.filter(signing_access__recipient__signing_request=issued,
                                    revoked_at__isnull=True).update(revoked_at=timezone.now())


def refresh_file_expiry(file):
    issued = file.current_request
    if (issued and issued.status in ("awaiting_client", "awaiting_company")
            and issued.expires_at and issued.expires_at <= timezone.now()):
        with transaction.atomic():
            return lock_file(file.pk, file.company_id)
    return file


def save_file_state(file, fields):
    previous = file.updated_at
    file.save(update_fields=[*fields, "updated_at"])
    if file.updated_at <= previous:
        file.updated_at = previous + timedelta(microseconds=1)
        ClientFile.objects.filter(pk=file.pk).update(updated_at=file.updated_at)


def set_request_status(issued, status, *, cancelled_by=None):
    """Called only while holding the owning file's writer lock."""
    transitions = {
        "awaiting_client": {"awaiting_company", "finalizing", "cancelled", "expired"},
        "awaiting_company": {"finalizing", "cancelled", "expired"},
        "finalizing": {"completed"},
    }
    if issued.client_file.current_request_id != issued.pk:
        raise RequestLifecycleError("This request is no longer current. Reload the client file.")
    if status != issued.status and status not in transitions.get(issued.status, set()):
        raise RequestLifecycleError("This request's recorded outcome cannot be changed.")
    issued.status = status
    if status == SigningRequest.Status.CANCELLED:
        issued.cancelled_at, issued.cancelled_by = timezone.now(), cancelled_by
    issued.full_clean()
    # Lifecycle fields may change; the public model save still rejects edits to
    # issued bindings, values and history.
    models.Model.save(issued, update_fields=["status", "cancelled_at", "cancelled_by"])
    file = issued.client_file
    file.current_request = issued
    file.status = status
    save_file_state(file, ["status"])


class RequestLifecycle:
    def __init__(self, company):
        self.company = company

    def _request(self, file, request_id):
        issued = SigningRequest.objects.select_related("client_file", "template_version__agreement").filter(
            pk=request_id, client_file=file, client_file__company=self.company,
            template_version__agreement__company=self.company,
        ).first()
        if issued is None:
            raise RequestLifecycleError("Signing request not found.", status=404)
        issued.client_file = file
        return issued

    @transaction.atomic
    def cancel(self, file_id, request_id):
        file = lock_file(file_id, self.company.pk)
        issued = self._request(file, request_id)
        if issued.status == SigningRequest.Status.CANCELLED:
            return file, "already_cancelled"
        if issued.status == SigningRequest.Status.EXPIRED:
            return file, "already_expired"
        if file.current_request_id != issued.pk:
            raise RequestLifecycleError("This request is no longer current. Reload the client file.")
        if issued.status == SigningRequest.Status.COMPLETED:
            raise RequestLifecycleError("Completed requests cannot be cancelled.", code="request_completed")
        recipients = set(issued.recipients.values_list("pk", flat=True))
        accepted = set(issued.submissions.values_list("recipient_id", flat=True))
        if recipients and recipients == accepted:
            from .finalization import AgreementFinalizer
            AgreementFinalizer.accepted(issued)
            completed = SigningCompletion.objects.filter(signing_request=issued, completed_at__isnull=False).exists()
            if issued.status != SigningRequest.Status.FINALIZING:
                set_request_status(issued, SigningRequest.Status.FINALIZING)
            if completed:
                set_request_status(issued, SigningRequest.Status.COMPLETED)
            return file, "signatures_complete"
        if issued.status == SigningRequest.Status.FINALIZING:
            raise ValidationError("Finalization requires reconciliation of the accepted signing records.")
        set_request_status(issued, SigningRequest.Status.CANCELLED, cancelled_by=self.company)
        ClientInvitation.objects.filter(signing_access__recipient__signing_request=issued).update(revoked_at=issued.cancelled_at)
        return file, "cancelled"

    @transaction.atomic
    def prepare_replacement(self, file_id, request_id):
        file = lock_file(file_id, self.company.pk)
        issued = self._request(file, request_id)
        if file.current_request_id != issued.pk or issued.status not in (SigningRequest.Status.CANCELLED, SigningRequest.Status.EXPIRED):
            raise RequestLifecycleError("Only the current cancelled or expired request can prepare a replacement. Reload the file.")
        if file.status == ClientFile.Status.DRAFT:
            return file, "already_prepared"
        if file.status not in (ClientFile.Status.CANCELLED, ClientFile.Status.EXPIRED):
            raise RequestLifecycleError("The saved request state changed. Reload the file.")
        file.template_version = issued.template_version
        file.preparation_values = dict(issued.preparation_values)
        for role, prefix in [("client", "signer"), ("company", "company_signer")]:
            recipient = issued.recipients.filter(role=role).first()
            setattr(file, f"{prefix}_name", recipient.name if recipient else "")
            setattr(file, f"{prefix}_email", recipient.email if recipient else "")
        file.status = ClientFile.Status.DRAFT
        save_file_state(file, ["status", "template_version", "preparation_values", "signer_name", "signer_email",
                               "company_signer_name", "company_signer_email"])
        return file, "replacement_prepared"
