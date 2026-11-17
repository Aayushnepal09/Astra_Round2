"""Restrict document assistance to the verified client's unsigned issued request."""

from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.utils import timezone

from agreements.models import Agreement
from agreements.storage import PrivateDocumentStorage
from .models import ClientFile, SigningSubmission
from .portal_access import client_access


class AssistantAccessError(Exception):
    def __init__(self, message, *, status=403, code="assistant_access_denied"):
        super().__init__(message)
        self.status, self.code = status, code


class AssistantAccess:
    @staticmethod
    def resolve(request, invitation_id, *, touch=True):
        """Resolve fresh scope before provider use and again before returning an answer."""
        invitation, recipient = client_access(request, invitation_id=invitation_id, touch=touch)
        if recipient is None:
            raise AssistantAccessError(
                "Your client access is unavailable. Verify your invitation before using the assistant.",
                status=401, code="access_expired",
            )

        issued = recipient.signing_request
        if (recipient.role != "client" or issued.status != ClientFile.Status.AWAITING_CLIENT
                or SigningSubmission.objects.filter(recipient=recipient).exists()):
            raise AssistantAccessError(
                "The assistant is available only before your signature is recorded. "
                "Read-only document access does not enable assistance.",
                code="assistant_read_only",
            )
        if issued.expires_at is not None and issued.expires_at <= timezone.now():
            raise AssistantAccessError(
                "This signing request has expired. The assistant is unavailable.",
                status=410, code="request_expired",
            )

        version = issued.template_version
        agreement = version.agreement
        if (issued.client_file.company_id != invitation.company_id
                or agreement.company_id != invitation.company_id
                or issued.client_file.template_version_id != version.pk
                or issued.client_file.current_request_id != issued.pk
                or recipient.email.casefold() != invitation.client_email.casefold()
                or agreement.processing_state != Agreement.ProcessingState.READY
                or version.pdf_sha256 != agreement.pdf_sha256):
            raise AssistantAccessError(
                "The issued agreement could not be validated. Contact the company.",
                status=409, code="issued_agreement_unavailable",
            )
        try:
            version.full_clean()
            issued.full_clean()
            PrivateDocumentStorage.validate_key(agreement.pdf_key, invitation.company_id, agreement.pk)
        except (ValidationError, SuspiciousFileOperation, ValueError) as error:
            raise AssistantAccessError(
                "The issued agreement configuration could not be validated. Contact the company.",
                status=409, code="issued_agreement_unavailable",
            ) from error

        return recipient
