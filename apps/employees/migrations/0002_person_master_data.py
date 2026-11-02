from django.db import migrations, models
from django.db.models import F, Q
import django.db.models.deletion
import django.utils.timezone


def backfill_people_history(apps, schema_editor):
    Person = apps.get_model("employees", "Person")
    EmploymentEpisode = apps.get_model("employees", "EmploymentEpisode")
    PersonNameHistory = apps.get_model("employees", "PersonNameHistory")

    for person in Person.objects.all().iterator():
        starts_at = person.created_at or django.utils.timezone.now()
        EmploymentEpisode.objects.get_or_create(
            person_id=person.pk,
            source="legacy",
            defaults={
                "personnel_number": person.personnel_number or None,
                "status": "active",
                "starts_at": starts_at,
            },
        )
        PersonNameHistory.objects.get_or_create(
            person_id=person.pk,
            valid_from=starts_at,
            defaults={
                "version": 1,
                "surname": person.surname,
                "firstname": person.firstname,
                "patronymic": person.patronymic or "",
                "valid_to": None,
                "change_reason": "legacy_import",
                "changed_by_user_id": None,
                "recorded_at": starts_at,
            },
        )


def reverse_backfill_people_history(apps, schema_editor):
    EmploymentEpisode = apps.get_model("employees", "EmploymentEpisode")
    Person = apps.get_model("employees", "Person")
    for person in Person.objects.all().iterator():
        episode = EmploymentEpisode.objects.filter(person_id=person.pk).order_by("-starts_at").first()
        if episode:
            person.personnel_number = episode.personnel_number or ""
            person.save(update_fields=("personnel_number",))


