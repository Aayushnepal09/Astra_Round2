"""Authenticated company review using the existing private agreement preview."""

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View

from login.common import auth_error, company_authenticated
from .client import ClientAgreement, ClientAgreementSigning
from .countersigning import CompanySigning
from .submissions import SigningError


class CompanyAgreement(ClientAgreement):
    signing_service = CompanySigning
    signing_route = "company_agreement_signing"
    page_route = "company_agreement_page"

    def dispatch(self, request, *args, **kwargs):
        try:
            if not company_authenticated(request) and not self.json_response and request.method == "GET" and "page_number" not in kwargs:
                response = redirect("company_login")
            else:
                self.recipient = CompanySigning.recipient(request, kwargs["file_id"])
                response = View.dispatch(self, request, *args, **kwargs)
        except SigningError as error:
            response = JsonResponse({"error": str(error), "fieldErrors": error.fields, "code": error.code}, status=error.status)
        except ValidationError:
            response = auth_error("The issued agreement configuration is invalid. Reopen the client file or contact the company.", status=422)
        except DatabaseError:
            response = auth_error("Couldn’t confirm the operation. Your entries remain here. Check submission status, then retry.", status=503)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def get(self, request, file_id, page_number=None):
        return super().get(request, file_id, page_number)

    def review_context(self, request, scope_id):
        return {
            "company_signing": True,
            "logout_url": reverse("company_logout"),
            "invitation_url": reverse("company_login"),
            "file_url": reverse("company_file_detail", args=[scope_id]),
            "client_values": CompanySigning.client_values(self.recipient),
        }


class CompanyAgreementSigning(CompanyAgreement):
    json_response = True

    def get(self, request, file_id):
        return JsonResponse({"signing": CompanySigning.snapshot(self.recipient), "csrfToken": get_token(request)})

    def post(self, request, file_id):
        return ClientAgreementSigning.post(self, request, file_id)
