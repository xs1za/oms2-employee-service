from django.contrib import admin

from apps.employees.models import Address, Employeepersinfo


class EmployeepersinfoAdmin(admin.ModelAdmin):
    list_display = ("surname", "firstname", "patronymic", "address", "personnel_number", "gender")
    list_display_links = ("surname", "firstname")
    search_fields = ("surname", "firstname", "personnel_number")


class AddressAdmin(admin.ModelAdmin):
    list_display = ("full_address", "country", "city", "street", "house", "block", "flat")
    list_display_links = ("full_address",)
    search_fields = ("full_address",)


admin.site.register(Employeepersinfo, EmployeepersinfoAdmin)
admin.site.register(Address, AddressAdmin)
