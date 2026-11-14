import json
import math
import uuid
from urllib.parse import unquote

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.urls import reverse
from django.utils import timezone
from django.views import View

from login.common import auth_error, company_authenticated

from .models import Agreement, AgreementField
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
        "format": agreement.detected_format,
        "conversionEngine": agreement.conversion_engine,
        "conversionVersion": agreement.conversion_version,
        "pdfSha256": agreement.pdf_sha256,
        "fieldsRevision": agreement.fields_revision,
        "fields": [
            {
                "id": str(field.pk),
                "type": field.field_type,
                "label": field.label,
                "required": field.required,
                "role": field.role,
                "page": field.page_number,
                "x": field.x,
                "y": field.y,
                "width": field.width,
                "height": field.height,
            }
            for field in agreement.fields.all()
        ],
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
        if request.content_type not in (
            "application/pdf", "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            return auth_error("Upload a PDF or DOCX file.", status=415)
        try:
            creation_key = uuid.UUID(request.headers.get("X-Upload-Key", ""))
            filename = unquote(request.headers.get("X-Upload-Filename", ""), errors="strict").strip()
        except (ValueError, UnicodeError):
            return auth_error("Choose the document again before uploading.")
        if (
            not filename or len(filename) > 255
            or any(character in "/\\" or ord(character) < 32 or ord(character) == 127 for character in filename)
        ):
            return auth_error("Rename the file using a short filename without directories or control characters.")
        content_length = request.META.get("CONTENT_LENGTH", "")
        if content_length.isdecimal() and int(content_length) > settings.AGREEMENT_MAX_BYTES:
            limit = settings.AGREEMENT_MAX_BYTES / (1024 * 1024)
            return JsonResponse({"error": f"This file is too large. Choose a PDF or DOCX up to {limit:g} MiB.", "code": "oversized"}, status=413)
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


class CompanyAgreementFields(CompanyAgreementAccessMixin, View):
    def post(self, request, agreement_id):
        agreement = Agreement.objects.filter(company=request.user, pk=agreement_id).first()
        if agreement is None:
            return auth_error("Agreement not found in this workspace.", status=404)
        if agreement.processing_state != Agreement.ProcessingState.READY:
            return auth_error("Fields require a validated agreement.", status=409)
        try:
            raw = request.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                return auth_error("This field draft is too large. Use at most 200 fields with short labels.", status=413)
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError, UnreadablePostError):
            return auth_error("Send a valid field draft.")
        if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
            return auth_error("Send a valid field draft.")
        if payload.get("pdfSha256") != agreement.pdf_sha256:
            return auth_error("This draft belongs to a different PDF. Reopen the correct agreement before saving.", status=409)
        revision = payload.get("revision")
        if type(revision) is not int or not 0 <= revision <= 2147483647:
            return auth_error("Reopen the agreement to load its current field draft before saving.", status=409)
        submitted = payload["fields"]
        if len(submitted) > 200:
            return auth_error("This preparation draft has too many fields.")

        fields = []
        seen_ids = set()
        for item in submitted:
            if not isinstance(item, dict):
                return auth_error("Each field must be an object.")
            if set(item) != {
                "id", "type", "label", "required", "role",
                "page", "x", "y", "width", "height",
            }:
                return auth_error("A field is missing required properties.")
            try:
                field_id = uuid.UUID(str(item["id"]))
            except (ValueError, TypeError, AttributeError):
                return auth_error("Each field needs a valid identifier.")
            if field_id in seen_ids:
                return auth_error("Field identifiers must be unique.")
            seen_ids.add(field_id)
            if (
                type(item["required"]) is not bool
                or not isinstance(item["label"], str)
                or len(item["label"].strip()) > 120
                or item["type"] not in AgreementField.FieldType.values
                or item["role"] not in AgreementField.Role.values
                or type(item["page"]) is not int
                or not all(
                    isinstance(item[key], (int, float))
                    and not isinstance(item[key], bool)
                    and 0 <= item[key] <= 1
                    and math.isfinite(item[key])
                    for key in ("x", "y", "width", "height")
                )
            ):
                return auth_error("A field has an invalid type, label, role, or value.")
            fields.append(AgreementField(
                id=field_id,
                agreement=agreement,
                agreement_pdf_sha256=agreement.pdf_sha256,
                field_type=item["type"],
                label=item["label"].strip(),
                required=item["required"],
                role=item["role"],
                page_number=item["page"],
                x=item["x"],
                y=item["y"],
                width=item["width"],
                height=item["height"],
            ))
        try:
            if AgreementField.objects.filter(pk__in=seen_ids).exclude(agreement=agreement).exists():
                return auth_error("A field identifier is unavailable. Reopen the agreement and add the field again.", status=409)
            for field in fields:
                # UUIDs already saved in this draft are deliberately reused.
                field.full_clean(validate_unique=False)
        except ValidationError as error:
            message = next(iter(error.messages), "A field is outside the page boundaries.")
            return auth_error(message, status=422)
        with transaction.atomic():
            # Compare-and-swap also protects SQLite, where row locks are unavailable.
            changed = Agreement.objects.filter(
                pk=agreement.pk, company=request.user, fields_revision=revision,
                pdf_sha256=payload["pdfSha256"], processing_state=Agreement.ProcessingState.READY,
            ).update(fields_revision=F("fields_revision") + 1, updated_at=timezone.now())
            if not changed:
                agreement.refresh_from_db()
                current = agreement_data(agreement)
                proposed = [dict(item, label=item["label"].strip(), id=str(uuid.UUID(str(item["id"])))) for item in submitted]
                if sorted(current["fields"], key=lambda item: item["id"]) == sorted(proposed, key=lambda item: item["id"]):
                    # A previous save may have succeeded before its response was lost.
                    return JsonResponse({"companyId": str(request.user.pk), "agreement": current})
                return JsonResponse({
                    "error": "Another tab saved this draft. Your changes are still here. Reopen the agreement to load the saved version before editing again.",
                    "code": "draft_conflict",
                }, status=409)
            AgreementField.objects.filter(agreement=agreement).delete()
            AgreementField.objects.bulk_create(fields)
            agreement.fields_revision = revision + 1
            result = agreement_data(agreement)
        return JsonResponse({
            "companyId": str(request.user.pk),
            "agreement": result,
        })
