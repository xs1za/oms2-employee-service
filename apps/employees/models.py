from django.contrib.gis.db import models as geomodels
from django.db import models
from django.db.models import Q


class Address(models.Model):
    full_address = models.CharField(max_length=1000, null=False, verbose_name="Адрес полный")
    postal_code = models.CharField(max_length=32, blank=True, verbose_name="Почтовый индекс")
    country_code = models.CharField(max_length=2, blank=True, null=True, verbose_name="Код страны")
    country = models.CharField(max_length=250, blank=True, verbose_name="Страна")
    federal_district = models.CharField(max_length=250, blank=True, verbose_name="Федеральный округ")
    region = models.CharField(max_length=250, blank=True, verbose_name="Регион")
    city = models.CharField(max_length=250, blank=True, verbose_name="Город")
    settlement = models.CharField(max_length=250, blank=True, verbose_name="Населенный пункт")
    street = models.CharField(max_length=250, blank=True, verbose_name="Улица")
    house = models.CharField(max_length=250, blank=True, verbose_name="Дом")
    block = models.CharField(max_length=250, blank=True, verbose_name="Корпус/строение")
    flat = models.CharField(max_length=250, blank=True, verbose_name="Квартира")
    main_address = models.BooleanField(default=False, verbose_name="Основной адрес")
    location = geomodels.PointField(blank=True, null=True, srid=4326, verbose_name="Координаты")
    qc = models.IntegerField(verbose_name="Качество распознавания", null=True, blank=True)
    is_auto = models.BooleanField(verbose_name="Получен автоматически", default=False)

    class Meta:
        verbose_name_plural = "Адреса"
        verbose_name = "Адрес"

    def __str__(self) -> str:
        return f"{self.country} {self.full_address}"


class Person(models.Model):
    IDENTITY_PROVISIONAL = "provisional"
    IDENTITY_VERIFIED = "verified"
    IDENTITY_MERGED = "merged"
    IDENTITY_CHOICES = (
        (IDENTITY_PROVISIONAL, "Предварительная"),
        (IDENTITY_VERIFIED, "Подтверждена"),
        (IDENTITY_MERGED, "Объединена"),
    )

    surname = models.CharField(max_length=50, blank=False, verbose_name="Фамилия")
    firstname = models.CharField(max_length=50, blank=False, verbose_name="Имя")
    patronymic = models.CharField(max_length=50, blank=True, verbose_name="Отчество")
    gender = models.CharField(max_length=6, default="U", verbose_name="Пол")
    identity_status = models.CharField(max_length=16, choices=IDENTITY_CHOICES, default=IDENTITY_PROVISIONAL)
    merged_into = models.ForeignKey(
        "self", blank=True, null=True, on_delete=models.PROTECT, related_name="merged_people"
    )
    name_version = models.PositiveBigIntegerField(default=1)
    access_event_version = models.BigIntegerField(default=0)
    performer_event_version = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True, verbose_name="создано")
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True, verbose_name="изменено")

    class Meta:
        db_table = "employees_employeepersinfo"
        verbose_name = "Персона"
        verbose_name_plural = "Персоны"
        indexes = [models.Index(fields=("surname", "firstname"), name="person_name_search_idx")]

    def __str__(self) -> str:
        return f"{self.firstname} {self.surname}"

    @property
    def person_id(self) -> int:
        return self.pk

    @property
    def display_name(self) -> str:
        parts = (self.firstname, self.patronymic, self.surname)
        return " ".join(part.strip() for part in parts if part and part.strip())

    @property
    def personnel_number(self) -> str:
        period = self.work_periods.filter(status=PerformerWorkPeriod.STATUS_ACTIVE).order_by("-starts_at").first()
        return period.personnel_number if period and period.personnel_number else ""


