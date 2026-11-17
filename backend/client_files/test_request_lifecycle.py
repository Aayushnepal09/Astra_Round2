"""Fictional cancellation/replacement flows and races on an isolated database."""

import hashlib
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agreements.models import Agreement, TemplateVersion
from agreements.storage import PrivateDocumentStorage
from login.models import ClientInvitation, CompanyAccount
from .assistant_access import AssistantAccess, AssistantAccessError
from .countersigning import CompanySigning
from .finalization import AgreementFinalizer
from .lifecycle import RequestLifecycle, RequestLifecycleError
from .models import ClientFile, SigningCompletion, SigningRequest
from .signing import DemoRequestIssuer, demo_code, signing_recipient
from .submissions import ClientSigning, SigningError
from .test_assistant import pdf_bytes
from .workspace import CompanyFileWorkspace


class LifecycleFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="astra-lifecycle-check-")
        self.addCleanup(self.directory.cleanup)
        self.configuration = override_settings(
            AGREEMENT_STORAGE_ROOT=Path(self.directory.name), ASSISTANT_GROQ_API_KEY="",
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
        )
        self.configuration.enable()
        self.addCleanup(self.configuration.disable)
        self.company = CompanyAccount.objects.create_user("lifecycle@example.test", company_name="Fictional Lifecycle Co")
        self.other = CompanyAccount.objects.create_user("other-lifecycle@example.test", company_name="Fictional Other Co")
        self.client.force_login(self.company)
        self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        content = pdf_bytes(["Fictional agreement for cancellation and replacement checks."])
        self.agreement = Agreement(company=self.company, original_filename="fictional.pdf", detected_format="pdf", processing_state="ready",
                                   original_sha256=hashlib.sha256(content).hexdigest(), original_size_bytes=len(content),
                                   pdf_sha256=hashlib.sha256(content).hexdigest(), pdf_size_bytes=len(content), page_count=1,
                                   page_geometry=[{"width": 612, "height": 792, "rotation": 0}], validated_at=timezone.now())
        self.storage = PrivateDocumentStorage(self.company.pk, self.agreement.pk)
        self.agreement.original_key = self.agreement.pdf_key = self.storage.write([content], max_bytes=settings.AGREEMENT_MAX_BYTES).key
        self.agreement.save()
        self.version = self.make_version()
        self.file = ClientFile.objects.create(
            company=self.company, reference="FICTIONAL-LIFECYCLE", client_name="Fictional Client", signer_name="Demo Client",
            signer_email="demo-client@example.test", company_signer_name="Demo Company", company_signer_email="demo-company@example.test",
            template_version=self.version, preparation_values={self.fee_id: "Fee 100"},
        )
        self.issuer = DemoRequestIssuer(self.company)
        self.lifecycle = RequestLifecycle(self.company)

    def make_version(self, countersign=False, number=1):
        common = {"page": 1, "x": .1, "width": .5, "height": .08, "required": True}
        self.fee_id = str(uuid.uuid4())
        fields = [dict(common, id=str(uuid.uuid4()), type="signature", role="client", label="Client signature", y=.3),
                  dict(common, id=self.fee_id, type="text", role="preparation", label="Fictional fee", y=.1)]
        if countersign:
            fields.append(dict(common, id=str(uuid.uuid4()), type="signature", role="company", label="Company signature", y=.6))
        return TemplateVersion.objects.create(agreement=self.agreement, number=number, name=f"Fictional version {number}",
                                               source_revision=number, pdf_sha256=self.agreement.pdf_sha256, fields=fields)

    def issue(self):
        self.file.refresh_from_db()
        issued, created = self.issuer.issue(
            self.file.pk, template_version_id=str(self.file.template_version_id), updated_at=self.file.updated_at.isoformat(),
            replaces_request_id=str(self.file.current_request_id) if self.file.current_request_id else None,
        )
        self.assertTrue(created)
        self.file.refresh_from_db()
        return issued

    def invitation(self, issued):
        return ClientInvitation.objects.get(signing_access__recipient__signing_request=issued)

    def verify(self, issued):
        invitation = self.invitation(issued)
        client = Client()
        result = client.post(reverse("client_invitation", args=[invitation.pk]), {"code": demo_code(invitation)}, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def client_request(self, client):
        return SimpleNamespace(user=AnonymousUser(), session=SessionStore(client.cookies[settings.CLIENT_SESSION_COOKIE_NAME].value))

    def payload(self, recipient):
        state = ClientSigning.snapshot(recipient)
        return {**{key: state[key] for key in ["requestId", "templateVersionId", "recipientId", "pdfSha256", "revision"]},
                "action": "submit", "consent": True, "submissionKey": str(uuid.uuid4()),
                "values": {field["id"]: recipient.name for field in state["fields"]}}

    def sign_client(self, issued, client=None, direct=False):
        client = client or self.verify(issued)
        recipient = issued.recipients.get(role="client")
        if direct:
            return client, ClientSigning.act(self.client_request(client), recipient, self.payload(recipient))
        response = client.post(reverse("client_agreement_signing", args=[self.invitation(issued).pk]),
                               self.payload(recipient), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        return client, response.json()["signing"]

    def countersigned(self):
        self.version = self.make_version(countersign=True, number=2)
        self.file.template_version = self.version
        self.file.preparation_values = {self.fee_id: "Two-party fee"}
        self.file.save()
        return self.issue()

    def cancel_post(self, issued):
        return self.client.post(reverse("company_file_cancel", args=[self.file.pk]),
                                {"workspace_id": str(self.company.pk), "requestId": str(issued.pk)}, content_type="application/json")


class RequestLifecycleChecks(LifecycleFixture, TestCase):
    def test_cancel_before_signatures_and_retry_preserves_history(self):
        issued = self.issue()
        result = self.cancel_post(issued)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["outcome"], "cancelled")
        self.assertEqual(result.json()["file"]["currentRequestStatus"], "cancelled")
        issued.refresh_from_db()
        cancelled_at = issued.cancelled_at
        self.assertEqual(issued.cancelled_by_id, self.company.pk)
        self.assertIsNotNone(self.invitation(issued).revoked_at)
        self.assertEqual(self.cancel_post(issued).json()["outcome"], "already_cancelled")
        issued.refresh_from_db()
        self.assertEqual(issued.cancelled_at, cancelled_at)
        self.assertEqual(SigningRequest.objects.count(), 1)
        page = self.client.get(reverse("company_request_history", args=[self.file.pk]))
        self.assertContains(page, "Cancelled")
        self.assertContains(page, str(issued.pk))
        self.assertContains(page, "Fee 100")
        self.assertContains(page, "No accepted signature recorded.")
        summary = CompanyFileWorkspace(self.company).snapshot()
        self.assertEqual(summary["counts"]["attention"], 1)
        self.assertEqual(summary["files"][0]["status"], "cancelled")

    def test_partial_cancellation_preserves_accepted_signature_and_blocks_both_signers(self):
        issued = self.countersigned()
        client, state = self.sign_client(issued)
        self.assertEqual(state["progress"], "awaiting_company")
        old_recipient = CompanySigning.recipient(SimpleNamespace(user=self.company, session={}), self.file.pk)
        old_payload = self.payload(old_recipient)
        original = list(issued.submissions.values())
        self.assertEqual(self.cancel_post(issued).json()["outcome"], "cancelled")
        self.assertEqual(list(issued.submissions.values()), original)
        with self.assertRaises(SigningError):
            CompanySigning.act(SimpleNamespace(user=self.company, session={}), old_recipient, old_payload)
        response = client.get(reverse("client_agreement_signing", args=[self.invitation(issued).pk]))
        self.assertEqual(response.status_code, 401)
        history = self.client.get(reverse("company_request_history", args=[self.file.pk]))
        self.assertContains(history, "Demo Client")
        self.assertContains(history, str(original[0]["id"]))
        self.assertContains(history, "No accepted signature recorded.")

    def test_old_sessions_codes_links_and_ai_do_not_reactivate_cancelled_or_replaced_request(self):
        issued = self.issue()
        client = self.verify(issued)
        invitation = self.invitation(issued)
        session = self.client_request(client)
        self.assertIsNotNone(AssistantAccess.resolve(session, invitation.pk))
        self.lifecycle.cancel(self.file.pk, issued.pk)
        for replacing in [False, True]:
            if replacing:
                self.lifecycle.prepare_replacement(self.file.pk, issued.pk)
                self.issue()
            with self.subTest(replaced=replacing):
                self.assertIsNone(signing_recipient(invitation))
                with self.assertRaises(AssistantAccessError):
                    AssistantAccess.resolve(session, invitation.pk)
                self.assertEqual(client.get(reverse("client_agreement_signing", args=[invitation.pk])).status_code, 401)
                self.assertEqual(client.post(reverse("client_agreement_assistant", args=[invitation.pk]),
                                             {"action": "summary"}, content_type="application/json").status_code, 401)
                self.assertEqual(Client().post(reverse("client_invitation", args=[invitation.pk]),
                                               {"code": demo_code(invitation)}, content_type="application/json").status_code, 401)
                with self.assertRaises((RequestLifecycleError, ValidationError)):
                    self.issuer.replace_code(self.file.pk, str(invitation.pk))
                self.assertEqual(client.get(reverse("client_agreement", args=[invitation.pk])).status_code, 302)

    def test_correct_replacement_and_complete_without_transferring_signatures(self):
        old = self.countersigned()
        self.sign_client(old)
        before = {"values": dict(old.preparation_values), "version": old.template_version_id,
                  "recipients": list(old.recipients.values()), "submissions": list(old.submissions.values())}
        self.lifecycle.cancel(self.file.pk, old.pk)
        file, _ = self.lifecycle.prepare_replacement(self.file.pk, old.pk)
        version = self.make_version(number=3)
        data = {"workspace_id": str(self.company.pk), "updatedAt": file.updated_at.isoformat(),
                "reference": file.reference, "client_name": file.client_name, "template_version": str(version.pk),
                "signer_name": "New Client", "signer_email": "new-client@example.test", "preparation_values": {self.fee_id: "Corrected fee 250"}}
        response = self.client.post(reverse("company_file_detail", args=[file.pk]), data, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.client.post(reverse("company_file_detail", args=[file.pk]), data, content_type="application/json").json()["code"], "draft_conflict")
        self.assertEqual(SigningRequest.objects.count(), 1)
        replacement = self.issue()
        self.assertNotEqual(replacement.pk, old.pk)
        self.assertEqual(replacement.replaces_id, old.pk)
        self.assertFalse(replacement.submissions.exists())
        self.assertEqual(replacement.recipients.get(role="client").name, "New Client")
        client, state = self.sign_client(replacement)
        self.assertEqual(state["progress"], "completed")
        self.file.refresh_from_db()
        self.assertEqual(self.file.current_request_id, replacement.pk)
        self.assertEqual(self.file.status, "completed")
        old.refresh_from_db()
        old.full_clean()
        self.assertEqual(old.status, "cancelled")
        self.assertEqual(old.preparation_values, before["values"])
        self.assertEqual(old.template_version_id, before["version"])
        self.assertEqual(list(old.recipients.values()), before["recipients"])
        self.assertEqual(list(old.submissions.values()), before["submissions"])
        for submission in old.submissions.all():
            submission.full_clean()
        completion = SigningCompletion.objects.get(signing_request=replacement)
        self.assertEqual(completion.history["requestId"], str(replacement.pk))
        self.assertEqual(completion.history["preparationValues"], {self.fee_id: "Corrected fee 250"})
        self.assertTrue(self.storage.inspect(completion.pdf_key, max_bytes=settings.AGREEMENT_MAX_BYTES).size_bytes)
        pdf = client.get(reverse("client_completed_pdf", args=[self.invitation(replacement).pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(b"".join(pdf.streaming_content).startswith(b"%PDF"))
        for route, args in [("company_completed_pdf", [self.file.pk]), ("company_request_completed_pdf", [self.file.pk, replacement.pk]),
                            ("company_signing_history", [self.file.pk]), ("company_request_signing_history", [self.file.pk, replacement.pk])]:
            result = self.client.get(reverse(route, args=args))
            self.assertEqual(result.status_code, 200)
            if result.streaming:
                self.assertTrue(b"".join(result.streaming_content).startswith(b"%PDF"))
        history = self.client.get(reverse("company_request_history", args=[self.file.pk]))
        self.assertContains(history, "Corrected fee 250")
        self.assertContains(history, "Two-party fee")

    def test_prepare_and_issue_retries_do_not_reset_edits_or_duplicate_requests(self):
        old = self.issue()
        self.lifecycle.cancel(self.file.pk, old.pk)
        file, _ = self.lifecycle.prepare_replacement(self.file.pk, old.pk)
        file.title = "Keep this correction"
        file.save()
        saved_at = file.updated_at
        file, outcome = self.lifecycle.prepare_replacement(self.file.pk, old.pk)
        self.assertEqual(outcome, "already_prepared")
        self.assertEqual(file.title, "Keep this correction")
        self.assertEqual(file.updated_at, saved_at)
        payload = {"workspace_id": str(self.company.pk), "templateVersionId": str(file.template_version_id),
                   "updatedAt": file.updated_at.isoformat(), "replacesRequestId": str(old.pk)}
        url = reverse("company_file_request", args=[file.pk])
        first = self.client.post(url, payload, content_type="application/json")
        retry = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(first.json()["invitation"]["requestId"], retry.json()["invitation"]["requestId"])
        self.assertFalse(retry.json()["created"])
        self.assertEqual(SigningRequest.objects.count(), 2)
        self.assertEqual(self.cancel_post(old).json()["outcome"], "already_cancelled")
        with self.assertRaises(RequestLifecycleError):
            self.lifecycle.prepare_replacement(self.file.pk, old.pk)
        late_initial = {**payload, "replacesRequestId": None}
        self.assertEqual(self.client.post(url, late_initial, content_type="application/json").status_code, 409)
        self.assertEqual(SigningRequest.objects.count(), 2)

    def test_cancellation_reconciles_accepted_signatures_even_if_stage_is_stale(self):
        issued = self.issue()
        _, state = self.sign_client(issued, direct=True)
        self.assertEqual(state["progress"], "finalizing")
        # Simulate a stale stage marker; accepted immutable submissions win.
        from django.db.models import QuerySet
        QuerySet(model=SigningRequest, using="default").filter(pk=issued.pk).update(status="awaiting_client")
        ClientFile.objects.filter(pk=self.file.pk).update(status="awaiting_client")
        result = self.cancel_post(issued)
        self.assertEqual(result.json()["outcome"], "signatures_complete", result.content)
        issued.refresh_from_db()
        self.assertEqual(issued.status, "finalizing")
        self.assertIsNone(issued.cancelled_at)
        with self.assertRaises(RequestLifecycleError):
            self.lifecycle.prepare_replacement(self.file.pk, issued.pk)
        self.assertEqual(AgreementFinalizer(self.company.pk).run(self.file.pk)["state"], "completed")
        self.assertEqual(self.cancel_post(issued).status_code, 409)

    def test_normal_countersigning_completes_and_cannot_be_cancelled(self):
        issued = self.countersigned()
        self.sign_client(issued)
        recipient = CompanySigning.recipient(SimpleNamespace(user=self.company, session={}), self.file.pk)
        result = self.client.post(reverse("company_agreement_signing", args=[self.file.pk]), self.payload(recipient), content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["signing"]["progress"], "completed")
        before = SigningCompletion.objects.get(signing_request=issued).history
        result = self.cancel_post(issued)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["code"], "request_completed")
        self.assertEqual(SigningCompletion.objects.get(signing_request=issued).history, before)

    def test_other_company_and_unauthenticated_requests_cannot_access_or_modify_history(self):
        issued = self.issue()
        routes = ["company_file_cancel", "company_file_replacement", "company_request_history", "company_request_completed_pdf", "company_request_signing_history"]
        for user in [None, self.other]:
            client = Client()
            if user:
                client.force_login(user)
                client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = client.cookies[settings.SESSION_COOKIE_NAME].value
            for route in routes:
                args = [self.file.pk, issued.pk] if route in ("company_request_completed_pdf", "company_request_signing_history") else [self.file.pk]
                url = reverse(route, args=args)
                response = (client.post(url, {"workspace_id": str(user.pk) if user else "", "requestId": str(issued.pk)}, content_type="application/json")
                            if route in ("company_file_cancel", "company_file_replacement") else client.get(url))
                self.assertIn(response.status_code, [401, 302] if user is None else [404])
        issued.refresh_from_db()
        self.assertEqual(issued.status, "awaiting_client")

    def test_cancellation_and_preparation_require_csrf_and_bound_request_identity(self):
        issued = self.issue()
        protected = Client(enforce_csrf_checks=True)
        protected.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME].value
        for route in ["company_file_cancel", "company_file_replacement"]:
            response = protected.post(reverse(route, args=[self.file.pk]), {"workspace_id": str(self.company.pk), "requestId": str(issued.pk)}, content_type="application/json")
            self.assertEqual(response.status_code, 403)
        other_file = ClientFile.objects.create(company=self.company, reference="OTHER-FICTIONAL", client_name="Other Client")
        response = self.client.post(reverse("company_file_cancel", args=[other_file.pk]),
                                    {"workspace_id": str(self.company.pk), "requestId": str(issued.pk)}, content_type="application/json")
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(ValidationError):
            issued.preparation_values = {self.fee_id: "Not an allowed edit"}
            issued.save()


class LifecycleRaceChecks(LifecycleFixture, TransactionTestCase):
    def race(self, operations):
        if connection.vendor == "sqlite" and connection.is_in_memory_db():
            self.skipTest("Use a temporary file test database for SQLite writer waiting.")
        barrier = Barrier(len(operations))

        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            except (SigningError, RequestLifecycleError) as error:
                return error
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run, operations))

    def test_cancel_racing_client_signature_has_one_authoritative_outcome(self):
        issued = self.issue()
        client = self.verify(issued)
        recipient = issued.recipients.get(role="client")
        payload = self.payload(recipient)
        results = self.race([lambda: RequestLifecycle(self.company).cancel(self.file.pk, issued.pk),
                             lambda: ClientSigning.act(self.client_request(client), recipient, payload)])
        issued.refresh_from_db()
        if issued.status == "cancelled":
            self.assertFalse(issued.submissions.exists())
            self.assertIsInstance(results[1], SigningError)
        else:
            self.assertEqual(issued.status, "finalizing")
            self.assertEqual(issued.submissions.count(), 1)
            self.assertEqual(results[0][1], "signatures_complete")
            self.assertIsNone(issued.cancelled_at)

    def test_cancel_racing_company_signature_preserves_partial_or_full_outcome(self):
        issued = self.countersigned()
        self.sign_client(issued)
        request = SimpleNamespace(user=self.company, session={})
        recipient = CompanySigning.recipient(request, self.file.pk)
        payload = self.payload(recipient)
        results = self.race([lambda: RequestLifecycle(self.company).cancel(self.file.pk, issued.pk),
                             lambda: CompanySigning.act(request, recipient, payload)])
        issued.refresh_from_db()
        if issued.status == "cancelled":
            self.assertEqual(issued.submissions.count(), 1)
            self.assertIsInstance(results[1], SigningError)
        else:
            self.assertEqual(issued.status, "finalizing")
            self.assertEqual(issued.submissions.count(), 2)
            self.assertEqual(results[0][1], "signatures_complete")

    def test_simultaneous_prepare_and_send_retries_have_one_draft_and_one_replacement(self):
        issued = self.issue()
        self.lifecycle.cancel(self.file.pk, issued.pk)
        prepared = self.race([lambda: RequestLifecycle(self.company).prepare_replacement(self.file.pk, issued.pk)] * 2)
        self.assertEqual(sorted(result[1] for result in prepared), ["already_prepared", "replacement_prepared"])
        self.file.refresh_from_db()
        version_id, updated_at = str(self.file.template_version_id), self.file.updated_at.isoformat()
        sends = self.race([lambda: DemoRequestIssuer(self.company).issue(self.file.pk, template_version_id=version_id,
                           updated_at=updated_at, replaces_request_id=str(issued.pk))] * 2)
        self.assertEqual(sorted(result[1] for result in sends), [False, True])
        self.assertEqual(sends[0][0].pk, sends[1][0].pk)
        self.assertEqual(SigningRequest.objects.count(), 2)

    def test_cancellation_racing_finalization_preserves_completed_artifacts(self):
        issued = self.issue()
        self.sign_client(issued, direct=True)
        results = self.race([lambda: RequestLifecycle(self.company).cancel(self.file.pk, issued.pk),
                             lambda: AgreementFinalizer(self.company.pk).run(self.file.pk)])
        self.assertEqual(results[1]["state"], "completed")
        if isinstance(results[0], RequestLifecycleError):
            self.assertEqual(results[0].code, "request_completed")
        else:
            self.assertEqual(results[0][1], "signatures_complete")
        issued.refresh_from_db()
        self.file.refresh_from_db()
        self.assertEqual(issued.status, "completed")
        self.assertEqual(self.file.status, "completed")
        self.assertIsNone(issued.cancelled_at)
        self.assertEqual(SigningRequest.objects.count(), 1)
        completion = SigningCompletion.objects.get(signing_request=issued)
        self.assertEqual(self.storage.inspect(completion.pdf_key, max_bytes=settings.AGREEMENT_MAX_BYTES).sha256,
                         completion.pdf_sha256)


