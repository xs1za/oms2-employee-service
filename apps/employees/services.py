from __future__ import annotations

from django.db import transaction
from datetime import timedelta

from django.utils import timezone

from apps.employees.events import (
    enqueue_identity_changed,
    enqueue_merge_events,
    enqueue_performer_profile_changed,
)
from apps.employees.funnel import recompute_funnel_stage, refresh_shift_statistics
from apps.employees.models import (
    PerformerEngagementTypeChange,
    PerformerProfile,
    PerformerShiftFact,
    PerformerWorkPeriod,
    Person,
    PersonAddress,
    PersonContact,
    PersonDocument,
    PersonIdentifier,
    PersonMerge,
    PersonNameHistory,
)


class MergeConflict(Exception):
    pass


def resolve_canonical_person(person: Person) -> Person:
    visited: set[int] = set()
    current = person
    while current.merged_into_id is not None:
        if current.pk in visited:
            raise MergeConflict("Person merge graph contains a cycle")
        visited.add(current.pk)
        current = Person.objects.get(pk=current.merged_into_id)
    return current


def _sync_address_primary_flags(person: Person) -> None:
    primary_address_id = (
        PersonAddress.objects.filter(
            person=person, address_type=PersonAddress.TYPE_WORK_SEARCH, is_primary=True
        )
        .values_list("address_id", flat=True)
        .first()
    )
    for person_address in PersonAddress.objects.filter(person=person).select_related("address"):
        expected = person_address.address_id == primary_address_id
        if person_address.address.main_address != expected:
            person_address.address.main_address = expected
            person_address.address.save(update_fields=("main_address",))


def _move_person_addresses(duplicate: Person, canonical: Person) -> None:
    canonical_registration = PersonAddress.objects.select_for_update().filter(
        person=canonical, address_type=PersonAddress.TYPE_REGISTRATION
    ).exists()
    canonical_primary_work = PersonAddress.objects.select_for_update().filter(
        person=canonical, address_type=PersonAddress.TYPE_WORK_SEARCH, is_primary=True
    ).exists()

    for person_address in PersonAddress.objects.select_for_update().filter(person=duplicate).order_by("id"):
        if person_address.address_type == PersonAddress.TYPE_REGISTRATION:
            if canonical_registration:
                person_address.delete()
                continue
            person_address.is_primary = False
            canonical_registration = True
        else:
            if person_address.is_primary and canonical_primary_work:
                person_address.is_primary = False
            elif person_address.is_primary:
                canonical_primary_work = True
        person_address.person = canonical
        person_address.save(update_fields=("person", "is_primary", "updated_at"))

    _sync_address_primary_flags(canonical)


def _move_shift_facts(duplicate: Person, canonical: Person) -> None:
    for fact in PerformerShiftFact.objects.select_for_update().filter(person=duplicate).order_by("id"):
        if PerformerShiftFact.objects.filter(
            person=canonical, assignment_id=fact.assignment_id, fact_type=fact.fact_type
        ).exists():
            fact.delete()
            continue
        fact.person = canonical
        fact.save(update_fields=("person",))


