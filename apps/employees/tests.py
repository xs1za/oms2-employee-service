import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.employees.models import (
    EmploymentEpisode,
    IntegrationOutbox,
    Person,
    PersonDocument,
    PersonIdentifier,
    PersonMerge,
    PersonNameHistory,
)
from apps.employees.privacy import normalize_document_number


@override_settings(
    PERSON_DATA_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
    PERSON_IDENTIFIER_HMAC_KEY="unit-test-hmac-key-not-for-production",
)
class PersonApiTest(TestCase):
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

    def create_person(self, surname: str = "Иванова", firstname: str = "Анна", phone: str | None = None) -> int:
        contacts = [] if phone is None else [{"type": "phone", "value": phone, "is_primary": True}]
        response = self.post_json(
            "/api/v1/people",
            {"surname": surname, "firstname": firstname, "patronymic": "Сергеевна", "gender": "F", "contacts": contacts},
        )
        self.assertEqual(response.status_code, 201, response.content)
        return int(response.json()["person_id"])

    def test_idempotent_person_creation_and_possible_duplicate_candidates(self):
        key = str(uuid4())
        payload = {
            "surname": "Иванова",
            "firstname": "Анна",
            "patronymic": "Сергеевна",
            "gender": "F",
            "contacts": [{"type": "phone", "value": "+375291234567", "is_primary": True}],
        }
        first = self.post_json("/api/v1/people", payload, idempotency_key=key)
        second = self.post_json("/api/v1/people", payload, idempotency_key=key)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["person_id"], second.json()["person_id"])
        self.assertEqual(Person.objects.count(), 1)

        duplicate = self.post_json(
            "/api/v1/people",
            {
                "surname": "Иванова",
                "firstname": "Анна",
                "patronymic": "Сергеевна",
                "gender": "F",
                "contacts": [{"type": "phone", "value": "+375291234568", "is_primary": True}],
            },
        )
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.json()["identity_status"], "provisional")
        self.assertEqual(duplicate.json()["possible_duplicates"][0]["match_reasons"], ["normalized_name"])
        self.assertEqual(Person.objects.count(), 2)

    def test_document_replacement_and_second_foreign_passport_are_historical(self):
        person_id = self.create_person()
        first = self.post_json(
            f"/api/v1/people/{person_id}/documents",
            {
                "document_type": "passport",
                "passport_kind": "foreign",
                "issuing_country_code": "BY",
                "document_number": "MP1234567",
                "issued_at": "2024-01-01",
                "valid_from": "2024-01-01",
                "valid_to": "2034-01-01",
                "is_primary": True,
            },
        )
        self.assertEqual(first.status_code, 201, first.content)
        first_id = int(first.json()["document_id"])
        self.client.post(
            f"/api/v1/people/{person_id}/documents/{first_id}/verify",
            data=json.dumps({"decision": "verified", "reason": "HR checked"}),
            content_type="application/json",
        )

        second = self.post_json(
            f"/api/v1/people/{person_id}/documents",
            {
                "document_type": "passport",
                "passport_kind": "foreign",
                "issuing_country_code": "BY",
                "document_number": "MP7654321",
                "issued_at": "2025-01-01",
                "valid_from": "2025-01-01",
                "valid_to": "2035-01-01",
                "is_primary": False,
            },
        )
        self.assertEqual(second.status_code, 201, second.content)
        second_id = int(second.json()["document_id"])
        verify_second = self.client.post(
            f"/api/v1/people/{person_id}/documents/{second_id}/verify",
            data=json.dumps({"decision": "verified", "reason": "second valid passport"}),
            content_type="application/json",
        )
        self.assertEqual(verify_second.status_code, 200, verify_second.content)

        replacement = self.post_json(
            f"/api/v1/people/{person_id}/documents",
            {
                "document_type": "passport",
                "passport_kind": "foreign",
                "issuing_country_code": "BY",
                "document_number": "MP9999999",
                "issued_at": "2026-10-01",
                "valid_from": "2026-10-01",
                "valid_to": "2036-10-01",
                "is_primary": False,
            },
        )
        replacement_id = int(replacement.json()["document_id"])
        verified = self.client.post(
            f"/api/v1/people/{person_id}/documents/{replacement_id}/verify",
            data=json.dumps({"decision": "verified", "reason": "renewal"}),
            content_type="application/json",
        )
        self.assertEqual(verified.status_code, 200, verified.content)
        replaced = self.client.post(
            f"/api/v1/people/{person_id}/documents/{first_id}/replace",
            data=json.dumps({
                "replacement_document_id": str(replacement_id),
                "effective_at": "2026-10-01T00:00:00Z",
                "reason_code": "expired_by_age",
            }),
            content_type="application/json",
        )
        self.assertEqual(replaced.status_code, 200, replaced.content)
        self.assertEqual(PersonDocument.objects.filter(person_id=person_id, status=PersonDocument.STATUS_ACTIVE).count(), 2)
        self.assertEqual(PersonDocument.objects.get(pk=first_id).status, PersonDocument.STATUS_REPLACED)
        self.assertEqual(PersonDocument.objects.get(pk=second_id).status, PersonDocument.STATUS_ACTIVE)
        self.assertEqual(PersonDocument.objects.get(pk=replacement_id).status, PersonDocument.STATUS_ACTIVE)

    def test_verified_inn_match_merges_provisional_person_and_preserves_ids(self):
        canonical_id = self.create_person("Иванова", "Анна", "+375291234567")
        first = self.post_json(
            f"/api/v1/people/{canonical_id}/identifiers",
            {"identifier_type": "inn", "country_code": "RU", "identifier_value": "500100732259"},
        )
        self.assertEqual(first.status_code, 201, first.content)
        first_identifier_id = int(first.json()["identifier_id"])
        verified_first = self.client.post(
            f"/api/v1/people/{canonical_id}/identifiers/{first_identifier_id}/verify",
            data=json.dumps({"decision": "verified", "verification_method": "manual_document", "reason": "HR checked"}),
            content_type="application/json",
        )
        self.assertEqual(verified_first.status_code, 200, verified_first.content)

        duplicate_id = self.create_person("Иванова", "Анна", "+375291234568")
        second = self.post_json(
            f"/api/v1/people/{duplicate_id}/identifiers",
            {"identifier_type": "inn", "country_code": "RU", "identifier_value": "500100732259"},
        )
        second_identifier_id = int(second.json()["identifier_id"])
        verified_second = self.client.post(
            f"/api/v1/people/{duplicate_id}/identifiers/{second_identifier_id}/verify",
            data=json.dumps({"decision": "verified", "verification_method": "manual_document", "reason": "HR checked duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(verified_second.status_code, 200, verified_second.content)
        self.assertEqual(verified_second.json()["resolution"], "linked_existing_person")
        self.assertEqual(verified_second.json()["person_id"], str(canonical_id))
        self.assertEqual(Person.objects.get(pk=duplicate_id).merged_into_id, canonical_id)
        self.assertEqual(PersonMerge.objects.get(duplicate_person_id=duplicate_id).canonical_person_id, canonical_id)
        self.assertEqual(PersonIdentifier.objects.get(pk=second_identifier_id).status, PersonIdentifier.STATUS_MERGED)

    def test_name_change_keeps_person_and_history(self):
        person_id = self.create_person()
        certificate = self.post_json(
            f"/api/v1/people/{person_id}/documents",
            {
                "document_type": "name_change_certificate",
                "issuing_country_code": "RU",
                "document_number": "CERT-777",
                "issued_at": "2026-10-01",
                "valid_from": "2026-10-01",
                "valid_to": None,
                "is_primary": None,
            },
        )
        certificate_id = int(certificate.json()["document_id"])
        verify = self.client.post(
            f"/api/v1/people/{person_id}/documents/{certificate_id}/verify",
            data=json.dumps({"decision": "verified", "reason": "verified certificate"}),
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 200, verify.content)
        changed = self.post_json(
            f"/api/v1/people/{person_id}/legal-name-changes",
            {
                "surname": "Петрова",
                "firstname": "Анна",
                "patronymic": "Сергеевна",
                "effective_at": (timezone.now() + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "reason_code": "marriage",
                "source_document_id": str(certificate_id),
            },
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["person_id"], str(person_id))
        self.assertEqual(Person.objects.get(pk=person_id).surname, "Петрова")
        self.assertEqual(PersonNameHistory.objects.filter(person_id=person_id).count(), 2)

    def test_inn_and_snils_validation_and_no_age_requirement(self):
        self.assertEqual(normalize_document_number("inn", "RU", "5001 0073 2259"), "500100732259")
        self.assertEqual(normalize_document_number("snils", "RU", "112-233-445 95"), "11223344595")
        with self.assertRaises(Exception):
            normalize_document_number("inn", "RU", "500100732250")
        person_id = self.create_person("Ребенок", "Тест")
        self.assertEqual(Person.objects.get(pk=person_id).identity_status, Person.IDENTITY_PROVISIONAL)
        self.assertFalse(PersonIdentifier.objects.filter(person_id=person_id).exists())


    def test_employment_lifecycle_and_performer_profile_events(self):
        person_id = self.create_person()
        created = self.post_json(
            f"/api/v1/people/{person_id}/employment-periods",
            {"starts_at": "2026-10-01T00:00:00Z", "position_code": "operator_software"},
        )
        self.assertEqual(created.status_code, 201, created.content)
        episode_id = int(created.json()["employment_episode_id"])
        self.assertEqual(EmploymentEpisode.objects.filter(person_id=person_id, status="active").count(), 1)

        profile = self.post_json(
            f"/api/v1/people/{person_id}/performer-profile",
            {"profile_status": "active", "display_name": "Анна Сергеевна Иванова"},
        )
        self.assertEqual(profile.status_code, 201, profile.content)
        self.assertEqual(profile.json()["person_id"], str(person_id))

        terminated = self.post_json(
            f"/api/v1/employment-periods/{episode_id}/terminate",
            {"effective_at": "2026-10-20T00:00:00Z", "reason": "dismissal"},
        )
        self.assertEqual(terminated.status_code, 200, terminated.content)
        self.assertEqual(EmploymentEpisode.objects.get(pk=episode_id).status, "terminated")

        topics = list(IntegrationOutbox.objects.order_by("id").values_list("topic", flat=True))
        self.assertIn("people.access.v1", topics)
        self.assertIn("people.performer.v1", topics)


if __name__ == "__main__":
    import unittest

    unittest.main()
