import json
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone
from django.core.exceptions import RequestDataTooBig, SuspiciousFileOperation, ValidationError
from django.db import DatabaseError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from agreements.processing import DocumentError, PdfProcessor
from agreements.storage import PrivateDocumentStorage
from login.common import auth_error, client_identity
from .portal_access import client_access
from .submissions import ClientSigning, SigningError
from .models import ClientFile, CompanyClient, SigningCompletion, SigningRecipient
from login.models import ClientInvitation
from .preparation import PreparationValues
from .accepted_values import preview_fields


def read_signing_json(request):
    if request.content_type != "application/json":
        raise SigningError("Send signing values as JSON. Your entries are still here.", status=400)
    try:
        raw = request.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise RequestDataTooBig
    except RequestDataTooBig as error:
        raise SigningError("Signing values exceed the 256 KiB request limit. Shorten the entered text and retry; your entries are still here.", status=413, code="submission_too_large") from error
    except UnreadablePostError as error:
        raise SigningError("The signing values could not be read. Retry; your entries are still here.", status=400) from error
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise SigningError("Send valid JSON signing values. Your entries are still here.", status=400) from error
    if not isinstance(payload, dict):
        raise SigningError("Send signing values as a JSON object. Your entries are still here.", status=400)
    return payload


class ClientAgreement(View):
    json_response = False
    signing_service = ClientSigning
    signing_route = "client_agreement_signing"
    page_route = "client_agreement_page"
    def dispatch(self, request, *args, **kwargs):
        try:
            invitation = client_identity(request)
            _, recipient = client_access(request, invitation_id=kwargs['invitation_id']) if invitation else (None, None)
            portal_anchor = getattr(self, 'portal_view', False) and invitation and str(invitation.pk) == str(kwargs['invitation_id'])
            if not recipient and not portal_anchor:
                response = (
                    auth_error("Your access session has expired. Open your invitation and verify a new code.", status=401)
                    if kwargs.get("page_number") or self.json_response or request.method != "GET"
                    else redirect("client_invitation", invitation_id=kwargs["invitation_id"])
                )
            else:
                self.recipient = recipient
                self.verification = invitation
                response = super().dispatch(request, *args, **kwargs)
        except ValidationError:
            response = auth_error("The issued agreement configuration could not be validated. Contact the company.", status=409)
        except DatabaseError:
            response = auth_error("The agreement is temporarily unavailable. Retry loading it.", status=503)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def get(self, request, invitation_id, page_number=None):
        issued = self.recipient.signing_request
        version = issued.template_version
        agreement = version.agreement
        if version.pdf_sha256 != agreement.pdf_sha256:
            return auth_error("The issued agreement is unavailable. Contact the company.", status=409)
        if page_number is not None:
            if not 1 <= page_number <= agreement.page_count:
                return auth_error("This page is not in your agreement.", status=404)
            try:
                if self.recipient.role == "client" and issued.status == ClientFile.Status.COMPLETED:
                    source = self.open_completed()
                else:
                    storage = PrivateDocumentStorage(agreement.company_id, agreement.pk)
                    source = storage.open(agreement.pdf_key)
                with source:
                    if self.recipient.role == "client" and issued.status == ClientFile.Status.COMPLETED:
                        png = PdfProcessor().render(source.name, page_number)
                    else:
                        prepared = preview_fields(issued)
                        png = (PdfProcessor().render_prepared(source.name, page_number, prepared)
                               if prepared else PdfProcessor().render(source.name, page_number))
            except SigningError as error:
                return auth_error(str(error), status=error.status)
            except ValidationError:
                return auth_error("The completed agreement could not be validated. Contact the company.", status=409)
            except (OSError, SuspiciousFileOperation, DocumentError):
                return auth_error("Couldn’t load this agreement page. Try again.", status=503)
            return HttpResponse(png, content_type="image/png")
        return render(request, "client_agreement.html", {
            "static_prefix": settings.STATIC_URL,
            "agreement_name": version.name, "version_number": version.number,
            "signer_name": self.recipient.name,
            "preparation_values": PreparationValues.displayed(issued),
            "logout_url": reverse("client_logout"),
            **self.review_context(request, invitation_id),
            "signing_url": reverse(self.signing_route, args=[invitation_id]),
            "review_data": {
                "pageCount": agreement.page_count,
                "pageGeometry": agreement.page_geometry,
                "pages": [reverse(self.page_route, args=[invitation_id, number]) for number in range(1, agreement.page_count + 1)],
                "signing": self.signing_snapshot(invitation_id),
            },
        })

    def review_context(self, request, scope_id):
        issued = self.recipient.signing_request
        return {"client_name": issued.client.name if issued.client_id else issued.client_file.client_name,
                "company_name": issued.client_file.company.company_name or "Your service provider",
                "portal_url": reverse("client_portal", args=[request.session.get("client_invitation", str(scope_id))]),
                "invitation_url": reverse("client_invitation", args=[scope_id]),
                "assistant_url": reverse("client_agreement_assistant", args=[scope_id])}

    def signing_snapshot(self, scope_id):
        state = self.signing_service.snapshot(self.recipient)
        if self.recipient.role == "client":
            issued = self.recipient.signing_request
            completion = SigningCompletion.objects.filter(signing_request=issued).first()
            complete = bool(state["receipt"] and issued.status == ClientFile.Status.COMPLETED
                            and completion and completion.completed_at)
            state["completion"] = {
                "pdfUrl": reverse("client_completed_pdf", args=[scope_id]) if complete else None,
                "error": "Final processing needs attention. The company can retry without new signatures."
                    if completion and completion.last_error and not complete else "",
            }
        return state

    def open_completed(self):
        from .finalization import AgreementFinalizer

        issued = self.recipient.signing_request
        submission = issued.submissions.filter(recipient=self.recipient).first()
        if self.recipient.role != "client" or submission is None:
            raise SigningError("This completed agreement is unavailable for your access.", status=403)
        submission.full_clean()
        completion, handle = AgreementFinalizer(issued.client_file.company_id).open_completed(
            issued.client_file_id, request_id=issued.pk,
        )
        if completion.signing_request_id != issued.pk:
            handle.close()
            raise SigningError("The completed agreement does not match your request.", status=409)
        return handle


