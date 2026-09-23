import json

from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt

from apps.employees.kafka import publish_event
from apps.employees.models import Address, Employeepersinfo


def index_page(request: HttpRequest):
    return render(request, "index.html")


def about_page(request: HttpRequest):
    return render(request, "about.html")


def employees_page(request: HttpRequest):
    return render(request, "employees.html", context={"data": Employeepersinfo.objects.select_related("address")})


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


def serialize_employee(employee: Employeepersinfo) -> dict:
    return {
        "id": employee.id,
        "surname": employee.surname,
        "firstname": employee.firstname,
        "patronymic": employee.patronymic,
        "personnel_number": employee.personnel_number,
        "gender": employee.gender,
        "address": serialize_address(employee.address),
        "created_at": employee.created_at,
        "updated_at": employee.updated_at,
    }


@csrf_exempt
def employees_api(request: HttpRequest):
    if request.method == "GET":
        employees = Employeepersinfo.objects.select_related("address").all()
        return JsonResponse({"items": [serialize_employee(employee) for employee in employees]})

    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON")

        employee = Employeepersinfo.objects.create(
            surname=payload.get("surname", ""),
            firstname=payload.get("firstname", ""),
            patronymic=payload.get("patronymic", ""),
            personnel_number=payload.get("personnel_number", ""),
            gender=payload.get("gender", ""),
        )
        publish_event("employee.created", serialize_employee(employee))
        return JsonResponse(serialize_employee(employee), status=201)

    return JsonResponse({"detail": "Method not allowed"}, status=405)


def employee_detail_api(request: HttpRequest, employee_id: int):
    employee = get_object_or_404(Employeepersinfo.objects.select_related("address"), id=employee_id)
    return JsonResponse(serialize_employee(employee))
