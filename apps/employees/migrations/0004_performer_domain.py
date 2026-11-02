from django.db import migrations, models
from django.db.models import F, Q
import django.db.models.deletion
import django.utils.timezone


def ensure_performer_profiles_empty(apps, schema_editor):
    PerformerProfile = apps.get_model("employees", "PerformerProfile")
    count = PerformerProfile.objects.count()
    if count:
        raise RuntimeError(
            "Migration 0004_performer_domain requires an empty employees_performerprofile table. "
            f"Found {count} rows. Recreate the development database according to OMS-OPS-0002 before applying "
            "this migration; historical performer profiles are not migrated."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0003_employment_personnel_number_unique"),
    ]

    operations = [
        migrations.RunPython(ensure_performer_profiles_empty, migrations.RunPython.noop),
        migrations.RemoveField(model_name="person", name="address"),
        migrations.DeleteModel(name="PositionAssignment"),
        migrations.DeleteModel(name="EmploymentEpisode"),
        migrations.DeleteModel(name="ExternalMembership"),
        migrations.AddField(
            model_name="address",
            name="country_code",
            field=models.CharField(blank=True, max_length=2, null=True, verbose_name="Код страны"),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="engagement_type",
            field=models.CharField(
                choices=[("gph", "ГПХ"), ("self_employed", "Самозанятый")],
                default="gph",
                max_length=32,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="engagement_type_validation_status",
            field=models.CharField(
                choices=[("pending", "Ожидает проверки"), ("verified", "Подтвержден"), ("rejected", "Отклонен")],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="engagement_type_selected_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="engagement_type_validated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="engagement_type_validated_by_user_id",
            field=models.CharField(blank=True, max_length=36, null=True),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="engagement_type_rejection_reason",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="medical_book_status",
            field=models.CharField(
                choices=[("none", "Нет"), ("non_food", "Непищевая"), ("food", "Пищевая")],
                default="none",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="funnel_stage",
            field=models.CharField(default="PERFORMER_PROFILE", max_length=40),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="funnel_stage_changed_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="completed_shifts_count",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="first_booked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="first_shift_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="performerprofile",
            name="fifth_shift_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="PerformerWorkPeriod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "engagement_type",
                    models.CharField(
                        choices=[("gph", "ГПХ"), ("self_employed", "Самозанятый")], max_length=32
                    ),
                ),
                ("personnel_number", models.CharField(blank=True, max_length=250, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Активен"), ("terminated", "Завершен")], default="active", max_length=16
                    ),
                ),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("termination_reason", models.CharField(blank=True, max_length=255, null=True)),
                ("source", models.CharField(default="hr", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="work_periods",
                        to="employees.person",
                    ),
                ),
            ],
            options={"ordering": ("person_id", "starts_at")},
        ),
        migrations.CreateModel(
            name="PersonAddress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "address_type",
                    models.CharField(
                        choices=[("registration", "Регистрация"), ("work_search", "Поиск подработки")],
                        max_length=16,
                    ),
                ),
                ("is_primary", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "address",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="person_addresses",
                        to="employees.address",
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="person_addresses",
                        to="employees.person",
                    ),
                ),
            ],
            options={"ordering": ("person_id", "address_type", "created_at", "id")},
        ),
        migrations.CreateModel(
            name="PerformerEngagementTypeChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_engagement_type", models.CharField(blank=True, max_length=32, null=True)),
                ("new_engagement_type", models.CharField(max_length=32)),
                ("previous_validation_status", models.CharField(blank=True, max_length=16, null=True)),
                ("new_validation_status", models.CharField(max_length=16)),
                (
                    "change_source",
                    models.CharField(
                        choices=[
                            ("registration", "Регистрация"),
                            ("profile_update", "Обновление профиля"),
                            ("merge", "Объединение"),
                        ],
                        default="profile_update",
                        max_length=32,
                    ),
                ),
                ("changed_by_user_id", models.CharField(blank=True, max_length=36, null=True)),
                ("reason", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="engagement_type_changes",
                        to="employees.person",
                    ),
                ),
            ],
            options={"ordering": ("person_id", "created_at", "id")},
        ),
        migrations.CreateModel(
            name="PerformerShiftFact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField(unique=True)),
                ("performer_id", models.UUIDField()),
                ("shift_id", models.UUIDField()),
                ("assignment_id", models.UUIDField()),
                (
                    "fact_type",
                    models.CharField(
                        choices=[("assigned", "Назначение"), ("completed", "Завершение")], max_length=16
                    ),
                ),
                ("engagement_type", models.CharField(max_length=32)),
                ("occurred_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shift_facts",
                        to="employees.person",
                    ),
                ),
            ],
            options={"ordering": ("person_id", "occurred_at", "id")},
        ),
        migrations.CreateModel(
            name="OperationsEventInbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField(unique=True)),
                ("event_type", models.CharField(max_length=80)),
                ("person_id", models.BigIntegerField(blank=True, null=True)),
                ("assignment_id", models.UUIDField(blank=True, null=True)),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("applied", "Применено"),
                            ("duplicate", "Дубликат"),
                            ("ignored", "Проигнорировано"),
                            ("person_not_found", "Персона не найдена"),
                            ("invalid", "Некорректное"),
                        ],
                        max_length=32,
                    ),
                ),
                ("payload", models.JSONField()),
                ("processed_at", models.DateTimeField()),
            ],
            options={"ordering": ("processed_at", "id")},
        ),
        migrations.AddIndex(
            model_name="performerworkperiod",
            index=models.Index(fields=["person", "status"], name="work_period_person_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="performerworkperiod",
            constraint=models.UniqueConstraint(
                condition=Q(status="active"), fields=("person",), name="uq_active_work_period_person"
            ),
        ),
        migrations.AddConstraint(
            model_name="performerworkperiod",
            constraint=models.UniqueConstraint(
                condition=Q(personnel_number__isnull=False),
                fields=("personnel_number",),
                name="uq_work_period_personnel_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="performerworkperiod",
            constraint=models.CheckConstraint(
                check=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="work_period_interval_ck"
            ),
        ),
        migrations.AddIndex(
            model_name="personaddress",
            index=models.Index(fields=["person", "address_type"], name="person_address_type_idx"),
        ),
        migrations.AddConstraint(
            model_name="personaddress",
            constraint=models.UniqueConstraint(
                condition=Q(address_type="registration"),
                fields=("person",),
                name="uq_person_registration_address",
            ),
        ),
        migrations.AddConstraint(
            model_name="personaddress",
            constraint=models.UniqueConstraint(
                condition=Q(address_type="work_search", is_primary=True),
                fields=("person",),
                name="uq_person_primary_work_address",
            ),
        ),
        migrations.AddConstraint(
            model_name="personaddress",
            constraint=models.CheckConstraint(
                check=Q(address_type="work_search") | Q(is_primary=False),
                name="registration_address_not_primary_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="performerengagementtypechange",
            index=models.Index(fields=["person", "created_at"], name="engagement_change_person_idx"),
        ),
        migrations.AddConstraint(
            model_name="performershiftfact",
            constraint=models.UniqueConstraint(
                fields=("assignment_id", "fact_type"), name="uq_shift_fact_assignment_type"
            ),
        ),
        migrations.AddIndex(
            model_name="performershiftfact",
            index=models.Index(fields=["person", "fact_type", "occurred_at"], name="ix_shift_fact_person_type"),
        ),
        migrations.AddIndex(
            model_name="operationseventinbox",
            index=models.Index(fields=["outcome", "processed_at"], name="operations_inbox_outcome_idx"),
        ),
        migrations.AddConstraint(
            model_name="performerprofile",
            constraint=models.CheckConstraint(
                check=Q(completed_shifts_count__gte=0), name="performer_completed_shifts_non_negative_ck"
            ),
        ),
    ]
