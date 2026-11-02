from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from apps.employees.api import (
    change_legal_name,
    create_person,
    create_person_document,
    create_person_identifier,
    create_work_period,
    merge_person_api,
    performer_profile,
    person_addresses,
    replace_person_document,
    set_primary_address,
    terminate_work_period,
    verify_engagement_type,
    verify_person_document,
    verify_person_identifier,
)
from apps.employees.views import about_page, employee_detail_api, employees_api, employees_page, index_page
from apps.healthcheck.views import health, live, ready

urlpatterns = [
    path("health/", health),
    path("health/live/", live),
    path("health/ready/", ready),
    path("admin/", admin.site.urls),
    path("", index_page),
    path("about/", about_page),
    path("employees/", employees_page),
    path("api/employees/", employees_api),
    path("api/employees/<int:employee_id>/", employee_detail_api),
    path("api/v1/people", create_person),
    path("api/v1/people/<int:person_id>/performer-profile", performer_profile),
    path("api/v1/people/<int:person_id>/performer-profile/engagement-type/verify", verify_engagement_type),
    path("api/v1/people/<int:person_id>/work-periods", create_work_period),
    path("api/v1/work-periods/<int:work_period_id>/terminate", terminate_work_period),
    path("api/v1/people/<int:person_id>/addresses", person_addresses),
    path("api/v1/people/<int:person_id>/addresses/<int:address_id>/set-primary", set_primary_address),
    path("api/v1/people/<int:duplicate_person_id>/merge", merge_person_api),
    path("api/v1/people/<int:person_id>/documents", create_person_document),
    path("api/v1/people/<int:person_id>/documents/<int:document_id>/verify", verify_person_document),
    path("api/v1/people/<int:person_id>/documents/<int:document_id>/replace", replace_person_document),
    path("api/v1/people/<int:person_id>/identifiers", create_person_identifier),
    path("api/v1/people/<int:person_id>/identifiers/<int:identifier_id>/verify", verify_person_identifier),
    path("api/v1/people/<int:person_id>/legal-name-changes", change_legal_name),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
