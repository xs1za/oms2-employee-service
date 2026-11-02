from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.employees.events import enqueue_performer_profile_changed
from apps.employees.models import (
    PerformerProfile,
    PerformerShiftFact,
    PerformerWorkPeriod,
    PersonDocument,
    PersonIdentifier,
)


_STAGE_ORDER = {
    PerformerProfile.STAGE_PROFILE_DELETED: 0,
    PerformerProfile.STAGE_PROFILE: 1,
    PerformerProfile.STAGE_DOCS_PENDING: 2,
    PerformerProfile.STAGE_READY_NON_MEDICAL: 3,
    PerformerProfile.STAGE_READY_NON_FOOD: 4,
    PerformerProfile.STAGE_READY_FOOD: 5,
    PerformerProfile.STAGE_BOOKED_SHIFT: 6,
    PerformerProfile.STAGE_FIRST_SHIFT_COMPLETED: 7,
    PerformerProfile.STAGE_FIFTH_SHIFT_COMPLETED: 8,
}

_PROTECTED_STAGES = {
    PerformerProfile.STAGE_BOOKED_SHIFT,
    PerformerProfile.STAGE_FIRST_SHIFT_COMPLETED,
    PerformerProfile.STAGE_FIFTH_SHIFT_COMPLETED,
}


def mandatory_documents_ready(person) -> bool:
    passport = PersonDocument.objects.filter(
        person=person,
        document_type=PersonDocument.DOC_PASSPORT,
        is_primary=True,
        status=PersonDocument.STATUS_ACTIVE,
        verification_status=PersonDocument.VERIFY_VERIFIED,
    ).exists()
    if not passport:
        return False
    for identifier_type in (PersonIdentifier.TYPE_INN, PersonIdentifier.TYPE_SNILS):
        if not PersonIdentifier.objects.filter(
            person=person,
            identifier_type=identifier_type,
            status=PersonIdentifier.STATUS_ACTIVE,
            verification_status=PersonIdentifier.VERIFY_VERIFIED,
        ).exists():
            return False
    return True


def has_mandatory_document(person) -> bool:
    if PersonDocument.objects.filter(
        person=person,
        document_type=PersonDocument.DOC_PASSPORT,
        status__in=(PersonDocument.STATUS_PENDING, PersonDocument.STATUS_ACTIVE),
    ).exists():
        return True
    return PersonIdentifier.objects.filter(
        person=person,
        identifier_type__in=(PersonIdentifier.TYPE_INN, PersonIdentifier.TYPE_SNILS),
        status=PersonIdentifier.STATUS_ACTIVE,
    ).exists()


def compute_funnel_stage(profile: PerformerProfile) -> str:
    if profile.status == PerformerProfile.STATUS_INACTIVE:
        return PerformerProfile.STAGE_PROFILE_DELETED
    if profile.completed_shifts_count >= 5:
        return PerformerProfile.STAGE_FIFTH_SHIFT_COMPLETED
    if profile.completed_shifts_count >= 1:
        return PerformerProfile.STAGE_FIRST_SHIFT_COMPLETED
    if profile.first_booked_at is not None:
        return PerformerProfile.STAGE_BOOKED_SHIFT

    docs_ready = mandatory_documents_ready(profile.person)
    active_period = PerformerWorkPeriod.objects.filter(
        person=profile.person, status=PerformerWorkPeriod.STATUS_ACTIVE
    ).exists()
    if docs_ready and profile.engagement_type_validation_status == PerformerProfile.VALIDATION_VERIFIED and active_period:
        if profile.medical_book_status == PerformerProfile.MEDICAL_FOOD:
            return PerformerProfile.STAGE_READY_FOOD
        if profile.medical_book_status == PerformerProfile.MEDICAL_NON_FOOD:
            return PerformerProfile.STAGE_READY_NON_FOOD
        return PerformerProfile.STAGE_READY_NON_MEDICAL

    if profile.engagement_type_validation_status == PerformerProfile.VALIDATION_REJECTED:
        return PerformerProfile.STAGE_PROFILE

    if not docs_ready and has_mandatory_document(profile.person):
        return PerformerProfile.STAGE_DOCS_PENDING
    return PerformerProfile.STAGE_PROFILE


def _keep_achieved_stage(previous: str, computed: str) -> str:
    if computed == PerformerProfile.STAGE_PROFILE_DELETED:
        return computed
    if previous in _PROTECTED_STAGES and _STAGE_ORDER[computed] < _STAGE_ORDER[previous]:
        return previous
    return computed


@transaction.atomic
def recompute_funnel_stage(person_id: int, *, enqueue: bool = True) -> str | None:
    profile = (
        PerformerProfile.objects.select_for_update()
        .select_related("person")
        .filter(person_id=person_id)
        .first()
    )
    if profile is None:
        return None

    computed = _keep_achieved_stage(profile.funnel_stage, compute_funnel_stage(profile))
    if computed != profile.funnel_stage:
        profile.funnel_stage = computed
        profile.funnel_stage_changed_at = timezone.now()
        profile.save(update_fields=("funnel_stage", "funnel_stage_changed_at", "updated_at"))
        if enqueue:
            enqueue_performer_profile_changed(profile.person)
    return computed


def refresh_shift_statistics(person, profile: PerformerProfile | None = None) -> PerformerProfile | None:
    """Recalculate completed shift counter and milestone dates from stored facts."""
    profile = profile or getattr(person, "performer_profile", None)
    if profile is None:
        return None

    assigned = (
        PerformerShiftFact.objects.filter(person=person, fact_type=PerformerShiftFact.FACT_ASSIGNED)
        .order_by("occurred_at", "id")
        .values_list("occurred_at", flat=True)
        .first()
    )
    completed = list(
        PerformerShiftFact.objects.filter(person=person, fact_type=PerformerShiftFact.FACT_COMPLETED)
        .order_by("occurred_at", "id")
        .values_list("occurred_at", flat=True)
    )
    profile.completed_shifts_count = len(completed)
    profile.first_booked_at = assigned
    profile.first_shift_completed_at = completed[0] if completed else None
    profile.fifth_shift_completed_at = completed[4] if len(completed) >= 5 else None
    profile.save(
        update_fields=(
            "completed_shifts_count",
            "first_booked_at",
            "first_shift_completed_at",
            "fifth_shift_completed_at",
            "updated_at",
        )
    )
    return profile
