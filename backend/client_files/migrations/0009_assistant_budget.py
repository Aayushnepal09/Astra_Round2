from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("client_files", "0008_preparation_values")]

    operations = [
        migrations.CreateModel(
            name="AssistantBudget",
            fields=[
                ("provider", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("day", models.DateField()),
                ("calls", models.PositiveIntegerField(default=0)),
                ("next_at", models.DateTimeField()),
            ],
        ),
    ]