@transaction.atomic
def merge_people(
    duplicate_id: int,
    canonical_id: int,
    reason_code: str,
    merged_by_user_id: str | None,
) -> tuple[Person, PersonMerge, bool]:
    duplicate = Person.objects.select_for_update().get(pk=duplicate_id)
    canonical = Person.objects.select_for_update().get(pk=canonical_id)
    canonical = resolve_canonical_person(canonical)

    if duplicate.pk == canonical.pk:
        raise MergeConflict("A Person cannot be merged into itself")
    if duplicate.merged_into_id:
        previous = resolve_canonical_person(duplicate)
        if previous.pk == canonical.pk:
            return previous, duplicate.merge_record, False
        raise MergeConflict("Person is already merged into another canonical Person")

    duplicate_active_period = PerformerWorkPeriod.objects.filter(
        person=duplicate, status=PerformerWorkPeriod.STATUS_ACTIVE
    ).exists()
    canonical_active_period = PerformerWorkPeriod.objects.filter(
        person=canonical, status=PerformerWorkPeriod.STATUS_ACTIVE
    ).exists()
    if duplicate_active_period and canonical_active_period:
        raise MergeConflict("Both Persons have an active PerformerWorkPeriod")

    # Keep one active primary contact per kind. Duplicate contacts with the
    # same verified fingerprint remain as revoked audit rows on the alias.
    for contact in PersonContact.objects.select_for_update().filter(person=duplicate).order_by("id"):
        match = PersonContact.objects.filter(
            person=canonical,
            contact_type=contact.contact_type,
            lookup_hmac=contact.lookup_hmac,
            verification_status=PersonContact.VERIFICATION_VERIFIED,
            status=PersonContact.STATUS_ACTIVE,
        ).first()
        if match:
            contact.status = PersonContact.STATUS_REVOKED
            contact.revoked_at = timezone.now()
            contact.is_primary = False
            contact.save(update_fields=("status", "revoked_at", "is_primary"))
            continue
        if contact.is_primary and PersonContact.objects.filter(
            person=canonical, contact_type=contact.contact_type, is_primary=True, status=PersonContact.STATUS_ACTIVE
        ).exists():
            contact.is_primary = False
            contact.save(update_fields=("is_primary",))
        contact.person = canonical
        contact.save(update_fields=("person", "is_primary"))

    PerformerWorkPeriod.objects.filter(
        person=duplicate, status=PerformerWorkPeriod.STATUS_ACTIVE
    ).update(person=canonical, updated_at=timezone.now())

    _move_person_addresses(duplicate, canonical)

    for document in PersonDocument.objects.select_for_update().filter(person=duplicate).order_by("id"):
        matching = None
        if document.lookup_hmac and document.status == PersonDocument.STATUS_ACTIVE:
            matching = PersonDocument.objects.filter(
                person=canonical,
                document_type=document.document_type,
                issuing_country_code=document.issuing_country_code,
                lookup_hmac=document.lookup_hmac,
                verification_status=PersonDocument.VERIFY_VERIFIED,
                status=PersonDocument.STATUS_ACTIVE,
            ).first()
        if matching:
            document.status = PersonDocument.STATUS_REVOKED
            document.is_primary = False
            document.replaced_by_document = matching
            document.save(update_fields=("status", "is_primary", "replaced_by_document"))
        else:
            if document.is_primary and PersonDocument.objects.filter(
                person=canonical,
                document_type=PersonDocument.DOC_PASSPORT,
                is_primary=True,
                status=PersonDocument.STATUS_ACTIVE,
            ).exists():
                document.is_primary = False
            document.person = canonical
            document.save(update_fields=("person", "is_primary"))

    for identifier in PersonIdentifier.objects.select_for_update().filter(person=duplicate).order_by("id"):
        match = PersonIdentifier.objects.filter(
            person=canonical,
            country_code=identifier.country_code,
            identifier_type=identifier.identifier_type,
            lookup_hmac=identifier.lookup_hmac,
            verification_status=PersonIdentifier.VERIFY_VERIFIED,
            status=PersonIdentifier.STATUS_ACTIVE,
        ).first()
        if match:
            identifier.person = canonical
            identifier.status = PersonIdentifier.STATUS_MERGED
            identifier.merged_into_identifier = match
            identifier.save(update_fields=("person", "status", "merged_into_identifier"))
        else:
            identifier.person = canonical
            identifier.save(update_fields=("person",))

    _move_shift_facts(duplicate, canonical)

    name_history = PersonNameHistory.objects.select_for_update().filter(person=duplicate).order_by("valid_from", "id")
    next_version = (PersonNameHistory.objects.filter(person=canonical).order_by("-version").values_list("version", flat=True).first() or 0) + 1
    for entry in name_history:
        entry.person = canonical
        entry.version = next_version
        entry.save(update_fields=("person", "version"))
        next_version += 1

    # Keep one current name version after combining histories. Preserve all old
    # rows; only close overlapping current intervals at the next version start.
    current_names = list(PersonNameHistory.objects.select_for_update().filter(person=canonical, valid_to__isnull=True).order_by("-valid_from", "-id"))
    if current_names:
        latest_name = current_names[0]
        for older_name in current_names[1:]:
            older_name.valid_to = max(latest_name.valid_from, older_name.valid_from + timedelta(microseconds=1))
            older_name.save(update_fields=("valid_to",))
        canonical.surname = latest_name.surname
        canonical.firstname = latest_name.firstname
        canonical.patronymic = latest_name.patronymic
        canonical.name_version = max(canonical.name_version, latest_name.version)

    duplicate_profile = PerformerProfile.objects.select_for_update().filter(person=duplicate).first()
    canonical_profile = PerformerProfile.objects.select_for_update().filter(person=canonical).first()
    if duplicate_profile and canonical_profile:
        if duplicate_profile.engagement_type != canonical_profile.engagement_type:
            PerformerEngagementTypeChange.objects.create(
                person=canonical,
                previous_engagement_type=duplicate_profile.engagement_type,
                new_engagement_type=canonical_profile.engagement_type,
                previous_validation_status=duplicate_profile.engagement_type_validation_status,
                new_validation_status=canonical_profile.engagement_type_validation_status,
                change_source=PerformerEngagementTypeChange.SOURCE_MERGE,
                changed_by_user_id=merged_by_user_id,
                reason="Person merge",
            )
        duplicate_profile.status = PerformerProfile.STATUS_INACTIVE
        duplicate_profile.save(update_fields=("status", "updated_at"))
    elif duplicate_profile:
        duplicate_profile.person = canonical
        duplicate_profile.save(update_fields=("person", "updated_at"))
        canonical_profile = duplicate_profile

    if duplicate.identity_status == Person.IDENTITY_VERIFIED:
        canonical.identity_status = Person.IDENTITY_VERIFIED
    duplicate.identity_status = Person.IDENTITY_MERGED
    duplicate.merged_into = canonical
    canonical.name_version = max(canonical.name_version, duplicate.name_version, next_version - 1)
    canonical.save(update_fields=("identity_status", "name_version", "surname", "firstname", "patronymic"))
    duplicate.save(update_fields=("identity_status", "merged_into"))

    merge_record = PersonMerge.objects.create(
        duplicate_person=duplicate,
        canonical_person=canonical,
        reason_code=reason_code,
        merged_at=timezone.now(),
        merged_by_user_id=merged_by_user_id,
    )

    if canonical_profile is not None:
        refresh_shift_statistics(canonical, canonical_profile)
        recompute_funnel_stage(canonical.pk, enqueue=False)

    enqueue_identity_changed(canonical, merge_record.merged_at)
    enqueue_merge_events(duplicate, canonical, reason_code, merge_record.merged_at)
    if canonical_profile is not None:
        enqueue_performer_profile_changed(canonical)
    return canonical, merge_record, True
