from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("agreements", "0002_agreementfield")]

    operations = [
        migrations.AddField(
            model_name="agreement", name="fields_revision",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AlterField(
            model_name="agreementfield", name="label",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