class PersonContact(models.Model):
    TYPE_PHONE = "phone"
    TYPE_EMAIL = "email"
    TYPE_CHOICES = ((TYPE_PHONE, "Телефон"), (TYPE_EMAIL, "Email"))
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    VERIFICATION_PENDING = "pending"
    VERIFICATION_VERIFIED = "verified"
    VERIFICATION_REJECTED = "rejected"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="contacts")
    contact_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    encrypted_value = models.TextField()
    lookup_hmac = models.CharField(max_length=64, db_index=True)
    key_version = models.PositiveSmallIntegerField(default=1)
    is_primary = models.BooleanField(default=False)
    verification_status = models.CharField(
        max_length=16,
        choices=((VERIFICATION_PENDING, "Ожидает"), (VERIFICATION_VERIFIED, "Подтвержден"), (VERIFICATION_REJECTED, "Отклонен")),
        default=VERIFICATION_PENDING,
    )
    status = models.CharField(max_length=16, choices=((STATUS_ACTIVE, "Активен"), (STATUS_REVOKED, "Отозван")), default=STATUS_ACTIVE)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("person", "contact_type", "status"), name="person_contact_lookup_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("lookup_hmac",),
                condition=Q(contact_type="phone", verification_status="verified", status="active"),
                name="uniq_active_verified_phone_hmac",
            ),
            models.UniqueConstraint(
                fields=("person", "contact_type"),
                condition=Q(is_primary=True, status="active"),
                name="uniq_person_primary_contact_type",
            ),
            models.CheckConstraint(
                check=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gt=models.F("valid_from")),
                name="person_contact_valid_interval_ck",
            ),
        ]


class PerformerProfile(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = ((STATUS_ACTIVE, "Активен"), (STATUS_INACTIVE, "Неактивен"))

    ENGAGEMENT_GPH = "gph"
    ENGAGEMENT_SELF_EMPLOYED = "self_employed"
    ENGAGEMENT_CHOICES = ((ENGAGEMENT_GPH, "ГПХ"), (ENGAGEMENT_SELF_EMPLOYED, "Самозанятый"))

    VALIDATION_PENDING = "pending"
    VALIDATION_VERIFIED = "verified"
    VALIDATION_REJECTED = "rejected"
    VALIDATION_CHOICES = (
        (VALIDATION_PENDING, "Ожидает проверки"),
        (VALIDATION_VERIFIED, "Подтвержден"),
        (VALIDATION_REJECTED, "Отклонен"),
    )

    MEDICAL_NONE = "none"
    MEDICAL_NON_FOOD = "non_food"
    MEDICAL_FOOD = "food"
    MEDICAL_CHOICES = (
        (MEDICAL_NONE, "Нет"),
        (MEDICAL_NON_FOOD, "Непищевая"),
        (MEDICAL_FOOD, "Пищевая"),
    )

    STAGE_BLANK = "PERFORMER_BLANK"
    STAGE_PROFILE = "PERFORMER_PROFILE"
    STAGE_PROFILE_DELETED = "PERFORMER_PROFILE_DELETED"
    STAGE_DOCS_PENDING = "PERFORMER_DOCS_PENDING"
    STAGE_READY_NON_MEDICAL = "PERFORMER_READY_NON_MEDICAL"
    STAGE_READY_NON_FOOD = "PERFORMER_READY_NON_FOOD"
    STAGE_READY_FOOD = "PERFORMER_READY_FOOD"
    STAGE_BOOKED_SHIFT = "PERFORMER_BOOKED_SHIFT"
    STAGE_FIRST_SHIFT_COMPLETED = "PERFORMER_FIRST_SHIFT_COMPLETED"
    STAGE_FIFTH_SHIFT_COMPLETED = "PERFORMER_FIFTH_SHIFT_COMPLETED"

    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="performer_profile")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    engagement_type = models.CharField(max_length=32, choices=ENGAGEMENT_CHOICES)
    engagement_type_validation_status = models.CharField(
        max_length=16, choices=VALIDATION_CHOICES, default=VALIDATION_PENDING
    )
    engagement_type_selected_at = models.DateTimeField()
    engagement_type_validated_at = models.DateTimeField(null=True, blank=True)
    engagement_type_validated_by_user_id = models.CharField(max_length=36, null=True, blank=True)
    engagement_type_rejection_reason = models.CharField(max_length=255, null=True, blank=True)
    medical_book_status = models.CharField(max_length=16, choices=MEDICAL_CHOICES, default=MEDICAL_NONE)
    funnel_stage = models.CharField(max_length=40, default=STAGE_PROFILE)
    funnel_stage_changed_at = models.DateTimeField()
    completed_shifts_count = models.IntegerField(default=0)
    first_booked_at = models.DateTimeField(null=True, blank=True)
    first_shift_completed_at = models.DateTimeField(null=True, blank=True)
    fifth_shift_completed_at = models.DateTimeField(null=True, blank=True)
    source_version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(completed_shifts_count__gte=0), name="performer_completed_shifts_non_negative_ck"),
        ]


