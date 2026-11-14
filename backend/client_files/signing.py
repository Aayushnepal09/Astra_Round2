"""Issue fixed demo requests and reuse the existing client access gate."""

import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from agreements.storage import PrivateDocumentStorage
from login.client_throttle import ClientAccessThrottle
from login.models import ClientInvitation
from .models import ClientFile, SigningAccess, SigningRecipient, SigningRequest


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
    if (recipient.role != "client" or issued.client_file.status not in (
            ClientFile.Status.AWAITING_CLIENT, ClientFile.Status.AWAITING_COMPANY, ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
            or (issued.client_file.status not in (ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
                and issued.expires_at is not None and issued.expires_at <= timezone.now())
            or issued.client_file.company_id != invitation.company_id
            or issued.template_version.agreement.company_id != invitation.company_id
            or invitation.request_ref != str(issued.pk) or recipient.email != invitation.client_email):
        return None
    return recipient


class DemoRequestIssuer:
    def __init__(self, company):
        self.company = company

    def _lock_file(self, file_id):
        files = ClientFile.objects.filter(pk=file_id, company=self.company)
        # Acquire a writer before reading on SQLite as well as row-locking DBs.
        if not files.update(updated_at=F("updated_at")):
            raise ClientFile.DoesNotExist
        return files.select_related("template_version__agreement").get()

    @transaction.atomic
    def issue(self, file_id):
        file = self._lock_file(file_id)
        existing = SigningRequest.objects.filter(client_file=file).first()
        if existing:
            return existing, False
        if file.status != ClientFile.Status.DRAFT:
            raise ValidationError("This file cannot issue a new request.")
        file.full_clean()
        if not file.template_version_id:
            raise ValidationError("Select a published template version before sending.")
        version = file.template_version
        version.full_clean()
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
        issued = SigningRequest.objects.create(client_file=file, template_version=version)
        for role in version.roles:
            prefix = "signer" if role == "client" else "company_signer"
            recipient = SigningRecipient.objects.create(
                signing_request=issued, role=role,
                name=getattr(file, f"{prefix}_name"), email=getattr(file, f"{prefix}_email"),
            )
            if role == "client":
                self._create_code(recipient)
        file.status = ClientFile.Status.AWAITING_CLIENT
        file.save(update_fields=["status", "updated_at"])
        return issued, True

    def _create_code(self, recipient):
        invitation = ClientInvitation(
            company=self.company, client_email=recipient.email,
            request_ref=str(recipient.signing_request_id),
            expires_at=timezone.now() + timedelta(seconds=settings.CLIENT_CODE_TTL),
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
        recipient = SigningRecipient.objects.get(signing_request__client_file=file, role="client")
        if recipient.signing_request.expires_at and recipient.signing_request.expires_at <= timezone.now():
            raise ValidationError("This signing request has expired. A replacement code cannot extend it.")
        current = ClientInvitation.objects.filter(signing_access__recipient=recipient).order_by("-created_at", "-id").first()
        # Reconcile a retry after the replacement response was lost.
        if str(current.pk) != previous_id:
            return recipient.signing_request
        if file.status != ClientFile.Status.AWAITING_CLIENT:
            raise ValidationError("This request is not awaiting the client.")
        if invitation_state(current) not in ("expired", "locked", "verified"):
            raise ValidationError("The current code is still available. Use it before requesting a replacement.")
        self._create_code(recipient)
        # Existing verified sessions remain valid; only unused codes are revoked.
        if not current.used_at:
            current.revoked_at = timezone.now()
            current.save(update_fields=["revoked_at"])
        return recipient.signing_request

    def snapshot(self, issued):
        if (issued.client_file.company_id != self.company.pk
                or issued.template_version.agreement.company_id != self.company.pk):
            raise ClientFile.DoesNotExist
        recipient = issued.recipients.get(role="client")
        invitation = ClientInvitation.objects.filter(signing_access__recipient=recipient).order_by("-created_at", "-id").first()
        state = invitation_state(invitation)
        progress = issued.client_file.status
        if progress != ClientFile.Status.AWAITING_CLIENT:
            state = "submitted"
        if progress not in (ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED) and issued.expires_at and issued.expires_at <= timezone.now():
            state = "request_expired"
        company_url = None
        if (state != "request_expired" and progress in (ClientFile.Status.AWAITING_COMPANY, ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED)
                and "company" in issued.template_version.roles
                and issued.submissions.filter(recipient__role="client").exists()):
            company_url = reverse("company_agreement", args=[issued.client_file_id])
        return {
            "requestId": str(issued.pk), "templateVersionId": str(issued.template_version_id),
            "invitationId": str(invitation.pk), "state": state,
            "progress": progress,
            "companySigningUrl": company_url,
            "finalization": self.finalization_snapshot(issued),
            "link": reverse("client_invitation", args=[invitation.pk]),
            "code": demo_code(invitation) if state == "pending" else None,
            "expiresAt": invitation.expires_at.isoformat(), "serverNow": timezone.now().isoformat(),
            "canReplace": state in ("expired", "locked", "verified") and progress == ClientFile.Status.AWAITING_CLIENT,
        }

    @staticmethod
    def finalization_snapshot(issued):
        from .finalization import AgreementFinalizer
        return AgreementFinalizer.snapshot(issued)
