from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import time
from uuid import UUID

from confluent_kafka import Consumer, KafkaError, TopicPartition
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone as django_timezone

from apps.employees.funnel import recompute_funnel_stage, refresh_shift_statistics
from apps.employees.models import (
    OperationsEventInbox,
    PerformerProfile,
    PerformerShiftFact,
    Person,
)
from apps.employees.services import resolve_canonical_person

logger = logging.getLogger(__name__)

TOPIC = "operations.performer.v1"
GROUP_ID = "oms2.operations.performer"
EVENT_ASSIGNED = "operations.shift.assigned"
EVENT_COMPLETED = "operations.shift.completed"
SUPPORTED_EVENTS = {EVENT_ASSIGNED, EVENT_COMPLETED}
VALID_ENGAGEMENT_TYPES = {
    PerformerProfile.ENGAGEMENT_GPH,
    PerformerProfile.ENGAGEMENT_SELF_EMPLOYED,
}
RETRY_DELAY_SECONDS = 5.0


def _log_invalid(event: dict, error_code: str) -> None:
    logger.warning(
        "operations_event_invalid",
        extra={
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "error_code": error_code,
        },
    )


def _uuid(value) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _decimal_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _occurred_at(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _record_inbox(event: dict, outcome: str, assignment_id: UUID | None, person_id: int | None) -> OperationsEventInbox:
    return OperationsEventInbox.objects.create(
        event_id=UUID(event["event_id"]),
        event_type=str(event.get("event_type") or "")[:80],
        person_id=person_id,
        assignment_id=assignment_id,
        outcome=outcome,
        payload=event,
        processed_at=django_timezone.now(),
    )


def process_event(event: dict) -> str:
    """Apply one operations.performer.v1 envelope.

    Returns the processing outcome. A database error is raised so that the
    consumer can retry the message without committing its offset.
    """
    event_id = _uuid(event.get("event_id"))
    if event_id is None:
        _log_invalid(event, "event_id")
        return OperationsEventInbox.OUTCOME_INVALID

    event_type = event.get("event_type")
    payload = event.get("payload")
    occurred_at = _occurred_at(event.get("occurred_at"))

    person_id = _decimal_id(payload.get("person_id")) if isinstance(payload, dict) else None
    assignment_id = _uuid(payload.get("assignment_id")) if isinstance(payload, dict) else None

    with transaction.atomic():
        if OperationsEventInbox.objects.select_for_update().filter(event_id=event_id).exists():
            return OperationsEventInbox.OUTCOME_DUPLICATE

        if not isinstance(event_type, str) or not isinstance(payload, dict) or occurred_at is None:
            _log_invalid(event, "envelope")
            _record_inbox(event, OperationsEventInbox.OUTCOME_INVALID, assignment_id, person_id)
            return OperationsEventInbox.OUTCOME_INVALID

        shift_id = _uuid(payload.get("shift_id"))
        performer_id = _uuid(payload.get("performer_id"))
        engagement_type = payload.get("engagement_type")
        if (
            person_id is None
            or assignment_id is None
            or shift_id is None
            or performer_id is None
            or engagement_type not in VALID_ENGAGEMENT_TYPES
        ):
            _log_invalid(event, "payload")
            _record_inbox(event, OperationsEventInbox.OUTCOME_INVALID, assignment_id, person_id)
            return OperationsEventInbox.OUTCOME_INVALID

        if event_type not in SUPPORTED_EVENTS:
            _record_inbox(event, OperationsEventInbox.OUTCOME_IGNORED, assignment_id, person_id)
            return OperationsEventInbox.OUTCOME_IGNORED

        person = Person.objects.select_for_update().filter(pk=person_id).first()
        if person is None:
            logger.info(
                "operations_event_person_not_found",
                extra={"event_id": str(event_id), "event_type": event_type},
            )
            _record_inbox(event, OperationsEventInbox.OUTCOME_PERSON_NOT_FOUND, assignment_id, person_id)
            return OperationsEventInbox.OUTCOME_PERSON_NOT_FOUND

        person = resolve_canonical_person(person)
        fact_type = (
            PerformerShiftFact.FACT_ASSIGNED
            if event_type == EVENT_ASSIGNED
            else PerformerShiftFact.FACT_COMPLETED
        )

        if not PerformerShiftFact.objects.filter(assignment_id=assignment_id, fact_type=fact_type).exists():
            PerformerShiftFact.objects.create(
                event_id=event_id,
                person=person,
                performer_id=performer_id,
                shift_id=shift_id,
                assignment_id=assignment_id,
                fact_type=fact_type,
                engagement_type=engagement_type,
                occurred_at=occurred_at,
            )

        profile = PerformerProfile.objects.select_for_update().filter(person=person).first()
        if profile is not None:
            refresh_shift_statistics(person, profile)
            recompute_funnel_stage(person.pk, enqueue=True)

        try:
            _record_inbox(event, OperationsEventInbox.OUTCOME_APPLIED, assignment_id, person_id)
        except IntegrityError:
            # A concurrent consumer inserted the same event_id first.
            return OperationsEventInbox.OUTCOME_DUPLICATE

    return OperationsEventInbox.OUTCOME_APPLIED


def handle_raw_message(raw: bytes | str) -> str:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        event = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("operations_event_invalid", extra={"error_code": "invalid_json"})
        return OperationsEventInbox.OUTCOME_INVALID
    if not isinstance(event, dict):
        logger.warning("operations_event_invalid", extra={"error_code": "envelope_not_object"})
        return OperationsEventInbox.OUTCOME_INVALID
    return process_event(event)


def run_consumer(*, once: bool = False, poll_interval: float = 1.0) -> int:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([TOPIC])
    logger.info("Started OMS2 operations performer consumer for %s", TOPIC)
    processed = 0
    try:
        while True:
            message = consumer.poll(max(0.1, poll_interval))
            if message is None:
                if once:
                    return processed
                continue
            if message.error():
                if message.error().code() != KafkaError._PARTITION_EOF:
                    logger.error("Kafka operations consumer error: %s", message.error())
                    time.sleep(max(0.1, poll_interval))
                continue
            try:
                handle_raw_message(message.value())
            except Exception:
                logger.exception(
                    "Failed to apply operations performer event; offset is not committed",
                    extra={"partition": message.partition(), "offset": message.offset()},
                )
                consumer.seek(TopicPartition(message.topic(), message.partition(), message.offset()))
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            consumer.commit(message=message, asynchronous=False)
            processed += 1
    finally:
        consumer.close()