class PerformerWorkPeriod(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_TERMINATED = "terminated"
    STATUS_CHOICES = ((STATUS_ACTIVE, "Активен"), (STATUS_TERMINATED, "Завершен"))

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="work_periods")
    engagement_type = models.CharField(max_length=32, choices=PerformerProfile.ENGAGEMENT_CHOICES)
    personnel_number = models.CharField(max_length=250, blank=True, null=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    termination_reason = models.CharField(max_length=255, null=True, blank=True)
    source = models.CharField(max_length=32, default="hr")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("person_id", "starts_at")
        indexes = [models.Index(fields=("person", "status"), name="work_period_person_status_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("person",), condition=Q(status="active"), name="uq_active_work_period_person"
            ),
            models.UniqueConstraint(
                fields=("personnel_number",),
                condition=Q(personnel_number__isnull=False),
                name="uq_work_period_personnel_number",
            ),
            models.CheckConstraint(
                check=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="work_period_interval_ck",
            ),
        ]


class PersonAddress(models.Model):
    TYPE_REGISTRATION = "registration"
    TYPE_WORK_SEARCH = "work_search"
    TYPE_CHOICES = ((TYPE_REGISTRATION, "Регистрация"), (TYPE_WORK_SEARCH, "Поиск подработки"))

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="person_addresses")
    address = models.ForeignKey(Address, on_delete=models.PROTECT, related_name="person_addresses")
    address_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("person_id", "address_type", "created_at", "id")
        indexes = [models.Index(fields=("person", "address_type"), name="person_address_type_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("person",),
                condition=Q(address_type="registration"),
                name="uq_person_registration_address",
            ),
            models.UniqueConstraint(
                fields=("person",),
                condition=Q(address_type="work_search", is_primary=True),
                name="uq_person_primary_work_address",
            ),
            models.CheckConstraint(
                check=Q(address_type="work_search") | Q(is_primary=False),
                name="registration_address_not_primary_ck",
            ),
        ]


class PerformerEngagementTypeChange(models.Model):
    SOURCE_REGISTRATION = "registration"
    SOURCE_PROFILE_UPDATE = "profile_update"
    SOURCE_MERGE = "merge"
    SOURCE_CHOICES = (
        (SOURCE_REGISTRATION, "Регистрация"),
        (SOURCE_PROFILE_UPDATE, "Обновление профиля"),
        (SOURCE_MERGE, "Объединение"),
    )

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="engagement_type_changes")
    previous_engagement_type = models.CharField(max_length=32, null=True, blank=True)
    new_engagement_type = models.CharField(max_length=32)
    previous_validation_status = models.CharField(max_length=16, null=True, blank=True)
    new_validation_status = models.CharField(max_length=16)
    change_source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default=SOURCE_PROFILE_UPDATE)
    changed_by_user_id = models.CharField(max_length=36, null=True, blank=True)
    reason = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("person_id", "created_at", "id")
        indexes = [models.Index(fields=("person", "created_at"), name="engagement_change_person_idx")]


