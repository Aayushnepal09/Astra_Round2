import uuid

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.urls import reverse
from django.views import View

from login.common import auth_error, read_json
from login.models import ClientInvitation
from .models import ClientFile, ClientJob, CompanyClient
from .organization import ClientOrganization
from .views import CompanyFileAccessMixin
from .workspace import CompanyFileWorkspace
from .lifecycle import refresh_file_expiry


def client_data(client):
    return {"id": str(client.pk), "name": client.name, "email": client.email,
            "signerName": client.signer_name, "signerEmail": client.signer_email,
            "detailUrl": reverse("company_client_detail", args=[client.pk]),
            "jobsUrl": reverse("company_client_jobs", args=[client.pk]),
            "jobCount": getattr(client, "job_count", 0), "agreementCount": getattr(client, "agreement_count", 0),
            "completedCount": getattr(client, "completed_count", 0), "waitingCount": getattr(client, "waiting_count", 0),
            "companySignatureCount": getattr(client, "company_signature_count", 0)}


def client_totals(company):
    return CompanyClient.objects.filter(company=company).annotate(
        job_count=Count("jobs", distinct=True), agreement_count=Count("jobs__agreements", distinct=True),
        completed_count=Count("jobs__agreements", filter=Q(jobs__agreements__status="completed"), distinct=True),
        waiting_count=Count("jobs__agreements", filter=Q(jobs__agreements__status="awaiting_client"), distinct=True),
        company_signature_count=Count("jobs__agreements", filter=Q(jobs__agreements__status="awaiting_company"), distinct=True),
    ).order_by("name", "id")


def agreement_row(file):
    current = file.current_request
    completion = getattr(current, 'completion', None) if current else None
    record = {key: getattr(file, key) for key in ('id', 'client_name', 'title', 'reference', 'status', 'updated_at')}
    record['current_request__completion__last_error'] = completion.last_error if completion else ''
    version = file.template_version
    issues = file.setup_issues() if file.status == 'draft' else []
    return {**CompanyFileWorkspace.summary(record), 'signerName': file.signer_name, 'signerEmail': file.signer_email,
            'templateName': version.name if version else '', 'templateVersionId': str(version.pk) if version else None,
            'versionNumber': version.number if version else None, 'prepared': bool(version),
            'readyToSend': file.status == 'draft' and not issues, 'setupIssues': issues,
            'requestId': str(current.pk) if current else None,
            'replacesRequestId': str(current.pk) if current and file.status == 'draft' else None,
            'requestUrl': reverse('company_file_request', args=[file.pk]),
            'cancelUrl': reverse('company_file_cancel', args=[file.pk]),
            'canCancel': bool(current and file.status in ('awaiting_client', 'awaiting_company') and current.status in ('awaiting_client', 'awaiting_company')),
            'clientLink': reverse('client_invitation', args=[file.latest_invitation]) if file.latest_invitation and file.status not in ('draft', 'cancelled', 'expired') else None,
            'countersignUrl': reverse('company_agreement', args=[file.pk]) if file.status == 'awaiting_company' else None,
            'pdfUrl': reverse('company_completed_pdf', args=[file.pk]) if file.status == 'completed' else None,
            'historyUrl': reverse('company_request_history', args=[file.pk])}


def job_data(job, page=1):
    latest = ClientInvitation.objects.filter(signing_access__recipient__signing_request_id=OuterRef('current_request_id'),
                                             signing_access__recipient__role='client').order_by('-created_at').values('pk')[:1]
    files = job.agreements.select_related("template_version__agreement", "current_request__completion").annotate(latest_invitation=Subquery(latest))
    for file in files.filter(status__in=("awaiting_client", "awaiting_company")):
        refresh_file_expiry(file)
    rows = Paginator(files, 20).get_page(page)
    return {"id": str(job.pk), "reference": job.reference, "title": job.title,
            "agreements": [agreement_row(row) for row in rows],
            "agreementCount": rows.paginator.count, "readyToStart": bool(rows.paginator.count) and not files.exclude(status="completed").exists(),
            "page": rows.number, "hasNext": rows.has_next(),
            "agreementsUrl": reverse("company_job_agreements", args=[job.pk]),
            "uploadUrl": f'{reverse("company_dashboard")}?job={job.pk}#templates',
            "templatesUrl": f'{reverse("company_template_list")}?job_id={job.pk}'}


