from django.contrib import admin

from apps.employees.models import (
    Address,
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


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("id", "surname", "firstname", "patronymic", "identity_status", "address")
    list_display_links = ("id", "surname", "firstname")
    search_fields = ("surname", "firstname", "patronymic")
    readonly_fields = ("identity_status", "merged_into", "access_event_version", "performer_event_version", "created_at", "updated_at")


@admin.register(EmploymentEpisode)
class EmploymentEpisodeAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "personnel_number", "status", "starts_at", "ends_at", "source")
    list_filter = ("status", "source")
    search_fields = ("person__surname", "person__firstname", "personnel_number")


@admin.register(PositionAssignment)
class PositionAssignmentAdmin(admin.ModelAdmin):
    list_display = ("id", "employment_episode", "position_code", "status", "starts_at", "ends_at")
    list_filter = ("position_code", "status")


@admin.register(ExternalMembership)
class ExternalMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "organization_id", "status", "valid_from", "valid_to")
    list_filter = ("status",)
    search_fields = ("person__surname", "person__firstname", "organization_id")


@admin.register(PerformerProfile)
class PerformerProfileAdmin(admin.ModelAdmin):
    list_display = ("person", "status", "source_version", "updated_at")
    list_filter = ("status",)
    search_fields = ("person__surname", "person__firstname")


@admin.register(PersonContact)
class PersonContactAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "contact_type", "verification_status", "status", "is_primary", "created_at")
    list_filter = ("contact_type", "verification_status", "status")
    search_fields = ("person__surname", "person__firstname", "lookup_hmac")
    exclude = ("encrypted_value",)


@admin.register(PersonDocument)
class PersonDocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "document_type", "issuing_country_code", "verification_status", "status", "valid_to")
    list_filter = ("document_type", "verification_status", "status", "issuing_country_code")
    search_fields = ("person__surname", "person__firstname", "lookup_hmac")
    exclude = ("encrypted_number", "lookup_hmac")
    readonly_fields = ("verified_at", "verified_by_user_id", "created_at", "updated_at")


@admin.register(PersonIdentifier)
class PersonIdentifierAdmin(admin.ModelAdmin):
    list_display = ("id", "person", "identifier_type", "country_code", "verification_status", "status", "verified_at")
    list_filter = ("identifier_type", "country_code", "verification_status", "status")
    search_fields = ("person__surname", "person__firstname", "lookup_hmac")
    exclude = ("encrypted_value", "lookup_hmac")
    readonly_fields = ("verified_at", "verified_by_user_id", "created_at", "revoked_at")


@admin.register(PersonNameHistory)
class PersonNameHistoryAdmin(admin.ModelAdmin):
    list_display = ("person", "surname", "firstname", "patronymic", "valid_from", "valid_to", "change_reason")
    list_filter = ("change_reason",)
    search_fields = ("person__surname", "surname", "firstname")
    readonly_fields = tuple(field.name for field in PersonNameHistory._meta.fields)


@admin.register(PersonMerge)
class PersonMergeAdmin(admin.ModelAdmin):
    list_display = ("duplicate_person", "canonical_person", "reason_code", "merged_at", "merged_by_user_id")
    readonly_fields = tuple(field.name for field in PersonMerge._meta.fields)


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("full_address", "country", "city", "street", "house", "block", "flat")
    list_display_links = ("full_address",)
    search_fields = ("full_address",)
