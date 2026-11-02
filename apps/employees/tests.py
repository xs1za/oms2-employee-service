import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.employees.models import (
    IntegrationOutbox,
    OperationsEventInbox,
    PerformerEngagementTypeChange,
    PerformerProfile,
    PerformerShiftFact,
    PerformerWorkPeriod,
    Person,
    PersonAddress,
    PersonDocument,
    PersonIdentifier,
    PersonMerge,
    PersonNameHistory,
)
from apps.employees.operations_consumer import process_event
from apps.employees.privacy import normalize_document_number


INN = "500100732259"
SNILS = "11223344595"


@override_settings(
    PERSON_DATA_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
    PERSON_IDENTIFIER_HMAC_KEY="unit-test-hmac-key-not-for-production",
)
class ApiTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.auth_patch = patch(
            "apps.employees.api.authorize",
            return_value=({"type": "user", "user_id": str(uuid4()), "roles": ["administrator"], "scopes": ["employees"]}, None),
        )
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

    def post_json(self, url: str, payload: dict, *, idempotency_key: str | None = None):
        headers = {"HTTP_IDEMPOTENCY_KEY": idempotency_key or str(uuid4())}
        return self.client.post(url, data=json.dumps(payload), content_type="application/json", **headers)

    def put_json(self, url: str, payload: dict):
        return self.client.put(url, data=json.dumps(payload), content_type="application/json")

    def get_json(self, url: str):
        return self.client.get(url)

    def create_person(self, surname: str = "Иванова", firstname: str = "Анна", phone: str | None = None) -> int:
        contacts = [] if phone is None else [{"type": "phone", "value": phone, "is_primary": True}]
        response = self.post_json(
            "/api/v1/people",
            {"surname": surname, "firstname": firstname, "patronymic": "Сергеевна", "gender": "F", "contacts": contacts},
        )
        self.assertEqual(response.status_code, 201, response.content)
        return int(response.json()["person_id"])

    def create_profile(
        self,
        person_id: int,
        engagement_type: str = "gph",
        *,
        profile_status: str = "active",
        medical_book_status: str | None = None,
        expected_status: int = 201,
    ):
        payload = {"profile_status": profile_status, "engagement_type": engagement_type, "change_reason": "Регистрация"}
        if medical_book_status is not None:
            payload["medical_book_status"] = medical_book_status
        response = self.put_json(f"/api/v1/people/{person_id}/performer-profile", payload)
        self.assertEqual(response.status_code, expected_status, response.content)
        return response

    def verify_engagement(self, person_id: int, decision: str = "verified", reason: str = "Проверено"):
        return self.post_json(
            f"/api/v1/people/{person_id}/performer-profile/engagement-type/verify",
            {"decision": decision, "reason": reason},
        )

    def create_document(self, person_id: int, number: str = "4510123456", *, is_primary: bool = True):
        return self.post_json(
            f"/api/v1/people/{person_id}/documents",
            {
                "document_type": "passport",
                "passport_kind": "national",
                "issuing_country_code": "RU",
                "document_number": number,
                "issued_at": "2021-01-01",
                "valid_from": "2021-01-01",
                "valid_to": "2031-01-01",
                "is_primary": is_primary,
            },
        )

    def verify_document(self, person_id: int, document_id: int, decision: str = "verified"):
        return self.post_json(
            f"/api/v1/people/{person_id}/documents/{document_id}/verify",
            {"decision": decision, "reason": "Проверено"},
        )

    def create_identifier(self, person_id: int, identifier_type: str, value: str):
        return self.post_json(
            f"/api/v1/people/{person_id}/identifiers",
            {"identifier_type": identifier_type, "country_code": "RU", "identifier_value": value},
        )

    def verify_identifier(self, person_id: int, identifier_id: int, decision: str = "verified"):
        return self.post_json(
            f"/api/v1/people/{person_id}/identifiers/{identifier_id}/verify",
            {"decision": decision, "verification_method": "manual_document", "reason": "Проверено"},
        )

    def add_ready_documents(self, person_id: int, *, verify: bool = True):
        passport = self.create_document(person_id)
        self.assertEqual(passport.status_code, 201, passport.content)
        inn = self.create_identifier(person_id, "inn", INN)
        self.assertEqual(inn.status_code, 201, inn.content)
        snils = self.create_identifier(person_id, "snils", SNILS)
        self.assertEqual(snils.status_code, 201, snils.content)
        if verify:
            self.assertEqual(self.verify_document(person_id, int(passport.json()["document_id"])).status_code, 200)
            self.assertEqual(self.verify_identifier(person_id, int(inn.json()["identifier_id"])).status_code, 200)
            self.assertEqual(self.verify_identifier(person_id, int(snils.json()["identifier_id"])).status_code, 200)
        return passport, inn, snils

    def create_work_period(self, person_id: int, engagement_type: str = "gph", personnel_number: str | None = None):
        starts_at = (timezone.now() - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        payload = {"starts_at": starts_at, "engagement_type": engagement_type, "reason": "Начало работы"}
        if personnel_number:
            payload["personnel_number"] = personnel_number
        return self.post_json(f"/api/v1/people/{person_id}/work-periods", payload)

    def stage(self, person_id: int) -> str:
        response = self.get_json(f"/api/v1/people/{person_id}/performer-profile")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["funnel_stage"]


class PerformerProfileApiTest(ApiTestCase):
    def test_profile_creation_returns_pending_and_profile_stage(self):
        person_id = self.create_person()
        response = self.create_profile(person_id, "gph")
        body = response.json()
        self.assertEqual(body["profile_status"], "active")
        self.assertEqual(body["engagement_type"], "gph")
        self.assertEqual(body["engagement_type_validation_status"], "pending")
        self.assertEqual(body["funnel_stage"], "PERFORMER_PROFILE")
        self.assertEqual(body["completed_shifts_count"], 0)
        self.assertEqual(body["work_periods"], [])
        self.assertEqual(body["addresses"], [])
        self.assertEqual(body["documents"], [])
        self.assertEqual(body["identifiers"], [])
        self.assertEqual(PerformerEngagementTypeChange.objects.filter(person_id=person_id).count(), 1)

        outbox = IntegrationOutbox.objects.filter(topic="people.performer.v1").latest("id")
        payload = outbox.payload["payload"]
        self.assertEqual(payload["engagement_type"], "gph")
        self.assertEqual(payload["funnel_stage"], "PERFORMER_PROFILE")
        self.assertNotIn("passport", json.dumps(payload))
        self.assertNotIn("addresses", payload)
        self.assertNotIn("producer", outbox.payload)

    def test_profile_update_rejects_service_managed_fields(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        response = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "funnel_stage": "PERFORMER_READY_FOOD"},
        )
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["detail"]["code"], "validation_error")
        response = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "completed_shifts_count": 5},
        )
        self.assertEqual(response.status_code, 422, response.content)

    def test_engagement_type_verification_and_conflict(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        verified = self.verify_engagement(person_id)
        self.assertEqual(verified.status_code, 200, verified.content)
        self.assertEqual(verified.json()["engagement_type_validation_status"], "verified")
        self.assertIsNotNone(verified.json()["engagement_type_validated_at"])
        repeated = self.verify_engagement(person_id)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        conflicting = self.verify_engagement(person_id, "rejected")
        self.assertEqual(conflicting.status_code, 409, conflicting.content)
        self.assertEqual(conflicting.json()["detail"]["code"], "engagement_type_already_validated")

    def test_rejected_type_cannot_be_repeated_and_type_change_creates_audit(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        rejected = self.verify_engagement(person_id, "rejected", "Нет договора")
        self.assertEqual(rejected.status_code, 200, rejected.content)
        self.assertEqual(rejected.json()["engagement_type_validation_status"], "rejected")
        self.assertEqual(rejected.json()["engagement_type_rejection_reason"], "Нет договора")

        same = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "engagement_type": "gph"},
        )
        self.assertEqual(same.status_code, 409, same.content)
        self.assertEqual(same.json()["detail"]["code"], "engagement_type_rejected")

        changed = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "engagement_type": "self_employed", "change_reason": "Смена типа"},
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["engagement_type"], "self_employed")
        self.assertEqual(changed.json()["engagement_type_validation_status"], "pending")
        self.assertIsNone(changed.json()["engagement_type_validated_at"])
        self.assertIsNone(changed.json()["engagement_type_rejection_reason"])
        self.assertEqual(PerformerEngagementTypeChange.objects.filter(person_id=person_id).count(), 2)
        latest = PerformerEngagementTypeChange.objects.filter(person_id=person_id).latest("id")
        self.assertEqual(latest.previous_engagement_type, "gph")
        self.assertEqual(latest.new_engagement_type, "self_employed")
        self.assertEqual(latest.change_source, "profile_update")

    def test_rejected_engagement_type_does_not_raise_stage_above_profile(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        passport = self.create_document(person_id)
        self.assertEqual(passport.status_code, 201, passport.content)
        self.assertEqual(self.stage(person_id), "PERFORMER_DOCS_PENDING")
        rejected = self.verify_engagement(person_id, "rejected", "Нет договора")
        self.assertEqual(rejected.status_code, 200, rejected.content)
        self.assertEqual(self.stage(person_id), "PERFORMER_PROFILE")

    def test_get_missing_profile_returns_not_found(self):
        person_id = self.create_person()
        response = self.get_json(f"/api/v1/people/{person_id}/performer-profile")
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.json()["detail"]["code"], "performer_profile_not_found")
        missing = self.get_json("/api/v1/people/999999/performer-profile")
        self.assertEqual(missing.status_code, 404, missing.content)
        self.assertEqual(missing.json()["detail"]["code"], "person_not_found")

    def test_profile_deactivation_and_reactivation(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        inactive = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "inactive"},
        )
        self.assertEqual(inactive.status_code, 200, inactive.content)
        self.assertEqual(inactive.json()["funnel_stage"], "PERFORMER_PROFILE_DELETED")
        active = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active"},
        )
        self.assertEqual(active.status_code, 200, active.content)
        self.assertEqual(active.json()["funnel_stage"], "PERFORMER_PROFILE")

    def test_only_stage_change_creates_extra_event(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        count = IntegrationOutbox.objects.filter(topic="people.performer.v1").count()
        self.put_json(f"/api/v1/people/{person_id}/performer-profile", {"profile_status": "active"})
        self.assertEqual(IntegrationOutbox.objects.filter(topic="people.performer.v1").count(), count)


class FunnelStageTest(ApiTestCase):
    def test_all_readiness_stages(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        self.assertEqual(self.stage(person_id), "PERFORMER_PROFILE")

        self.verify_engagement(person_id)
        self.assertEqual(self.stage(person_id), "PERFORMER_PROFILE")

        self.add_ready_documents(person_id, verify=False)
        self.assertEqual(self.stage(person_id), "PERFORMER_DOCS_PENDING")

        self.add_ready_documents(person_id, verify=True)
        self.assertEqual(self.stage(person_id), "PERFORMER_PROFILE")

        period = self.create_work_period(person_id, "gph")
        self.assertEqual(period.status_code, 201, period.content)
        self.assertEqual(self.stage(person_id), "PERFORMER_READY_NON_MEDICAL")

        non_food = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "medical_book_status": "non_food"},
        )
        self.assertEqual(non_food.status_code, 200, non_food.content)
        self.assertEqual(non_food.json()["funnel_stage"], "PERFORMER_READY_NON_FOOD")

        food = self.put_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "medical_book_status": "food"},
        )
        self.assertEqual(food.json()["funnel_stage"], "PERFORMER_READY_FOOD")

    def test_work_period_termination_recomputes_stage(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        self.verify_engagement(person_id)
        self.add_ready_documents(person_id, verify=True)
        period = self.create_work_period(person_id, "gph")
        self.assertEqual(self.stage(person_id), "PERFORMER_READY_NON_MEDICAL")

        terminate = self.post_json(
            f"/api/v1/work-periods/{period.json()['work_period_id']}/terminate",
            {"effective_at": (timezone.now() + timedelta(days=1)).isoformat().replace("+00:00", "Z"), "reason": "Завершение"},
        )
        self.assertEqual(terminate.status_code, 200, terminate.content)
        self.assertEqual(terminate.json()["funnel_stage"], "PERFORMER_PROFILE")


class WorkPeriodApiTest(ApiTestCase):
    def test_profile_is_required(self):
        person_id = self.create_person()
        response = self.create_work_period(person_id)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["detail"]["code"], "performer_profile_required")

    def test_engagement_type_must_match_profile(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        response = self.create_work_period(person_id, "self_employed")
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["detail"]["code"], "engagement_type_mismatch")

    def test_second_active_period_is_rejected(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        first = self.create_work_period(person_id, "gph", "QA-EMP-001")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["status"], "active")
        self.assertEqual(first.json()["source"] if "source" in first.json() else "hr", "hr")
        second = self.create_work_period(person_id, "gph", "QA-EMP-002")
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(second.json()["detail"]["code"], "active_work_period_exists")

    def test_starts_at_window_and_termination_conflict(self):
        person_id = self.create_person()
        self.create_profile(person_id, "gph")
        far = self.post_json(
            f"/api/v1/people/{person_id}/work-periods",
            {
                "starts_at": (timezone.now() + timedelta(days=120)).isoformat().replace("+00:00", "Z"),
                "engagement_type": "gph",
                "reason": "Начало работы",
            },
        )
        self.assertEqual(far.status_code, 422, far.content)

        period = self.create_work_period(person_id, "gph")
        period_id = period.json()["work_period_id"]
        effective = (timezone.now() + timedelta(days=10)).isoformat().replace("+00:00", "Z")
        first = self.post_json(f"/api/v1/work-periods/{period_id}/terminate", {"effective_at": effective, "reason": "Завершение"})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["status"], "terminated")
        repeated = self.post_json(f"/api/v1/work-periods/{period_id}/terminate", {"effective_at": effective, "reason": "Завершение"})
        self.assertEqual(repeated.status_code, 200, repeated.content)
        conflict = self.post_json(
            f"/api/v1/work-periods/{period_id}/terminate",
            {"effective_at": (timezone.now() + timedelta(days=20)).isoformat().replace("+00:00", "Z"), "reason": "Другое"},
        )
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(conflict.json()["detail"]["code"], "work_period_conflict")