class PerformerShiftFact(models.Model):
    FACT_ASSIGNED = "assigned"
    FACT_COMPLETED = "completed"
    FACT_CHOICES = ((FACT_ASSIGNED, "Назначение"), (FACT_COMPLETED, "Завершение"))

    event_id = models.UUIDField(unique=True)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="shift_facts")
    performer_id = models.UUIDField()
    shift_id = models.UUIDField()
    assignment_id = models.UUIDField()
    fact_type = models.CharField(max_length=16, choices=FACT_CHOICES)
    engagement_type = models.CharField(max_length=32)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("person_id", "occurred_at", "id")
        constraints = [
            models.UniqueConstraint(fields=("assignment_id", "fact_type"), name="uq_shift_fact_assignment_type"),
        ]
        indexes = [models.Index(fields=("person", "fact_type", "occurred_at"), name="ix_shift_fact_person_type")]


class OperationsEventInbox(models.Model):
    OUTCOME_APPLIED = "applied"
    OUTCOME_DUPLICATE = "duplicate"
    OUTCOME_IGNORED = "ignored"
    OUTCOME_PERSON_NOT_FOUND = "person_not_found"
    OUTCOME_INVALID = "invalid"
    OUTCOME_CHOICES = (
        (OUTCOME_APPLIED, "Применено"),
        (OUTCOME_DUPLICATE, "Дубликат"),
        (OUTCOME_IGNORED, "Проигнорировано"),
        (OUTCOME_PERSON_NOT_FOUND, "Персона не найдена"),
        (OUTCOME_INVALID, "Некорректное"),
    )

    event_id = models.UUIDField(unique=True)
    event_type = models.CharField(max_length=80)
    person_id = models.BigIntegerField(null=True, blank=True)
    assignment_id = models.UUIDField(null=True, blank=True)
    outcome = models.CharField(max_length=32, choices=OUTCOME_CHOICES)
    payload = models.JSONField()
    processed_at = models.DateTimeField()

    class Meta:
        ordering = ("processed_at", "id")
        indexes = [models.Index(fields=("outcome", "processed_at"), name="operations_inbox_outcome_idx")]


class PersonDocument(models.Model):
    DOC_PASSPORT = "passport"
    DOC_NAME_CHANGE = "name_change_certificate"
    DOC_CHOICES = ((DOC_PASSPORT, "Паспорт"), (DOC_NAME_CHANGE, "Документ смены имени"))
    VERIFY_PENDING = "pending"
    VERIFY_VERIFIED = "verified"
    VERIFY_REJECTED = "rejected"
    STATUS_PENDING = "pending_verification"
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_REPLACED = "replaced"
    STATUS_REVOKED = "revoked"
    STATUS_REJECTED = "rejected"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=32, choices=DOC_CHOICES)
    passport_kind = models.CharField(max_length=16, null=True, blank=True)
    issuing_country_code = models.CharField(max_length=2)
    encrypted_number = models.TextField()
    lookup_hmac = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    key_version = models.PositiveSmallIntegerField(default=1)
    verification_status = models.CharField(
        max_length=16,
        choices=((VERIFY_PENDING, "Ожидает"), (VERIFY_VERIFIED, "Подтвержден"), (VERIFY_REJECTED, "Отклонен")),
        default=VERIFY_PENDING,
    )
    status = models.CharField(
        max_length=24,
        choices=(
            (STATUS_PENDING, "Ожидает проверки"), (STATUS_ACTIVE, "Действителен"), (STATUS_EXPIRED, "Истек"),
            (STATUS_REPLACED, "Заменен"), (STATUS_REVOKED, "Отозван"), (STATUS_REJECTED, "Отклонен"),
        ),
        default=STATUS_PENDING,
    )
    issued_at = models.DateField()
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    replaced_by_document = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replaces")
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by_user_id = models.CharField(max_length=36, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=("person", "document_type", "status"), name="person_doc_lookup_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("issuing_country_code", "document_type", "lookup_hmac"),
                condition=Q(document_type="passport", verification_status="verified", status="active"),
                name="uniq_active_verified_passport_hmac",
            ),
            models.UniqueConstraint(
                fields=("person",), condition=Q(document_type="passport", is_primary=True, status="active", verification_status="verified"),
                name="uniq_primary_active_passport",
            ),
            models.CheckConstraint(
                check=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gte=models.F("valid_from")),
                name="person_document_valid_interval_ck",
            ),
        ]


