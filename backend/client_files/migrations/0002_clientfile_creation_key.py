import uuid

from django.db import migrations, models


def populate_creation_keys(apps, schema_editor):
    files = apps.get_model("client_files", "ClientFile").objects.using(schema_editor.connection.alias)
    for file_id in files.values_list("pk", flat=True).iterator(chunk_size=500):
        files.filter(pk=file_id).update(creation_key=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("client_files", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="clientfile",
            name="creation_key",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_creation_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="clientfile",
            name="creation_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddConstraint(
            model_name="clientfile",
            constraint=models.UniqueConstraint(
                fields=("company", "creation_key"), name="clientfile_company_key_uniq"
            ),
        ),
        migrations.AddIndex(
            model_name="clientfile",
            index=models.Index(
                fields=["company", "-updated_at", "-id"],
                name="clientfile_company_updated",
            ),
        ),
    ]
