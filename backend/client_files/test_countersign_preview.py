"""Accepted client signatures are visible before company countersigning."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from django.test import TestCase
from django.urls import reverse

from agreements.processing import PdfProcessor
from .accepted_values import accepted_fields
from .clients import client_data, client_totals
from .countersigning import CompanySigning
from .models import CompanyClient, ClientJob, SigningDraft, SigningSubmission, SigningCompletion
from .preparation import PreparationValues
from .test_request_lifecycle import LifecycleFixture
from .workspace import CompanyFileWorkspace


class CountersignPreviewChecks(LifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.customer=CompanyClient.objects.create(company=self.company,name='Fictional countersign client',email=self.file.signer_email)
        job=ClientJob.objects.create(company=self.company,client=self.customer,reference='COUNTER-JOB')
        self.file.job=job; self.file.save()
        self.issued=self.countersigned()

    def company_page(self):
        return reverse('company_countersign_page',args=[self.file.pk,1])

    def test_client_signature_moves_from_waiting_to_attention_and_clears_after_company_signs(self):
        before=CompanyFileWorkspace(self.company).snapshot()
        self.assertEqual((before['counts']['waiting'],before['counts']['attention']),(1,0))
        self.sign_client(self.issued)
        workspace=CompanyFileWorkspace(self.company)
        attention=workspace.snapshot({'filter':'attention'})
        self.assertEqual((attention['counts']['waiting'],attention['counts']['attention']),(0,1))
        self.assertEqual(attention['files'][0]['status'],'awaiting_company')
        self.assertTrue(attention['files'][0]['needsAttention'])
        self.assertEqual(attention['files'][0]['countersignUrl'],reverse('company_agreement',args=[self.file.pk]))
        self.assertEqual(workspace.snapshot({'filter':'waiting'})['files'],[])
        totals=client_data(client_totals(self.company).get(pk=self.customer.pk))
        self.assertEqual((totals['waitingCount'],totals['companySignatureCount']),(0,1))
        recipient=CompanySigning.recipient(SimpleNamespace(user=self.company,session={}),self.file.pk)
        response=self.client.post(reverse('company_agreement_signing',args=[self.file.pk]),self.payload(recipient),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['signing']['progress'],'completed')
        after=workspace.snapshot()
        self.assertEqual((after['counts']['attention'],after['counts']['ready']),(0,1))
        completed=self.client.get(reverse('company_agreement',args=[self.file.pk]))
        self.assertNotContains(completed,'Client signed · your company signature is required')

    def test_partial_preview_contains_accepted_client_signature_but_not_company_draft(self):
        with self.storage.open(self.agreement.pdf_key) as source:
            original=Path(source.name).read_bytes()
            baseline=PdfProcessor().render_prepared(source.name,1,PreparationValues.displayed(self.issued))
        client,_=self.sign_client(self.issued)
        self.issued.refresh_from_db()
        company=self.issued.recipients.get(role='company')
        company_field=next(field for field in self.version.fields if field['role']=='company')
        SigningDraft.objects.create(recipient=company,values={company_field['id']:'UNSUBMITTED COMPANY DRAFT'},revision=1)
        fields=PreparationValues.displayed(self.issued)+accepted_fields(self.issued)
        self.assertEqual([field['value'] for field in fields if field['type']=='signature'],['Demo Client'])
        with self.storage.open(self.agreement.pdf_key) as source:
            expected=PdfProcessor().render_prepared(source.name,1,fields)
            self.assertEqual(hashlib.sha256(Path(source.name).read_bytes()).digest(),hashlib.sha256(original).digest())
        response=self.client.get(self.company_page())
        self.assertEqual(response.status_code,200)
        self.assertNotEqual(response.content,baseline)
        self.assertEqual(response.content,expected)
        own=client.get(reverse('client_agreement_page',args=[self.invitation(self.issued).pk,1]))
        self.assertEqual(own.content,expected)
        self.assertFalse(SigningCompletion.objects.filter(signing_request=self.issued,completed_at__isnull=False).exists())
        review=self.client.get(reverse('company_agreement',args=[self.file.pk]))
        self.assertContains(review,'Their accepted signature and entries are shown on the document below.')
        self.assertContains(review,'This is an in-progress preview.')

    def test_unaccepted_values_are_never_painted_and_company_cannot_view_before_client_signs(self):
        client=self.verify(self.issued)
        recipient=self.issued.recipients.get(role='client')
        field=next(field for field in self.version.fields if field['role']=='client')
        SigningDraft.objects.create(recipient=recipient,values={field['id']:'UNSUBMITTED CLIENT DRAFT'},revision=1)
        self.assertEqual(accepted_fields(self.issued),[])
        self.assertEqual(self.client.get(self.company_page()).status_code,409)
        self.assertEqual(client.get(reverse('client_agreement_page',args=[self.invitation(self.issued).pk,1])).status_code,200)

    def test_invalid_recorded_consent_is_not_rendered_as_a_signed_document(self):
        self.sign_client(self.issued)
        SigningSubmission._base_manager.filter(signing_request=self.issued,recipient__role='client').update(consent_text='tampered')
        self.assertEqual(self.client.get(self.company_page()).status_code,409)

