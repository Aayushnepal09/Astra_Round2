"""Company-session access and sequential acceptance for the issued agreement."""

import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from login.common import company_authenticated
from .models import ClientFile, SigningDraft, SigningRecipient, SigningSubmission
from .submissions import ClientSigning, CONSENT_TEXT, SigningError


class CompanySigning(ClientSigning):
    @classmethod
    def recipient(cls, request, file_id):
        if not company_authenticated(request) or not request.user.is_active:
            raise SigningError("Company sign-in is required. Sign in in a new tab, then check status here.", status=401)
        recipient = SigningRecipient.objects.select_related(
            "signing_request__client_file", "signing_request__template_version__agreement",
        ).filter(
            signing_request__client_file_id=file_id, signing_request__client_file__company=request.user,
            signing_request__template_version__agreement__company=request.user, role="company",
        ).first()
        if recipient is None:
            raise SigningError("Company signing request not found.", status=404)
        issued = recipient.signing_request
        issued.full_clean()
        issued.template_version.full_clean()
        if "company" not in issued.template_version.roles:
            raise SigningError("This agreement does not require a company signer.", status=409)
        if issued.template_version.pdf_sha256 != issued.template_version.agreement.pdf_sha256:
            raise SigningError("The exact issued agreement is unavailable. Contact the company.", status=409)
        client = SigningSubmission.objects.filter(signing_request=issued, recipient__role="client").first()
        if client is None:
            raise SigningError("Awaiting the client's accepted submission. Refresh the client file after they sign.", status=409)
        client.full_clean()
        existing = SigningSubmission.objects.filter(recipient=recipient).exists()
        allowed = (ClientFile.Status.FINALIZING, ClientFile.Status.COMPLETED) if existing else (ClientFile.Status.AWAITING_COMPANY,)
        if issued.client_file.status not in allowed:
            raise SigningError("This request is not available for company countersigning.", status=409)
        if not existing and issued.expires_at and issued.expires_at <= timezone.now():
            raise SigningError("This signing request has expired. No further signatures can be accepted.", status=410)
        return recipient

    @classmethod
    def snapshot(cls, recipient):
        return {**super().snapshot(recipient), "role": "company"}

    @staticmethod
    def client_values(recipient):
        issued = recipient.signing_request
        submission = SigningSubmission.objects.get(signing_request=issued, recipient__role="client")
        return [
            {**field, "value": submission.values.get(field["id"], "")}
            for field in issued.template_version.fields if field["role"] == "client"
        ]

    @classmethod
    @transaction.atomic
    def act(cls, request, recipient, payload):
        # Acquire the same writer lock as client signing before reading state.
        ClientFile.objects.filter(
            pk=recipient.signing_request.client_file_id, company=request.user,
        ).update(updated_at=F("updated_at"))
        recipient = cls.recipient(request, recipient.signing_request.client_file_id)
        issued = recipient.signing_request
        expected = {
            "requestId": str(issued.pk), "templateVersionId": str(issued.template_version_id),
            "recipientId": str(recipient.pk), "pdfSha256": issued.template_version.pdf_sha256,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise SigningError("The signer or exact agreement version changed. Reopen this client file.", status=409)
        action = payload.get("action")
        if action not in ("save", "submit") or set(payload) - {*expected, "action", "values", "revision", "consent", "submissionKey"}:
            raise SigningError("Choose Save progress or Sign & submit.")
        values = cls.validate_values(recipient, payload.get("values"), complete=action == "submit")
        existing = SigningSubmission.objects.filter(recipient=recipient).first()
        if action == "submit":
            if payload.get("consent") is not True:
                raise SigningError("Confirm your intent to sign first.", fields={"consent": ["Consent is required to sign."]})
            try:
                key = uuid.UUID(payload.get("submissionKey", ""))
            except (ValueError, TypeError, AttributeError):
                raise SigningError("Check submission status before retrying.")
            if existing:
                if existing.values != values or existing.company_user_id != request.user.pk:
                    raise SigningError("A company submission is already recorded. Check its confirmation.", status=409, code="already_submitted")
                return cls.snapshot(recipient)
        elif existing:
            raise SigningError("A company submission is already recorded. Check its confirmation.", status=409, code="already_submitted")
        draft = SigningDraft.objects.filter(recipient=recipient).first()
        revision = draft.revision if draft else 0
        if type(payload.get("revision")) is not int or payload["revision"] != revision:
            if action == "save" and draft and draft.values == values:
                return cls.snapshot(recipient)
            raise SigningError("Another tab saved progress. Your entries remain here. Load saved progress before retrying.", status=409, code="draft_conflict")
        if action == "save":
            SigningDraft.objects.update_or_create(recipient=recipient, defaults={"values": values, "revision": revision + 1})
        else:
            cls.preflight(recipient, values)
            # Recheck the unfinished-stage deadline immediately before acceptance.
            if issued.expires_at and issued.expires_at <= timezone.now():
                raise SigningError("This signing request has expired.", status=410)
            SigningSubmission.objects.create(
                recipient=recipient, signing_request=issued, template_version=issued.template_version,
                company_user=request.user, submission_key=key, pdf_sha256=issued.template_version.pdf_sha256,
                values=values, consent_text=CONSENT_TEXT,
            )
            file = issued.client_file
            file.status = ClientFile.Status.FINALIZING
            file.save(update_fields=["status", "updated_at"])
        return cls.snapshot(recipient)
