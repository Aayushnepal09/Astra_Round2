import json
import uuid
from datetime import timedelta

from django.core.exceptions import RequestDataTooBig, SuspiciousFileOperation, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.http import FileResponse, HttpResponseNotFound, JsonResponse
from django.http.request import UnreadablePostError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from login.common import auth_error, company_authenticated, company_context, read_json
from login.client_throttle import ClientThrottleExceeded

from .forms import ClientFileDraftForm
from .models import ClientFile, SigningRecipient, SigningRequest
from .signing import DemoRequestIssuer
from .workspace import CompanyFileWorkspace
from .finalization import AgreementFinalizer
from .submissions import SigningError
from .lifecycle import RequestLifecycle, RequestLifecycleError, refresh_file_expiry
from .preparation import PreparationValues


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
            if request.content_type != "application/json":
                raise ValueError("Send the draft as JSON.")
            raw = request.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                return auth_error("The draft exceeds 256 KiB. Shorten its values and retry.", status=413)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Send the draft as a JSON object.")
        except (ValueError, UnicodeDecodeError, RequestDataTooBig, UnreadablePostError):
            return auth_error("Send a valid JSON draft of at most 256 KiB.")
        if data.pop("workspace_id", None) != str(request.user.pk):
            return JsonResponse({
                "error": "The signed-in workspace has changed. Reload this page before saving.",
                "code": "workspace_changed",
            }, status=409)
        allowed = set(ClientFileDraftForm.Meta.fields)
        updated_at = data.pop("updatedAt", None) if not self.is_new else None
        if self.is_new:
            allowed.add("creation_key")
        if (set(data) - allowed
                or not isinstance(data.get("preparation_values", {}), dict)
                or any(not isinstance(value, str) for key, value in data.items() if key != "preparation_values")):
            return auth_error("Send only the supported draft fields as text.")
        data.setdefault("preparation_values", {})
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
                if not self.is_new:
                    files.filter(pk=file_id).update(updated_at=F("updated_at"))
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
                # The writer lock above keeps this check and save indivisible,
                # including on SQLite, where select_for_update alone is a no-op.
                previous_updated_at = file.updated_at
                if not self.is_new and updated_at != previous_updated_at.isoformat():
                    return JsonResponse({
                        "error": "This file was updated in another tab. Your changes have not been saved.",
                        "code": "draft_conflict",
                    }, status=409)
                form = ClientFileDraftForm(data, instance=file)
                if not form.is_valid():
                    return self.invalid(form)
                if self.is_new:
                    from .organization import ClientOrganization
                    ClientOrganization(request.user).attach_legacy_draft(form.instance)
                file = form.save()
                if not self.is_new and file.updated_at <= previous_updated_at:
                    # Keep versions distinct even if the clock repeats or moves back.
                    file.updated_at = previous_updated_at + timedelta(microseconds=1)
                    files.filter(pk=file.pk).update(updated_at=file.updated_at)
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
            file = ClientFile.objects.select_related("current_request", "template_version__agreement").filter(pk=file_id, company=request.user).first()
            if file is None:
                return auth_error("Client file not found.", status=404)
            return JsonResponse(self.state(request.user, file))
        except DatabaseError:
            return auth_error("Couldn’t load the invitation. Try again.", status=503)

    def post(self, request, file_id):
        try:
            payload = read_json(request)
            if payload.get("workspace_id") != str(request.user.pk):
                return JsonResponse({"error": "Your workspace changed. Reload this file.", "code": "workspace_changed"}, status=409)
            if set(payload) - {"workspace_id", "replaceInvitationId", "templateVersionId", "updatedAt", "replacesRequestId"}:
                return auth_error("Send only the supported request options.")
            issuer = DemoRequestIssuer(request.user)
            if "replaceInvitationId" in payload:
                previous = str(uuid.UUID(payload["replaceInvitationId"]))
                issued = issuer.replace_code(file_id, previous)
                created = False
            else:
                issued, created = issuer.issue(file_id, template_version_id=payload.get("templateVersionId"),
                                              updated_at=payload.get("updatedAt"), replaces_request_id=payload.get("replacesRequestId"))
            return JsonResponse({**self.state(request.user, issued.client_file), "created": created}, status=201 if created else 200)
        except (ClientFile.DoesNotExist, SigningRecipient.DoesNotExist):
            return auth_error("Client file or request not found.", status=404)
        except (ValueError, TypeError, AttributeError, RequestDataTooBig):
            return auth_error("Send a valid request option.")
        except ValidationError as error:
            return JsonResponse({"error": " ".join(error.messages),
                                 "fieldErrors": getattr(error, "message_dict", {})}, status=422)
        except RequestLifecycleError as error:
            return JsonResponse({"error": str(error), "code": error.code}, status=error.status)
        except ClientThrottleExceeded:
            return auth_error("Code generation is temporarily locked. Wait and retry; the same request will be used.", status=429)
        except (DatabaseError, OSError, SuspiciousFileOperation):
            return auth_error("Couldn’t confirm the invitation. Retry to recover the same request.", status=503)

    @staticmethod
    def state(company, file):
        file = refresh_file_expiry(file)
        issued = file.current_request
        return {
            'companyId': str(company.pk),
            "file": CompanyFileWorkspace.draft(file),
            "invitation": DemoRequestIssuer(company).snapshot(issued)
                if issued and file.status != ClientFile.Status.DRAFT else None,
        }