class ClientPortal(ClientAgreement):
    """All current requests explicitly shared with this client and verified signer."""
    portal_view = True

    @staticmethod
    def row(recipient):
        issued = recipient.signing_request
        file = issued.client_file
        submitted = getattr(recipient, 'submission', None) is not None
        completion = getattr(issued, 'completion', None)
        status = issued.status
        if status in ('awaiting_client','awaiting_company') and issued.expires_at and issued.expires_at <= timezone.now():
            status = 'expired'
        available = (status in ('awaiting_client','awaiting_company','finalizing','completed')
                     and file.status == status and file.template_version_id == issued.template_version_id)
        complete = available and status == 'completed' and submitted and completion and completion.completed_at
        invitation_id = recipient.portal_invitation
        if not invitation_id:
            available = False
        labels = {'awaiting_client':'Needs your signature', 'awaiting_company':'Waiting for company',
                  'finalizing':'Finalizing', 'completed':'Signed', 'cancelled':'Cancelled', 'expired':'Expired'}
        label = labels.get(status, 'Unavailable')
        if not available and status not in ('cancelled','expired'):
            label = 'Unavailable'
        review_url = reverse('client_agreement', args=[invitation_id]) if available else None
        return {'id':str(issued.pk), 'title':file.title or issued.template_version.name, 'job_title': (file.job.title or file.job.reference) if file.job_id else file.title or file.reference,
                'reference':file.reference, 'agreement_name':issued.template_version.name, 'version_number':issued.template_version.number,
                'status':status, 'status_label':label, 'submitted':submitted, 'completed':bool(complete),
                'needs_signature':available and status == 'awaiting_client' and not submitted,
                'review_url':review_url, 'pdf_url':reverse('client_completed_pdf', args=[invitation_id]) if complete else None,
                'invitation_id':str(invitation_id) if invitation_id else None, 'updated_at':file.updated_at,
                'status_copy': ('All signatures and final records saved' if complete else
                                'Further signing stopped' if status == 'cancelled' else
                                'Ask the company for a replacement request' if status == 'expired' else
                                'Your signature is recorded' if submitted else
                                'Waiting for your signature' if available else 'Contact the company')}

    def get(self, request, invitation_id):
        from .portal_access import shared_recipients
        scope = request.session.get('client_portal')
        query = request.GET.get('q','').strip()
        selected_filter = request.GET.get('filter','all')
        if len(query) > 200 or selected_filter not in ('all','needs','signed'):
            return auth_error('Use a supported agreement filter and a search of 200 characters or fewer.')
        recipients = shared_recipients(scope) if scope else SigningRecipient.objects.filter(pk=self.recipient.pk)
        latest = ClientInvitation.objects.filter(signing_access__recipient_id=OuterRef('pk')).order_by('-created_at','-pk').values('pk')[:1]
        recipients = recipients.select_related('signing_request__client_file__job', 'signing_request__template_version',
            'signing_request__completion', 'submission').annotate(portal_invitation=Subquery(latest))
        expired = Q(signing_request__status__in=('awaiting_client','awaiting_company'), signing_request__expires_at__lte=timezone.now())
        current = Q(signing_request__status=F('signing_request__client_file__status'),
                    signing_request__template_version_id=F('signing_request__client_file__template_version_id'))
        needs = current & ~expired & Q(signing_request__status='awaiting_client', submission__isnull=True)
        signed = current & Q(signing_request__status='completed', submission__isnull=False,
                             signing_request__completion__completed_at__isnull=False)
        counts = recipients.aggregate(all=Count('pk'), needs=Count('pk',filter=needs), signed=Count('pk',filter=signed))
        matching = recipients.filter({'all':Q(), 'needs':needs, 'signed':signed}[selected_filter])
        if query:
            matching = matching.filter(Q(signing_request__template_version__name__icontains=query)
                | Q(signing_request__client_file__reference__icontains=query) | Q(signing_request__client_file__title__icontains=query)
                | Q(signing_request__client_file__job__reference__icontains=query) | Q(signing_request__client_file__job__title__icontains=query))
        page = Paginator(matching.order_by('-signing_request__created_at','-pk'),25).get_page(request.GET.get('page',1))
        rows = [self.row(recipient) for recipient in page]
        pending = recipients.filter(needs).order_by('signing_request__created_at').first()
        ready = recipients.filter(signed).order_by('-signing_request__created_at').first()
        attention = self.row(pending or ready) if pending or ready else None
        portal_path = reverse('client_portal',args=[self.verification.pk])
        def url(filter_name, page_number=1):
            return portal_path + '?' + urlencode({'filter':filter_name, 'q':query, 'page':page_number})
        customer = CompanyClient.objects.get(pk=scope['client_id'],company_id=scope['company_id']) if scope else None
        first = attention or (rows[0] if rows else None)
        return render(request, 'client_portal.html', {
            'static_prefix':settings.STATIC_URL, 'client_name':customer.name if customer else self.recipient.signing_request.client_file.client_name,
            'company_name':self.verification.company.company_name or 'Your service provider',
            'agreements':rows, 'counts':counts, 'attention':attention, 'query':query, 'selected_filter':selected_filter, 'portal_url':portal_path,
            'filter_links':{key:url(key) for key in ('all','needs','signed')}, 'page':page,
            'previous_url':url(selected_filter,page.previous_page_number()) if page.has_previous() else None,
            'next_url':url(selected_filter,page.next_page_number()) if page.has_next() else None,
            'logout_url':reverse('client_logout'),
            'login_url':reverse('client_invitation',args=[first['invitation_id'] if first and first['invitation_id'] else invitation_id]),
        })


