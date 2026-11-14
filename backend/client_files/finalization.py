"""Retryable, company-scoped completion of the exact accepted signing request."""

import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from agreements.processing import DocumentError, PdfProcessor
from agreements.storage import PrivateDocumentStorage
from .models import ClientFile, SigningCompletion, SigningRequest
from .submissions import ClientSigning, CONSENT_TEXT, SigningError


class AgreementFinalizer:
    def __init__(self, company_id):
        self.company_id = company_id

    def issued(self, file_id, *, lock=False):
        if lock:
            if not ClientFile.objects.filter(pk=file_id, company_id=self.company_id).update(updated_at=F("updated_at")):
                raise SigningError("Client file not found.", status=404)
        issued = SigningRequest.objects.select_related("client_file", "template_version__agreement").filter(
            client_file_id=file_id, client_file__company_id=self.company_id,
            template_version__agreement__company_id=self.company_id,
        ).first()
        if issued is None:
            raise SigningError("Signing request not found.", status=404)
        return issued

    @staticmethod
    def accepted(issued):
        issued.full_clean()
        version = issued.template_version
        version.full_clean()
        recipients = {recipient.role: recipient for recipient in issued.recipients.all()}
        if set(recipients) != set(version.roles):
            raise SigningError("Required signer assignments do not match the issued version.", status=409)
        submissions = list(issued.submissions.select_related("recipient", "invitation").all())
        if len(submissions) != len(recipients) or {s.recipient_id for s in submissions} != {r.pk for r in recipients.values()}:
            raise SigningError("Every required signer must have an accepted submission before finalization.", status=409)
        by_role = {}
        for submission in submissions:
            submission.full_clean()
            if (submission.template_version_id != version.pk or submission.pdf_sha256 != version.pdf_sha256
                    or submission.consent_text != CONSENT_TEXT
                    or submission.submitted_at < issued.created_at
                    or (issued.expires_at and submission.submitted_at > issued.expires_at)):
                raise SigningError("A signer submission does not match this request, version, consent or signing deadline.", status=409)
            values = ClientSigning.validate_values(submission.recipient, submission.values, complete=True)
            if values != submission.values:
                raise SigningError("Accepted values no longer match the recorded submission.", status=409)
            by_role[submission.recipient.role] = submission
        if "company" in by_role and by_role["company"].submitted_at < by_role["client"].submitted_at:
            raise SigningError("The company signature must follow the accepted client signature.", status=409)
        return [by_role[role] for role in version.roles]

    @staticmethod
    def storage(issued):
        return PrivateDocumentStorage(issued.client_file.company_id, issued.template_version.agreement_id)

    def run(self, file_id):
        # Reserve identity in a short transaction before any external artifact write.
        with transaction.atomic():
            issued = self.issued(file_id, lock=True)
            if issued.status == ClientFile.Status.COMPLETED:
                return self.snapshot(issued)
            if issued.status != ClientFile.Status.FINALIZING:
                raise SigningError("This request is still awaiting required signatures.", status=409)
            self.accepted(issued)
            storage = self.storage(issued)
            key = f"{storage.prefix}/{uuid.uuid5(issued.pk, 'completed-pdf-v1')}.bin"
            SigningCompletion.objects.get_or_create(signing_request=issued, defaults={"pdf_key": key})
        try:
            with transaction.atomic():
                issued = self.issued(file_id, lock=True)
                completion = SigningCompletion.objects.get(signing_request=issued)
                if completion.completed_at:
                    return self.snapshot(issued)
                if issued.status != ClientFile.Status.FINALIZING:
                    raise SigningError("This request is unavailable for finalization.", status=409)
                submissions = self.accepted(issued)
                version, storage = issued.template_version, self.storage(issued)
                if completion.pdf_key in (version.agreement.original_key, version.agreement.pdf_key):
                    raise SigningError("Completion cannot replace an original or published artifact.", status=409)
                source = storage.inspect(version.agreement.pdf_key, max_bytes=settings.AGREEMENT_MAX_BYTES)
                if source.sha256 != version.pdf_sha256 or source.size_bytes != version.agreement.pdf_size_bytes:
                    raise SigningError("The published source PDF does not match the issued agreement.", status=409)
                values = {key: value for submission in submissions for key, value in submission.values.items()}
                fields = [{**field, "value": values[field["id"]]} for field in version.fields]
                try:
                    artifact = storage.inspect(completion.pdf_key, max_bytes=settings.AGREEMENT_MAX_BYTES)
                except FileNotFoundError:
                    with storage.open(source.key) as handle:
                        output = PdfProcessor().complete(handle.name, fields)
                    artifact = storage.write_once(completion.pdf_key, [output], max_bytes=settings.AGREEMENT_MAX_BYTES)
                # Reopen the saved bytes, not just the in-memory generation result.
                try:
                    with storage.open(artifact.key) as handle:
                        pages = PdfProcessor().validate(handle.name)
                    if len(pages) != version.agreement.page_count or pages != version.agreement.page_geometry:
                        raise DocumentError("Completed PDF page count or geometry does not match the issued agreement. Retry finalization.")
                except DocumentError:
                    # Only an unaccepted reserved output may be discarded for regeneration.
                    storage.delete(completion.pdf_key)
                    raise
                now = timezone.now()
                completion.pdf_sha256, completion.pdf_size_bytes = artifact.sha256, artifact.size_bytes
                completion.page_count = len(pages)
                completion.history = self.history(issued, submissions, completion, now)
                completion.completed_at, completion.last_error = now, ""
                completion.save()
                # History and completion status commit together, after the durable PDF.
                issued.client_file.status = ClientFile.Status.COMPLETED
                issued.client_file.save(update_fields=["status", "updated_at"])
                return self.snapshot(issued)
        except (SigningError, ValidationError, DocumentError, OSError, ValueError, SuspiciousFileOperation, DatabaseError) as error:
            message = str(error) if isinstance(error, (SigningError, DocumentError)) else "Final-document generation or storage could not be confirmed. Retry finalization; recorded signatures are preserved."
            try:
                with transaction.atomic():
                    issued = self.issued(file_id, lock=True)
                    completion = SigningCompletion.objects.get(signing_request=issued)
                    if not completion.completed_at:
                        completion.last_error = message
                        completion.save(update_fields=["last_error"])
            except DatabaseError:
                pass
            raise SigningError(message, status=503, code="finalization_failed") from error

    @staticmethod
    def history(issued, submissions, completion, now):
        return {
            "schemaVersion": 1, "requestId": str(issued.pk), "clientFileId": str(issued.client_file_id),
            "templateVersionId": str(issued.template_version_id), "templateVersionNumber": issued.template_version.number,
            "sourcePdfSha256": issued.template_version.pdf_sha256,
            "completedPdfSha256": completion.pdf_sha256, "pageCount": completion.page_count,
            "signers": [{
                "role": s.recipient.role, "name": s.recipient.name, "email": s.recipient.email,
                "recipientId": str(s.recipient_id), "submissionId": str(s.pk),
                "submittedAt": s.submitted_at.isoformat(), "signatureMethod": s.signature_method,
                "companyUserId": s.company_user_id, "consentText": s.consent_text,
            } for s in submissions],
            "events": [
                {"type": "request_issued", "at": issued.created_at.isoformat()},
                *[{"type": "submission_accepted", "role": s.recipient.role, "submissionId": str(s.pk), "at": s.submitted_at.isoformat()} for s in submissions],
                {"type": "finalization_started", "at": completion.started_at.isoformat()},
                {"type": "completed_pdf_saved_and_verified", "at": now.isoformat()},
                {"type": "request_completed", "at": now.isoformat()},
            ],
        }

    @staticmethod
    def snapshot(issued):
        completion = SigningCompletion.objects.filter(signing_request=issued).first()
        complete = bool(completion and completion.completed_at and issued.status == ClientFile.Status.COMPLETED)
        return {
            "state": "completed" if complete else issued.status,
            "error": completion.last_error if completion else "",
            "completedAt": completion.completed_at.isoformat() if complete else None,
            "retryUrl": reverse("company_file_finalize", args=[issued.client_file_id]),
            "pdfUrl": reverse("company_completed_pdf", args=[issued.client_file_id]) if complete else None,
            "historyUrl": reverse("company_signing_history", args=[issued.client_file_id]) if complete else None,
        }

    def open_completed(self, file_id):
        issued = self.issued(file_id)
        completion = SigningCompletion.objects.filter(signing_request=issued, completed_at__isnull=False).first()
        if issued.status != ClientFile.Status.COMPLETED or completion is None:
            raise SigningError("Final artifacts are not yet available. Retry finalization from the client file.", status=409)
        issued.full_clean()
        if (completion.history.get("requestId") != str(issued.pk)
                or completion.history.get("templateVersionId") != str(issued.template_version_id)
                or completion.history.get("completedPdfSha256") != completion.pdf_sha256):
            raise SigningError("The completion record does not match this issued agreement.", status=503)
        storage = self.storage(issued)
        handle = storage.open(completion.pdf_key)
        try:
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                size += len(chunk)
                if size > completion.pdf_size_bytes:
                    raise SigningError("The completed artifact failed its integrity check.", status=503)
                digest.update(chunk)
            if size != completion.pdf_size_bytes or digest.hexdigest() != completion.pdf_sha256:
                raise SigningError("The completed artifact failed its integrity check.", status=503)
            handle.seek(0)
            return completion, handle
        except BaseException:
            handle.close()
            raise