class CompanyFileLifecycle(CompanyFileAccessMixin, View):
    json_response = True
    action = "cancel"

    def post(self, request, file_id):
        try:
            payload = read_json(request)
            if payload.get("workspace_id") != str(request.user.pk):
                return JsonResponse({"error": "Your workspace changed. Reload this file.", "code": "workspace_changed"}, status=409)
            if set(payload) != {"workspace_id", "requestId"}:
                return auth_error("Send the request identity and workspace only.")
            request_id = uuid.UUID(payload["requestId"])
            lifecycle = RequestLifecycle(request.user)
            operation = lifecycle.cancel if self.action == "cancel" else lifecycle.prepare_replacement
            file, outcome = operation(file_id, request_id)
            return JsonResponse({**CompanyFileRequest.state(request.user, file), "outcome": outcome})
        except ClientFile.DoesNotExist:
            return auth_error("Client file not found.", status=404)
        except (RequestLifecycleError, SigningError) as error:
            return JsonResponse({"error": str(error), "code": error.code}, status=error.status)
        except (ValueError, TypeError, AttributeError, RequestDataTooBig):
            return auth_error("Send a valid request identity.")
        except ValidationError as error:
            return auth_error(" ".join(error.messages), status=422)
        except DatabaseError:
            return auth_error("Couldn’t confirm the request state. Refresh status, then retry the same action.", status=503)


class CompanyRequestHistory(CompanyFileAccessMixin, View):
    def get(self, request, file_id):
        file = ClientFile.objects.filter(pk=file_id, company=request.user).first()
        if file is None:
            return HttpResponseNotFound("Client file not found.")
        file = refresh_file_expiry(file)
        requests = file.signing_requests.select_related("template_version__agreement", "cancelled_by").prefetch_related(
            "recipients", "submissions__recipient",
        ).order_by("-created_at", "-id")
        page = Paginator(requests, 25).get_page(request.GET.get("page", 1))
        records = []
        for issued in page:
            submissions = {s.recipient_id: s for s in issued.submissions.all()}
            signers = []
            for recipient in sorted(issued.recipients.all(), key=lambda r: r.role != "client"):
                submission = submissions.get(recipient.pk)
                signers.append({"recipient": recipient, "submission": submission,
                                "values": [{"label": field["label"] or field["type"], "value": submission.values.get(field["id"], "")}
                                           for field in issued.template_version.fields if submission and field["role"] == recipient.role]})
            records.append({"request": issued, "current": issued.pk == file.current_request_id, "signers": signers,
                            "preparation": [{"label": field["label"] or field["type"], "value": issued.preparation_values.get(field["id"], "")}
                                            for field in PreparationValues.fields(issued.template_version)],
                            "finalization": AgreementFinalizer.snapshot(issued)})
        return render(request, "client_file_history.html", {**company_context(request), "file": file, "records": records, "page": page})


class CompanyFileFinalize(CompanyFileAccessMixin, View):
    json_response = True

    def post(self, request, file_id):
        try:
            payload = read_json(request)
            if payload != {"workspace_id": str(request.user.pk)}:
                return auth_error("Your workspace changed or the request options are invalid. Reopen this file.", status=409)
            result = AgreementFinalizer(request.user.pk).run(file_id)
            return JsonResponse({"finalization": result})
        except SigningError as error:
            return auth_error(str(error), status=error.status)
        except (ValueError, RequestDataTooBig):
            return auth_error("Send a valid finalization request.")
        except ValidationError:
            return auth_error("The issued agreement or accepted submissions could not be validated. The file remains pending.", status=422)
        except DatabaseError:
            return auth_error("Couldn’t confirm finalization. Refresh status, then retry; signatures are preserved.", status=503)


class CompanyCompletedArtifact(CompanyFileAccessMixin, View):
    json_response = True
    history = False

    def get(self, request, file_id, request_id=None):
        try:
            completion, handle = AgreementFinalizer(request.user.pk).open_completed(file_id, request_id=request_id)
            if self.history:
                handle.close()
                response = JsonResponse(completion.history, json_dumps_params={"indent": 2})
                if request.GET.get("download") == "1":
                    response["Content-Disposition"] = 'attachment; filename="signing-history.json"'
            else:
                response = FileResponse(handle, content_type="application/pdf", filename="completed-agreement.pdf",
                                        as_attachment=request.GET.get("download") == "1")
            response["X-Content-Type-Options"] = "nosniff"
            response["Content-Security-Policy"] = "sandbox"
            return response
        except SigningError as error:
            return auth_error(str(error), status=error.status)
        except (DatabaseError, OSError, SuspiciousFileOperation):
            return auth_error("The completed artifact is temporarily unavailable. Retry from the client file.", status=503)
        except ValidationError:
            return auth_error("The completion record does not match this client file. Contact the company.", status=409)