class PersonAddressApiTest(ApiTestCase):
    def address_payload(self, address_type: str = "work_search", **overrides) -> dict:
        payload = {
            "address_type": address_type,
            "country_code": "RU",
            "region": "Москва",
            "city": "Москва",
            "street": "Тверская",
            "house": "17",
            "flat": "12",
            "postal_code": "125009",
            "lat": 55.771216,
            "lon": 37.57729,
        }
        payload.update(overrides)
        return payload

    def test_registration_address_is_unique_and_not_primary(self):
        person_id = self.create_person()
        first = self.post_json(f"/api/v1/people/{person_id}/addresses", self.address_payload("registration"))
        self.assertEqual(first.status_code, 201, first.content)
        self.assertFalse(first.json()["is_primary"])
        self.assertEqual(first.json()["country_code"], "RU")
        second = self.post_json(f"/api/v1/people/{person_id}/addresses", self.address_payload("registration"))
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(second.json()["detail"]["code"], "registration_address_exists")
        invalid = self.post_json(
            f"/api/v1/people/{person_id}/addresses",
            self.address_payload("registration", is_primary=True),
        )
        self.assertEqual(invalid.status_code, 422, invalid.content)
        self.assertEqual(invalid.json()["detail"]["code"], "invalid_address_type")

    def test_first_work_address_becomes_primary_and_priority_can_be_switched(self):
        person_id = self.create_person()
        first = self.post_json(f"/api/v1/people/{person_id}/addresses", self.address_payload("work_search"))
        self.assertEqual(first.status_code, 201, first.content)
        self.assertTrue(first.json()["is_primary"])
        second = self.post_json(
            f"/api/v1/people/{person_id}/addresses",
            self.address_payload("work_search", city="Санкт-Петербург", lat=59.93, lon=30.36),
        )
        self.assertEqual(second.status_code, 201, second.content)
        self.assertFalse(second.json()["is_primary"])
        conflict = self.post_json(
            f"/api/v1/people/{person_id}/addresses",
            self.address_payload("work_search", is_primary=True, city="Казань", lat=55.79, lon=49.12),
        )
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(conflict.json()["detail"]["code"], "primary_work_address_exists")

        switched = self.post_json(
            f"/api/v1/people/{person_id}/addresses/{second.json()['person_address_id']}/set-primary",
            {"is_primary": True},
        )
        self.assertEqual(switched.status_code, 200, switched.content)
        self.assertTrue(switched.json()["is_primary"])
        self.assertFalse(PersonAddress.objects.get(pk=int(first.json()["person_address_id"])).is_primary)

        listed = self.get_json(f"/api/v1/people/{person_id}/addresses")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertTrue(listed.json()[0]["is_primary"])

        registration = self.post_json(f"/api/v1/people/{person_id}/addresses", self.address_payload("registration"))
        invalid = self.post_json(
            f"/api/v1/people/{person_id}/addresses/{registration.json()['person_address_id']}/set-primary",
            {"is_primary": True},
        )
        self.assertEqual(invalid.status_code, 422, invalid.content)

    def test_addresses_are_listed_registration_primary_then_rest(self):
        person_id = self.create_person()
        work = self.post_json(f"/api/v1/people/{person_id}/addresses", self.address_payload("work_search"))
        registration = self.post_json(f"/api/v1/people/{person_id}/addresses", self.address_payload("registration"))
        listed = self.get_json(f"/api/v1/people/{person_id}/addresses")
        types = [item["address_type"] for item in listed.json()]
        self.assertEqual(types, ["registration", "work_search"])
        self.assertEqual(listed.json()[0]["person_address_id"], registration.json()["person_address_id"])
        self.assertEqual(listed.json()[1]["person_address_id"], work.json()["person_address_id"])


