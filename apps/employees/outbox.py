from __future__ import annotations

import json
import logging
import time

from confluent_kafka import Producer
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from apps.employees.models import IntegrationOutbox
from django.conf import settings

logger = logging.getLogger(__name__)


def dispatch_outbox_once(batch_size: int = 100) -> int:
    """Publish the next unacknowledged event for each aggregate stream.

    The database lock is kept until Kafka acknowledges the message and the
    outbox row is marked published. A crashed publisher can resend the same
    event_id; consumers must treat delivery as at-least-once.
    """
    prior_pending = IntegrationOutbox.objects.filter(
        topic=OuterRef("topic"),
        aggregate_id=OuterRef("aggregate_id"),
        aggregate_version__lt=OuterRef("aggregate_version"),
        published_at__isnull=True,
    )
    candidate_ids = list(
        IntegrationOutbox.objects.filter(published_at__isnull=True)
        .annotate(has_prior=Exists(prior_pending))
        .filter(has_prior=False)
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    if not candidate_ids:
        return 0

    producer = Producer({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS, "enable.idempotence": True, "acks": "all"})
    published = 0
    for event_id in candidate_ids:
        delivery_errors: list[str] = []
        try:
            with transaction.atomic():
                event = IntegrationOutbox.objects.select_for_update(skip_locked=True).get(pk=event_id)
                if event.published_at is not None:
                    continue
                if IntegrationOutbox.objects.filter(
                    topic=event.topic,
                    aggregate_id=event.aggregate_id,
                    aggregate_version__lt=event.aggregate_version,
                    published_at__isnull=True,
                ).exists():
                    continue

                producer.produce(
                    event.topic,
                    key=event.aggregate_id.encode("utf-8"),
                    value=json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    on_delivery=lambda error, message: delivery_errors.append(str(error)) if error else None,
                )
                remaining = producer.flush(10)
                if remaining or delivery_errors:
                    raise RuntimeError(delivery_errors[0] if delivery_errors else "Kafka delivery timeout")
                event.published_at = timezone.now()
                event.last_error = ""
                event.save(update_fields=("published_at", "last_error"))
                published += 1
        except IntegrationOutbox.DoesNotExist:
            continue
        except Exception as exc:  # keep the outbox row pending for retry
            IntegrationOutbox.objects.filter(pk=event_id, published_at__isnull=True).update(
                attempts=F("attempts") + 1,
                last_error=type(exc).__name__[:500],
            )
            logger.error("Failed publishing OMS2 outbox event %s (%s)", event_id, type(exc).__name__)
    return published


def run_outbox_worker(poll_interval: float = 1.0) -> None:
    while True:
        try:
            if dispatch_outbox_once() == 0:
                time.sleep(poll_interval)
        except Exception:
            logger.exception("OMS2 outbox dispatcher iteration failed")
            time.sleep(poll_interval)
