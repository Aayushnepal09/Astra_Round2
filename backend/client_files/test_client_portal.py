"""Fictional multi-agreement portal isolation and independent signing."""
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from agreements.models import TemplateVersion
from agreements.uploads import AgreementUpload
from login.models import ClientInvitation
from .assistant_access import AssistantAccess, AssistantAccessError
from .lifecycle import save_file_state
from .models import ClientFile, CompanyClient, ClientJob, SigningDraft, SigningRequest, SigningRecipient, SigningAccess, SigningSubmission
from .signing import DemoRequestIssuer
from .test_request_lifecycle import LifecycleFixture
from .test_assistant import pdf_bytes


class SharedClientPortalChecks(LifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.customer = CompanyClient.objects.create(company=self.company,name='Shared fictional client',email=self.file.signer_email)
        self.job = ClientJob.objects.create(company=self.company,client=self.customer,reference='PORTAL-JOB-1',title='First shared job')
        self.second_job = ClientJob.objects.create(company=self.company,client=self.customer,reference='PORTAL-JOB-2',title='Second shared job')
        self.file.job = self.job
        self.file.save()
        self.source = self.issue()
        self.browser = self.verify(self.source)
        self.root_invitation = self.invitation(self.source)
        self.portal_url = reverse('client_portal',args=[self.root_invitation.pk])

    def new_file(self, *, job=None, email=None, company=None, version=None, reference=None):
        company = company or self.company
        return ClientFile.objects.create(company=company,job=job or self.second_job,reference=reference or 'PORTAL-'+uuid.uuid4().hex,
            client_name='Fictional shared client',title='Fictional shared agreement',signer_name='Demo Client',
            signer_email=email or self.file.signer_email,template_version=version or self.version,
            preparation_values={self.fee_id:'Fee 100'})

    def issue_file(self, file):
        issued,_=DemoRequestIssuer(file.company).issue(file.pk,template_version_id=str(file.template_version_id),updated_at=file.updated_at.isoformat(), replaces_request_id=str(file.current_request_id) if file.current_request_id else None)
        return issued

    def route(self, issued, name='client_agreement'):
        return reverse(name,args=[self.invitation(issued).pk])

    def test_shared_jobs_and_new_requests_appear_but_other_clients_signers_and_drafts_do_not(self):
        second = self.issue_file(self.new_file())
        other_client = CompanyClient.objects.create(company=self.company,name='Another client',email=self.file.signer_email)
        other_job = ClientJob.objects.create(company=self.company,client=other_client,reference='PRIVATE-JOB')
        private = self.issue_file(self.new_file(job=other_job))
        other_signer = self.issue_file(self.new_file(email='another-signer@example.test'))
        draft = self.new_file(reference='NEVER-SHARED-DRAFT')
        result=self.browser.get(self.portal_url)
        self.assertEqual(result.status_code,200)
        self.assertEqual({row['id'] for row in result.context['agreements']},{str(self.source.pk),str(second.pk)})
        self.assertEqual(result.context['counts'],{'all':2,'needs':2,'signed':0})
        self.assertNotContains(result,draft.reference)
        for issued in (private,other_signer):
            self.assertEqual(self.browser.get(self.route(issued)).status_code,302)
            self.assertEqual(self.browser.get(self.route(issued,'client_agreement_signing')).status_code,401)
            self.assertEqual(self.browser.get(reverse('client_agreement_page',args=[self.invitation(issued).pk,1])).status_code,401)
            self.assertEqual(self.browser.get(self.route(issued,'client_agreement_assistant')).status_code,401)

    def test_signing_another_agreement_uses_verified_portal_identity_and_keeps_signatures_separate(self):
        second=self.issue_file(self.new_file())
        review=self.browser.get(self.route(second))
        self.assertEqual(review.status_code,200)
        self.assertContains(review,self.portal_url)
        self.assertEqual(self.browser.get(reverse('client_agreement_page',args=[self.invitation(second).pk,1])).status_code,200)
        self.assertEqual(AssistantAccess.resolve(self.client_request(self.browser),self.invitation(second).pk).signing_request_id,second.pk)
        self.sign_client(second,client=self.browser)
        submission=SigningSubmission.objects.get(signing_request=second)
        self.assertEqual(submission.invitation_id,self.root_invitation.pk)
        self.assertFalse(SigningSubmission.objects.filter(signing_request=self.source).exists())
        self.assertIsNone(self.invitation(second).used_at)  # No fabricated code-verification event.
        pdf=self.browser.get(self.route(second,'client_completed_pdf'))
        self.assertEqual(pdf.status_code,200)
        self.assertTrue(b''.join(pdf.streaming_content).startswith(b'%PDF-'))
        history=second.completion.history['signers'][0]
        self.assertEqual(history['accessMethod'],'verified_client_portal')
        self.assertEqual(history['verificationInvitationId'],str(self.root_invitation.pk))
        self.assertEqual(self.browser.get(self.route(second,'client_agreement_assistant')).status_code,403)
        signed=self.browser.get(self.portal_url,{'filter':'signed'})
        self.assertEqual([row['id'] for row in signed.context['agreements']],[str(second.pk)])
        needs=self.browser.get(self.portal_url,{'filter':'needs'})
        self.assertEqual([row['id'] for row in needs.context['agreements']],[str(self.source.pk)])

    def test_cancelling_source_does_not_remove_other_access_or_its_saved_progress(self):
        second=self.issue_file(self.new_file())
        recipient=second.recipients.get(role='client')
        payload=self.payload(recipient); payload['action']='save'; payload['consent']=False
        self.assertEqual(self.browser.post(self.route(second,'client_agreement_signing'),payload,content_type='application/json').status_code,200)
        self.assertEqual(SigningDraft.objects.filter(recipient=recipient).count(),1)
        self.assertEqual(SigningDraft.objects.filter(recipient__signing_request=self.source).count(),0)
        self.lifecycle.cancel(self.file.pk,self.source.pk)
        self.assertEqual(self.browser.get(self.portal_url).status_code,200)
        self.assertEqual(self.browser.get(self.route(self.source)).status_code,302)
        self.assertEqual(self.browser.get(self.route(self.source,'client_agreement_assistant')).status_code,401)
        self.assertEqual(self.browser.get(self.route(second)).status_code,200)
        self.sign_client(second,client=self.browser)
        self.assertEqual(second.submissions.count(),1)

    def test_cancelled_and_replaced_target_cannot_be_read_or_signed(self):
        file=self.new_file(); second=self.issue_file(file)
        old_url=self.route(second)
        self.lifecycle.cancel(file.pk,second.pk)
        result=self.browser.get(self.portal_url)
        row=next(row for row in result.context['agreements'] if row['id']==str(second.pk))
        self.assertEqual(row['status'],'cancelled'); self.assertIsNone(row['review_url'])
        self.assertEqual(self.browser.get(old_url).status_code,302)
        self.lifecycle.prepare_replacement(file.pk,second.pk)
        self.assertNotIn(str(second.pk), {row['id'] for row in self.browser.get(self.portal_url).context['agreements']})
        file.refresh_from_db(); replacement=self.issue_file(file)
        self.assertEqual(self.browser.get(self.route(replacement)).status_code,200)
        self.assertEqual(self.browser.get(old_url).status_code,302)
        ids={row['id'] for row in self.browser.get(self.portal_url).context['agreements']}
        self.assertIn(str(replacement.pk),ids); self.assertNotIn(str(second.pk),ids)
        self.assertFalse(replacement.submissions.exists())

    def test_expired_target_is_denied_while_other_agreements_stay_available(self):
        second=self.issue_file(self.new_file())
        SigningRequest._base_manager.filter(pk=second.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.browser.get(self.route(second,'client_agreement_signing')).status_code,401)
        self.assertEqual(self.browser.get(self.route(self.source)).status_code,200)
        self.assertEqual(self.browser.get(self.portal_url).status_code,200)

    def test_company_and_email_isolation_even_with_same_client_display_name(self):
        customer=CompanyClient.objects.create(company=self.other,name=self.customer.name,email=self.file.signer_email)
        job=ClientJob.objects.create(company=self.other,client=customer,reference='OTHER-COMPANY')
        agreement,_=AgreementUpload(self.other).save([pdf_bytes(['Fictional other-company agreement only.'])],filename='private.pdf',creation_key=uuid.uuid4())
        version=TemplateVersion.objects.create(agreement=agreement,name='PRIVATE OTHER COMPANY',number=1,source_revision=0,pdf_sha256=agreement.pdf_sha256,fields=self.version.fields)
        private=self.issue_file(self.new_file(job=job,company=self.other,version=version))
        self.assertEqual(self.browser.get(self.route(private)).status_code,302)
        self.assertEqual(self.browser.get(self.route(private,'client_agreement_assistant')).status_code,401)
        self.assertNotContains(self.browser.get(self.portal_url),'PRIVATE OTHER COMPANY')
        self.customer.email='new-default@example.test'; self.customer.save()
        self.assertEqual(self.browser.get(self.portal_url).status_code,200)
        self.assertEqual(self.source.client_id,self.customer.pk)
        self.source.client=customer
        with self.assertRaises(ValidationError): self.source.save()

    def test_csrf_wrong_document_binding_and_logout_still_apply(self):
        second=self.issue_file(self.new_file()); recipient=second.recipients.get(role='client')
        strict=Client(enforce_csrf_checks=True); strict.cookies=self.browser.cookies
        response=strict.get(self.route(second,'client_agreement_signing')); token=response.json()['csrfToken']
        payload=self.payload(recipient); payload['action']='save'
        self.assertEqual(strict.post(self.route(second,'client_agreement_signing'),payload,content_type='application/json').status_code,403)
        payload['requestId']=str(self.source.pk)
        self.assertEqual(strict.post(self.route(second,'client_agreement_signing'),payload,content_type='application/json',HTTP_X_CSRFTOKEN=token).status_code,409)
        self.assertEqual(self.browser.post(reverse('client_logout'),{},content_type='application/json').status_code,200)
        self.assertEqual(self.browser.get(self.route(second,'client_agreement_signing')).status_code,401)

    def test_search_and_pagination_cover_all_shared_requests(self):
        for index in range(26):
            file=self.new_file(reference=f'PAGED-SHARED-{index:02}')
            issued=SigningRequest.objects.create(client_file=file,template_version=self.version,preparation_values=file.preparation_values,
                                                 expires_at=timezone.now()+timedelta(days=14))
            recipient=SigningRecipient.objects.create(signing_request=issued,role='client',name=file.signer_name,email=file.signer_email)
            invitation=ClientInvitation.objects.create(company=self.company,client_email=recipient.email,request_ref=str(issued.pk),code_hash='not-used',expires_at=issued.expires_at)
            SigningAccess.objects.create(invitation=invitation,recipient=recipient)
            file.current_request=issued; file.status='awaiting_client'; save_file_state(file,['current_request','status'])
        first=self.browser.get(self.portal_url)
        self.assertEqual(first.context['counts']['all'],27)
        self.assertEqual(len(first.context['agreements']),25)
        self.assertEqual(len(self.browser.get(self.portal_url,{'page':2}).context['agreements']),2)
        result=self.browser.get(self.portal_url,{'q':'PAGED-SHARED-00'})
        self.assertEqual(len(result.context['agreements']),1)
        self.assertContains(result,'PAGED-SHARED-00')
        self.assertEqual(self.browser.get(self.portal_url,{'filter':'unknown'}).status_code,400)

    def test_session_time_limits_and_explicit_verification_revocation_apply_to_every_document(self):
        second=self.issue_file(self.new_file())
        request=self.client_request(self.browser)
        request.session['client_started']-=7201
        from .portal_access import client_access
        self.assertIsNone(client_access(request,invitation_id=self.invitation(second).pk)[1])
        ClientInvitation.objects.filter(pk=self.root_invitation.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.browser.get(self.route(second,'client_agreement_signing')).status_code,401)

    def test_issued_client_binding_does_not_follow_later_job_contact_changes(self):
        other=CompanyClient.objects.create(company=self.company,name='Other fictional client',email=self.file.signer_email)
        self.job.client=other; self.job.save()
        private=self.issue_file(self.new_file(job=self.job))
        self.assertEqual(self.browser.get(self.portal_url).status_code,200)
        self.assertEqual(self.browser.get(self.route(private)).status_code,302)
        self.source.refresh_from_db()
        self.assertEqual(self.source.client_id,self.customer.pk)
