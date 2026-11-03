# OMS2 Employee Service

OMS2 ведет мастер-данные Person и профили исполнителей. Старые /api/employees и HTML-страницы оставлены для совместимости.

## Возможности

- Персоны, история имен, объединение дублей, адреса и контактные данные.
- Зашифрованные контакты, проверенные документы и идентификаторы.
- Профили исполнителей, периоды работы, проверка типа оформления и стадия воронки.
- Статистика смен из OMS5 обновляет счетчик завершенных смен и стадию профиля.
- Django admin и PostGIS/PostgreSQL.

## API

OMS2 работает на Django и не предоставляет FastAPI Swagger UI. Общий контракт: ../platform/contracts/openapi_oms_microservices.json.

Основные маршруты:

- GET /health/live/ и GET /health/ready/
- POST /api/v1/people
- GET и PUT /api/v1/people/{personId}/performer-profile
- POST /api/v1/people/{personId}/performer-profile/engagement-type/verify
- POST /api/v1/people/{personId}/work-periods и POST /api/v1/work-periods/{workPeriodId}/terminate
- GET и POST /api/v1/people/{personId}/addresses; POST .../set-primary
- Документы, идентификаторы, смена юридического имени и merge доступны под /api/v1/people/{personId}/...

API проверяет Bearer token через OMS1 /auth/verify. Admin routes требуют роль administrator; service callers используют scope employees.

## Kafka

OMS2 публикует события из transactional outbox после commit:

- people.access.v1: people.identity.changed, people.merged
- people.performer.v1: people.performer_profile.changed, people.merged

Envelope содержит event_id, schema_version и aggregate_version. Kafka key равен person_id. Доставка может повторяться.

OMS2 читает operations.performer.v1 в consumer group oms2.operations.performer. События operations.shift.assigned и operations.shift.completed обновляют shift facts и performer funnel statistics.

Запускайте workers в отдельных терминалах при проверке Kafka-интеграции:

```powershell
python manage.py publish_people_outbox
```

```powershell
python manage.py consume_operations_performer
```

## Локальный запуск

Нужны PostgreSQL/PostGIS и значения DATABASE_URL, SECRET_KEY, OMS1_AUTH_BASE_URL, PERSON_DATA_ENCRYPTION_KEY и PERSON_IDENTIFIER_HMAC_KEY в локальном .env. Не добавляйте ключи в Git.

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8002
```

HTML и legacy API: http://localhost:8002/employees/ и http://localhost:8002/api/employees/.
