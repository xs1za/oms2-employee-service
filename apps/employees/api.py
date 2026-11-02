from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import re
from functools import wraps
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone as django_timezone
from django.views.decorators.csrf import csrf_exempt

from apps.employees.auth import authorize
from apps.employees.events import (
    enqueue_employment_changed,
    enqueue_identity_changed,
    enqueue_membership_changed,
    enqueue_performer_profile_changed,
)
from apps.employees.models import (
    ApiIdempotencyRecord,
    EmploymentEpisode,
    ExternalMembership,
    Person,
    PersonContact,
    PersonDocument,
    PersonIdentifier,
    PersonMerge,
    PersonNameHistory,
    PerformerProfile,
    PositionAssignment,
)
from apps.employees.privacy import encrypt_value, lookup_hmac, normalize_document_number, normalize_email, normalize_phone
from apps.employees.services import MergeConflict, merge_people, resolve_canonical_person


class ApiProblem(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _error(status_code: int, code: str, message: str) -> JsonResponse:
    return JsonResponse({"detail": {"code": code, "message": message}}, status=status_code)


def _handle(problem: ApiProblem) -> JsonResponse:
    return _error(problem.status_code, problem.code, problem.message)


def api_endpoint(function):
    @wraps(function)
    @csrf_exempt
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ApiProblem as problem:
            return _handle(problem)

    return wrapped


def _body(request: HttpRequest) -> dict:
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiProblem(400, "invalid_json", "Request body must be valid JSON")
    if not isinstance(value, dict):
        raise ApiProblem(422, "validation_error", "JSON body must be an object")
    return value


def _required_string(payload: dict, field: str, maximum: int, *, minimum: int = 1) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ApiProblem(422, "validation_error", f"{field} must be a string")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ApiProblem(422, "validation_error", f"{field} length must be between {minimum} and {maximum}")
    return value


def _date_time(value, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ApiProblem(422, "validation_error", f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiProblem(422, "validation_error", f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ApiProblem(422, "validation_error", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value, field: str, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ApiProblem(422, "validation_error", f"{field} must be a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiProblem(422, "validation_error", f"{field} must be a YYYY-MM-DD date") from exc


def _person_id(value) -> int:
    if not isinstance(value, (str, int)) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ApiProblem(422, "validation_error", "person_id must be a positive decimal BIGINT string")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ApiProblem(422, "validation_error", "person_id is invalid") from exc


def _idempotent(request: HttpRequest, scope: str, payload: dict, operation) -> JsonResponse:
    raw_key = request.headers.get("Idempotency-Key", "")
    try:
        key = UUID(raw_key)
    except (ValueError, AttributeError) as exc:
        raise ApiProblem(422, "idempotency_key_required", "Idempotency-Key must be a UUID") from exc
    body_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    try:
        with transaction.atomic():
            previous = ApiIdempotencyRecord.objects.select_for_update().filter(key=key, scope=scope).first()
            if previous:
                if previous.request_hash != body_hash:
                    raise ApiProblem(409, "idempotency_key_reused", "Idempotency-Key was already used with a different request")
                return JsonResponse(previous.response_body, status=previous.response_status, json_dumps_params={"ensure_ascii": False})
            response_body, response_status = operation()
            ApiIdempotencyRecord.objects.create(
                key=key,
                scope=scope,
                request_hash=body_hash,
                response_status=response_status,
                response_body=response_body,
            )
        return JsonResponse(response_body, status=response_status, json_dumps_params={"ensure_ascii": False})
    except IntegrityError:
        previous = ApiIdempotencyRecord.objects.filter(key=key, scope=scope).first()
        if previous and previous.request_hash == body_hash:
            return JsonResponse(previous.response_body, status=previous.response_status, json_dumps_params={"ensure_ascii": False})
        if previous:
            return _error(409, "idempotency_key_reused", "Idempotency-Key was already used with a different request")
        raise


def _person_or_404(person_id: int) -> Person:
    try:
        person = Person.objects.get(pk=person_id)
    except Person.DoesNotExist as exc:
        raise ApiProblem(404, "person_not_found", "Person was not found") from exc
    return resolve_canonical_person(person)


def _contact_payloads(person: Person, contacts: object) -> list[tuple[str, str]]:
    if contacts is None:
        return []
    if not isinstance(contacts, list):
        raise ApiProblem(422, "validation_error", "contacts must be an array")
    seen: set[tuple[str, str]] = set()
    primary_types: set[str] = set()
    rows = []
    for item in contacts:
        if not isinstance(item, dict):
            raise ApiProblem(422, "validation_error", "Each contacts item must be an object")
        contact_type = item.get("type")
        if contact_type not in (PersonContact.TYPE_PHONE, PersonContact.TYPE_EMAIL):
            raise ApiProblem(422, "validation_error", "contacts[].type must be phone or email")
        raw_value = _required_string(item, "value", 254)
        try:
            normalized = normalize_phone(raw_value) if contact_type == PersonContact.TYPE_PHONE else normalize_email(raw_value)
        except Exception as exc:
            raise ApiProblem(422, "invalid_contact", str(exc)) from exc
        is_primary = item.get("is_primary")
        if not isinstance(is_primary, bool):
            raise ApiProblem(422, "validation_error", "contacts[].is_primary must be boolean")
        if is_primary and contact_type in primary_types:
            raise ApiProblem(422, "validation_error", "Only one contact per type may be primary")
        primary_types.add(contact_type) if is_primary else None
        fingerprint = lookup_hmac(f"contact:{contact_type}", normalized)
        if (contact_type, fingerprint) in seen:
            raise ApiProblem(422, "duplicate_contact", "Duplicate contact in request")
        seen.add((contact_type, fingerprint))
        rows.append((contact_type, normalized, fingerprint, is_primary))

    fingerprints = []
    for contact_type, normalized, fingerprint, is_primary in rows:
        PersonContact.objects.create(
            person=person,
            contact_type=contact_type,
            encrypted_value=encrypt_value(normalized),
            lookup_hmac=fingerprint,
            is_primary=is_primary,
            verification_status=PersonContact.VERIFICATION_PENDING,
            status=PersonContact.STATUS_ACTIVE,
        )
        fingerprints.append((contact_type, fingerprint))
    return fingerprints


def _possible_duplicate_candidates(
    person: Person,
    contact_fingerprints: list[tuple[str, str]],
) -> list[dict]:
    candidate_reasons: dict[int, set[str]] = {}
    name_candidates = Person.objects.filter(
        merged_into__isnull=True,
        surname__iexact=person.surname.strip(),
        firstname__iexact=person.firstname.strip(),
    ).exclude(pk=person.pk)
    if person.patronymic.strip():
        name_candidates = name_candidates.filter(patronymic__iexact=person.patronymic.strip())
    for candidate_id in name_candidates.values_list("id", flat=True):
        candidate_reasons.setdefault(candidate_id, set()).add("normalized_name")

    for contact_type, fingerprint in contact_fingerprints:
        person_ids = PersonContact.objects.filter(
            contact_type=contact_type,
            lookup_hmac=fingerprint,
            status=PersonContact.STATUS_ACTIVE,
        ).exclude(person_id=person.pk).values_list("person_id", flat=True)
        for candidate_id in person_ids:
            canonical = Person.objects.filter(pk=candidate_id).values_list("merged_into_id", flat=True).first()
            canonical_id = canonical or candidate_id
            candidate_reasons.setdefault(canonical_id, set()).add(f"matching_{contact_type}")

    return [
        {"person_id": str(candidate_id), "match_reasons": sorted(reasons)}
        for candidate_id, reasons in sorted(candidate_reasons.items())
    ]


@api_endpoint
def create_person(request: HttpRequest) -> JsonResponse:
    principal, error = authorize(request, scope="employees")
    if error:
        return error
    payload = _body(request)
    surname = _required_string(payload, "surname", 50)
    firstname = _required_string(payload, "firstname", 50)
    patronymic = payload.get("patronymic", "")
    if patronymic is not None and (not isinstance(patronymic, str) or len(patronymic.strip()) > 50):
        raise ApiProblem(422, "validation_error", "patronymic must be null or a string up to 50 characters")
    gender = payload.get("gender")
    if gender not in ("M", "F", "U"):
        raise ApiProblem(422, "validation_error", "gender must be M, F or U")
    contacts = payload.get("contacts", [])

    def operation():
        person = Person.objects.create(
            surname=surname,
            firstname=firstname,
            patronymic=(patronymic or "").strip(),
            gender=gender,
            identity_status=Person.IDENTITY_PROVISIONAL,
            name_version=1,
        )
        contact_fingerprints = _contact_payloads(person, contacts)
        PersonNameHistory.objects.create(
            person=person,
            surname=person.surname,
            firstname=person.firstname,
            patronymic=person.patronymic,
            valid_from=person.created_at or django_timezone.now(),
            valid_to=None,
            version=1,
            change_reason="initial",
            changed_by_user_id=principal.get("user_id"),
        )
        enqueue_identity_changed(person)
        return ({
            "person_id": str(person.pk),
            "identity_status": person.identity_status,
            "created_at": _iso(person.created_at),
            "possible_duplicates": _possible_duplicate_candidates(person, contact_fingerprints),
        }, 201)

    return _idempotent(request, "people:create", payload, operation)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


@api_endpoint
def create_employment_period(request: HttpRequest, person_id: int) -> JsonResponse:
    _, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    position_code = payload.get("position_code")
    if position_code != "operator_software":
        raise ApiProblem(422, "invalid_position_code", "position_code must be operator_software")
    personnel_number = payload.get("personnel_number")
    if personnel_number is not None and (not isinstance(personnel_number, str) or len(personnel_number) > 250):
        raise ApiProblem(422, "validation_error", "personnel_number must be null or a string up to 250 characters")
    starts_at = _date_time(payload.get("starts_at"), "starts_at")

    def operation():
        try:
            person = Person.objects.select_for_update().get(pk=person_id)
        except Person.DoesNotExist as exc:
            raise ApiProblem(404, "person_not_found", "Person was not found") from exc
        person = resolve_canonical_person(person)
        if person.merged_into_id:
            raise ApiProblem(409, "person_already_merged", "Employment must reference the canonical Person")
        if EmploymentEpisode.objects.filter(person=person, status=EmploymentEpisode.STATUS_ACTIVE).exists():
            raise ApiProblem(409, "active_employment_exists", "Person already has an active employment episode")
        episode = EmploymentEpisode.objects.create(
            person=person,
            personnel_number=personnel_number or None,
            status=EmploymentEpisode.STATUS_ACTIVE,
            starts_at=starts_at,
            source="hr",
        )
        assignment = PositionAssignment.objects.create(
            employment_episode=episode,
            position_code=position_code,
            status=PositionAssignment.STATUS_ACTIVE,
            starts_at=starts_at,
        )
        enqueue_employment_changed(person, episode.pk, episode.status, assignment.pk, assignment.position_code, starts_at)
        return ({
            "employment_episode_id": str(episode.pk), "person_id": str(person.pk),
            "employment_status": episode.status, "position_assignment_id": str(assignment.pk),
            "position_code": assignment.position_code, "starts_at": _iso(episode.starts_at), "ends_at": None,
        }, 201)

    return _idempotent(request, f"person:{person_id}:employment:create", payload, operation)


@api_endpoint
def terminate_employment(request: HttpRequest, employment_episode_id: int) -> JsonResponse:
    _, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    effective_at = _date_time(payload.get("effective_at"), "effective_at")
    reason = _required_string(payload, "reason", 255)
    idem_payload = {**payload, "effective_at": _iso(effective_at)}

    def operation():
        try:
            episode = EmploymentEpisode.objects.select_for_update().select_related("person").get(pk=employment_episode_id)
        except EmploymentEpisode.DoesNotExist as exc:
            raise ApiProblem(404, "employment_episode_not_found", "Employment episode was not found") from exc
        if episode.status == EmploymentEpisode.STATUS_TERMINATED:
            if episode.ends_at == effective_at and episode.termination_reason == reason:
                return ({
                    "employment_episode_id": str(episode.pk), "person_id": str(episode.person_id),
                    "employment_status": episode.status, "ended_at": _iso(episode.ends_at),
                    "position_assignment_id": str(episode.positions.order_by("-starts_at").values_list("id", flat=True).first()) if episode.positions.exists() else None,
                }, 200)
            raise ApiProblem(409, "employment_period_conflict", "Employment episode is already terminated at a different time")
        if effective_at <= episode.starts_at:
            raise ApiProblem(422, "invalid_period", "effective_at must be later than starts_at")
        episode.status = EmploymentEpisode.STATUS_TERMINATED
        episode.ends_at = effective_at
        episode.termination_reason = reason
        episode.save(update_fields=("status", "ends_at", "termination_reason", "updated_at"))
        assignments = episode.positions.filter(status=PositionAssignment.STATUS_ACTIVE).order_by("-starts_at")
        assignment = assignments.first()
        if assignment:
            assignments.update(status=PositionAssignment.STATUS_ENDED, ends_at=effective_at, updated_at=django_timezone.now())
        enqueue_employment_changed(
            episode.person, episode.pk, episode.status, assignment.pk if assignment else None,
            assignment.position_code if assignment else None, effective_at,
        )
        return ({
            "employment_episode_id": str(episode.pk), "person_id": str(episode.person_id),
            "employment_status": episode.status, "ended_at": _iso(effective_at),
            "position_assignment_id": str(assignment.pk) if assignment else None,
        }, 200)

    return _idempotent(request, f"employment:{employment_episode_id}:terminate", idem_payload, operation)


@api_endpoint
def create_external_membership(request: HttpRequest, person_id: int) -> JsonResponse:
    _, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    try:
        organization_id = str(UUID(payload.get("organization_id", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApiProblem(422, "validation_error", "organization_id must be a UUID") from exc
    valid_from = _date_time(payload.get("valid_from"), "valid_from")

    def operation():
        try:
            person = Person.objects.select_for_update().get(pk=person_id)
        except Person.DoesNotExist as exc:
            raise ApiProblem(404, "person_not_found", "Person was not found") from exc
        person = resolve_canonical_person(person)
        if ExternalMembership.objects.filter(person=person, organization_id=organization_id, status=ExternalMembership.STATUS_ACTIVE).exists():
            raise ApiProblem(409, "external_membership_already_active", "Person already has active membership in this organization")
        membership = ExternalMembership.objects.create(
            person=person, organization_id=organization_id, status=ExternalMembership.STATUS_ACTIVE, valid_from=valid_from
        )
        enqueue_membership_changed(person, membership, membership.status, valid_from)
        return ({
            "membership_id": str(membership.pk), "person_id": str(person.pk), "organization_id": organization_id,
            "membership_status": membership.status, "valid_from": _iso(membership.valid_from), "valid_to": None,
        }, 201)

    return _idempotent(request, f"person:{person_id}:external-membership:create", payload, operation)


@api_endpoint
def revoke_external_membership(request: HttpRequest, membership_id: int) -> JsonResponse:
    _, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    effective_at = _date_time(payload.get("effective_at"), "effective_at")
    reason = _required_string(payload, "reason", 255)
    idem_payload = {**payload, "effective_at": _iso(effective_at)}

    def operation():
        try:
            membership = ExternalMembership.objects.select_for_update().select_related("person").get(pk=membership_id)
        except ExternalMembership.DoesNotExist as exc:
            raise ApiProblem(404, "external_membership_not_found", "External membership was not found") from exc
        if membership.status == ExternalMembership.STATUS_REVOKED:
            if membership.valid_to == effective_at and membership.revoked_reason == reason:
                return _membership_response(membership), 200
            raise ApiProblem(409, "external_membership_conflict", "Membership was already revoked with different data")
        if effective_at <= membership.valid_from:
            raise ApiProblem(422, "invalid_period", "effective_at must be later than valid_from")
        membership.status = ExternalMembership.STATUS_REVOKED
        membership.valid_to = effective_at
        membership.revoked_reason = reason
        membership.revoked_at = django_timezone.now()
        membership.save(update_fields=("status", "valid_to", "revoked_reason", "revoked_at"))
        enqueue_membership_changed(membership.person, membership, membership.status, effective_at)
        return _membership_response(membership), 200

    return _idempotent(request, f"membership:{membership_id}:revoke", idem_payload, operation)


def _membership_response(membership: ExternalMembership) -> dict:
    return {
        "membership_id": str(membership.pk), "person_id": str(membership.person_id),
        "organization_id": membership.organization_id, "membership_status": membership.status,
        "valid_from": _iso(membership.valid_from), "valid_to": _iso(membership.valid_to),
    }


@api_endpoint
def set_performer_profile(request: HttpRequest, person_id: int) -> JsonResponse:
    _, error = authorize(request, scope="employees")
    if error:
        return error
    payload = _body(request)
    profile_status = payload.get("profile_status")
    if profile_status not in (PerformerProfile.STATUS_ACTIVE, PerformerProfile.STATUS_INACTIVE):
        raise ApiProblem(422, "validation_error", "profile_status must be active or inactive")
    with transaction.atomic():
        try:
            person = Person.objects.select_for_update().get(pk=person_id)
        except Person.DoesNotExist as exc:
            raise ApiProblem(404, "person_not_found", "Person was not found") from exc
        person = resolve_canonical_person(person)
        if person.merged_into_id:
            raise ApiProblem(409, "person_already_merged", "Performer profile must reference canonical Person")
        profile, created = PerformerProfile.objects.get_or_create(person=person, defaults={"status": profile_status})
        changed = created or profile.status != profile_status
        if not created and changed:
            profile.status = profile_status
            profile.save(update_fields=("status", "updated_at"))
        if changed:
            enqueue_performer_profile_changed(person)
    return JsonResponse(_performer_response(person, profile), status=201 if created else 200, json_dumps_params={"ensure_ascii": False})


@api_endpoint
def create_person_document(request: HttpRequest, person_id: int) -> JsonResponse:
    principal, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    document_type = payload.get("document_type")
    if document_type not in (PersonDocument.DOC_PASSPORT, PersonDocument.DOC_NAME_CHANGE):
        raise ApiProblem(422, "invalid_document", "document_type must be passport or name_change_certificate")
    country_code = payload.get("issuing_country_code")
    if not isinstance(country_code, str) or not re.fullmatch(r"[A-Za-z]{2}", country_code):
        raise ApiProblem(422, "invalid_document", "issuing_country_code must be ISO 3166-1 alpha-2")
    country_code = country_code.upper()
    passport_kind = payload.get("passport_kind")
    if document_type == PersonDocument.DOC_PASSPORT:
        if passport_kind not in ("national", "foreign", "other"):
            raise ApiProblem(422, "invalid_document", "passport_kind must be national, foreign or other")
    elif passport_kind is not None:
        raise ApiProblem(422, "invalid_document", "passport_kind must be null for name_change_certificate")
    raw_number = _required_string(payload, "document_number", 64)
    issued_at = _date(payload.get("issued_at"), "issued_at")
    valid_from = _date(payload.get("valid_from"), "valid_from", nullable=True)
    valid_to = _date(payload.get("valid_to"), "valid_to", nullable=True)
    if document_type == PersonDocument.DOC_PASSPORT and valid_from is None:
        raise ApiProblem(422, "invalid_document", "valid_from is required for passport")
    if valid_to and valid_from and valid_to < valid_from:
        raise ApiProblem(422, "invalid_document", "valid_to must not precede valid_from")
    is_primary = payload.get("is_primary", False)
    if document_type == PersonDocument.DOC_PASSPORT:
        if not isinstance(is_primary, bool):
            raise ApiProblem(422, "invalid_document", "is_primary must be boolean for passport")
    elif is_primary not in (None, False):
        raise ApiProblem(422, "invalid_document", "is_primary is only valid for passport")

    try:
        normalized = normalize_document_number(document_type, country_code, raw_number)
    except Exception as exc:
        raise ApiProblem(422, "invalid_document", str(exc)) from exc
    fingerprint = lookup_hmac(f"document:{country_code}:{document_type}", normalized) if document_type == PersonDocument.DOC_PASSPORT else None

    def operation():
        person = _person_or_404(person_id)
        document = PersonDocument.objects.create(
            person=person,
            document_type=document_type,
            passport_kind=passport_kind,
            issuing_country_code=country_code,
            encrypted_number=encrypt_value(normalized),
            lookup_hmac=fingerprint,
            verification_status=PersonDocument.VERIFY_PENDING,
            status=PersonDocument.STATUS_PENDING,
            issued_at=issued_at,
            valid_from=valid_from,
            valid_to=valid_to,
            is_primary=is_primary if document_type == PersonDocument.DOC_PASSPORT else False,
        )
        return (_document_response(document), 201)

    return _idempotent(request, f"person:{person_id}:document:create", payload, operation)


def _document_response(document: PersonDocument) -> dict:
    return {
        "document_id": str(document.pk),
        "person_id": str(document.person_id),
        "document_type": document.document_type,
        "passport_kind": document.passport_kind,
        "issuing_country_code": document.issuing_country_code,
        "verification_status": document.verification_status,
        "status": document.status,
        "issued_at": document.issued_at.isoformat(),
        "valid_from": document.valid_from.isoformat() if document.valid_from else None,
        "valid_to": document.valid_to.isoformat() if document.valid_to else None,
        "is_primary": document.is_primary if document.document_type == PersonDocument.DOC_PASSPORT else None,
    }


@api_endpoint
def verify_person_document(request: HttpRequest, person_id: int, document_id: int) -> JsonResponse:
    principal, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    decision = payload.get("decision")
    if decision not in (PersonDocument.VERIFY_VERIFIED, PersonDocument.VERIFY_REJECTED):
        raise ApiProblem(422, "invalid_document", "decision must be verified or rejected")
    reason = _required_string(payload, "reason", 255)
    with transaction.atomic():
        person = _person_or_404(person_id)
        try:
            document = PersonDocument.objects.select_for_update().get(pk=document_id, person=person)
        except PersonDocument.DoesNotExist as exc:
            raise ApiProblem(404, "person_document_not_found", "Document was not found") from exc
        if document.verification_status == decision:
            return JsonResponse(_document_verification_response(document, principal), status=200)
        if document.verification_status != PersonDocument.VERIFY_PENDING:
            raise ApiProblem(409, "document_not_verified", "Document has already received a verification decision")
        if decision == PersonDocument.VERIFY_VERIFIED and document.document_type == PersonDocument.DOC_PASSPORT:
            duplicate = PersonDocument.objects.filter(
                document_type=PersonDocument.DOC_PASSPORT,
                issuing_country_code=document.issuing_country_code,
                lookup_hmac=document.lookup_hmac,
                verification_status=PersonDocument.VERIFY_VERIFIED,
                status=PersonDocument.STATUS_ACTIVE,
            ).exclude(person=person).first()
            if duplicate:
                raise ApiProblem(409, "document_identity_conflict", "Passport identifier requires manual identity review")
            if document.is_primary:
                PersonDocument.objects.filter(
                    person=person,
                    document_type=PersonDocument.DOC_PASSPORT,
                    is_primary=True,
                    verification_status=PersonDocument.VERIFY_VERIFIED,
                    status=PersonDocument.STATUS_ACTIVE,
                ).exclude(pk=document.pk).update(is_primary=False)
        now = django_timezone.now()
        document.verification_status = decision
        document.status = PersonDocument.STATUS_ACTIVE if decision == PersonDocument.VERIFY_VERIFIED else PersonDocument.STATUS_REJECTED
        document.verified_at = now
        document.verified_by_user_id = principal.get("user_id")
        document.save(update_fields=("verification_status", "status", "verified_at", "verified_by_user_id", "updated_at"))
        if (
            decision == PersonDocument.VERIFY_VERIFIED
            and document.document_type == PersonDocument.DOC_PASSPORT
            and person.identity_status == Person.IDENTITY_PROVISIONAL
        ):
            person.identity_status = Person.IDENTITY_VERIFIED
            person.save(update_fields=("identity_status",))
        return JsonResponse(_document_verification_response(document, principal), status=200)


def _document_verification_response(document: PersonDocument, principal: dict) -> dict:
    return {
        "document_id": str(document.pk),
        "verification_status": document.verification_status,
        "status": document.status,
        "verified_at": _iso(document.verified_at),
        "verified_by_user_id": principal.get("user_id"),
    }


@api_endpoint
def create_person_identifier(request: HttpRequest, person_id: int) -> JsonResponse:
    _, error = authorize(request, scope="employees")
    if error:
        return error
    payload = _body(request)
    identifier_type = payload.get("identifier_type")
    if identifier_type not in (PersonIdentifier.TYPE_INN, PersonIdentifier.TYPE_SNILS):
        raise ApiProblem(422, "invalid_identifier", "identifier_type must be inn or snils")
    country_code = payload.get("country_code")
    if country_code != "RU":
        raise ApiProblem(422, "invalid_identifier", "country_code must be RU for INN/SNILS in this API version")
    raw_value = _required_string(payload, "identifier_value", 32)
    try:
        normalized = normalize_document_number(identifier_type, country_code, raw_value)
    except Exception as exc:
        raise ApiProblem(422, "invalid_identifier", str(exc)) from exc
    fingerprint = lookup_hmac(f"identifier:{country_code}:{identifier_type}", normalized)

    def operation():
        person = _person_or_404(person_id)
        identifier = PersonIdentifier.objects.create(
            person=person,
            identifier_type=identifier_type,
            country_code=country_code,
            encrypted_value=encrypt_value(normalized),
            lookup_hmac=fingerprint,
            verification_status=PersonIdentifier.VERIFY_PENDING,
            status=PersonIdentifier.STATUS_ACTIVE,
        )
        return (_identifier_response(identifier), 201)

    return _idempotent(request, f"person:{person_id}:identifier:create", payload, operation)


def _identifier_response(identifier: PersonIdentifier) -> dict:
    return {
        "identifier_id": str(identifier.pk),
        "person_id": str(identifier.person_id),
        "identifier_type": identifier.identifier_type,
        "country_code": identifier.country_code,
        "verification_status": identifier.verification_status,
        "status": identifier.status,
        "created_at": _iso(identifier.created_at),
    }


@api_endpoint
def verify_person_identifier(request: HttpRequest, person_id: int, identifier_id: int) -> JsonResponse:
    principal, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    decision = payload.get("decision")
    if decision not in (PersonIdentifier.VERIFY_VERIFIED, PersonIdentifier.VERIFY_REJECTED):
        raise ApiProblem(422, "invalid_identifier", "decision must be verified or rejected")
    verification_method = payload.get("verification_method")
    if verification_method not in ("official_source", "manual_document"):
        raise ApiProblem(422, "invalid_identifier", "verification_method must be official_source or manual_document")
    reason = _required_string(payload, "reason", 255)

    with transaction.atomic():
        submitted_person = _person_or_404(person_id)
        try:
            identifier = PersonIdentifier.objects.select_for_update().get(pk=identifier_id, person=submitted_person)
        except PersonIdentifier.DoesNotExist as exc:
            raise ApiProblem(404, "person_identifier_not_found", "Identifier was not found") from exc
        if identifier.verification_status != PersonIdentifier.VERIFY_PENDING:
            if identifier.verification_status == decision:
                return JsonResponse(_identifier_verification_response(identifier, submitted_person, principal, "new_identifier", None), status=200)
            raise ApiProblem(409, "identifier_identity_conflict", "Identifier already has a different verification decision")

        now = django_timezone.now()
        if decision == PersonIdentifier.VERIFY_REJECTED:
            identifier.verification_status = PersonIdentifier.VERIFY_REJECTED
            identifier.status = PersonIdentifier.STATUS_REVOKED
            identifier.verified_at = now
            identifier.verified_by_user_id = principal.get("user_id")
            identifier.verification_source = verification_method
            identifier.revoked_at = now
            identifier.save(update_fields=("verification_status", "status", "verified_at", "verified_by_user_id", "verification_source", "revoked_at"))
            return JsonResponse(_identifier_verification_response(identifier, submitted_person, principal, "new_identifier", None), status=200)

        match = PersonIdentifier.objects.select_for_update().filter(
            identifier_type=identifier.identifier_type,
            country_code=identifier.country_code,
            lookup_hmac=identifier.lookup_hmac,
            verification_status=PersonIdentifier.VERIFY_VERIFIED,
            status=PersonIdentifier.STATUS_ACTIVE,
        ).exclude(person=submitted_person).first()
        if match:
            canonical = resolve_canonical_person(match.person)
            other_submitted = PersonIdentifier.objects.filter(
                person=submitted_person,
                status=PersonIdentifier.STATUS_ACTIVE,
                verification_status=PersonIdentifier.VERIFY_VERIFIED,
            ).exclude(pk=identifier.pk)
            for other_identifier in other_submitted:
                conflicting = PersonIdentifier.objects.filter(
                    identifier_type=other_identifier.identifier_type,
                    country_code=other_identifier.country_code,
                    verification_status=PersonIdentifier.VERIFY_VERIFIED,
                    status=PersonIdentifier.STATUS_ACTIVE,
                    lookup_hmac=other_identifier.lookup_hmac,
                ).exclude(person__in=(submitted_person, canonical)).exists()
                if conflicting:
                    raise ApiProblem(409, "identifier_identity_conflict", "Verified identifiers point to conflicting people")

            identifier.verification_status = PersonIdentifier.VERIFY_VERIFIED
            identifier.status = PersonIdentifier.STATUS_MERGED
            identifier.merged_into_identifier = match
            identifier.verified_at = now
            identifier.verified_by_user_id = principal.get("user_id")
            identifier.verification_source = verification_method
            identifier.save(update_fields=("verification_status", "status", "merged_into_identifier", "verified_at", "verified_by_user_id", "verification_source"))
            try:
                canonical, merge_record, created = merge_people(
                    submitted_person.pk, canonical.pk, "verified_identifier", principal.get("user_id")
                )
            except MergeConflict as exc:
                raise ApiProblem(409, "identifier_identity_conflict", str(exc)) from exc
            return JsonResponse(_identifier_verification_response(identifier, canonical, principal, "linked_existing_person", submitted_person.pk), status=200)

        identifier.verification_status = PersonIdentifier.VERIFY_VERIFIED
        identifier.verified_at = now
        identifier.verified_by_user_id = principal.get("user_id")
        identifier.verification_source = verification_method
        identifier.save(update_fields=("verification_status", "verified_at", "verified_by_user_id", "verification_source"))
        if submitted_person.identity_status == Person.IDENTITY_PROVISIONAL:
            submitted_person.identity_status = Person.IDENTITY_VERIFIED
            submitted_person.save(update_fields=("identity_status",))
        return JsonResponse(_identifier_verification_response(identifier, submitted_person, principal, "new_identifier", None), status=200)


def _identifier_verification_response(
    identifier: PersonIdentifier,
    canonical_person: Person,
    principal: dict,
    resolution: str,
    merged_from_person_id: int | None,
) -> dict:
    return {
        "identifier_id": str(identifier.pk),
        "person_id": str(canonical_person.pk),
        "identifier_type": identifier.identifier_type,
        "country_code": identifier.country_code,
        "verification_status": identifier.verification_status,
        "verified_at": _iso(identifier.verified_at),
        "verified_by_user_id": principal.get("user_id"),
        "resolution": resolution,
        "merged_from_person_id": str(merged_from_person_id) if merged_from_person_id else None,
    }


@api_endpoint
def replace_person_document(request: HttpRequest, person_id: int, document_id: int) -> JsonResponse:
    _, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    replacement_id = _person_id(payload.get("replacement_document_id"))
    effective_at = _date_time(payload.get("effective_at"), "effective_at")
    reason_code = payload.get("reason_code")
    if reason_code not in ("expired_by_age", "damaged", "lost", "other"):
        raise ApiProblem(422, "invalid_document", "reason_code is invalid")

    with transaction.atomic():
        person = _person_or_404(person_id)
        try:
            old_doc = PersonDocument.objects.select_for_update().get(pk=document_id, person=person, document_type=PersonDocument.DOC_PASSPORT)
            new_doc = PersonDocument.objects.select_for_update().get(pk=replacement_id, person=person, document_type=PersonDocument.DOC_PASSPORT)
        except PersonDocument.DoesNotExist as exc:
            raise ApiProblem(404, "person_document_not_found", "Passport was not found") from exc
        if old_doc.status != PersonDocument.STATUS_ACTIVE or old_doc.verification_status != PersonDocument.VERIFY_VERIFIED:
            raise ApiProblem(409, "replacement_document_invalid", "Only an active verified passport can be replaced")
        if new_doc.status != PersonDocument.STATUS_ACTIVE or new_doc.verification_status != PersonDocument.VERIFY_VERIFIED:
            raise ApiProblem(409, "replacement_document_invalid", "Replacement passport must be verified and active")
        if old_doc.pk == new_doc.pk or effective_at.date() < old_doc.valid_from:
            raise ApiProblem(422, "replacement_document_invalid", "Replacement passport or effective date is invalid")
        was_primary = old_doc.is_primary
        old_doc.status = PersonDocument.STATUS_REPLACED
        old_doc.valid_to = effective_at.date()
        old_doc.replaced_by_document = new_doc
        old_doc.is_primary = False
        old_doc.save(update_fields=("status", "valid_to", "replaced_by_document", "is_primary", "updated_at"))
        if was_primary:
            new_doc.is_primary = True
            new_doc.save(update_fields=("is_primary", "updated_at"))
        response_body = {
            "old_document_id": str(old_doc.pk), "replacement_document_id": str(new_doc.pk),
            "old_status": old_doc.status, "new_status": new_doc.status, "effective_at": _iso(effective_at),
        }
        return JsonResponse(response_body, status=200)


@api_endpoint
def change_legal_name(request: HttpRequest, person_id: int) -> JsonResponse:
    principal, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    surname = _required_string(payload, "surname", 50)
    firstname = _required_string(payload, "firstname", 50)
    patronymic = payload.get("patronymic")
    if patronymic is not None and (not isinstance(patronymic, str) or len(patronymic.strip()) > 50):
        raise ApiProblem(422, "validation_error", "patronymic must be null or a string up to 50 characters")
    effective_at = _date_time(payload.get("effective_at"), "effective_at")
    reason_code = payload.get("reason_code")
    allowed_reasons = ("marriage", "divorce", "court_order", "correction", "other")
    if reason_code not in allowed_reasons:
        raise ApiProblem(422, "validation_error", "reason_code is invalid")
    source_document_id = payload.get("source_document_id")
    if source_document_id is not None:
        source_document_id = _person_id(source_document_id)
    if reason_code in ("marriage", "divorce", "court_order") and source_document_id is None:
        raise ApiProblem(422, "source_document_required", "A verified name-change document is required")

    def operation():
        person = _person_or_404(person_id)
        source_document = None
        if source_document_id:
            try:
                source_document = PersonDocument.objects.get(
                    pk=source_document_id,
                    person=person,
                    document_type=PersonDocument.DOC_NAME_CHANGE,
                    verification_status=PersonDocument.VERIFY_VERIFIED,
                    status=PersonDocument.STATUS_ACTIVE,
                )
            except PersonDocument.DoesNotExist as exc:
                raise ApiProblem(404, "person_document_not_found", "Verified name-change document was not found") from exc
        current = PersonNameHistory.objects.select_for_update().filter(person=person, valid_to__isnull=True).order_by("-version").first()
        if current and effective_at <= current.valid_from:
            raise ApiProblem(409, "name_history_conflict", "Name change effective_at must follow the current name version")
        if (person.surname, person.firstname, person.patronymic) == (surname, firstname, (patronymic or "").strip()):
            return ({
                "person_id": str(person.pk), "surname": person.surname, "firstname": person.firstname,
                "patronymic": person.patronymic or None, "effective_at": _iso(effective_at), "name_version": person.name_version,
            }, 200)
        if current:
            current.valid_to = effective_at
            current.save(update_fields=("valid_to",))
        version = person.name_version + 1
        PersonNameHistory.objects.create(
            person=person, surname=surname, firstname=firstname, patronymic=(patronymic or "").strip(),
            valid_from=effective_at, valid_to=None, version=version, change_reason=reason_code,
            source_document=source_document, changed_by_user_id=principal.get("user_id"),
        )
        person.surname = surname
        person.firstname = firstname
        person.patronymic = (patronymic or "").strip()
        person.name_version = version
        person.save(update_fields=("surname", "firstname", "patronymic", "name_version", "updated_at"))
        enqueue_identity_changed(person, effective_at)
        enqueue_performer_profile_changed(person)
        return ({
            "person_id": str(person.pk), "surname": person.surname, "firstname": person.firstname,
            "patronymic": person.patronymic or None, "effective_at": _iso(effective_at), "name_version": version,
        }, 200)

    return _idempotent(request, f"person:{person_id}:legal-name-change", payload, operation)


def _primary_contact(person: Person, contact_type: str) -> str | None:
    contact = person.contacts.filter(
        contact_type=contact_type,
        is_primary=True,
        status=PersonContact.STATUS_ACTIVE,
        verification_status=PersonContact.VERIFICATION_VERIFIED,
    ).first()
    return decrypt_value(contact.encrypted_value) if contact else None


def _performer_response(person: Person, profile: PerformerProfile) -> dict:
    return {
        "person_id": str(person.pk), "profile_status": profile.status, "display_name": person.display_name,
        "email": _primary_contact(person, PersonContact.TYPE_EMAIL), "phone": _primary_contact(person, PersonContact.TYPE_PHONE),
    }


def _merge_response(duplicate: Person, canonical: Person, merged_at: datetime) -> dict:
    return {
        "duplicate_person_id": str(duplicate.pk), "canonical_person_id": str(canonical.pk),
        "merged_at": _iso(merged_at), "merge_status": "merged",
    }


@api_endpoint
def merge_person_api(request: HttpRequest, duplicate_person_id: int) -> JsonResponse:
    principal, error = authorize(request, administrator=True)
    if error:
        return error
    payload = _body(request)
    canonical_id = _person_id(payload.get("canonical_person_id"))
    reason_code = payload.get("reason_code")
    if reason_code not in ("verified_identifier", "manual_review", "data_correction"):
        raise ApiProblem(422, "validation_error", "reason_code is invalid")

    def operation():
        try:
            duplicate_person = Person.objects.select_for_update().get(pk=duplicate_person_id)
        except Person.DoesNotExist as exc:
            raise ApiProblem(404, "person_not_found", "Duplicate Person was not found") from exc
        canonical_person = _person_or_404(canonical_id)
        if duplicate_person.merged_into_id:
            existing_canonical = resolve_canonical_person(duplicate_person)
            if existing_canonical.pk == canonical_person.pk:
                merge_record = duplicate_person.merge_record
                return (_merge_response(duplicate_person, existing_canonical, merge_record.merged_at), 200)
            raise ApiProblem(409, "person_already_merged", "Duplicate Person is already linked to another canonical Person")
        if reason_code == "verified_identifier":
            identifier_match = PersonIdentifier.objects.filter(
                person=duplicate_person,
                verification_status=PersonIdentifier.VERIFY_VERIFIED,
                status=PersonIdentifier.STATUS_ACTIVE,
            ).filter(
                lookup_hmac__in=PersonIdentifier.objects.filter(
                    person=canonical_person,
                    verification_status=PersonIdentifier.VERIFY_VERIFIED,
                    status=PersonIdentifier.STATUS_ACTIVE,
                ).values("lookup_hmac")
            ).exists()
            passport_match = PersonDocument.objects.filter(
                person=duplicate_person,
                document_type=PersonDocument.DOC_PASSPORT,
                verification_status=PersonDocument.VERIFY_VERIFIED,
                status=PersonDocument.STATUS_ACTIVE,
            ).filter(
                lookup_hmac__in=PersonDocument.objects.filter(
                    person=canonical_person,
                    document_type=PersonDocument.DOC_PASSPORT,
                    verification_status=PersonDocument.VERIFY_VERIFIED,
                    status=PersonDocument.STATUS_ACTIVE,
                ).values("lookup_hmac")
            ).exists()
            if not (identifier_match or passport_match):
                raise ApiProblem(409, "person_merge_conflict", "verified_identifier reason requires an exact verified identifier match")
        try:
            canonical, merge_record, created = merge_people(
                duplicate_person_id, canonical_id, reason_code, principal.get("user_id")
            )
        except Person.DoesNotExist as exc:
            raise ApiProblem(404, "person_not_found", "Person was not found") from exc
        except MergeConflict as exc:
            raise ApiProblem(409, "person_merge_conflict", str(exc)) from exc
        return (_merge_response(Person.objects.get(pk=duplicate_person_id), canonical, merge_record.merged_at), 200 if not created else 200)

    return _idempotent(request, f"person:{duplicate_person_id}:merge", payload, operation)