class OperationsConsumerTest(ApiTestCase):
    def envelope(self, person_id: int, event_type: str = "operations.shift.assigned", **payload_overrides) -> dict:
        payload = {
            "person_id": str(person_id),
            "performer_id": str(uuid4()),
            "shift_id": str(uuid4()),
            "assignment_id": str(uuid4()),
            "engagement_type": "gph",
        }
        payload.update(payload_overrides)
        return {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "schema_version": 1,
            "occurred_at": timezone.now().isoformat().replace("+00:00", "Z"),
            "producer": "oms5",
            "aggregate_type": "performer",
            "aggregate_id": str(person_id),
            "payload": payload,
        }

    def test_assigned_and_completed_update_funnel(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        assigned = self.envelope(person_id)
        self.assertEqual(process_event(assigned), "applied")
        self.assertEqual(self.stage(person_id), "PERFORMER_BOOKED_SHIFT")
        profile = PerformerProfile.objects.get(person_id=person_id)
        self.assertIsNotNone(profile.first_booked_at)
        self.assertEqual(OperationsEventInbox.objects.get(event_id=assigned["event_id"]).outcome, "applied")

        completed = self.envelope(person_id, "operations.shift.completed")
        self.assertEqual(process_event(completed), "applied")
        self.assertEqual(self.stage(person_id), "PERFORMER_FIRST_SHIFT_COMPLETED")
        profile.refresh_from_db()
        self.assertEqual(profile.completed_shifts_count, 1)
        self.assertIsNotNone(profile.first_shift_completed_at)

    def test_duplicate_event_id_and_assignment_do_not_increment_counter(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        completed = self.envelope(person_id, "operations.shift.completed")
        self.assertEqual(process_event(completed), "applied")
        self.assertEqual(process_event(completed), "duplicate")
        profile = PerformerProfile.objects.get(person_id=person_id)
        profile.refresh_from_db()
        self.assertEqual(profile.completed_shifts_count, 1)

        same_assignment = self.envelope(
            person_id,
            "operations.shift.completed",
            assignment_id=completed["payload"]["assignment_id"],
        )
        self.assertEqual(process_event(same_assignment), "applied")
        profile.refresh_from_db()
        self.assertEqual(profile.completed_shifts_count, 1)
        self.assertEqual(PerformerShiftFact.objects.filter(person_id=person_id, fact_type="completed").count(), 1)

    def test_fifth_completed_shift_stage(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        for _ in range(5):
            self.assertEqual(process_event(self.envelope(person_id, "operations.shift.completed")), "applied")
        profile = PerformerProfile.objects.get(person_id=person_id)
        profile.refresh_from_db()
        self.assertEqual(profile.completed_shifts_count, 5)
        self.assertEqual(profile.funnel_stage, "PERFORMER_FIFTH_SHIFT_COMPLETED")
        self.assertIsNotNone(profile.fifth_shift_completed_at)

    def test_invalid_unknown_and_missing_person_outcomes(self):
        missing_assignment = self.envelope(1)
        missing_assignment["payload"].pop("assignment_id")
        self.assertEqual(process_event(missing_assignment), "invalid")
        self.assertEqual(OperationsEventInbox.objects.get(event_id=missing_assignment["event_id"]).outcome, "invalid")

        unknown = self.envelope(1, "operations.shift.cancelled")
        self.assertEqual(process_event(unknown), "ignored")
        self.assertEqual(OperationsEventInbox.objects.get(event_id=unknown["event_id"]).outcome, "ignored")

        person_id = self.create_person()
        not_found = self.envelope(999999)
        self.assertEqual(process_event(not_found), "person_not_found")
        self.assertEqual(OperationsEventInbox.objects.get(event_id=not_found["event_id"]).outcome, "person_not_found")

    def test_invalid_json_does_not_write_inbox(self):
        from apps.employees.operations_consumer import handle_raw_message

        self.assertEqual(handle_raw_message(b"not-json"), "invalid")
        self.assertEqual(OperationsEventInbox.objects.count(), 0)


class MergeApiTest(ApiTestCase):
    def test_merge_moves_facts_and_sums_unique_completions(self):
        canonical_id = self.create_person("Иванова", "Анна")
        duplicate_id = self.create_person("Иванов", "Анна")
        self.create_profile(canonical_id, "gph")
        self.create_profile(duplicate_id, "self_employed")

        for person_id, count in ((canonical_id, 2), (duplicate_id, 3)):
            for _ in range(count):
                event = OperationsConsumerTest.envelope(self, person_id, "operations.shift.completed")
                self.assertEqual(process_event(event), "applied")
        self.assertEqual(PerformerProfile.objects.get(person_id=canonical_id).completed_shifts_count, 2)
        self.assertEqual(PerformerProfile.objects.get(person_id=duplicate_id).completed_shifts_count, 3)

        merged = self.post_json(
            f"/api/v1/people/{duplicate_id}/merge",
            {"canonical_person_id": str(canonical_id), "reason_code": "manual_review"},
        )
        self.assertEqual(merged.status_code, 200, merged.content)
        self.assertEqual(Person.objects.get(pk=duplicate_id).merged_into_id, canonical_id)
        profile = PerformerProfile.objects.get(person_id=canonical_id)
        self.assertEqual(profile.completed_shifts_count, 5)
        self.assertEqual(profile.funnel_stage, "PERFORMER_FIFTH_SHIFT_COMPLETED")
        self.assertEqual(PerformerProfile.objects.get(person_id=duplicate_id).status, "inactive")
        self.assertEqual(PerformerShiftFact.objects.filter(person_id=canonical_id).count(), 5)
        self.assertTrue(PersonMerge.objects.filter(duplicate_person_id=duplicate_id).exists())
        self.assertTrue(
            PerformerEngagementTypeChange.objects.filter(
                person_id=canonical_id, change_source="merge", previous_engagement_type="self_employed"
            ).exists()
        )
        self.assertTrue(
            IntegrationOutbox.objects.filter(topic="people.performer.v1", event_type="people.performer_profile.changed").exists()
        )
        self.assertTrue(
            IntegrationOutbox.objects.filter(topic="people.access.v1", event_type="people.merged").exists()
        )
        self.assertTrue(
            IntegrationOutbox.objects.filter(topic="people.performer.v1", event_type="people.merged").exists()
        )

        merged_profile = self.put_json(
            f"/api/v1/people/{duplicate_id}/performer-profile",
            {"profile_status": "active", "engagement_type": "gph"},
        )
        self.assertEqual(merged_profile.status_code, 409, merged_profile.content)
        self.assertEqual(merged_profile.json()["detail"]["code"], "person_already_merged")

        merged_address = self.post_json(
            f"/api/v1/people/{duplicate_id}/addresses",
            PersonAddressApiTest.address_payload(self, "registration"),
        )
        self.assertEqual(merged_address.status_code, 409, merged_address.content)
        self.assertEqual(merged_address.json()["detail"]["code"], "person_already_merged")


class EventContractTest(ApiTestCase):
    def test_employment_and_membership_events_are_not_published(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        event_types = set(IntegrationOutbox.objects.values_list("event_type", flat=True))
        self.assertNotIn("people.employment.changed", event_types)
        self.assertNotIn("people.external_membership.changed", event_types)
        self.assertEqual(
            set(IntegrationOutbox.objects.filter(topic="people.access.v1").values_list("event_type", flat=True)),
            {"people.identity.changed"},
        )

    def test_performer_event_has_no_documents_or_addresses(self):
        person_id = self.create_person()
        self.create_profile(person_id)
        self.add_ready_documents(person_id, verify=True)
        self.post_json(f"/api/v1/people/{person_id}/addresses", PersonAddressApiTest.address_payload(self, "registration"))
        payload = IntegrationOutbox.objects.filter(topic="people.performer.v1").latest("id").payload["payload"]
        for field in ("engagement_type", "engagement_type_validation_status", "medical_book_status", "funnel_stage", "completed_shifts_count"):
            self.assertIn(field, payload)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("4510123456", serialized)
        self.assertNotIn(INN, serialized)
        self.assertNotIn(SNILS, serialized)
        self.assertNotIn("Тверская", serialized)
        self.assertNotIn("addresses", payload)


class DocumentNormalizationTest(TestCase):
    def test_inn_and_snils_checksums(self):
        self.assertEqual(normalize_document_number("inn", "RU", "5001 0073 2259"), INN)
        self.assertEqual(normalize_document_number("snils", "RU", "112-233-445 95"), SNILS)
        with self.assertRaises(Exception):
            normalize_document_number("inn", "RU", "500100732250")
