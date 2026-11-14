from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("agreements", "0005_preparation_fields"),
        ("client_files", "0007_agreement_completion"),
    ]

    operations = [
        migrations.AddField(model_name="clientfile", name="preparation_values",
                            field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name="signingrequest", name="preparation_values",
                            field=models.JSONField(default=dict, blank=True)),
    ]
