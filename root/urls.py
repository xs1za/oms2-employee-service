from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

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
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
