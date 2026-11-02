from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from apps.employees.api import (
    change_legal_name,
    create_employment_period,
    create_external_membership,
    create_person,
    create_person_document,
    create_person_identifier,
    merge_person_api,
    replace_person_document,
    revoke_external_membership,
    set_performer_profile,
    terminate_employment,
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
    path("api/v1/people/<int:person_id>/employment-periods", create_employment_period),
    path("api/v1/employment-periods/<int:employment_episode_id>/terminate", terminate_employment),
    path("api/v1/people/<int:person_id>/external-memberships", create_external_membership),
    path("api/v1/external-memberships/<int:membership_id>/revoke", revoke_external_membership),
    path("api/v1/people/<int:person_id>/performer-profile", set_performer_profile),
    path("api/v1/people/<int:duplicate_person_id>/merge", merge_person_api),
    path("api/v1/people/<int:person_id>/documents", create_person_document),
    path("api/v1/people/<int:person_id>/documents/<int:document_id>/verify", verify_person_document),
    path("api/v1/people/<int:person_id>/documents/<int:document_id>/replace", replace_person_document),
    path("api/v1/people/<int:person_id>/identifiers", create_person_identifier),
    path("api/v1/people/<int:person_id>/identifiers/<int:identifier_id>/verify", verify_person_identifier),
    path("api/v1/people/<int:person_id>/legal-name-changes", change_legal_name),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
