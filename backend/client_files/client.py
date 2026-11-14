from django.conf import settings
from django.core.exceptions import RequestDataTooBig, SuspiciousFileOperation, ValidationError
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from agreements.processing import DocumentError, PdfProcessor
from agreements.storage import PrivateDocumentStorage
from login.common import auth_error, client_identity, read_json
from .signing import signing_recipient
from .submissions import ClientSigning, SigningError
from .models import SigningAccess


class ClientAgreement(View):
    json_response = False
    def dispatch(self, request, *args, **kwargs):
        try:
            invitation = client_identity(request)
            recipient = signing_recipient(invitation) if invitation else None
            if not recipient or not SigningAccess.objects.filter(invitation_id=kwargs["invitation_id"], recipient=recipient).exists():
                response = (
                    auth_error("Your access session has expired. Open your invitation and verify a new code.", status=401)
                    if kwargs.get("page_number") or self.json_response or request.method != "GET"
                    else redirect("client_invitation", invitation_id=kwargs["invitation_id"])
                )
            else:
                self.recipient = recipient
                response = super().dispatch(request, *args, **kwargs)
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
                storage = PrivateDocumentStorage(agreement.company_id, agreement.pk)
                with storage.open(agreement.pdf_key) as source:
                    png = PdfProcessor().render(source.name, page_number)
            except (OSError, SuspiciousFileOperation, DocumentError):
                return auth_error("Couldn’t load this agreement page. Try again.", status=503)
            return HttpResponse(png, content_type="image/png")
        return render(request, "client_agreement.html", {
            "static_prefix": settings.STATIC_URL,
            "agreement_name": version.name, "version_number": version.number,
            "signer_name": self.recipient.name,
            "logout_url": reverse("client_logout"),
            "invitation_url": reverse("client_invitation", args=[invitation_id]),
            "signing_url": reverse("client_agreement_signing", args=[invitation_id]),
            "review_data": {
                "pageCount": agreement.page_count,
                "pages": [reverse("client_agreement_page", args=[invitation_id, number]) for number in range(1, agreement.page_count + 1)],
                "signing": ClientSigning.snapshot(self.recipient),
            },
        })


class ClientAgreementSigning(ClientAgreement):
    json_response = True

    def get(self, request, invitation_id):
        return JsonResponse({"signing": ClientSigning.snapshot(self.recipient), "csrfToken": get_token(request)})

    def post(self, request, invitation_id):
        try:
            payload = read_json(request)
            result = ClientSigning.act(request, self.recipient, payload)
            return JsonResponse({"signing": result, "csrfToken": get_token(request)})
        except (ValueError, RequestDataTooBig):
            return auth_error("Send valid field values within the existing request size limit. Your entries are still here.")
        except SigningError as error:
            return JsonResponse({"error": str(error), "fieldErrors": error.fields, "code": error.code}, status=error.status)
        except ValidationError:
            return auth_error("The issued agreement configuration is invalid. Contact the company; your entries are still here.", status=422)
