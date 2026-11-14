"""Validate signer-owned values and atomically accept explicit signing intent."""

import re
import uuid
from datetime import date

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from login.common import client_identity
from .models import ClientFile, SigningDraft, SigningSubmission
from .signing import signing_recipient


CONSENT_TEXT = "I have reviewed this agreement and intend my typed name in each signature field to be my electronic signature when I select Sign & submit."


class SigningError(Exception):
    def __init__(self, message, *, status=422, fields=None, code="invalid_submission"):
        super().__init__(message)
        self.status, self.fields, self.code = status, fields or {}, code


class ClientSigning:
    @staticmethod
    def receipt(submission):
        return {
            "id": str(submission.pk), "submittedAt": submission.submitted_at.isoformat(),
            "requestId": str(submission.signing_request_id),
            "templateVersionId": str(submission.template_version_id),
            "signerName": submission.recipient.name, "signatureMethod": submission.signature_method,
        }

    @classmethod
    def snapshot(cls, recipient):
        issued = recipient.signing_request
        draft = SigningDraft.objects.filter(recipient=recipient).first()
        submission = SigningSubmission.objects.filter(recipient=recipient).first()
        return {
            "requestId": str(issued.pk), "templateVersionId": str(issued.template_version_id),
            "recipientId": str(recipient.pk), "pdfSha256": issued.template_version.pdf_sha256,
            "fields": [field for field in issued.template_version.fields if field["role"] == recipient.role],
            "values": submission.values if submission else draft.values if draft else {},
            "revision": draft.revision if draft else 0,
            "consentText": CONSENT_TEXT, "progress": issued.client_file.status,
            "receipt": cls.receipt(submission) if submission else None,
        }

    @staticmethod
    def validate_values(recipient, supplied, *, complete):
        if not isinstance(supplied, dict):
            raise SigningError("Send the assigned field values as an object.")
        fields = {field["id"]: field for field in recipient.signing_request.template_version.fields if field["role"] == recipient.role}
        if set(supplied) - set(fields):
            raise SigningError("Only fields assigned to this signer can be submitted.")
        errors, values = {}, {}
        for key, field in fields.items():
            value = supplied.get(key, False if field["type"] == "acknowledgement" else "")
            if field["type"] == "acknowledgement":
                if type(value) is not bool:
                    errors[key] = ["Use a checked or unchecked acknowledgement."]
                elif complete and field["required"] and not value:
                    errors[key] = ["Acknowledge this statement before signing."]
            elif not isinstance(value, str):
                errors[key] = ["Enter a text value."]
            else:
                value = value.strip()
                if complete and field["required"] and not value:
                    errors[key] = ["Complete this required field."]
                elif any(ord(character) < 32 and character not in "\n\r\t" for character in value):
                    errors[key] = ["Remove unsupported control characters."]
                elif complete and value:
                    if field["type"] == "date":
                        try:
                            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                                raise ValueError
                            date.fromisoformat(value)
                        except ValueError:
                            errors[key] = ["Enter a valid date in YYYY-MM-DD format."]
                    elif field["type"] in ("name", "signature") and not any(character.isalpha() for character in value):
                        errors[key] = ["Enter a name containing letters."]
                    elif field["type"] == "signature" and " ".join(value.split()).casefold() != " ".join(recipient.name.split()).casefold():
                        errors[key] = [f"Type your assigned signer name: {recipient.name}."]
                    elif field["type"] == "initials" and (not any(character.isalpha() for character in value)
                            or any(not (character.isalpha() or character in ". -’'") for character in value)):
                        errors[key] = ["Enter initials using letters, spaces, periods or hyphens."]
            values[key] = value
        if errors:
            raise SigningError("Check the highlighted fields. Your entered values are still here.", fields=errors)
        return values

    @classmethod
    @transaction.atomic
    def act(cls, request, recipient, payload):
        # Serialize draft saves and submissions, including on SQLite.
        ClientFile.objects.filter(pk=recipient.signing_request.client_file_id).update(updated_at=F("updated_at"))
        invitation = client_identity(request, touch=False)
        current = signing_recipient(invitation) if invitation else None
        if current is None or current.pk != recipient.pk:
            raise SigningError("Your access has expired. Verify a replacement code, then retry. Your entered values are still here.", status=401, code="access_expired")
        recipient = current
        issued = recipient.signing_request
        if issued.expires_at and issued.expires_at <= timezone.now():
            raise SigningError("This request has expired. Contact the company.", status=410, code="request_expired")
        expected = {
            "requestId": str(issued.pk), "templateVersionId": str(issued.template_version_id),
            "recipientId": str(recipient.pk), "pdfSha256": issued.template_version.pdf_sha256,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise SigningError("The signer or agreement version does not match this request. Reopen your invitation.", status=409)
        action = payload.get("action")
        if action not in ("save", "submit") or set(payload) - {*expected, "action", "values", "revision", "consent", "submissionKey"}:
            raise SigningError("Choose Save progress or Sign & submit.")
        issued.template_version.full_clean()
        values = cls.validate_values(recipient, payload.get("values"), complete=action == "submit")
        existing = SigningSubmission.objects.filter(recipient=recipient).first()
        if action == "submit":
            if payload.get("consent") is not True:
                raise SigningError("Confirm your intent to sign, then select Sign & submit.", fields={"consent": ["Consent is required to sign."]})
            try:
                key = uuid.UUID(payload.get("submissionKey", ""))
            except (ValueError, TypeError, AttributeError):
                raise SigningError("Recheck the submission status before retrying.")
            if existing:
                if existing.values != values:
                    raise SigningError("A submission was already accepted. Check its confirmation; accepted values cannot be changed.", status=409, code="already_submitted")
                return cls.snapshot(recipient)
        elif existing:
            raise SigningError("This agreement was already submitted. Check its confirmation.", status=409, code="already_submitted")
        if issued.client_file.status != ClientFile.Status.AWAITING_CLIENT:
            raise SigningError("This request is not awaiting your signature.", status=409)
        draft = SigningDraft.objects.filter(recipient=recipient).first()
        revision = draft.revision if draft else 0
        if type(payload.get("revision")) is not int or payload["revision"] != revision:
            if action == "save" and draft and draft.values == values:
                return cls.snapshot(recipient)
            raise SigningError("Another tab saved progress. Your entries are still here. Reload saved progress before trying again.", status=409, code="draft_conflict")
        if action == "save":
            SigningDraft.objects.update_or_create(recipient=recipient, defaults={"values": values, "revision": revision + 1})
        else:
            if client_identity(request, touch=False) is None or (issued.expires_at and issued.expires_at <= timezone.now()):
                raise SigningError("Your access or request has expired. Verify access again before retrying.", status=401, code="access_expired")
            SigningSubmission.objects.create(
                recipient=recipient, signing_request=issued, template_version=issued.template_version,
                invitation=invitation, submission_key=key, pdf_sha256=issued.template_version.pdf_sha256,
                values=values, consent_text=CONSENT_TEXT,
            )
            file = issued.client_file
            file.status = (ClientFile.Status.AWAITING_COMPANY if issued.recipients.filter(role="company").exists()
                           else ClientFile.Status.FINALIZING)
            file.save(update_fields=["status", "updated_at"])
        return cls.snapshot(recipient)
