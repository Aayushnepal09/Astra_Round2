"""Issue fixed demo requests and reuse the existing client access gate."""

import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from agreements.storage import PrivateDocumentStorage
from agreements.processing import DocumentError, PdfProcessor
from login.client_throttle import ClientAccessThrottle
from login.models import ClientInvitation
from .models import ClientFile, SigningAccess, SigningRecipient, SigningRequest
from .preparation import PreparationValues
from .lifecycle import RequestLifecycleError, lock_file, save_file_state, refresh_file_expiry


def demo_code(invitation):
    # Redisplay without persisting a recoverable plaintext code. The server secret
    # and random invitation UUID are both needed; verification still uses its hash.
    digest = salted_hmac("signing.demo-code.v1", str(invitation.pk), algorithm="sha256").hexdigest()
    return f"{int(digest, 16) % 1_000_000:06d}"


def invitation_state(invitation):
    if invitation.revoked_at:
        return "replaced"
    if invitation.used_at:
        return "verified"
    if invitation.expires_at <= timezone.now():
        return "expired"
    if invitation.attempts >= settings.CLIENT_MAX_ATTEMPTS:
        return "locked"
    return "pending"


def signing_recipient(invitation):
    access = SigningAccess.objects.select_related(
        "recipient__signing_request__template_version__agreement", "recipient__signing_request__client_file",
    ).filter(invitation=invitation).first()
    if access is None:
        return None
    recipient = access.recipient
    issued = recipient.signing_request
    issued.client_file = refresh_file_expiry(issued.client_file)
    if issued.client_file.current_request_id == issued.pk:
        issued = issued.client_file.current_request
        recipient.signing_request = issued
    if (recipient.role != "client" or issued.client_file.current_request_id != issued.pk
            or issued.status != issued.client_file.status or issued.status not in (
            ClientFile.Status.AWAITING_CLIENT, ClientFile.Status.AWAITING_COMPANY, ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
            or (issued.status not in (ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
                and issued.expires_at is not None and issued.expires_at <= timezone.now())
            or issued.client_file.company_id != invitation.company_id
            or issued.template_version.agreement.company_id != invitation.company_id
            or issued.client_file.template_version_id != issued.template_version_id
            or invitation.request_ref != str(issued.pk) or recipient.email != invitation.client_email):
        return None
    return recipient


class DemoRequestIssuer:
    def __init__(self, company):
        self.company = company

    def _lock_file(self, file_id):
        return lock_file(file_id, self.company.pk)

    @transaction.atomic
    def issue(self, file_id, *, template_version_id=None, updated_at=None, replaces_request_id=None):
        file = self._lock_file(file_id)
        existing = file.current_request
        if existing and existing.status not in (SigningRequest.Status.CANCELLED, SigningRequest.Status.EXPIRED):
            if (str(existing.replaces_id) if existing.replaces_id else None) != replaces_request_id:
                raise RequestLifecycleError("The current request changed. Reload the file before sending.")
            return existing, False
        if (str(file.current_request_id) if file.current_request_id else None) != replaces_request_id:
            raise RequestLifecycleError("The replacement setup changed. Reload the file before sending.")
        if str(file.template_version_id) != template_version_id or file.updated_at.isoformat() != updated_at:
            raise ValidationError("The saved setup changed. Reload the file, review its details, then send again.")
        if file.status != ClientFile.Status.DRAFT:
            raise ValidationError("This file cannot issue a new request.")
        file.full_clean()
        if not file.template_version_id:
            raise ValidationError("Select a published template version before sending.")
        version = file.template_version
        version.full_clean()
        preparation_values = PreparationValues.validate(version, file.preparation_values, complete=True)
        issues = file.setup_issues()
        if issues:
            raise ValidationError(issues)
        agreement = version.agreement
        agreement.full_clean()
        storage = PrivateDocumentStorage(self.company.pk, agreement.pk)
        digest, size = hashlib.sha256(), 0
        with storage.open(agreement.pdf_key) as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                size += len(chunk)
                if size > agreement.pdf_size_bytes:
                    break
                digest.update(chunk)
        if size != agreement.pdf_size_bytes or digest.hexdigest() != version.pdf_sha256:
            raise ValidationError("The published PDF no longer matches its validated identity. Prepare a new agreement.")
        prepared_fields = [{**field, "value": preparation_values[field["id"]]}
                           for field in PreparationValues.fields(version)]
        if prepared_fields:
            try:
                with storage.open(agreement.pdf_key) as source:
                    PdfProcessor().complete(source.name, prepared_fields)
            except DocumentError as error:
                raise ValidationError({"preparation_values":
                    "Company preparation could not be rendered. Check field sizes and entered characters, "
                    "then retry. No request was issued. " + str(error)
                }) from error
        issued = SigningRequest.objects.create(client_file=file, template_version=version,
                                               preparation_values=preparation_values, replaces=existing,
                                               expires_at=timezone.now() + timedelta(seconds=settings.SIGNING_REQUEST_TTL))
        for role in version.roles:
            prefix = "signer" if role == "client" else "company_signer"
            recipient = SigningRecipient.objects.create(
                signing_request=issued, role=role,
                name=getattr(file, f"{prefix}_name"), email=getattr(file, f"{prefix}_email"),
            )
            if role == "client":
                self._create_code(recipient)
        file.status = ClientFile.Status.AWAITING_CLIENT
        file.current_request = issued
        save_file_state(file, ["status", "current_request"])
        return issued, True

    def _create_code(self, recipient):
        code_expiry = timezone.now() + timedelta(seconds=settings.CLIENT_CODE_TTL)
        issued = recipient.signing_request
        if issued.status not in (ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED) and issued.expires_at:
            code_expiry = min(code_expiry, issued.expires_at)
        invitation = ClientInvitation(
            company=self.company, client_email=recipient.email,
            request_ref=str(recipient.signing_request_id),
            expires_at=code_expiry,
        )
        with ClientAccessThrottle.generation(invitation):
            invitation.code_hash = make_password(demo_code(invitation))
            invitation.full_clean()
            invitation.save()
            SigningAccess.objects.create(invitation=invitation, recipient=recipient)
        return invitation

    @transaction.atomic
    def replace_code(self, file_id, previous_id):
        file = self._lock_file(file_id)
        recipient = SigningRecipient.objects.get(signing_request_id=file.current_request_id, role="client")
        if not ClientInvitation.objects.filter(pk=previous_id, signing_access__recipient=recipient).exists():
            raise RequestLifecycleError("This code belongs to an earlier request. Reload the client file.")
        if (file.status not in (ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
                and recipient.signing_request.expires_at and recipient.signing_request.expires_at <= timezone.now()):
            raise ValidationError("This signing request has expired. A replacement code cannot extend it.")
        current = ClientInvitation.objects.filter(signing_access__recipient=recipient).order_by("-created_at", "-id").first()
        if current is None or signing_recipient(current) is None:
            raise ValidationError("This request is unavailable for client access.")
        if (file.status != ClientFile.Status.AWAITING_CLIENT
                and not recipient.signing_request.submissions.filter(recipient=recipient).exists()):
            raise ValidationError("Read-only access requires the client's recorded submission.")
        # Reconcile a retry after the replacement response was lost.
        if str(current.pk) != previous_id:
            return recipient.signing_request
        if invitation_state(current) not in ("expired", "locked", "verified"):
            raise ValidationError("The current code is still available. Use it before requesting a replacement.")
        self._create_code(recipient)
        # Existing verified sessions remain valid; only unused codes are revoked.
        if not current.used_at:
            current.revoked_at = timezone.now()
            current.save(update_fields=["revoked_at"])
        return recipient.signing_request

    def snapshot(self, issued):
        file = refresh_file_expiry(issued.client_file)
        if file.current_request_id == issued.pk:
            issued = file.current_request
            issued.client_file = file
        if (issued.client_file.company_id != self.company.pk
                or issued.template_version.agreement.company_id != self.company.pk):
            raise ClientFile.DoesNotExist
        recipient = issued.recipients.get(role="client")
        invitation = ClientInvitation.objects.filter(signing_access__recipient=recipient).order_by("-created_at", "-id").first()
        state = invitation_state(invitation)
        progress = issued.status
        available = signing_recipient(invitation) is not None
        read_only = issued.submissions.filter(recipient=recipient).exists()
        if progress == SigningRequest.Status.EXPIRED:
            state = "request_expired"
        if progress == SigningRequest.Status.CANCELLED:
            state = "cancelled"
        company_url = None
        if (state != "request_expired" and progress in (ClientFile.Status.AWAITING_COMPANY, ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
                and "company" in issued.template_version.roles
                and issued.submissions.filter(recipient__role="client").exists()):
            company_url = reverse("company_agreement", args=[issued.client_file_id])
        return {
            "requestId": str(issued.pk), "templateVersionId": str(issued.template_version_id),
            "requestExpiresAt": issued.expires_at.isoformat() if issued.expires_at else None,
            "recipient": {"name": recipient.name, "email": recipient.email},
            "preparationValues": dict(issued.preparation_values),
            "selectedTemplate": {
                "id": str(issued.template_version_id), "name": issued.template_version.name,
                "number": issued.template_version.number, "roles": issued.template_version.roles,
                "preparationFields": PreparationValues.fields(issued.template_version),
                "previewUrl": f'{reverse("company_dashboard")}#templates/published/{issued.template_version_id}',
            },
            "invitationId": str(invitation.pk), "state": state,
            "progress": progress,
            "readOnly": read_only,
            "accessAvailable": available,
            "companySigningUrl": company_url,
            "finalization": self.finalization_snapshot(issued),
            "link": reverse("client_invitation", args=[invitation.pk]),
            "code": demo_code(invitation) if available and state == "pending" else None,
            "expiresAt": invitation.expires_at.isoformat(), "serverNow": timezone.now().isoformat(),
            "canReplace": available and state in ("expired", "locked", "verified")
                and (progress == ClientFile.Status.AWAITING_CLIENT or read_only),
        }

    @staticmethod
    def finalization_snapshot(issued):
        from .finalization import AgreementFinalizer
        return AgreementFinalizer.snapshot(issued)