class PersonIdentifier(models.Model):
    TYPE_INN = "inn"
    TYPE_SNILS = "snils"
    TYPE_CHOICES = ((TYPE_INN, "ИНН"), (TYPE_SNILS, "СНИЛС"))
    VERIFY_PENDING = "pending"
    VERIFY_VERIFIED = "verified"
    VERIFY_REJECTED = "rejected"
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_MERGED = "merged"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="identifiers")
    identifier_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    country_code = models.CharField(max_length=2, default="RU")
    encrypted_value = models.TextField()
    lookup_hmac = models.CharField(max_length=64, db_index=True)
    key_version = models.PositiveSmallIntegerField(default=1)
    verification_status = models.CharField(
        max_length=16,
        choices=((VERIFY_PENDING, "Ожидает"), (VERIFY_VERIFIED, "Подтвержден"), (VERIFY_REJECTED, "Отклонен")),
        default=VERIFY_PENDING,
    )
    status = models.CharField(max_length=16, choices=((STATUS_ACTIVE, "Активен"), (STATUS_REVOKED, "Отозван"), (STATUS_MERGED, "Объединен")), default=STATUS_ACTIVE)
    merged_into_identifier = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="merged_identifiers")
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by_user_id = models.CharField(max_length=36, null=True, blank=True)
    verification_source = models.CharField(max_length=32, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("person", "identifier_type", "status"), name="person_identifier_lookup_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("country_code", "identifier_type", "lookup_hmac"),
                condition=Q(verification_status="verified", status="active"),
                name="uniq_active_verified_person_identifier",
            ),
            models.UniqueConstraint(
                fields=("person", "identifier_type"),
                condition=Q(verification_status="verified", status="active"),
                name="uniq_person_active_identifier_type",
            ),
        ]


class PersonNameHistory(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="name_history")
    surname = models.CharField(max_length=50)
    firstname = models.CharField(max_length=50)
    patronymic = models.CharField(max_length=50, blank=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    version = models.PositiveBigIntegerField()
    change_reason = models.CharField(max_length=32)
    source_document = models.ForeignKey(PersonDocument, null=True, blank=True, on_delete=models.PROTECT)
    changed_by_user_id = models.CharField(max_length=36, null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("person_id", "valid_from")
        indexes = [models.Index(fields=("person", "valid_from"), name="person_name_history_idx")]
        constraints = [
            models.UniqueConstraint(fields=("person", "version"), name="uniq_person_name_version"),
            models.CheckConstraint(
                check=Q(valid_to__isnull=True) | Q(valid_to__gt=models.F("valid_from")),
                name="person_name_history_interval_ck",
            ),
        ]


class PersonMerge(models.Model):
    duplicate_person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="merge_record")
    canonical_person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="merged_from_records")
    reason_code = models.CharField(max_length=32)
    merged_at = models.DateTimeField()
    merged_by_user_id = models.CharField(max_length=36, null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(check=~Q(duplicate_person=models.F("canonical_person")), name="person_merge_not_self_ck")]


class IntegrationOutbox(models.Model):
    event_id = models.UUIDField(unique=True)
    topic = models.CharField(max_length=120)
    event_type = models.CharField(max_length=120)
    aggregate_type = models.CharField(max_length=32, default="person")
    aggregate_id = models.CharField(max_length=32)
    aggregate_version = models.BigIntegerField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [models.UniqueConstraint(fields=("topic", "aggregate_id", "aggregate_version"), name="uniq_outbox_aggregate_version")]
        indexes = [models.Index(fields=("topic", "aggregate_id", "aggregate_version", "published_at"), name="outbox_publish_order_idx")]


class ApiIdempotencyRecord(models.Model):
    key = models.UUIDField()
    scope = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("key", "scope"), name="uniq_api_idempotency_scope_key")]
        indexes = [models.Index(fields=("created_at",), name="api_idempotency_created_idx")]