class Migration(migrations.Migration):
    dependencies = [("employees", "0001_initial")]

    operations = [
        migrations.RenameModel(old_name="Employeepersinfo", new_name="Person"),
        # Keep the legacy physical table name and primary-key values for a no-rekey migration.
        migrations.AlterModelTable(name="person", table="employees_employeepersinfo"),
        migrations.AddField(
            model_name="person",
            name="identity_status",
            field=models.CharField(
                choices=[("provisional", "Предварительная"), ("verified", "Подтверждена"), ("merged", "Объединена")],
                default="provisional",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="person",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="merged_people",
                to="employees.person",
            ),
        ),
        migrations.AddField(
            model_name="person",
            name="name_version",
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="person",
            name="access_event_version",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="person",
            name="performer_event_version",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="person",
            name="gender",
            field=models.CharField(default="U", max_length=6, verbose_name="Пол"),
        ),
        migrations.CreateModel(
            name="EmploymentEpisode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("personnel_number", models.CharField(blank=True, max_length=250, null=True)),
                ("status", models.CharField(choices=[("active", "Активен"), ("terminated", "Завершен")], default="active", max_length=16)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("termination_reason", models.CharField(blank=True, max_length=255, null=True)),
                ("source", models.CharField(default="hr", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employment_periods", to="employees.person")),
            ],
            options={"ordering": ("person_id", "starts_at")},
        ),
        migrations.CreateModel(
            name="PositionAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position_code", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("active", "Активно"), ("ended", "Завершено")], default="active", max_length=16)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("employment_episode", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="positions", to="employees.employmentepisode")),
            ],
            options={"ordering": ("employment_episode_id", "starts_at")},
        ),
        migrations.CreateModel(
            name="PersonContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("contact_type", models.CharField(choices=[("phone", "Телефон"), ("email", "Email")], max_length=16)),
                ("encrypted_value", models.TextField()),
                ("lookup_hmac", models.CharField(db_index=True, max_length=64)),
                ("key_version", models.PositiveSmallIntegerField(default=1)),
                ("is_primary", models.BooleanField(default=False)),
                ("verification_status", models.CharField(choices=[("pending", "Ожидает"), ("verified", "Подтвержден"), ("rejected", "Отклонен")], default="pending", max_length=16)),
                ("status", models.CharField(choices=[("active", "Активен"), ("revoked", "Отозван")], default="active", max_length=16)),
                ("valid_from", models.DateTimeField(blank=True, null=True)),
                ("valid_to", models.DateTimeField(blank=True, null=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contacts", to="employees.person")),
            ],
        ),
        migrations.CreateModel(
            name="PersonDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(choices=[("passport", "Паспорт"), ("name_change_certificate", "Документ смены имени")], max_length=32)),
                ("passport_kind", models.CharField(blank=True, max_length=16, null=True)),
                ("issuing_country_code", models.CharField(max_length=2)),
                ("encrypted_number", models.TextField()),
                ("lookup_hmac", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("key_version", models.PositiveSmallIntegerField(default=1)),
                ("verification_status", models.CharField(choices=[("pending", "Ожидает"), ("verified", "Подтвержден"), ("rejected", "Отклонен")], default="pending", max_length=16)),
                ("status", models.CharField(choices=[("pending_verification", "Ожидает проверки"), ("active", "Действителен"), ("expired", "Истек"), ("replaced", "Заменен"), ("revoked", "Отозван"), ("rejected", "Отклонен")], default="pending_verification", max_length=24)),
                ("issued_at", models.DateField()),
                ("valid_from", models.DateField(blank=True, null=True)),
                ("valid_to", models.DateField(blank=True, null=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("verified_by_user_id", models.CharField(blank=True, max_length=36, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="documents", to="employees.person")),
                ("replaced_by_document", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="replaces", to="employees.persondocument")),
            ],
        ),
        migrations.CreateModel(
            name="PersonIdentifier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifier_type", models.CharField(choices=[("inn", "ИНН"), ("snils", "СНИЛС")], max_length=16)),
                ("country_code", models.CharField(default="RU", max_length=2)),
                ("encrypted_value", models.TextField()),
                ("lookup_hmac", models.CharField(db_index=True, max_length=64)),
                ("key_version", models.PositiveSmallIntegerField(default=1)),
                ("verification_status", models.CharField(choices=[("pending", "Ожидает"), ("verified", "Подтвержден"), ("rejected", "Отклонен")], default="pending", max_length=16)),
                ("status", models.CharField(choices=[("active", "Активен"), ("revoked", "Отозван"), ("merged", "Объединен")], default="active", max_length=16)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("verified_by_user_id", models.CharField(blank=True, max_length=36, null=True)),
                ("verification_source", models.CharField(blank=True, max_length=32, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="identifiers", to="employees.person")),
                ("merged_into_identifier", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="merged_identifiers", to="employees.personidentifier")),
            ],
        ),
        migrations.CreateModel(
            name="PersonNameHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("surname", models.CharField(max_length=50)),
                ("firstname", models.CharField(max_length=50)),
                ("patronymic", models.CharField(blank=True, max_length=50)),
                ("valid_from", models.DateTimeField()),
                ("valid_to", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveBigIntegerField()),
                ("change_reason", models.CharField(max_length=32)),
                ("changed_by_user_id", models.CharField(blank=True, max_length=36, null=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="name_history", to="employees.person")),
                ("source_document", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="employees.persondocument")),
            ],
            options={"ordering": ("person_id", "valid_from")},
        ),
        migrations.CreateModel(
            name="PerformerProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("active", "Активен"), ("inactive", "Неактивен")], default="active", max_length=16)),
                ("source_version", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("person", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="performer_profile", to="employees.person")),
            ],
        ),
        migrations.CreateModel(
            name="ExternalMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organization_id", models.CharField(max_length=36)),
                ("status", models.CharField(choices=[("active", "Активно"), ("revoked", "Отозвано")], default="active", max_length=16)),
                ("valid_from", models.DateTimeField()),
                ("valid_to", models.DateTimeField(blank=True, null=True)),
                ("revoked_reason", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="external_memberships", to="employees.person")),
            ],
        ),
        migrations.CreateModel(
            name="PersonMerge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason_code", models.CharField(max_length=32)),
                ("merged_at", models.DateTimeField()),
                ("merged_by_user_id", models.CharField(blank=True, max_length=36, null=True)),
                ("canonical_person", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="merged_from_records", to="employees.person")),
                ("duplicate_person", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="merge_record", to="employees.person")),
            ],
        ),
        migrations.CreateModel(
            name="IntegrationOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField(unique=True)),
                ("topic", models.CharField(max_length=120)),
                ("event_type", models.CharField(max_length=120)),
                ("aggregate_type", models.CharField(default="person", max_length=32)),
                ("aggregate_id", models.CharField(max_length=32)),
                ("aggregate_version", models.BigIntegerField()),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.CharField(blank=True, max_length=500)),
            ],
            options={"ordering": ("created_at", "id")},
        ),
        migrations.CreateModel(
            name="ApiIdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.UUIDField()),
                ("scope", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=64)),
                ("response_status", models.PositiveSmallIntegerField()),
                ("response_body", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.RunPython(backfill_people_history, reverse_backfill_people_history),
        migrations.RemoveField(model_name="person", name="personnel_number"),
        migrations.AlterModelOptions(name="person", options={"verbose_name": "Персона", "verbose_name_plural": "Персоны"}),
        migrations.AddIndex(model_name="person", index=models.Index(fields=["surname", "firstname"], name="person_name_search_idx")),
        migrations.AddIndex(model_name="personcontact", index=models.Index(fields=["person", "contact_type", "status"], name="person_contact_lookup_idx")),
        migrations.AddConstraint(model_name="personcontact", constraint=models.UniqueConstraint(condition=Q(contact_type="phone", verification_status="verified", status="active"), fields=("lookup_hmac",), name="uniq_active_verified_phone_hmac")),
        migrations.AddConstraint(model_name="personcontact", constraint=models.UniqueConstraint(condition=Q(is_primary=True, status="active"), fields=("person", "contact_type"), name="uniq_person_primary_contact_type")),
        migrations.AddConstraint(model_name="personcontact", constraint=models.CheckConstraint(check=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gt=F("valid_from")), name="person_contact_valid_interval_ck")),
        migrations.AddIndex(model_name="employmentepisode", index=models.Index(fields=["person", "status"], name="employment_person_status_idx")),
        migrations.AddConstraint(model_name="employmentepisode", constraint=models.UniqueConstraint(condition=Q(status="active"), fields=("person",), name="uniq_active_employment_person")),
        migrations.AddConstraint(model_name="employmentepisode", constraint=models.CheckConstraint(check=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="employment_period_interval_ck")),
        migrations.AddIndex(model_name="positionassignment", index=models.Index(fields=["employment_episode", "status"], name="position_episode_status_idx")),
        migrations.AddConstraint(model_name="positionassignment", constraint=models.CheckConstraint(check=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="position_assignment_interval_ck")),
        migrations.AddConstraint(model_name="positionassignment", constraint=models.UniqueConstraint(condition=Q(status="active"), fields=("employment_episode",), name="uniq_active_position_assignment")),
        migrations.AddIndex(model_name="externalmembership", index=models.Index(fields=["person", "organization_id", "status"], name="external_member_lookup_idx")),
        migrations.AddConstraint(model_name="externalmembership", constraint=models.UniqueConstraint(condition=Q(status="active"), fields=("person", "organization_id"), name="uniq_active_external_membership")),
        migrations.AddConstraint(model_name="externalmembership", constraint=models.CheckConstraint(check=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")), name="external_membership_interval_ck")),
        migrations.AddIndex(model_name="persondocument", index=models.Index(fields=["person", "document_type", "status"], name="person_doc_lookup_idx")),
        migrations.AddConstraint(model_name="persondocument", constraint=models.UniqueConstraint(condition=Q(document_type="passport", verification_status="verified", status="active"), fields=("issuing_country_code", "document_type", "lookup_hmac"), name="uniq_active_verified_passport_hmac")),
        migrations.AddConstraint(model_name="persondocument", constraint=models.UniqueConstraint(condition=Q(document_type="passport", is_primary=True, status="active", verification_status="verified"), fields=("person",), name="uniq_primary_active_passport")),
        migrations.AddConstraint(model_name="persondocument", constraint=models.CheckConstraint(check=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gte=F("valid_from")), name="person_document_valid_interval_ck")),
        migrations.AddIndex(model_name="personidentifier", index=models.Index(fields=["person", "identifier_type", "status"], name="person_identifier_lookup_idx")),
        migrations.AddConstraint(model_name="personidentifier", constraint=models.UniqueConstraint(condition=Q(verification_status="verified", status="active"), fields=("country_code", "identifier_type", "lookup_hmac"), name="uniq_active_verified_person_identifier")),
        migrations.AddConstraint(model_name="personidentifier", constraint=models.UniqueConstraint(condition=Q(verification_status="verified", status="active"), fields=("person", "identifier_type"), name="uniq_person_active_identifier_type")),
        migrations.AddIndex(model_name="personnamehistory", index=models.Index(fields=["person", "valid_from"], name="person_name_history_idx")),
        migrations.AddConstraint(model_name="personnamehistory", constraint=models.UniqueConstraint(fields=("person", "version"), name="uniq_person_name_version")),
        migrations.AddConstraint(model_name="personnamehistory", constraint=models.CheckConstraint(check=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")), name="person_name_history_interval_ck")),
        migrations.AddConstraint(model_name="personmerge", constraint=models.CheckConstraint(check=~Q(duplicate_person=F("canonical_person")), name="person_merge_not_self_ck")),
        migrations.AddConstraint(model_name="integrationoutbox", constraint=models.UniqueConstraint(fields=("topic", "aggregate_id", "aggregate_version"), name="uniq_outbox_aggregate_version")),
        migrations.AddIndex(model_name="integrationoutbox", index=models.Index(fields=["topic", "aggregate_id", "aggregate_version", "published_at"], name="outbox_publish_order_idx")),
        migrations.AddConstraint(model_name="apiidempotencyrecord", constraint=models.UniqueConstraint(fields=("key", "scope"), name="uniq_api_idempotency_scope_key")),
        migrations.AddIndex(model_name="apiidempotencyrecord", index=models.Index(fields=["created_at"], name="api_idempotency_created_idx")),
    ]
