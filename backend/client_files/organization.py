"""Client/job grouping around the existing immutable signing workflow."""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q

from agreements.models import TemplateVersion
from .models import ClientFile, ClientJob, CompanyClient


class ClientOrganization:
    MAX_AGREEMENTS = 20

    def __init__(self, company):
        self.company = company

    def versions(self, job=None):
        scope = Q(agreement__job__isnull=True)
        if job:
            scope |= Q(agreement__job=job)
        return TemplateVersion.objects.filter(scope, agreement__company=self.company).select_related("agreement")

    def add_agreements(self, job, version_ids, key):
        if not isinstance(version_ids, list) or len(version_ids) > self.MAX_AGREEMENTS:
            raise ValidationError("Choose up to 20 agreement versions at a time.")
        try:
            ids = [uuid.UUID(value) for value in version_ids]
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("Choose valid published agreement versions.")
        if len(ids) != len(set(ids)):
            raise ValidationError("Choose each version only once.")
        with transaction.atomic():
            # Serialize additions on SQLite as well as databases with row locks.
            if not ClientJob.objects.filter(pk=job.pk, company=self.company).update(title=F("title")):
                raise ValidationError("Job not found in this workspace.")
            versions = {v.pk: v for v in self.versions(job).filter(pk__in=ids)}
            if set(versions) != set(ids):
                raise ValidationError("A selected version is unavailable for this job. Reload the available agreements.")
            result = []
            ordinal = job.agreements.count() + 1
            for version_id in ids:
                version = versions[version_id]
                creation_key = uuid.uuid5(key, str(version_id))
                existing = ClientFile.objects.filter(company=self.company, creation_key=creation_key).first()
                if existing:
                    if existing.job_id != job.pk or existing.template_version_id != version_id:
                        raise ValidationError("This addition key belongs to another job. Reload and try again.")
                    result.append(existing)
                    continue
                client = job.client
                reference = f"{job.reference[:110]}-A{ordinal}"
                while ClientFile.objects.filter(company=self.company, reference__iexact=reference).exists():
                    ordinal += 1
                    reference = f"{job.reference[:110]}-A{ordinal}"
                result.append(ClientFile.objects.create(
                    company=self.company, job=job, creation_key=creation_key,
                    reference=reference, title=version.name,
                    client_name=client.name, client_email=client.email,
                    signer_name=client.signer_name or client.name,
                    signer_email=client.signer_email or client.email, template_version=version,
                ))
                ordinal += 1
            return result

    def create_job(self, client, *, reference, title, version_ids, key):
        with transaction.atomic():
            job = ClientJob.objects.filter(company=self.company, creation_key=key).first()
            if job:
                if job.client_id != client.pk:
                    raise ValidationError("This creation key belongs to another client.")
                return job
            job = ClientJob.objects.create(company=self.company, client=client, creation_key=key,
                                          reference=reference, title=title)
            self.add_agreements(job, version_ids, key)
            return job

    def attach_legacy_draft(self, file):
        """Keep the original single-agreement entry point usable."""
        if file.job_id:
            return
        client = None
        if file.client_email:
            client = CompanyClient.objects.filter(company=self.company, name__iexact=file.client_name,
                                                   email__iexact=file.client_email).first()
        if client is None:
            client = CompanyClient.objects.create(company=self.company, name=file.client_name, email=file.client_email,
                                                  signer_name=file.signer_name, signer_email=file.signer_email)
        reference = file.reference
        if ClientJob.objects.filter(company=self.company, reference__iexact=reference).exists():
            reference = f"{reference[:90]}-{file.pk.hex[:20]}"
        file.job = ClientJob.objects.create(company=self.company, client=client, reference=reference, title=file.title)

