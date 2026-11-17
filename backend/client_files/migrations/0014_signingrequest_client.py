from django.db import migrations, models
import django.db.models.deletion


def bind_existing(apps, schema_editor):
    Request = apps.get_model('client_files', 'SigningRequest')
    for request in Request.objects.using(schema_editor.connection.alias).select_related('client_file__job__client').filter(client_file__job__isnull=False).iterator():
        job = request.client_file.job
        if job.company_id == request.client_file.company_id and job.client.company_id == job.company_id:
            Request.objects.using(schema_editor.connection.alias).filter(pk=request.pk).update(client_id=job.client_id)


class Migration(migrations.Migration):
    dependencies = [('client_files', '0013_group_existing_agreements')]
    operations = [
        migrations.AddField(model_name='signingrequest', name='client', field=models.ForeignKey(blank=True, editable=False, null=True,
            on_delete=django.db.models.deletion.PROTECT, related_name='issued_agreements', to='client_files.companyclient')),
        migrations.RunPython(bind_existing, migrations.RunPython.noop),
    ]
