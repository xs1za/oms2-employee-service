from __future__ import annotations

from datetime import timezone
import uuid

from django.db import transaction
from django.utils import timezone as django_timezone

from apps.employees.models import IntegrationOutbox, Person
from apps.employees.privacy import decrypt_value


ACCESS_TOPIC = "people.access.v1"
PERFORMER_TOPIC = "people.performer.v1"


def _enqueue(topic: str, person_id: int, event_type: str, payload: dict) -> IntegrationOutbox:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Person events must be enqueued inside the business transaction")

    person = Person.objects.select_for_update().get(pk=person_id)
    version_field = "access_event_version" if topic == ACCESS_TOPIC else "performer_event_version"
    version = getattr(person, version_field) + 1
    setattr(person, version_field, version)
    person.save(update_fields=(version_field,))

    occurred_at = django_timezone.now().astimezone(timezone.utc)
    aggregate_id = str(person.pk)
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "aggregate_type": "person",
        "aggregate_id": aggregate_id,
        "aggregate_version": version,
        "payload": payload,
    }
    return IntegrationOutbox.objects.create(
        event_id=envelope["event_id"],
        topic=topic,
        event_type=event_type,
        aggregate_type="person",
        aggregate_id=aggregate_id,
        aggregate_version=version,
        payload=envelope,
    )


def enqueue_identity_changed(person: Person, effective_at=None) -> IntegrationOutbox:
    return _enqueue(
        ACCESS_TOPIC,
        person.pk,
        "people.identity.changed",
        {
            "person_id": str(person.pk),
            "display_name": person.display_name,
            "name_version": person.name_version,
            "effective_at": (effective_at or django_timezone.now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def enqueue_employment_changed(
    person: Person,
    episode_id: int,
    employment_status: str,
    position_assignment_id: int | None,
    position_code: str | None,
    effective_at,
) -> IntegrationOutbox:
    return _enqueue(
        ACCESS_TOPIC,
        person.pk,
        "people.employment.changed",
        {
            "person_id": str(person.pk),
            "employment_episode_id": str(episode_id),
            "employment_status": employment_status,
            "position_assignment_id": str(position_assignment_id) if position_assignment_id else None,
            "position_code": position_code,
            "effective_at": effective_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def enqueue_membership_changed(person: Person, membership, status: str, effective_at) -> IntegrationOutbox:
    return _enqueue(
        ACCESS_TOPIC,
        person.pk,
        "people.external_membership.changed",
        {
            "person_id": str(person.pk),
            "membership_id": str(membership.pk),
            "organization_id": membership.organization_id,
            "membership_status": status,
            "effective_at": effective_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def enqueue_performer_profile_changed(person: Person) -> IntegrationOutbox:
    profile = getattr(person, "performer_profile", None)
    if profile is None:
        return None
    contacts = list(
        person.contacts.filter(status="active", verification_status="verified", is_primary=True)
    )
    email = phone = None
    for contact in contacts:
        value = decrypt_value(contact.encrypted_value)
        if contact.contact_type == "email":
            email = value
        elif contact.contact_type == "phone":
            phone = value
    return _enqueue(
        PERFORMER_TOPIC,
        person.pk,
        "people.performer_profile.changed",
        {
            "person_id": str(person.pk),
            "profile_status": profile.status,
            "display_name": person.display_name,
            "email": email,
            "phone": phone,
        },
    )


def enqueue_merge_events(duplicate: Person, canonical: Person, reason_code: str, merged_at) -> tuple[IntegrationOutbox, IntegrationOutbox]:
    payload = {
        "duplicate_person_id": str(duplicate.pk),
        "canonical_person_id": str(canonical.pk),
        "merged_at": merged_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason_code": reason_code,
    }
    access = _enqueue(ACCESS_TOPIC, duplicate.pk, "people.merged", payload)
    performer = _enqueue(PERFORMER_TOPIC, duplicate.pk, "people.merged", payload)
    return access, performer
