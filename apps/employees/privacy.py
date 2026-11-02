"""Normalization, encryption and exact-match fingerprints for personal data."""

import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email


PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
PASSPORT_RE = re.compile(r"^[A-Z0-9]{1,64}$")


def check_privacy_keys() -> dict:
    encryption_key = settings.PERSON_DATA_ENCRYPTION_KEY
    hmac_key = settings.PERSON_IDENTIFIER_HMAC_KEY
    if not encryption_key or not hmac_key:
        return {"status": "failed", "error": "personal data keys are not configured"}
    try:
        Fernet(encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return {"status": "failed", "error": "personal data encryption key is invalid"}
    if len(hmac_key.encode("utf-8")) < 32:
        return {"status": "failed", "error": "personal identifier HMAC key is too short"}
    return {"status": "ok"}


def encrypt_value(value: str) -> str:
    key = settings.PERSON_DATA_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured("PERSON_DATA_ENCRYPTION_KEY is required for storing personal data")
    try:
        return Fernet(key.encode("ascii")).encrypt(value.encode("utf-8")).decode("ascii")
    except (ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("PERSON_DATA_ENCRYPTION_KEY must be a valid Fernet key") from exc


def decrypt_value(ciphertext: str) -> str:
    key = settings.PERSON_DATA_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured("PERSON_DATA_ENCRYPTION_KEY is required for reading personal data")
    try:
        return Fernet(key.encode("ascii")).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("Personal data could not be decrypted with the configured key") from exc


def normalize_phone(value: str) -> str:
    normalized = re.sub(r"[\s().-]", "", value)
    if not PHONE_RE.fullmatch(normalized):
        raise ValidationError("Phone must use E.164 format, for example +375291234567")
    return normalized


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    try:
        validate_email(normalized)
    except ValidationError as exc:
        raise ValidationError("Email address is invalid") from exc
    return normalized


def normalize_document_number(document_type: str, country_code: str, value: str) -> str:
    country = country_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValidationError("Country code must be ISO 3166-1 alpha-2")

    normalized = re.sub(r"[\s-]", "", value).upper()
    if document_type == "passport":
        if not PASSPORT_RE.fullmatch(normalized):
            raise ValidationError("Passport number must contain 1-64 ASCII letters or digits")
        return normalized

    if document_type == "inn":
        if country != "RU" or not re.fullmatch(r"[0-9]{12}", normalized):
            raise ValidationError("Russian individual INN must contain 12 digits")
        weights_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        weights_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        digits = [int(item) for item in normalized]
        check_11 = sum(digit * weight for digit, weight in zip(digits[:10], weights_11)) % 11 % 10
        check_12 = sum(digit * weight for digit, weight in zip(digits[:11], weights_12)) % 11 % 10
        if digits[10] != check_11 or digits[11] != check_12:
            raise ValidationError("Russian individual INN checksum is invalid")
        return normalized

    if document_type == "snils":
        if country != "RU" or not re.fullmatch(r"[0-9]{11}", normalized):
            raise ValidationError("Russian SNILS must contain 11 digits")
        digits = [int(item) for item in normalized]
        checksum = sum(digit * (9 - index) for index, digit in enumerate(digits[:9]))
        if checksum < 100:
            expected = checksum
        elif checksum in (100, 101):
            expected = 0
        else:
            expected = checksum % 101
            if expected in (100, 101):
                expected = 0
        if int(normalized[9:]) != expected:
            raise ValidationError("Russian SNILS checksum is invalid")
        return normalized

    if document_type == "name_change_certificate":
        if not normalized:
            raise ValidationError("Name change certificate number cannot be blank")
        return normalized

    raise ValidationError("Unsupported document type")


def lookup_hmac(namespace: str, normalized_value: str) -> str:
    key = settings.PERSON_IDENTIFIER_HMAC_KEY
    if not key:
        raise ImproperlyConfigured("PERSON_IDENTIFIER_HMAC_KEY is required for exact identifier matching")
    material = f"{namespace}:{normalized_value}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), material, hashlib.sha256).hexdigest()