class RequestLifecycleMigrationChecks(TransactionTestCase):
    def test_existing_requests_receive_independent_status_and_current_pointer(self):
        executor = MigrationExecutor(connection)
        previous = [("client_files", "0009_assistant_budget")]
        current = [("client_files", "0010_request_lifecycle")]
        executor.migrate(previous)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(current))
        apps = executor.loader.project_state(previous).apps
        company = apps.get_model("login", "CompanyAccount").objects.create(email="migration@example.test", company_name="Fictional migration")
        agreement = apps.get_model("agreements", "Agreement").objects.create(company_id=company.pk, original_filename="fictional.pdf")
        version = apps.get_model("agreements", "TemplateVersion").objects.create(agreement_id=agreement.pk, number=1, name="Fictional",
                                             source_revision=0, pdf_sha256="a" * 64, fields=[])
        records = []
        for status in ["awaiting_client", "awaiting_company", "finalizing", "completed"]:
            file = apps.get_model("client_files", "ClientFile").objects.create(company_id=company.pk, reference=status,
                                               client_name="Fictional migration client", template_version_id=version.pk, status=status)
            issued = apps.get_model("client_files", "SigningRequest").objects.create(client_file_id=file.pk, template_version_id=version.pk,
                                                 preparation_values={"fictional": status})
            records.append((file.pk, issued.pk, status))
        executor = MigrationExecutor(connection)
        executor.migrate(current)
        for file_id, request_id, status in records:
            self.assertEqual(ClientFile.objects.get(pk=file_id).current_request_id, request_id)
            issued = SigningRequest.objects.get(pk=request_id)
            self.assertEqual(issued.status, status)
            self.assertEqual(issued.preparation_values, {"fictional": status})


class ClientPortalChecks(LifecycleFixture, TestCase):
    # Reuse fictional agreement fixtures; run only these named checks.
    def test_portal_is_invitation_bound_and_signing_still_works(self):
        issued = self.issue()
        invitation = self.invitation(issued)
        url = reverse('client_portal', args=[invitation.pk])
        self.assertEqual(Client().get(url).status_code, 302)
        client = self.verify(issued)
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Review &amp; sign')
        self.assertContains(response, 'Showing issued agreements shared with your verified email for this client')
        other = ClientInvitation.objects.create(company=self.company, code_hash='unused', expires_at=invitation.expires_at)
        self.assertEqual(client.get(reverse('client_portal', args=[other.pk])).status_code, 302)
        self.sign_client(issued, client=client)
        response = client.get(url)
        self.assertContains(response, 'Download PDF')
        self.assertContains(response, 'Signed')

    def test_cancelled_portal_access_is_denied(self):
        issued = self.issue()
        client = self.verify(issued)
        self.lifecycle.cancel(self.file.pk, request_id=issued.pk)
        self.assertEqual(client.get(reverse('client_portal', args=[self.invitation(issued).pk])).status_code, 302)
