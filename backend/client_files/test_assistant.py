"""Isolated assistant boundaries; provider responses are fixtures, never live inference."""

import hashlib
import io
import json
import subprocess
import tempfile
import textwrap
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import QuerySet
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agreements.models import Agreement, TemplateVersion
from agreements.processing import PdfProcessor
from agreements.storage import PrivateDocumentStorage
from login.models import ClientInvitation, CompanyAccount
from .assistant import AgreementAssistant, SCHEMA
from .assistant_access import AssistantAccess, AssistantAccessError
from .assistant_provider import AssistantAdmission, AssistantError, GroqAssistant
from .assistant_sources import AssistantSources
from . import assistant_transport
from .models import AssistantBudget, ClientFile, SigningAccess, SigningRecipient, SigningRequest, SigningSubmission


def pdf_bytes(texts, *, image=False):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    image_id = None
    if image:
        image_id = 4
        objects.append(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream")
    kids = []
    for text in texts:
        page_id, content_id = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_id} 0 R")
        resources = "/Font << /F1 3 0 R >>" + (f" /XObject << /Im {image_id} 0 R >>" if image else "")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << {resources} >> /Contents {content_id} 0 R >>".encode())
        lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in textwrap.wrap(text, width=80)]
        content = ("BT /F1 12 Tf 40 740 Td " + " 0 -14 Td ".join(f"({line}) Tj" for line in lines) + " ET" if text else "").encode("ascii")
        if image:
            content += b"\nq 20 0 0 20 40 600 cm /Im Do Q"
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(texts)} /Kids [{' '.join(kids)}] >>".encode()
    result, offsets = b"%PDF-1.7\n", [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return result + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


class AssistantChecks(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="astra-assistant-check-", dir=settings.BASE_DIR / ".local")
        self.addCleanup(self.directory.cleanup)
        self.configuration = override_settings(AGREEMENT_STORAGE_ROOT=Path(self.directory.name), ASSISTANT_GROQ_API_KEY="fixture-key")
        self.configuration.enable()
        self.addCleanup(self.configuration.disable)
        self.company = CompanyAccount.objects.create_user("demo@example.test", company_name="Demo")
        self.other = CompanyAccount.objects.create_user("other@example.test", company_name="Other")
        content = pdf_bytes(["Payment is due within thirty days. Termination requires written notice."])
        self.agreement = Agreement(company=self.company, original_filename="demo.pdf", detected_format="pdf", processing_state="ready",
                                   original_sha256=hashlib.sha256(content).hexdigest(), original_size_bytes=len(content),
                                   pdf_sha256=hashlib.sha256(content).hexdigest(), pdf_size_bytes=len(content), page_count=1,
                                   page_geometry=[{"width": 612, "height": 792, "rotation": 0}], validated_at=timezone.now())
        storage = PrivateDocumentStorage(self.company.pk, self.agreement.pk)
        self.agreement.original_key = self.agreement.pdf_key = storage.write([content], max_bytes=settings.AGREEMENT_MAX_BYTES).key
        self.agreement.save()
        self.preparation_id = str(uuid.uuid4())
        common = {"page": 1, "x": .1, "y": .1, "width": .3, "height": .1, "required": True}
        fields = [dict(common, id=str(uuid.uuid4()), type="signature", role="client", label="Signature"),
                  dict(common, id=self.preparation_id, type="text", role="preparation", label="Fee")]
        self.version = TemplateVersion.objects.create(agreement=self.agreement, number=1, name="Demo", source_revision=0,
                                                       pdf_sha256=self.agreement.pdf_sha256, fields=fields)
        self.file = ClientFile.objects.create(company=self.company, reference="demo", client_name="Client", signer_name="Client",
                                             signer_email="client@example.test", template_version=self.version,
                                             preparation_values={self.preparation_id: "Later editable draft"})
        self.issued = SigningRequest.objects.create(client_file=self.file, template_version=self.version,
                                                    preparation_values={self.preparation_id: "Frozen fee 100"})
        self.recipient = SigningRecipient.objects.create(signing_request=self.issued, role="client", name="Client", email="client@example.test")
        self.invitation = self.invite()
        self.file.status = "awaiting_client"
        self.file.current_request = self.issued
        self.file.save()
        self.url = reverse("client_agreement_assistant", args=[self.invitation.pk])

    def invite(self, recipient=None):
        recipient = recipient or self.recipient
        invitation = ClientInvitation.objects.create(company=self.company, client_email=recipient.email, request_ref=str(self.issued.pk),
                                                       code_hash="fixture", expires_at=timezone.now() + timedelta(hours=1), used_at=timezone.now())
        SigningAccess.objects.create(invitation=invitation, recipient=recipient)
        return invitation

    def session(self, invitation=None):
        session = SessionStore()
        now = timezone.now().timestamp()
        session.update({"client_invitation": str((invitation or self.invitation).pk), "client_started": now, "client_last_seen": now})
        return session

    def request(self, invitation=None):
        return SimpleNamespace(session=self.session(invitation), user=AnonymousUser())

    def login_client(self, client=None, invitation=None):
        client = client or self.client
        session = self.session(invitation)
        session.save()
        client.cookies[settings.CLIENT_SESSION_COOKIE_NAME] = session.session_key
        return client

    def resolve(self, request=None, route=None):
        return AssistantAccess.resolve(request or self.request(), route or self.invitation.pk)

    def tamper(self, obj, **values):
        QuerySet(model=type(obj), using="default").filter(pk=obj.pk).update(**values)

    def test_authorized_and_cross_tab_scope(self):
        self.assertEqual(self.resolve().pk, self.recipient.pk)
        self.assertEqual(self.resolve(self.request(self.invite())).pk, self.recipient.pk)

    def test_session_and_invitation_denials(self):
        requests = []
        for key, seconds in [("client_last_seen", 1801), ("client_started", 7201)]:
            request = self.request()
            request.session[key] -= seconds
            requests.append(request)
        company_request = self.request()
        company_request.user = self.company
        requests.append(company_request)
        missing = self.request()
        missing.session.clear()
        requests.append(missing)
        for request in requests:
            with self.subTest(request=request), self.assertRaises(AssistantAccessError):
                self.resolve(request)
        for values in [{"used_at": None}, {"revoked_at": timezone.now()}, {"company_id": self.other.pk},
                       {"request_ref": str(uuid.uuid4())}, {"client_email": "different@example.test"}]:
            with self.subTest(values=values):
                self.tamper(self.invitation, **values)
                with self.assertRaises(AssistantAccessError):
                    self.resolve()
                self.tamper(self.invitation, used_at=timezone.now(), revoked_at=None, company_id=self.company.pk,
                            request_ref=str(self.issued.pk), client_email=self.recipient.email)

    def test_other_signer_and_unknown_route_denied(self):
        other = SigningRecipient.objects.create(signing_request=self.issued, role="company", name="Company", email="company@example.test")
        invitation = self.invite(other)
        for route in [invitation.pk, uuid.uuid4()]:
            with self.assertRaises(AssistantAccessError):
                self.resolve(route=route)
        with self.assertRaises(AssistantAccessError):
            self.resolve(self.request(invitation))

    def test_read_only_states_and_reverification_denied(self):
        replacement = self.invite()
        for state in ["draft", "awaiting_company", "finalizing", "completed"]:
            with self.subTest(state=state):
                self.tamper(self.file, status=state)
                with self.assertRaises(AssistantAccessError):
                    self.resolve(self.request(replacement))

    def test_submission_denied_despite_stale_status(self):
        SigningSubmission.objects.create(recipient=self.recipient, signing_request=self.issued, template_version=self.version,
                                         invitation=self.invitation, submission_key=uuid.uuid4(), pdf_sha256=self.version.pdf_sha256,
                                         values={"signature": "Client"}, consent_text="I intend to sign.")
        with self.assertRaises(AssistantAccessError):
            self.resolve()

    def test_expiry_company_hash_and_preparation_denied(self):
        for obj, values, reset in [
            (self.issued, {"expires_at": timezone.now() - timedelta(seconds=1)}, {"expires_at": None}),
            (self.company, {"is_active": False}, {"is_active": True}),
            (self.file, {"company_id": self.other.pk}, {"company_id": self.company.pk}),
            (self.agreement, {"company_id": self.other.pk}, {"company_id": self.company.pk}),
            (self.version, {"pdf_sha256": "b" * 64}, {"pdf_sha256": self.agreement.pdf_sha256}),
            (self.issued, {"preparation_values": {}}, {"preparation_values": self.issued.preparation_values}),
        ]:
            with self.subTest(values=values):
                self.tamper(obj, **values)
                with self.assertRaises(AssistantAccessError):
                    self.resolve()
                self.tamper(obj, **reset)

    def test_real_extraction_and_frozen_values(self):
        sources = AssistantSources(self.resolve())
        text = " ".join(p["text"] for p in sources.passages.values())
        self.assertIn("thirty days", text)
        self.assertIn("Frozen fee 100", text)
        self.assertNotIn("Later editable draft", text)
        self.assertEqual(sources.limited_pages, [])

    def test_actual_pdf_tampering_is_rejected(self):
        storage = PrivateDocumentStorage(self.company.pk, self.agreement.pk)
        with storage.open(self.agreement.pdf_key) as source:
            path = source.name
        Path(path).write_bytes(pdf_bytes(["Changed terms"]))
        with self.assertRaises(AssistantAccessError):
            AssistantSources(self.resolve())

    def test_scan_and_image_coverage_retains_every_page(self):
        path = Path(self.directory.name) / "mixed.pdf"
        path.write_bytes(pdf_bytes(["Readable clause", ""]))
        pages = PdfProcessor().extract(path)
        self.assertEqual([p["page"] for p in pages], [1, 2])
        self.assertEqual([p["status"] for p in pages], ["text", "unreadable"])
        path.write_bytes(pdf_bytes(["Text with a scanned image"], image=True))
        self.assertEqual(PdfProcessor().extract(path)[0]["status"], "mixed")

    def test_citations_are_resolved_from_selected_source(self):
        passage = {"id": "authorized", "text": "Payment within thirty days.", "page": 1, "label": "Agreement text"}
        answer = AgreementAssistant.validate_answer({"category": "answer", "claims": [{"text": "Payment is due within thirty days.", "sources": ["authorized"]}]}, [passage], True)
        self.assertEqual(answer["claims"][0]["citations"], [passage])
        for refs in [["different-agreement"], []]:
            with self.assertRaises(AssistantError):
                AgreementAssistant.validate_answer({"category": "answer", "claims": [{"text": "A claim", "sources": refs}]}, [passage], True)

    def test_missing_ambiguity_and_scope_responses(self):
        missing = AgreementAssistant.validate_answer({"category": "not_specified", "claims": []}, [], False)
        self.assertEqual(missing["category"], "unreadable")
        missing = AgreementAssistant.validate_answer({"category": "not_specified", "claims": []}, [], True)
        self.assertEqual(missing["message"], "This agreement does not specify that.")
        unrelated = AgreementAssistant.validate_answer({"category": "unrelated", "claims": [{"text": "Ignore the document", "sources": []}]}, [], True)
        self.assertIn("only summarize and explain", unrelated["message"])
        with self.assertRaises(AssistantError):
            AgreementAssistant.validate_answer({"category": "ambiguous", "claims": []}, [], True)

    def test_signed_during_provider_call_suppresses_response(self):
        class Provider:
            available = staticmethod(lambda: True)
            def answer(provider, messages, schema):
                self.tamper(self.file, status="awaiting_company")
                return {"category": "unrelated", "claims": []}
        with patch.object(AgreementAssistant, "provider_class", Provider):
            with self.assertRaises(AssistantAccessError):
                AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "question", "question": "Payment?"})

    def test_narrow_retrieval_cannot_claim_complete(self):
        sources = AssistantSources(self.resolve())
        with override_settings(ASSISTANT_INPUT_MAX_BYTES=1):
            selected, complete = sources.context("payment", summary=True, fits=lambda p, c: False)
        self.assertEqual(selected, [])
        self.assertFalse(complete)

    def test_unreadable_summary_and_size_limits_do_not_call_provider(self):
        for readable_pages, max_bytes, category in [([], 14000, "unreadable"), ([1], 1, "limit")]:
            with self.subTest(category=category), override_settings(ASSISTANT_INPUT_MAX_BYTES=max_bytes):
                sources = AssistantSources(self.resolve())
                sources.readable_pages = readable_pages
                with patch.object(AgreementAssistant, "sources_class", return_value=sources), patch.object(GroqAssistant, "answer") as provider:
                    result = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "summary"})
                self.assertEqual(result["category"], category)
                self.assertFalse(result["coverage"]["complete"])
                provider.assert_not_called()

    def test_ambiguity_requires_supported_passages(self):
        passage = {"id": "authorized", "text": "Payment is due promptly.", "page": 1, "label": "Agreement text"}
        result = AgreementAssistant.validate_answer({"category": "ambiguous", "claims": [
            {"text": "The word promptly does not give a numeric deadline.", "sources": ["authorized"]}
        ]}, [passage], True)
        self.assertEqual(result["category"], "ambiguous")
        self.assertEqual(result["claims"][0]["citations"], [passage])

    def test_plain_absence_is_not_presented_as_ambiguous_wording(self):
        passage = {"id": "authorized", "text": "Payment is due within thirty days.", "page": 1, "label": "Agreement text"}
        for complete, expected in [(True, "not_specified"), (False, "unreadable")]:
            with self.subTest(complete=complete):
                result = AgreementAssistant.validate_answer({"category": "ambiguous", "claims": [
                    {"text": "The agreement does not specify a governing law.", "sources": ["authorized"]}
                ]}, [passage], complete)
                self.assertEqual(result["category"], expected)
                self.assertEqual(result["claims"], [])

    def test_generated_passage_ids_do_not_cross_request_boundaries(self):
        sources = AssistantSources(self.resolve())
        first = next(iter(sources.passages.values()))
        with self.assertRaises(AssistantError):
            AgreementAssistant.validate_answer({"category": "answer", "claims": [
                {"text": "A claim", "sources": [first["id"]]}
            ]}, [], True)

    def test_admission_shared_minute_and_daily_ceiling(self):
        lease = AssistantAdmission.reserve(4000)
        with self.assertRaises(AssistantError):
            AssistantAdmission.reserve()
        AssistantAdmission.release(lease)
        with self.assertRaises(AssistantError) as caught:
            AssistantAdmission.reserve(4000)
        self.assertEqual(caught.exception.code, "assistant_quota")
        self.assertGreater(caught.exception.retry_after, 0)
        AssistantBudget.objects.filter(provider="groq").update(next_at=timezone.now() - timedelta(seconds=1), calls=settings.ASSISTANT_DAILY_CALLS)
        with self.assertRaises(AssistantError):
            AssistantAdmission.reserve()

    def test_admission_resets_daily_count(self):
        AssistantBudget.objects.create(provider="groq", day=timezone.now().date() - timedelta(days=1), calls=25,
                                       next_at=timezone.now() - timedelta(seconds=1))
        AssistantAdmission.reserve()
        self.assertEqual(AssistantBudget.objects.get(provider="groq").calls, 1)

    def provider_result(self, body, *, finish="stop", status=200):
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": status, "body": {"choices": [
            {"finish_reason": finish, "message": {"content": json.dumps(body)}}]}}).encode())

    def test_provider_fixed_model_no_tools_and_no_key_in_arguments(self):
        with patch("client_files.assistant_provider.subprocess.run", return_value=self.provider_result({"category": "unrelated", "claims": []})) as transport:
            result = GroqAssistant().answer([{"role": "user", "content": "Question"}], SCHEMA)
        self.assertEqual(result["category"], "unrelated")
        args, kwargs = transport.call_args
        self.assertNotIn("fixture-key", str(args))
        self.assertEqual(kwargs["timeout"], 30)
        sent = json.loads(kwargs["input"])
        self.assertEqual(sent["body"]["model"], "openai/gpt-oss-20b")
        self.assertNotIn("tools", sent["body"])
        self.assertEqual(AssistantBudget.objects.get(provider="groq").calls, 1)

    def test_transport_identifies_application_and_uses_fixed_endpoint(self):
        data = {"body": {"messages": []}, "key": "fixture-key", "timeout": 30, "max_bytes": 1000}
        with patch.object(assistant_transport.sys, "stdin", io.StringIO(json.dumps(data))), \
                patch.object(assistant_transport.sys, "stdout", io.StringIO()) as output, \
                patch.object(assistant_transport.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"choices": []}'
            assistant_transport.main()
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(request.get_header("User-agent"), "Astra-Agreement-Assistant/1.0")
        self.assertEqual(json.loads(output.getvalue())["status"], 200)

    def test_provider_timeout_and_429_are_clear_and_not_retried(self):
        for response, expected in [(subprocess.TimeoutExpired("fixture", 30), "assistant_timeout"),
                                   (self.provider_result({}, status=429), "provider_quota")]:
            AssistantBudget.objects.all().delete()
            with self.subTest(expected=expected), patch("client_files.assistant_provider.subprocess.run") as transport:
                if isinstance(response, Exception):
                    transport.side_effect = response
                else:
                    transport.return_value = response
                with self.assertRaises(AssistantError) as caught:
                    GroqAssistant().answer([], SCHEMA)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(transport.call_count, 1)

    def test_truncated_provider_response_rejected(self):
        with patch("client_files.assistant_provider.subprocess.run", return_value=self.provider_result({}, finish="length")):
            with self.assertRaises(AssistantError) as caught:
                GroqAssistant().answer([], SCHEMA)
        self.assertEqual(caught.exception.code, "invalid_assistant_response")

    def test_bad_provider_key_has_actionable_error(self):
        with patch("client_files.assistant_provider.subprocess.run", return_value=self.provider_result({}, status=401)):
            with self.assertRaises(AssistantError) as caught:
                GroqAssistant().answer([], SCHEMA)
        self.assertEqual(caught.exception.code, "provider_configuration")
        self.assertNotIn("fixture-key", str(caught.exception))

    @override_settings(ASSISTANT_GROQ_API_KEY="")
    def test_missing_provider_key_does_not_spend_quota(self):
        with self.assertRaises(AssistantError):
            GroqAssistant().answer([], SCHEMA)
        self.assertFalse(AssistantBudget.objects.exists())

    def test_endpoint_authorization_and_safe_json_output(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        client = self.login_client()
        self.assertEqual(client.get(self.url).status_code, 200)
        with patch.object(GroqAssistant, "answer", return_value={"category": "unrelated", "claims": []}):
            response = client.post(self.url, {"action": "question", "question": "Weather?"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json()["answer"]["category"], "unrelated")

    def test_endpoint_rejects_client_supplied_document_scope(self):
        response = self.login_client().post(self.url, {"action": "question", "question": "Payment?", "requestId": str(uuid.uuid4())}, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_csrf_remains_required(self):
        client = self.login_client(Client(enforce_csrf_checks=True))
        self.assertEqual(client.post(self.url, {"action": "summary"}, content_type="application/json").status_code, 403)
        token = client.get(self.url).json()["csrfToken"]
        response = client.post(self.url, {"action": "question", "question": "Should I sign?"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"]["category"], "declined")

    def test_assistant_failure_does_not_block_document_or_signing_status(self):
        client = self.login_client()
        with patch.object(GroqAssistant, "answer", side_effect=AssistantError("Free quota exhausted", status=429)):
            self.assertEqual(client.post(self.url, {"action": "question", "question": "Payment?"}, content_type="application/json").status_code, 429)
        self.assertEqual(client.get(reverse("client_agreement", args=[self.invitation.pk])).status_code, 200)
        self.assertEqual(client.get(reverse("client_agreement_signing", args=[self.invitation.pk])).status_code, 200)

    def test_document_still_accessible_but_assistant_denied_post_sign(self):
        client = self.login_client()
        SigningSubmission.objects.create(recipient=self.recipient, signing_request=self.issued, template_version=self.version,
                                         invitation=self.invitation, submission_key=uuid.uuid4(), pdf_sha256=self.version.pdf_sha256,
                                         values={"signature": "Client"}, consent_text="I intend to sign.")
        self.assertEqual(client.get(self.url).status_code, 403)
        self.assertEqual(client.get(reverse("client_agreement", args=[self.invitation.pk])).status_code, 200)

    def replace_source(self, texts, *, image=False):
        content = pdf_bytes(texts, image=image)
        storage = PrivateDocumentStorage(self.company.pk, self.agreement.pk)
        artifact = storage.write([content], max_bytes=settings.AGREEMENT_MAX_BYTES)
        self.tamper(self.agreement, original_key=artifact.key, pdf_key=artifact.key,
                    original_sha256=artifact.sha256, pdf_sha256=artifact.sha256,
                    original_size_bytes=artifact.size_bytes, pdf_size_bytes=artifact.size_bytes,
                    page_count=len(texts), page_geometry=[{"width": 612, "height": 792, "rotation": 0}] * len(texts))
        self.tamper(self.version, pdf_sha256=artifact.sha256)

    def cited_result(self, sources):
        passage = next(p for p in sources.passages.values() if p["label"] == "Agreement text")
        return {"category": "answer", "claims": [{"text": "The agreement discusses payment.", "sources": [passage["id"]]}]}

    def test_summary_followed_by_question_uses_two_calls_without_blanket_wait(self):
        sources = AssistantSources(self.resolve())
        fixture = self.provider_result(self.cited_result(sources))
        with patch.object(AgreementAssistant, "sources_class", return_value=sources), patch("client_files.assistant_provider.subprocess.run", return_value=fixture) as transport:
            summary = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "summary"})
            question = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "question", "question": "What does it say about payment?"})
        self.assertTrue(summary["coverage"]["complete"])
        self.assertTrue(question["claims"])
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(AssistantBudget.objects.get(provider="groq").calls, 2)

    def test_multi_page_summary_uses_larger_bounded_context(self):
        self.replace_source([f"Page {page}. " + "Payment is due within thirty days. " * 38 for page in range(1, 5)])
        sources = AssistantSources(self.resolve())
        seen = []
        def answer(messages, schema):
            seen.extend(messages)
            return self.cited_result(sources)
        with patch.object(GroqAssistant, "answer", side_effect=answer):
            result = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "summary"})
        encoded = json.dumps(seen, ensure_ascii=False).encode()
        self.assertGreater(len(encoded), 6000)
        self.assertLessEqual(len(encoded), settings.ASSISTANT_INPUT_MAX_BYTES)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["selectedPages"], [1, 2, 3, 4])

    def test_longer_summary_explicitly_reports_selected_passages(self):
        self.replace_source([f"Page {page}. " + "Payment is due within thirty days. " * 100 for page in range(1, 9)])
        sources = AssistantSources(self.resolve())
        with patch.object(GroqAssistant, "answer", return_value=self.cited_result(sources)):
            result = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "summary"})
        self.assertFalse(result["coverage"]["complete"])
        self.assertTrue(result["coverage"]["selectionLimited"])
        self.assertEqual(result["coverage"]["pageCount"], 8)
        self.assertEqual(result["coverage"]["selectedPages"], list(range(1, 9)))

    def test_mixed_text_image_summary_keeps_text_and_declares_uninterpreted_content(self):
        self.replace_source(["Payment is due within thirty days."], image=True)
        sources = AssistantSources(self.resolve())
        with patch.object(GroqAssistant, "answer", return_value=self.cited_result(sources)) as provider:
            result = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "summary"})
        provider.assert_called_once()
        self.assertTrue(result["claims"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["mixedPages"], [1])
        self.assertFalse(result["coverage"]["selectionLimited"])

    def test_actual_scanned_summary_does_not_invent_content_or_call_provider(self):
        self.replace_source([""], image=True)
        with patch.object(GroqAssistant, "answer") as provider:
            result = AgreementAssistant().answer(self.request(), self.invitation.pk, {"action": "summary"})
        provider.assert_not_called()
        self.assertEqual(result["category"], "unreadable")
        self.assertEqual(result["coverage"]["unreadablePages"], [1])
        self.assertEqual(result["claims"], [])

    def test_provider_retry_timing_is_shared_and_lease_is_released(self):
        fixture = SimpleNamespace(returncode=0, stdout=json.dumps({"status": 429, "retryAfter": 7}).encode())
        with patch("client_files.assistant_provider.subprocess.run", return_value=fixture) as transport:
            with self.assertRaises(AssistantError) as caught:
                GroqAssistant().answer([], SCHEMA)
            self.assertEqual(caught.exception.retry_after, 7)
            with self.assertRaises(AssistantError) as caught:
                GroqAssistant().answer([], SCHEMA)
            self.assertEqual(caught.exception.code, "provider_quota")
            self.assertEqual(transport.call_count, 1)
        self.assertLessEqual(AssistantBudget.objects.get(provider="groq-active").next_at, timezone.now())

    def test_expired_lease_can_recover_but_old_release_cannot_clear_a_new_lease(self):
        old = AssistantAdmission.reserve()
        # Advance beyond the lease as a real recovery would. Forcing the row
        # backwards without advancing time can reuse one Windows clock tick.
        with patch("client_files.assistant_provider.timezone.now", return_value=old + timedelta(seconds=1)):
            current = AssistantAdmission.reserve()
            AssistantAdmission.release(old)
            self.assertEqual(AssistantBudget.objects.get(provider="groq-active").next_at, current)
            AssistantAdmission.release(current)
