import uuid
from urllib.parse import unquote

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.urls import reverse
from django.views import View

from login.common import auth_error, company_authenticated

from .models import Agreement
from .processing import DocumentError, PdfProcessor
from .storage import PrivateDocumentStorage
from .uploads import AgreementUpload


def agreement_data(agreement):
    return {
        "id": str(agreement.pk),
        "filename": agreement.original_filename,
        "preparationState": agreement.preparation_state,
        "processingState": agreement.processing_state,
        "processingLabel": agreement.get_processing_state_display(),
        "processingError": agreement.processing_error,
        "pageCount": agreement.page_count,
        "createdAt": agreement.created_at.isoformat(),
        "detailUrl": reverse("company_agreement_detail", args=[agreement.pk]),
        "pageUrl": reverse("company_agreement_page", args=[agreement.pk, 1]).removesuffix("1/"),
    }


class CompanyAgreementAccessMixin:
    def dispatch(self, request, *args, **kwargs):
        if not company_authenticated(request):
            response = auth_error("Company sign-in is required.", status=401)
        elif (
            request.headers.get("X-Workspace-ID") if request.method == "POST"
            else request.GET.get("workspace_id")
        ) != str(request.user.pk):
            response = JsonResponse({
                "error": "The signed-in workspace has changed. Reload this page to continue.",
                "code": "workspace_changed",
            }, status=409)
        else:
            try:
                response = super().dispatch(request, *args, **kwargs)
            except DatabaseError:
                response = auth_error("We couldn’t load or save this agreement. Please try again.", status=503)
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "same-origin"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class CompanyAgreementList(CompanyAgreementAccessMixin, View):
    def get(self, request):
        agreements = Agreement.objects.filter(company=request.user)
        page = Paginator(agreements, 20).get_page(request.GET.get("page", 1))
        return JsonResponse({
            "companyId": str(request.user.pk),
            "agreements": [agreement_data(agreement) for agreement in page],
            "pagination": {
                "number": page.number, "pages": page.paginator.num_pages,
                "total": page.paginator.count,
                "hasPrevious": page.has_previous(), "hasNext": page.has_next(),
            },
            "limits": {"maxBytes": settings.AGREEMENT_MAX_BYTES, "maxPages": settings.AGREEMENT_MAX_PAGES},
        })


class CompanyAgreementUpload(CompanyAgreementAccessMixin, View):
    def post(self, request):
        # A raw file body lets us enforce the byte cap before multipart buffering.
        if request.content_type not in ("application/pdf", "application/octet-stream"):
            return auth_error("Upload a PDF file. DOCX conversion is not supported yet.", status=415)
        try:
            creation_key = uuid.UUID(request.headers.get("X-Upload-Key", ""))
            filename = unquote(request.headers.get("X-Upload-Filename", ""), errors="strict").strip()
        except (ValueError, UnicodeError):
            return auth_error("Choose the PDF again before uploading.")
        if (
            not filename or len(filename) > 255
            or any(character in "/\\" or ord(character) < 32 or ord(character) == 127 for character in filename)
        ):
            return auth_error("Rename the file using a short filename without directories or control characters.")
        content_length = request.META.get("CONTENT_LENGTH", "")
        if content_length.isdecimal() and int(content_length) > settings.AGREEMENT_MAX_BYTES:
            limit = settings.AGREEMENT_MAX_BYTES / (1024 * 1024)
            return JsonResponse({"error": f"This file is too large. Choose a PDF up to {limit:g} MiB.", "code": "oversized"}, status=413)
        try:
            agreement, created = AgreementUpload(request.user).save(
                iter(lambda: request.read(64 * 1024), b""),
                filename=filename, creation_key=creation_key,
            )
        except DocumentError as error:
            return JsonResponse({"error": str(error), "code": error.code}, status=413 if error.code == "oversized" else 422)
        except (UnreadablePostError, OSError):
            return auth_error("The upload could not be saved. Check your connection and try again.", status=503)
        except ValidationError:
            return auth_error("The agreement could not be saved. Please try again.", status=503)
        return JsonResponse({
            "companyId": str(request.user.pk), "agreement": agreement_data(agreement),
        }, status=201 if created else 200)


class CompanyAgreementDetail(CompanyAgreementAccessMixin, View):
    def get(self, request, agreement_id):
        agreement = Agreement.objects.filter(company=request.user, pk=agreement_id).first()
        if agreement is None:
            return auth_error("Agreement not found in this workspace.", status=404)
        return JsonResponse({"companyId": str(request.user.pk), "agreement": agreement_data(agreement)})


class CompanyAgreementPage(CompanyAgreementAccessMixin, View):
    def get(self, request, agreement_id, page_number):
        agreement = Agreement.objects.filter(company=request.user, pk=agreement_id).first()
        if agreement is None:
            return auth_error("Agreement not found in this workspace.", status=404)
        if agreement.processing_state != Agreement.ProcessingState.READY:
            return auth_error("This agreement has not passed document validation.", status=409)
        if not 1 <= page_number <= agreement.page_count:
            return auth_error("This page does not exist in the agreement.", status=404)
        try:
            storage = PrivateDocumentStorage(request.user.pk, agreement.pk)
            with storage.open(agreement.pdf_key) as source:
                png = PdfProcessor().render(source.name, page_number)
        except DocumentError as error:
            return auth_error(str(error), status=503)
        except (OSError, SuspiciousFileOperation):
            return auth_error("The saved PDF is temporarily unavailable. Try loading the preview again.", status=503)
        return HttpResponse(png, content_type="image/png")
