"""Fictional PDF anchors and read-only suggestion authorization."""
import hashlib
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from agreements.models import Agreement, AgreementField, TemplateVersion
from agreements.uploads import AgreementUpload
from agreements.processing import PdfProcessor
from agreements.field_suggestions import FieldSuggester
from client_files.assistant_provider import AssistantError
from client_files.models import SigningRequest
from client_files.test_assistant import pdf_bytes
from login.models import CompanyAccount, ClientInvitation


def column_pdf(intro=None):
    """Fictional PDF with two signing columns joined into rows by extraction."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Count 1 /Kids [4 0 R] >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>']
    rows = [(740, 'Fictional agreement only. The date may be amended by written notice.', None),
            (700, 'COMPANY', 'CLIENT'),
            (680, 'Signature: ______________', 'Signature: ______________'),
            (660, 'Name: Example', 'Name: Example'),
            (640, 'Title: ______________', 'Title: ______________'),
            (620, 'Date: Date', 'Date: Date')]
    if intro:
        import textwrap
        lines = textwrap.wrap(intro, width=78)
        rows = [(740-i*16, line, None) for i, line in enumerate(lines)] + [(y-len(lines)*16, left, right) for y,left,right in rows[1:]]
    content = []
    for y, left, right in rows:
        for x, text in [(60, left), (340, right)]:
            if text:
                content.append(f'BT /F1 10 Tf 1 0 0 1 {x} {y} Tm ({text}) Tj ET')
    content = '\n'.join(content).encode('ascii')
    objects.append(f'<< /Length {len(content)} >>\nstream\n'.encode()+content+b'\nendstream')
    data, offsets = b'%PDF-1.7\n', [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{number} 0 obj\n'.encode()+body+b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return data+f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


class FieldSuggestionChecks(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="field-suggestion-check-", dir=settings.BASE_DIR / ".local")
        self.addCleanup(self.directory.cleanup)
        self.config = override_settings(AGREEMENT_STORAGE_ROOT=Path(self.directory.name), ASSISTANT_GROQ_API_KEY="fixture-key",
                                       PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.company = CompanyAccount.objects.create_user("fields@example.test", company_name="Fictional Fields")
        self.other = CompanyAccount.objects.create_user("other-fields@example.test", company_name="Fictional Other")
        self.agreement = self.upload(pdf_bytes(["Client signature: ____________________  Date: ____________", "Company signature: ____________________"]))
        self.url = reverse("company_field_suggestions", args=[self.agreement.pk])
        self.client.force_login(self.company)
        self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.headers = {"HTTP_X_WORKSPACE_ID": str(self.company.pk)}

    def upload(self, content):
        import uuid
        return AgreementUpload(self.company).save([content], filename="fictional.pdf", creation_key=uuid.uuid4())[0]

    def payload(self):
        return {"pdfSha256": self.agreement.pdf_sha256, "revision": self.agreement.fields_revision}

    @staticmethod
    def response(messages, schema):
        anchors = json.loads(messages[1]["content"].split("\n", 1)[1])
        anchor = next((a for a in anchors if "Client signature" in a["text"]), anchors[0])
        return {"fields": [{"anchorId": anchor["id"], "type": "signature", "role": "client",
                            "label": "Client signature", "required": True}]}

    def test_native_layout_has_real_blank_regions_and_bounded_positions(self):
        from agreements.storage import PrivateDocumentStorage
        storage = PrivateDocumentStorage(self.company.pk, self.agreement.pk)
        with storage.open(self.agreement.pdf_key) as source:
            pages = PdfProcessor().field_layout(source.name)
        self.assertEqual(len(pages), 2)
        self.assertGreaterEqual(len(pages[0]["anchors"]), 2)
        for page in pages:
            for anchor in page["anchors"]:
                box = anchor["box"]
                self.assertGreater(box["width"], 0)
                self.assertGreater(box["height"], 0)
                self.assertGreaterEqual(box["x"], 0)
                self.assertGreaterEqual(box["y"], 0)
                self.assertLessEqual(box["x"] + box["width"], 1)
                self.assertLessEqual(box["y"] + box["height"], 1)

    def test_suggestions_do_not_save_fields_publish_or_issue(self):
        with patch("agreements.field_suggestions.GroqAssistant.answer", side_effect=self.response) as provider:
            result = self.client.post(self.url, self.payload(), content_type="application/json", **self.headers)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["fields"][0]["type"], "signature")
        self.assertIn("Client signature", result.json()["fields"][0]["evidence"])
        self.assertEqual(provider.call_count, 0)
        self.assertEqual(AgreementField.objects.count(), 0)
        self.assertEqual(TemplateVersion.objects.count(), 0)
        self.assertEqual(SigningRequest.objects.count(), 0)
        self.assertEqual(ClientInvitation.objects.count(), 0)
        self.agreement.refresh_from_db()
        self.assertEqual(self.agreement.fields_revision, 0)

    def test_blank_on_next_line_uses_the_preceding_field_label(self):
        from agreements.storage import PrivateDocumentStorage
        agreement = self.upload(pdf_bytes(["TEST DATA ONLY. Client name: ____________________ Client signature: ____________________ Date: ____________"]))
        with PrivateDocumentStorage(self.company.pk, agreement.pk).open(agreement.pdf_key) as source:
            pages = PdfProcessor().field_layout(source.name)
        labels = [a["text"] for a in pages[0]["anchors"]]
        self.assertIn("Client signature:", labels)
        self.assertIn("Date:", labels)

    def test_two_column_signing_blocks_have_distinct_anchors_and_role_context(self):
        from agreements.storage import PrivateDocumentStorage
        agreement = self.upload(column_pdf())
        with PrivateDocumentStorage(self.company.pk, agreement.pk).open(agreement.pdf_key) as source:
            anchors = PdfProcessor().field_layout(source.name)[0]['anchors']
        self.assertEqual(len(anchors), 8)
        self.assertEqual(len({a['id'] for a in anchors}), 8)
        for label in ['Signature:', 'Name: Example', 'Title:', 'Date: Date']:
            pair = [a for a in anchors if a['text'] == label]
            self.assertEqual(len(pair), 2, label)
            self.assertIn('COMPANY signing block', pair[0]['context'])
            self.assertIn('CLIENT signing block', pair[1]['context'])
            self.assertLess(pair[0]['box']['x'] + pair[0]['box']['width'], pair[1]['box']['x'])
        self.assertFalse(any('amended' in a['text'] for a in anchors))

    def test_ordinary_contract_sentences_are_not_field_labels(self):
        from agreements.field_layout import FIELD_LABEL
        for text in ['Date of termination is determined by notice.',
                     'The amount owing by Client under this agreement.',
                     'Agreement as of the Effective Date.',
                     'The name of a party may be changed by written notice.']:
            self.assertIsNone(FIELD_LABEL.fullmatch(text), text)
        for text in ['Client signature:', 'Printed name:', 'Effective date:', 'Title:', 'By:']:
            self.assertIsNotNone(FIELD_LABEL.fullmatch(text), text)

    def test_unauthenticated_and_other_company_denied_without_provider(self):
        with patch("agreements.field_suggestions.GroqAssistant.answer") as provider:
            self.assertEqual(Client().post(self.url, self.payload(), content_type="application/json", **self.headers).status_code, 401)
            self.client.force_login(self.other)
            self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
            result = self.client.post(self.url, self.payload(), content_type="application/json", HTTP_X_WORKSPACE_ID=str(self.other.pk))
            self.assertEqual(result.status_code, 404)
            provider.assert_not_called()

    def test_csrf_and_workspace_identity_remain_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.company)
        c.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = c.cookies[settings.SESSION_COOKIE_NAME].value
        with patch("agreements.field_suggestions.GroqAssistant.answer") as provider:
            self.assertEqual(c.post(self.url, self.payload(), content_type="application/json", **self.headers).status_code, 403)
            self.assertEqual(self.client.post(self.url, self.payload(), content_type="application/json").status_code, 409)
            provider.assert_not_called()

    def test_stale_or_wrong_source_is_rejected_before_provider(self):
        with patch("agreements.field_suggestions.GroqAssistant.answer") as provider:
            for payload in [dict(self.payload(), revision=2), dict(self.payload(), revision=True),
                            dict(self.payload(), pdfSha256="a"*64), dict(self.payload(), companyId="2")]:
                with self.subTest(payload=payload):
                    self.assertEqual(self.client.post(self.url, payload, content_type="application/json", **self.headers).status_code, 409)
            provider.assert_not_called()

    def test_hallucinated_anchor_or_illegal_preparation_type_rejected(self):
        self.use_ambiguous_blank()
        for changed in [{"anchorId": "invented"}, {"type": "execute"}, {"role": "preparation"}, {"required": 1}]:
            def response(messages, schema):
                result = self.response(messages, schema)
                result["fields"][0].update(changed)
                return result
            with self.subTest(changed=changed), patch("agreements.field_suggestions.GroqAssistant.answer", side_effect=response):
                result = self.client.post(self.url, self.payload(), content_type="application/json", **self.headers)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["code"], "invalid_field_suggestions")
        self.assertEqual(AgreementField.objects.count(), 0)

    def test_saved_revision_change_during_inference_returns_conflict(self):
        self.use_ambiguous_blank()
        def response(messages, schema):
            Agreement.objects.filter(pk=self.agreement.pk).update(fields_revision=1)
            return self.response(messages, schema)
        with patch("agreements.field_suggestions.GroqAssistant.answer", side_effect=response):
            result = self.client.post(self.url, self.payload(), content_type="application/json", **self.headers)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(AgreementField.objects.count(), 0)

    def test_scan_is_reported_without_inference(self):
        self.agreement = self.upload(pdf_bytes([""], image=True))
        self.url = reverse("company_field_suggestions", args=[self.agreement.pk])
        with patch("agreements.field_suggestions.GroqAssistant.answer") as provider:
            result = self.client.post(self.url, self.payload(), content_type="application/json", **self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["fields"], [])
        self.assertEqual(result.json()["coverage"]["limitedPages"], [1])
        provider.assert_not_called()

    def test_quota_errors_keep_manual_workflow_and_retry_timing(self):
        self.use_ambiguous_blank()
        with patch("agreements.field_suggestions.GroqAssistant.answer",
                   side_effect=AssistantError("Shared allowance busy.", status=429, code="assistant_quota", retry_after=12)):
            result = self.client.post(self.url, self.payload(), content_type="application/json", **self.headers)
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers["Retry-After"], "12")
        self.assertIn("Manual field placement", result.json()["error"])

    def use_ambiguous_blank(self):
        self.agreement = self.upload(pdf_bytes(['Client approval: __________________']))
        self.url = reverse('company_field_suggestions', args=[self.agreement.pk])

    def test_inline_company_client_details_and_both_signing_columns_are_found_locally(self):
        intro = ('This Agreement is entered into as of Date ("Effective Date") by and between Person, '
                 'a corporation with a principal place of business at Place ("Company"), and Person, '
                 'a corporation with a principal place of business at Place ("Client").')
        agreement = self.upload(column_pdf(intro))
        with patch('agreements.field_suggestions.GroqAssistant.answer', side_effect=AssertionError('External call forbidden')) as provider:
            result = FieldSuggester().suggest(agreement)
        self.assertEqual(len(result['fields']), 13)
        roles = [field['role'] for field in result['fields']]
        self.assertEqual((roles.count('preparation'), roles.count('company'), roles.count('client')), (5,4,4))
        labels = {field['label'] for field in result['fields'] if field['role']=='preparation'}
        self.assertEqual(labels, {'Effective date','Company legal name','Company address','Client legal name','Client address'})
        provider.assert_not_called()
        self.assertEqual(agreement.fields.count(), 0)

        # Inline token boxes must accept realistic business values without
        # overflowing or changing the immutable uploaded document.
        from agreements.storage import PrivateDocumentStorage
        values = {'Effective date': '2026-11-17', 'Company legal name': 'Fictional Company Limited',
                  'Company address': '10 Fictional Avenue, Test City', 'Client legal name': 'Example Client Corporation',
                  'Client address': '20 Demonstration Road, Sample City'}
        fields = [dict(field, value=values[field['label']]) for field in result['fields'] if field['role']=='preparation']
        storage = PrivateDocumentStorage(self.company.pk, agreement.pk)
        with storage.open(agreement.pdf_key) as source:
            original = Path(source.name).read_bytes()
            self.assertTrue(PdfProcessor().render_prepared(source.name, 1, fields).startswith(b'\x89PNG'))
            completed = PdfProcessor().complete(source.name, fields)
            self.assertTrue(completed.startswith(b'%PDF-'))
            self.assertEqual(Path(source.name).read_bytes(), original)

    def test_tampered_pdf_is_not_sent_to_provider(self):
        from agreements.storage import PrivateDocumentStorage
        storage = PrivateDocumentStorage(self.company.pk, self.agreement.pk)
        with storage.open(self.agreement.pdf_key) as source:
            Path(source.name).write_bytes(b"tampered")
        with patch("agreements.field_suggestions.GroqAssistant.answer") as provider:
            result = self.client.post(self.url, self.payload(), content_type="application/json", **self.headers)
        self.assertEqual(result.status_code, 422)
        provider.assert_not_called()