class ClientCompletedArtifact(ClientAgreement):
    # Native PDF links return an expired session to invitation verification.
    http_method_names = ["get", "head", "options"]

    def get(self, request, invitation_id):
        try:
            response = FileResponse(self.open_completed(), content_type="application/pdf",
                                    filename="completed-agreement.pdf", as_attachment=request.GET.get("download") == "1")
            response["Content-Security-Policy"] = "sandbox"
            return response
        except SigningError as error:
            return auth_error(str(error), status=error.status)
        except ValidationError:
            return auth_error("The completed agreement could not be validated. Contact the company.", status=409)
        except (OSError, SuspiciousFileOperation):
            return auth_error("The completed PDF is temporarily unavailable. Retry or contact the company.", status=503)


class ClientAgreementSigning(ClientAgreement):
    json_response = True

    def get(self, request, invitation_id):
        return JsonResponse({"signing": self.signing_snapshot(invitation_id), "csrfToken": get_token(request)})

    def post(self, request, invitation_id):
        try:
            if (self.recipient.role == "client"
                    and self.recipient.signing_request.submissions.filter(recipient=self.recipient).exists()):
                raise SigningError("Your signature is already recorded. This agreement is read-only. Check submission status.",
                                   status=409, code="already_submitted")
            payload = read_signing_json(request)
            result = self.signing_service.act(request, self.recipient, payload)
            if result["progress"] == "finalizing":
                from .finalization import AgreementFinalizer
                try:
                    AgreementFinalizer(self.recipient.signing_request.client_file.company_id).run(
                        self.recipient.signing_request.client_file_id,
                    )
                except (SigningError, ValidationError, DatabaseError):
                    # An accepted signature stays accepted when finalization needs retry.
                    pass
            self.recipient.signing_request.refresh_from_db()
            return JsonResponse({"signing": self.signing_snapshot(invitation_id), "csrfToken": get_token(request)})
        except (ValueError, RequestDataTooBig):
            return auth_error("Send valid field values within the existing request size limit. Your entries are still here.")
        except SigningError as error:
            return JsonResponse({"error": str(error), "fieldErrors": error.fields, "code": error.code}, status=error.status)
        except ValidationError:
            return auth_error("The issued agreement configuration is invalid. Contact the company; your entries are still here.", status=422)
