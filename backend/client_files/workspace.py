from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.urls import reverse

from .models import ClientFile


class CompanyFileWorkspace:
    PAGE_SIZE = 25
    SUMMARY_FIELDS = ("id", "client_name", "title", "reference", "status", "updated_at")
    FILTERS = {
        "all": Q(),
        "waiting": Q(status__in=("awaiting_client", "awaiting_company")),
        "ready": Q(status="completed"),
        "attention": Q(status__in=("draft", "expired")),
    }
    ORDERINGS = {
        "newest": ("-updated_at", "-id"),
        "oldest": ("updated_at", "id"),
        "name": (Lower("client_name"), "id"),
    }

    def __init__(self, company):
        self.company_id = str(company.pk)
        self.files = ClientFile.objects.filter(company=company)

    @staticmethod
    def summary(record):
        return {
            "id": str(record["id"]),
            "clientName": record["client_name"],
            "title": record["title"],
            "reference": record["reference"],
            "status": record["status"],
            "updatedAt": record["updated_at"].isoformat(),
            "detailUrl": reverse("company_file_detail", args=[record["id"]]),
            "needsAttention": False,
        }

    @staticmethod
    def draft(file):
        saved = not file._state.adding
        version = file.template_version if file.template_version_id else None
        return {
            "id": str(file.pk) if saved else None,
            "status": file.status,
            "statusLabel": file.get_status_display(),
            "creationKey": str(file.creation_key),
            "reference": file.reference,
            "title": file.title,
            "clientName": file.client_name,
            "clientEmail": file.client_email,
            "signerName": file.signer_name,
            "signerEmail": file.signer_email,
            "templateVersionId": str(version.pk) if version else "",
            "selectedTemplate": {
                "id": str(version.pk), "name": version.name, "number": version.number,
                "roles": version.roles,
                "previewUrl": f'{reverse("company_dashboard")}#templates/published/{version.pk}',
            } if version else None,
            "companySignerName": file.company_signer_name,
            "companySignerEmail": file.company_signer_email,
            "setupIssues": file.setup_issues(),
            "updatedAt": file.updated_at.isoformat() if saved else None,
            "detailUrl": reverse("company_file_detail", args=[file.pk]) if saved else None,
            "requestUrl": reverse("company_file_request", args=[file.pk]) if saved else None,
        }

    def snapshot(self, params=None):
        params = params or {}
        query = params.get("q", "").strip()
        filter_name = params.get("filter", "all")
        sort_name = params.get("sort", "newest")
        if len(query) > 200:
            raise ValueError("Search must be 200 characters or fewer.")
        if filter_name not in self.FILTERS or sort_name not in self.ORDERINGS:
            raise ValueError("Choose a supported file filter and sort order.")

        counts = self.files.aggregate(
            all=Count("pk"),
            **{
                name: Count("pk", filter=self.FILTERS[name])
                for name in ("waiting", "ready", "attention")
            },
        )
        matching = self.files.filter(self.FILTERS[filter_name])
        if query:
            matching = matching.filter(
                Q(client_name__icontains=query)
                | Q(title__icontains=query)
                | Q(reference__icontains=query)
            )
        # Pagination and projections keep each response independent of workspace size.
        page = Paginator(
            matching.order_by(*self.ORDERINGS[sort_name]).values(*self.SUMMARY_FIELDS),
            self.PAGE_SIZE,
        ).get_page(params.get("page", 1))
        recent = self.files.order_by("-updated_at", "-id").values(*self.SUMMARY_FIELDS)[:4]
        return {
            "companyId": self.company_id,
            "files": [self.summary(record) for record in page.object_list],
            "recentFiles": [self.summary(record) for record in recent],
            "counts": counts,
            "pagination": {
                "number": page.number,
                "pages": page.paginator.num_pages,
                "total": page.paginator.count,
                "pageSize": self.PAGE_SIZE,
                "hasPrevious": page.has_previous(),
                "hasNext": page.has_next(),
            },
        }
