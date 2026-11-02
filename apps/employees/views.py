import json

from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from apps.employees.api import authorize, _error
from apps.employees.events import enqueue_employment_changed, enqueue_identity_changed
from apps.employees.models import Address, EmploymentEpisode, Person, PersonNameHistory


def index_page(request: HttpRequest):
    return render(request, "index.html")


def about_page(request: HttpRequest):
    return render(request, "about.html")


def employees_page(request: HttpRequest):
    _, error = authorize(request, administrator=True)
    if error:
        return error
    return render(request, "employees.html", context={"data": Person.objects.select_related("address")})


def serialize_address(address: Address | None) -> dict | None:
    if address is None:
        return None
    return {
        "id": address.id,
        "full_address": address.full_address,
        "postal_code": address.postal_code,
        "country": address.country,
        "region": address.region,
        "city": address.city,
        "street": address.street,
        "house": address.house,
    }


def serialize_employee(person: Person) -> dict:
    return {
        "id": person.id,
        "person_id": str(person.pk),
        "surname": person.surname,
        "firstname": person.firstname,
        "patronymic": person.patronymic,
        "personnel_number": person.personnel_number,
        "gender": person.gender,
        "identity_status": person.identity_status,
        "address": serialize_address(person.address),
        "created_at": person.created_at,
        "updated_at": person.updated_at,
    }


def _legacy_person_payload(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Invalid JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


@csrf_exempt
def employees_api(request: HttpRequest):
    principal, error = authorize(request, scope="employees")
    if error:
        return error
    if request.method == "GET":
        if principal.get("type") == "user" and "administrator" not in set(principal.get("roles") or []):
            return _error(403, "administrator_required", "Administrator role is required to read employee PII")
        people = Person.objects.select_related("address").filter(merged_into__isnull=True)
        return JsonResponse({"items": [serialize_employee(person) for person in people]})

    if request.method == "POST":
        try:
            payload = _legacy_person_payload(request)
            surname = payload.get("surname", "")
            firstname = payload.get("firstname", "")
            patronymic = payload.get("patronymic", "")
            personnel_number = payload.get("personnel_number", "")
            gender = payload.get("gender", "U")
            if not isinstance(surname, str) or not 1 <= len(surname.strip()) <= 50:
                return _error(422, "validation_error", "surname must contain 1-50 characters")
            if not isinstance(firstname, str) or not 1 <= len(firstname.strip()) <= 50:
                return _error(422, "validation_error", "firstname must contain 1-50 characters")
            if not isinstance(patronymic, str) or len(patronymic) > 50:
                return _error(422, "validation_error", "patronymic must contain at most 50 characters")
            if not isinstance(personnel_number, str) or len(personnel_number) > 250:
                return _error(422, "validation_error", "personnel_number must contain at most 250 characters")
            if gender not in ("M", "F", "U"):
                return _error(422, "validation_error", "gender must be M, F or U")
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        from django.db import transaction
        from django.utils import timezone

        with transaction.atomic():
            now = timezone.now()
            person = Person.objects.create(
                surname=surname.strip(), firstname=firstname.strip(), patronymic=patronymic.strip(),
                gender=gender, identity_status=Person.IDENTITY_PROVISIONAL,
            )
            episode = EmploymentEpisode.objects.create(
                person=person, personnel_number=personnel_number.strip() or None,
                status=EmploymentEpisode.STATUS_ACTIVE, starts_at=now, source="legacy_employee_api",
            )
            PersonNameHistory.objects.create(
                person=person, surname=person.surname, firstname=person.firstname, patronymic=person.patronymic,
                valid_from=now, version=1, change_reason="legacy_import", changed_by_user_id=principal.get("user_id"),
            )
            enqueue_identity_changed(person, now)
            enqueue_employment_changed(person, episode.id, "active", None, None, now)
        return JsonResponse(serialize_employee(person), status=201)

    return JsonResponse({"detail": "Method not allowed"}, status=405)


def employee_detail_api(request: HttpRequest, employee_id: int):
    principal, error = authorize(request, scope="employees")
    if error:
        return error
    if principal.get("type") == "user" and "administrator" not in set(principal.get("roles") or []):
        return _error(403, "administrator_required", "Administrator role is required to read employee PII")
    person = get_object_or_404(Person.objects.select_related("address"), id=employee_id)
    if person.merged_into_id:
        person = person.merged_into
    result = serialize_employee(person)
    result["requested_person_id"] = str(employee_id)
    return JsonResponse(result)
