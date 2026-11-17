from django.db import migrations


def group_existing(apps, schema_editor):
    File = apps.get_model("client_files", "ClientFile")
    Client = apps.get_model("client_files", "CompanyClient")
    Job = apps.get_model("client_files", "ClientJob")
    grouped = {}
    for file in File.objects.using(schema_editor.connection.alias).filter(job__isnull=True).order_by("created_at", "id"):
        # A name alone is insufficient evidence that two old records are the same client.
        identity = (file.company_id, file.client_name.strip().casefold(), file.client_email.strip().casefold())
        client = grouped.get(identity) if file.client_email.strip() else None
        if client is None:
            client = Client.objects.using(schema_editor.connection.alias).create(
                company_id=file.company_id, name=file.client_name, email=file.client_email,
                signer_name=file.signer_name, signer_email=file.signer_email,
            )
            if file.client_email.strip():
                grouped[identity] = client
        job = Job.objects.using(schema_editor.connection.alias).create(
            company_id=file.company_id, client_id=client.pk, reference=file.reference, title=file.title,
        )
        File.objects.using(schema_editor.connection.alias).filter(pk=file.pk).update(job_id=job.pk)


class Migration(migrations.Migration):
    dependencies = [("client_files", "0012_clientjob_clientfile_job_companyclient_and_more"),
                    ("agreements", "0006_agreement_job")]
    operations = [migrations.RunPython(group_existing, migrations.RunPython.noop)]
