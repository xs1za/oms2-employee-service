from django.contrib.gis.db import models as geomodels
from django.db import models
from django.db.models import Q


class Address(models.Model):
    full_address = models.CharField(max_length=1000, null=False, verbose_name="Адрес полный")
    postal_code = models.CharField(max_length=32, blank=True, verbose_name="Почтовый индекс")
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
    address = models.ForeignKey(Address, blank=True, null=True, on_delete=models.PROTECT, verbose_name="Адрес")
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
        episode = self.employment_periods.filter(status=EmploymentEpisode.STATUS_ACTIVE).order_by("-starts_at").first()
        return episode.personnel_number if episode and episode.personnel_number else ""


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


class EmploymentEpisode(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_TERMINATED = "terminated"
    STATUS_CHOICES = ((STATUS_ACTIVE, "Активен"), (STATUS_TERMINATED, "Завершен"))

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="employment_periods")
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
        indexes = [models.Index(fields=("person", "status"), name="employment_person_status_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("person",), condition=Q(status="active"), name="uniq_active_employment_person"
            ),
            models.UniqueConstraint(
                fields=("personnel_number",),
                condition=Q(personnel_number__isnull=False),
                name="uniq_employment_personnel_number",
            ),
            models.CheckConstraint(
                check=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="employment_period_interval_ck",
            ),
        ]


class PositionAssignment(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ENDED = "ended"
    STATUS_CHOICES = ((STATUS_ACTIVE, "Активно"), (STATUS_ENDED, "Завершено"))

    employment_episode = models.ForeignKey(EmploymentEpisode, on_delete=models.PROTECT, related_name="positions")
    position_code = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("employment_episode_id", "starts_at")
        indexes = [models.Index(fields=("employment_episode", "status"), name="position_episode_status_idx")]
        constraints = [
            models.CheckConstraint(
                check=Q(ends_at__isnull=True) | Q(ends_at__gt=models.F("starts_at")),
                name="position_assignment_interval_ck",
            ),
            models.UniqueConstraint(
                fields=("employment_episode",), condition=Q(status="active"), name="uniq_active_position_assignment"
            ),
        ]


class ExternalMembership(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="external_memberships")
    organization_id = models.CharField(max_length=36)
    status = models.CharField(max_length=16, choices=((STATUS_ACTIVE, "Активно"), (STATUS_REVOKED, "Отозвано")), default=STATUS_ACTIVE)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("person", "organization_id", "status"), name="external_member_lookup_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("person", "organization_id"), condition=Q(status="active"), name="uniq_active_external_membership"
            ),
            models.CheckConstraint(
                check=Q(valid_to__isnull=True) | Q(valid_to__gt=models.F("valid_from")),
                name="external_membership_interval_ck",
            ),
        ]


class PerformerProfile(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = ((STATUS_ACTIVE, "Активен"), (STATUS_INACTIVE, "Неактивен"))

    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="performer_profile")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    source_version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


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
