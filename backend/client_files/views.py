import uuid

from django.core.exceptions import RequestDataTooBig, SuspiciousFileOperation, ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from login.common import auth_error, company_authenticated, company_context, read_json
from login.client_throttle import ClientThrottleExceeded

from .forms import ClientFileDraftForm
from .models import ClientFile, SigningRecipient, SigningRequest
from .signing import DemoRequestIssuer
from .workspace import CompanyFileWorkspace


class CompanyFileAccessMixin:
    json_response = False

    def dispatch(self, request, *args, **kwargs):
        if not company_authenticated(request):
            response = (
                auth_error("Company sign-in is required.", status=401)
                if self.json_response or request.method != "GET"
                else redirect("company_login")
            )
        else:
            response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "same-origin"
        return response


class CompanyFileList(CompanyFileAccessMixin, View):
    json_response = True

    def get(self, request):
        if request.GET.get("workspace_id", str(request.user.pk)) != str(request.user.pk):
            return JsonResponse({
                "error": "The signed-in workspace has changed. Reload this page to continue.",
                "code": "workspace_changed",
            }, status=409)
        try:
            return JsonResponse(CompanyFileWorkspace(request.user).snapshot(request.GET))
        except ValueError as error:
            return auth_error(str(error))
        except DatabaseError:
            return auth_error("We couldn’t load your files. Please try again.", status=503)


class CompanyFileEditor(CompanyFileAccessMixin, View):
    is_new = False

    def get(self, request, file_id=None):
        file = (
            ClientFile(company=request.user)
            if self.is_new
            else ClientFile.objects.select_related("template_version__agreement").filter(company=request.user, pk=file_id).first()
        )
        if file is None:
            return HttpResponseNotFound("Client file not found.")
        return render(request, "client_file.html", {
            **company_context(request),
            "draft_data": CompanyFileWorkspace.draft(file),
            "save_url": reverse("company_file_create") if self.is_new else reverse(
                "company_file_detail", args=[file.pk]
            ),
            "is_new": self.is_new,
            "templates_url": reverse("company_template_list"),
            "request_url": reverse("company_file_request", args=[file.pk]) if not self.is_new else "",
        })

    def post(self, request, file_id=None):
        try:
            data = read_json(request)
        except ValueError as error:
            return auth_error(str(error))
        if data.pop("workspace_id", None) != str(request.user.pk):
            return JsonResponse({
                "error": "The signed-in workspace has changed. Reload this page before saving.",
                "code": "workspace_changed",
            }, status=409)
        allowed = set(ClientFileDraftForm.Meta.fields)
        if self.is_new:
            allowed.add("creation_key")
        if set(data) - allowed or any(not isinstance(value, str) for value in data.values()):
            return auth_error("Send only the supported draft fields as text.")
        creation_key = None
        if self.is_new:
            try:
                creation_key = uuid.UUID(data.pop("creation_key", ""))
            except ValueError:
                return auth_error("Reload the new-file page before saving this draft.")

        files = ClientFile.objects.filter(company=request.user)
        form = None
        try:
            with transaction.atomic():
                file = (
                    files.filter(creation_key=creation_key).first()
                    if self.is_new
                    else files.select_for_update().filter(pk=file_id).first()
                )
                if self.is_new and file is not None:
                    return self.saved(file, created=False)
                if not self.is_new and file is None:
                    return auth_error("Client file not found.", status=404)
                if self.is_new:
                    file = ClientFile(company=request.user, creation_key=creation_key)
                if file.status != ClientFile.Status.DRAFT:
                    return auth_error("Only draft files can be edited.", status=409)
                form = ClientFileDraftForm(data, instance=file)
                if not form.is_valid():
                    return self.invalid(form)
                file = form.save()
        except (ValidationError, IntegrityError) as error:
            if self.is_new:
                existing = files.filter(creation_key=creation_key).first()
                if existing is not None:
                    return self.saved(existing, created=False)
            reference = data.get("reference", "").strip()
            if files.filter(reference__iexact=reference).exclude(pk=file_id).exists():
                return JsonResponse({
                    "error": "Check the highlighted fields.",
                    "fieldErrors": {"reference": ["This reference is already used in your workspace."]},
                }, status=409)
            if isinstance(error, ValidationError) and form is not None:
                for name, messages in getattr(error, "message_dict", {"__all__": error.messages}).items():
                    form.add_error(name if name in form.fields else None, messages)
                return self.invalid(form)
            return auth_error("We couldn’t save this draft. Please try again.", status=409)
        except DatabaseError:
            return auth_error("We couldn’t confirm the save. Try saving again to check its result.", status=503)
        return self.saved(file, created=self.is_new)

    @staticmethod
    def invalid(form):
        return JsonResponse({
            "error": "Check the highlighted fields.",
            "fieldErrors": {name: list(messages) for name, messages in form.errors.items()},
        }, status=400)

    @staticmethod
    def saved(file, created):
        return JsonResponse({"file": CompanyFileWorkspace.draft(file), "created": created}, status=201 if created else 200)


class CompanyFileCreate(CompanyFileEditor):
    is_new = True


class CompanyFileRequest(CompanyFileAccessMixin, View):
    json_response = True

    def get(self, request, file_id):
        try:
            if not ClientFile.objects.filter(pk=file_id, company=request.user).exists():
                return auth_error("Client file not found.", status=404)
            issued = SigningRequest.objects.filter(client_file_id=file_id, client_file__company=request.user).first()
            return JsonResponse({"invitation": DemoRequestIssuer(request.user).snapshot(issued) if issued else None})
        except DatabaseError:
            return auth_error("Couldn’t load the invitation. Try again.", status=503)

    def post(self, request, file_id):
        try:
            payload = read_json(request)
            if payload.get("workspace_id") != str(request.user.pk):
                return JsonResponse({"error": "Your workspace changed. Reload this file.", "code": "workspace_changed"}, status=409)
            if set(payload) - {"workspace_id", "replaceInvitationId"}:
                return auth_error("Send only the supported request options.")
            issuer = DemoRequestIssuer(request.user)
            if "replaceInvitationId" in payload:
                previous = str(uuid.UUID(payload["replaceInvitationId"]))
                issued = issuer.replace_code(file_id, previous)
                created = False
            else:
                issued, created = issuer.issue(file_id)
            return JsonResponse({"invitation": issuer.snapshot(issued), "created": created}, status=201 if created else 200)
        except (ClientFile.DoesNotExist, SigningRecipient.DoesNotExist):
            return auth_error("Client file or request not found.", status=404)
        except (ValueError, TypeError, AttributeError, RequestDataTooBig):
            return auth_error("Send a valid request option.")
        except ValidationError as error:
            return auth_error(" ".join(error.messages), status=422)
        except ClientThrottleExceeded:
            return auth_error("Code generation is temporarily locked. Wait and retry; the same request will be used.", status=429)
        except (DatabaseError, OSError, SuspiciousFileOperation):
            return auth_error("Couldn’t confirm the invitation. Retry to recover the same request.", status=503)