class ClientWorkspaceAccess(CompanyFileAccessMixin):
    json_response = True

    def dispatch(self, request, *args, **kwargs):
        # CSRF remains enforced by Django's middleware; bind reads and writes to the displayed workspace.
        if request.user.is_authenticated:
            identity = request.GET.get("workspace_id") if request.method == "GET" else None
            if identity is not None and identity != str(request.user.pk):
                return auth_error("Your workspace changed. Reload before continuing.", status=409)
        try:
            return super().dispatch(request, *args, **kwargs)
        except ValidationError as error:
            return auth_error(" ".join(error.messages))
        except (ValueError, TypeError):
            return auth_error("Send valid client details and published agreement versions.")
        except IntegrityError:
            return auth_error("This job reference is already in use. Choose another reference or retry the original action.", status=409)
        except DatabaseError:
            return auth_error("The change could not be confirmed. Retry with your current entries.", status=503)

    def payload(self, request):
        data = read_json(request)
        if data.pop("workspace_id", None) != str(request.user.pk):
            raise ValidationError("Your workspace changed. Reload before continuing.")
        return data

    @staticmethod
    def key(data):
        value = data.pop("creation_key", None)
        if not isinstance(value, str):
            raise ValidationError("Supply a valid creation key. Reload the form and try again.")
        try:
            return uuid.UUID(value)
        except ValueError as error:
            raise ValidationError("Supply a valid creation key. Reload the form and try again.") from error


class CompanyClients(ClientWorkspaceAccess, View):
    def get(self, request):
        query = request.GET.get("q", "").strip()
        if len(query) > 200:
            raise ValidationError("Search must be 200 characters or fewer.")
        clients = client_totals(request.user)
        if query:
            clients = clients.filter(Q(name__icontains=query) | Q(email__icontains=query)
                                     | Q(jobs__reference__icontains=query) | Q(jobs__title__icontains=query)).distinct()
        page = Paginator(clients, 25).get_page(request.GET.get("page", 1))
        return JsonResponse({"companyId": str(request.user.pk),
            "clients": [client_data(c) for c in page],
            "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
            "hasNext": page.has_next(), "hasPrevious": page.has_previous()})

    def post(self, request):
        data = self.payload(request)
        key = self.key(data)
        if set(data) - {"name", "email", "signer_name", "signer_email"} or any(not isinstance(v, str) for v in data.values()):
            raise ValidationError("Send only client name, email and default signer details.")
        with transaction.atomic():
            client = CompanyClient.objects.filter(company=request.user, creation_key=key).first()
            if client is None:
                client = CompanyClient.objects.create(company=request.user, creation_key=key, **data)
        return JsonResponse({"companyId": str(request.user.pk), "client": client_data(client),
                             "clientCount": CompanyClient.objects.filter(company=request.user).count()}, status=201)


class CompanyClientDetail(ClientWorkspaceAccess, View):
    def get(self, request, client_id):
        client = CompanyClient.objects.filter(company=request.user, pk=client_id).first()
        if not client:
            return auth_error("Client not found.", status=404)
        jobs = Paginator(client.jobs.all(), 10).get_page(request.GET.get("page", 1))
        data = [job_data(job) for job in jobs]
        if request.GET.get("open_job") and jobs.number == 1:
            selected = client.jobs.filter(pk=uuid.UUID(request.GET["open_job"])).first()
            if selected and all(row["id"] != str(selected.pk) for row in data):
                data.insert(0, job_data(selected))
        return JsonResponse({"companyId": str(request.user.pk), "client": client_data(client_totals(request.user).get(pk=client.pk)),
                             "jobs": data, "page": jobs.number,
                             "hasNext": jobs.has_next(), "total": jobs.paginator.count})


class CompanyClientJobs(ClientWorkspaceAccess, View):
    def post(self, request, client_id):
        client = CompanyClient.objects.filter(company=request.user, pk=client_id).first()
        if not client:
            return auth_error("Client not found.", status=404)
        data = self.payload(request)
        key = self.key(data)
        if set(data) != {"reference", "title", "versions"} or not all(isinstance(data[n], str) for n in ("reference", "title")):
            raise ValidationError("Supply the job reference, title and selected versions.")
        job = ClientOrganization(request.user).create_job(client, reference=data["reference"], title=data["title"],
                                                         version_ids=data["versions"], key=key)
        return JsonResponse({"companyId": str(request.user.pk), "job": job_data(job),
                             "client": client_data(client_totals(request.user).get(pk=client.pk))}, status=201)


class CompanyJobAgreements(ClientWorkspaceAccess, View):
    def get(self, request, job_id):
        job = ClientJob.objects.filter(company=request.user, pk=job_id).first()
        if not job:
            return auth_error("Job not found.", status=404)
        return JsonResponse({"companyId": str(request.user.pk), "job": job_data(job, request.GET.get("page", 1)),
                             'client': client_data(client_totals(request.user).get(pk=job.client_id))})

    def post(self, request, job_id):
        job = ClientJob.objects.select_related("client").filter(company=request.user, pk=job_id).first()
        if not job:
            return auth_error("Job not found.", status=404)
        data = self.payload(request)
        key = self.key(data)
        if set(data) != {"versions"} or not data["versions"]:
            raise ValidationError("Choose at least one prepared agreement version.")
        files = ClientOrganization(request.user).add_agreements(job, data["versions"], key)
        return JsonResponse({"companyId": str(request.user.pk), "job": job_data(job),
                             "client": client_data(client_totals(request.user).get(pk=job.client_id)),
                             "createdIds": [str(file.pk) for file in files],
                             "createdFiles": [{"id": str(file.pk), "detailUrl": reverse("company_file_detail", args=[file.pk])} for file in files]}, status=201)
