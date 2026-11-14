import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("agreements", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgreementField",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("agreement_pdf_sha256", models.CharField(editable=False, max_length=64, validators=[django.core.validators.RegexValidator("\\A[0-9a-f]{64}\\Z", "Use a SHA-256 hexadecimal digest.")])),
                ("field_type", models.CharField(choices=[("name", "Name"), ("text", "Text"), ("date", "Date"), ("signature", "Signature"), ("initials", "Initials"), ("acknowledgement", "Acknowledgement")], max_length=20)),
                ("label", models.CharField(max_length=120)),
                ("required", models.BooleanField(default=False)),
                ("role", models.CharField(choices=[("client", "Client"), ("company", "Company")], max_length=8)),
                ("page_number", models.PositiveIntegerField()),
                ("x", models.FloatField()),
                ("y", models.FloatField()),
                ("width", models.FloatField()),
                ("height", models.FloatField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agreement", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.CASCADE, related_name="fields", to="agreements.agreement")),
            ],
            options={
                "ordering": ["page_number", "y", "x", "id"],
                "indexes": [models.Index(fields=["agreement", "page_number", "y", "x"], name="agreement_field_position")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("page_number__gt", 0)), name="agreement_field_positive_page"),
                    models.CheckConstraint(condition=models.Q(("height__gt", 0), ("width__gt", 0), ("x__gte", 0), ("y__gte", 0), ("height__lte", 1), ("width__lte", 1), ("x__lte", 1), ("y__lte", 1)), name="agreement_field_normalized_bounds"),
                ],
            },
        ),
    ]
