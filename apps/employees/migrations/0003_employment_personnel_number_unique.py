from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0002_person_master_data"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="employmentepisode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("personnel_number__isnull", False)),
                fields=("personnel_number",),
                name="uniq_employment_personnel_number",
            ),
        ),
    ]
