"""Draft save conflicts, using fictional accounts and an isolated test database."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from agreements.models import Agreement, TemplateVersion
from agreements.storage import PrivateDocumentStorage
from login.models import CompanyAccount
from .models import ClientFile, SigningRequest
from .signing import DemoRequestIssuer
from .views import CompanyFileEditor


class DraftFixture:
    def setUp(self):
        self.company = CompanyAccount.objects.create_user("draft@example.test", company_name="Fictional Draft Co")
        self.other = CompanyAccount.objects.create_user("other@example.test", company_name="Fictional Other Co")
        self.file = ClientFile.objects.create(company=self.company, reference="FICTIONAL-1", client_name="Example Client")
        self.url = reverse("company_file_detail", args=[self.file.pk])
        self.client.force_login(self.company)
        self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def payload(self, **changes):
        return {"workspace_id": str(self.company.pk), "updatedAt": self.file.updated_at.isoformat(),
                "reference": self.file.reference, "client_name": "Example Client", "title": "First tab", **changes}

    def save(self, payload):
        return self.client.post(self.url, payload, content_type="application/json")

    def add_template(self):
        agreement = Agreement(company=self.company, original_filename="fictional.pdf", detected_format="pdf",
                              processing_state="ready", original_sha256="a" * 64, original_size_bytes=100,
                              pdf_sha256="a" * 64, pdf_size_bytes=100, page_count=1,
                              page_geometry=[{"width": 612, "height": 792, "rotation": 0}], validated_at=timezone.now())
        agreement.original_key = agreement.pdf_key = (
            f"{PrivateDocumentStorage.key_prefix(self.company.pk, agreement.pk)}/{uuid.uuid4()}.bin"
        )
        agreement.save()
        self.preparation_id = str(uuid.uuid4())
        common = {"page": 1, "x": .1, "y": .1, "width": .3, "height": .1, "required": True}
        self.version = TemplateVersion.objects.create(
            agreement=agreement, number=1, name="Fictional template", source_revision=0, pdf_sha256="a" * 64,
            fields=[dict(common, id=str(uuid.uuid4()), type="signature", role="client", label="Signature"),
                    dict(common, id=self.preparation_id, type="text", role="preparation", label="Fee")],
        )
        self.file.template_version = self.version
        self.file.preparation_values = {self.preparation_id: "Original fee"}
        self.file.save()


class DraftSaveChecks(DraftFixture, TestCase):
    def test_normal_save_returns_new_version(self):
        before = self.file.updated_at
        result = self.save(self.payload())
        self.assertEqual(result.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, "First tab")
        self.assertGreater(self.file.updated_at, before)
        self.assertEqual(result.json()["file"]["updatedAt"], self.file.updated_at.isoformat())

    def test_stale_save_preserves_first_save_and_preparation(self):
        self.add_template()
        first = self.payload(template_version=str(self.version.pk), preparation_values={self.preparation_id: "First fee"})
        stale = {**first, "title": "Stale tab", "template_version": "", "preparation_values": {}}
        self.assertEqual(self.save(first).status_code, 200)
        self.file.refresh_from_db()
        saved_version = self.file.updated_at
        result = self.save(stale)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["code"], "draft_conflict")
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, "First tab")
        self.assertEqual(self.file.template_version_id, self.version.pk)
        self.assertEqual(self.file.preparation_values, {self.preparation_id: "First fee"})
        self.assertEqual(self.file.updated_at, saved_version)

    def test_explicit_reload_allows_subsequent_save(self):
        stale = self.payload(title="Second tab")
        self.assertEqual(self.save(self.payload()).status_code, 200)
        self.assertEqual(self.save(stale).status_code, 409)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        latest = page.context["draft_data"]
        self.assertEqual(latest["title"], "First tab")
        result = self.save({**stale, "updatedAt": latest["updatedAt"]})
        self.assertEqual(result.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, "Second tab")

    def test_missing_or_invalid_version_cannot_bypass_guard(self):
        for version in [None, "", "invalid", "2000-01-01T00:00:00+00:00", 1, [], {}]:
            with self.subTest(version=version):
                payload = self.payload(updatedAt=version)
                if version is None:
                    payload.pop("updatedAt")
                result = self.save(payload)
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result.json()["code"], "draft_conflict")
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, "")

    def test_other_company_cannot_modify_file(self):
        self.client.force_login(self.other)
        self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        result = self.save(self.payload(workspace_id=str(self.other.pk)))
        self.assertEqual(result.status_code, 404)
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, "")

    def test_issued_file_stays_uneditable(self):
        ClientFile.objects.filter(pk=self.file.pk).update(status=ClientFile.Status.AWAITING_CLIENT)
        result = self.save(self.payload())
        self.assertEqual(result.status_code, 409)
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, "")

    def test_creation_retry_does_not_require_version_or_overwrite(self):
        url = reverse("company_file_create")
        payload = self.payload(reference="FICTIONAL-NEW", creation_key=str(uuid.uuid4()))
        payload.pop("updatedAt")
        first = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(first.status_code, 201)
        retry = self.client.post(url, {**payload, "title": "Unconfirmed retry"}, content_type="application/json")
        self.assertEqual(retry.status_code, 200)
        self.assertFalse(retry.json()["created"])
        self.assertEqual(retry.json()["file"], first.json()["file"])

    def test_repeating_or_backward_clock_still_invalidates_version(self):
        for now in [self.file.updated_at, self.file.updated_at - timedelta(days=1)]:
            with self.subTest(now=now):
                self.file.refresh_from_db()
                before = self.file.updated_at
                payload = self.payload()
                with patch("django.db.models.fields.timezone.now", return_value=now):
                    self.assertEqual(self.save(payload).status_code, 200)
                self.file.refresh_from_db()
                self.assertGreater(self.file.updated_at, before)
                self.assertEqual(self.save(payload).status_code, 409)

    def test_signing_request_still_checks_saved_version(self):
        self.add_template()
        old = self.file.updated_at.isoformat()
        payload = self.payload(template_version=str(self.version.pk), preparation_values={self.preparation_id: "New fee"})
        self.assertEqual(self.save(payload).status_code, 200)
        with self.assertRaisesMessage(ValidationError, "The saved setup changed"):
            DemoRequestIssuer(self.company).issue(self.file.pk, template_version_id=str(self.version.pk), updated_at=old)
        self.assertFalse(SigningRequest.objects.filter(client_file=self.file).exists())

    def test_authentication_and_csrf_still_required(self):
        self.assertEqual(Client().post(self.url, self.payload(), content_type="application/json").status_code, 401)
        protected = Client(enforce_csrf_checks=True)
        protected.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = self.client.cookies[settings.COMPANY_SESSION_COOKIE_NAME].value
        self.assertEqual(protected.post(self.url, self.payload(), content_type="application/json").status_code, 403)
        page = protected.get(self.url)
        token = page.content.decode().split('name="csrf-token" content="')[1].split('"')[0]
        self.assertEqual(protected.post(self.url, self.payload(), content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 200)


class ConcurrentDraftSaveChecks(DraftFixture, TransactionTestCase):
    def test_simultaneous_saves_have_one_winner(self):
        if connection.vendor == "sqlite" and connection.is_in_memory_db():
            self.skipTest("Use a temporary file test database to exercise SQLite's writer waiting.")
        barrier = Barrier(2)
        payload = self.payload()

        def save(title):
            close_old_connections()
            try:
                request = RequestFactory().post(self.url, {**payload, "title": title}, content_type="application/json")
                request.user, request.session = self.company, {}
                barrier.wait(timeout=10)
                response = CompanyFileEditor.as_view()(request, file_id=self.file.pk)
                return title, response.status_code, json.loads(response.content)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, ["Concurrent tab A", "Concurrent tab B"]))
        self.assertEqual(sorted(status for _, status, _ in results), [200, 409])
        winner = next(title for title, status, _ in results if status == 200)
        self.assertEqual(next(body for _, status, body in results if status == 409)["code"], "draft_conflict")
        self.file.refresh_from_db()
        self.assertEqual(self.file.title, winner)
