from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("agreements", "0004_templateversion")]

    operations = [
        migrations.AlterField(
            model_name="agreementfield", name="role",
            field=models.CharField(max_length=16, choices=[
                ("client", "Client"), ("company", "Company"),
                ("preparation", "Company preparation"),
            ]),
        ),
        migrations.AddConstraint(
            model_name="agreementfield",
            constraint=models.CheckConstraint(
                condition=(~models.Q(role="preparation")
                           | models.Q(field_type__in=("text", "name", "date"))),
                name="agreement_preparation_type",
            ),
        ),
    ]
