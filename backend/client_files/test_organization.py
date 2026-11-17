"""Focused grouping, isolation and signing checks with fictional documents."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from agreements.models import Agreement, TemplateVersion
from agreements.uploads import AgreementUpload
from .models import ClientFile, ClientJob, CompanyClient, SigningRequest
from .organization import ClientOrganization
from .test_assistant import pdf_bytes
from .test_request_lifecycle import LifecycleFixture


class ClientOrganizationChecks(LifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.customer = CompanyClient.objects.create(company=self.company, name="Fictional Repeat Client",
                                                     email="repeat@example.test", signer_name="Fictional Signer")
        self.organization = ClientOrganization(self.company)
        self.job = self.organization.create_job(self.customer, reference="JOB-1", title="First job", version_ids=[], key=uuid.uuid4())

    def post(self, url, **payload):
        return self.client.post(url, {"workspace_id": str(self.company.pk), "creation_key": str(uuid.uuid4()), **payload}, content_type="application/json")

    def test_multiple_agreements_reuse_contacts_and_retries_do_not_duplicate(self):
        second = self.make_version(number=2)
        key = str(uuid.uuid4())
        url = reverse("company_client_jobs", args=[self.customer.pk])
        payload = dict(reference="JOB-2", title="Two agreements", versions=[str(self.version.pk), str(second.pk)], creation_key=key)
        first = self.post(url, **payload)
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.post(url, **payload)
        self.assertEqual(replay.json()["job"]["id"], first.json()["job"]["id"])
        files = ClientFile.objects.filter(job_id=first.json()["job"]["id"])
        self.assertEqual(files.count(), 2)
        self.assertEqual(set(files.values_list("signer_name", flat=True)), {"Fictional Signer"})
        self.assertEqual(set(files.values_list("signer_email", flat=True)), {"repeat@example.test"})
        self.assertFalse(SigningRequest.objects.exists())

    def test_new_agreement_does_not_change_completed_sibling(self):
        self.file.job = self.job
        self.file.save()
        issued = self.issue()
        self.sign_client(issued)
        self.file.refresh_from_db()
        self.assertEqual(self.file.status, "completed")
        before = (self.file.current_request_id, self.file.updated_at, issued.recipients.get(role="client").submission.values)
        key = uuid.uuid4()
        added = self.organization.add_agreements(self.job, [str(self.version.pk)], key)
        replay = self.organization.add_agreements(self.job, [str(self.version.pk)], key)
        self.assertEqual(added[0].pk, replay[0].pk)
        self.file.refresh_from_db()
        self.assertEqual(before, (self.file.current_request_id, self.file.updated_at, issued.recipients.get(role="client").submission.values))
        self.assertEqual(added[0].status, "draft")
        self.assertEqual(SigningRequest.objects.count(), 1)

    def test_job_only_upload_is_hidden_from_library_and_cannot_cross_jobs(self):
        agreement, _ = AgreementUpload(self.company, job=self.job).save([pdf_bytes(["Fictional one-off agreement."])],
                                filename="fictional-one-off.pdf", creation_key=uuid.uuid4())
        scoped = TemplateVersion.objects.create(agreement=agreement, number=1, name="Job only", source_revision=0,
                                                 pdf_sha256=agreement.pdf_sha256, fields=self.version.fields)
        shared = self.client.get(reverse("company_template_list"), {"workspace_id": str(self.company.pk)}).json()
        self.assertNotIn(str(scoped.pk), [v["id"] for v in shared["versions"]])
        local = self.client.get(reverse("company_template_list"), {"workspace_id": str(self.company.pk), "job_id": str(self.job.pk)}).json()
        self.assertIn(str(scoped.pk), [v["id"] for v in local["versions"]])
        other_job = self.organization.create_job(self.customer, reference="JOB-OTHER", title="", version_ids=[], key=uuid.uuid4())
        with self.assertRaises(ValidationError):
            self.organization.add_agreements(other_job, [str(scoped.pk)], uuid.uuid4())
        valid = self.organization.add_agreements(self.job, [str(scoped.pk)], uuid.uuid4())
        self.assertEqual(valid[0].template_version_id, scoped.pk)
        library = self.client.get(reverse("company_agreement_list"), {"workspace_id": str(self.company.pk)}).json()
        self.assertNotIn(str(agreement.pk), [a["id"] for a in library["agreements"]])

    def test_invalid_batch_is_atomic_and_other_company_is_denied(self):
        before = (ClientJob.objects.count(), ClientFile.objects.count())
        response = self.post(reverse("company_client_jobs", args=[self.customer.pk]), reference="BAD-BATCH", title="",
                             versions=[str(self.version.pk), str(uuid.uuid4())])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, (ClientJob.objects.count(), ClientFile.objects.count()))
        foreign = CompanyClient.objects.create(company=self.other, name="Other fictional client")
        self.assertEqual(self.client.get(reverse("company_client_detail", args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.post(reverse("company_client_jobs", args=[foreign.pk]), reference="BAD", title="", versions=[]).status_code, 404)
        self.assertEqual(self.client.get(reverse("company_clients"), {"workspace_id": str(self.other.pk)}).status_code, 409)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.company)
        csrf.cookies[settings.COMPANY_SESSION_COOKIE_NAME] = csrf.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertEqual(csrf.post(reverse("company_clients"), {"name": "Blocked"}, content_type="application/json").status_code, 403)
        self.assertEqual(Client().get(reverse("company_clients")).status_code, 401)

    def test_new_legacy_entry_is_grouped_without_changing_saved_signer_snapshot(self):
        response = self.post(reverse("company_file_create"), reference="LEGACY-ENTRY", client_name=self.customer.name,
                             client_email=self.customer.email, signer_name="Different assigned signer", signer_email="other-signer@example.test")
        self.assertEqual(response.status_code, 201, response.content)
        saved = ClientFile.objects.get(pk=response.json()["file"]["id"])
        self.assertEqual(saved.job.client_id, self.customer.pk)
        self.assertEqual(saved.signer_name, "Different assigned signer")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.signer_name, "Fictional Signer")

    def test_directory_counts_and_owned_job_template_context(self):
        self.organization.add_agreements(self.job, [str(self.version.pk)], uuid.uuid4())
        response = self.client.get(reverse("company_clients"))
        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["clients"][0]
        self.assertEqual((row["jobCount"], row["agreementCount"], row["completedCount"]), (1, 1, 0))
        detail = self.client.get(reverse("company_client_detail", args=[self.customer.pk])).json()
        self.assertFalse(detail["jobs"][0]["readyToStart"])
        self.assertEqual(self.client.get(reverse("company_dashboard"), {"job": str(self.job.pk)}).status_code, 200)
        self.assertEqual(self.client.get(reverse("company_dashboard"), {"job": str(uuid.uuid4())}).status_code, 404)
