# OMS2 Employee Service

`OMS2` - микросервис сотрудников. В него перенесен текущий функционал исходного проекта `extrawork`: сотрудники, адреса, Django admin, HTML-страницы и базовый JSON API.

## Функции

- Хранение сотрудников `Employeepersinfo`.
- Хранение адресов `Address` с поддержкой PostGIS-координат.
- Просмотр сотрудников через HTML-страницу `/employees/`.
- Управление сотрудниками и адресами через Django admin.
- JSON API для списка, создания и получения сотрудника.
- Публикация события `employee.created` в Kafka при создании сотрудника через API.

## Технологии

- Python 3.11
- Django 4.2
- PostGIS/PostgreSQL
- Gunicorn
- confluent-kafka

## Основные модели

### Employeepersinfo

- `surname` - фамилия.
- `firstname` - имя.
- `patronymic` - отчество.
- `personnel_number` - табельный/персональный номер.
- `gender` - пол.
- `address` - ссылка на адрес.
- `created_at`, `updated_at` - служебные даты.

### Address

- `full_address` - полный адрес.
- `postal_code` - почтовый индекс.
- `country`, `region`, `city`, `settlement`, `street`, `house`, `block`, `flat` - части адреса.
- `location` - координаты PostGIS Point.
- `qc` - качество распознавания.
- `is_auto` - адрес получен автоматически.

## API

### Health check

```http
GET /health/
```

### HTML-страницы

```http
GET /
GET /about/
GET /employees/
```

### Получить список сотрудников

```http
GET /api/employees/
```

Ответ:

```json
{
  "items": [
    {
      "id": 1,
      "surname": "Иванов",
      "firstname": "Иван",
      "patronymic": "Иванович",
      "personnel_number": "EMP-0001",
      "gender": "M",
      "address": null,
      "created_at": "2026-01-20T09:00:00Z",
      "updated_at": "2026-01-20T09:00:00Z"
    }
  ]
}
```

### Создать сотрудника

```http
POST /api/employees/
Content-Type: application/json
```

Тело запроса:

```json
{
  "surname": "Иванов",
  "firstname": "Иван",
  "patronymic": "Иванович",
  "personnel_number": "EMP-0001",
  "gender": "M"
}
```

### Получить сотрудника по ID

```http
GET /api/employees/1/
```

## Kafka events

- `employee.created` - сотрудник создан через API.

## Переменные окружения

| Переменная | Значение по умолчанию | Назначение |
| --- | --- | --- |
| `DEBUG` | `false` | Режим отладки Django |
| `SECRET_KEY` | `change-me` | Django secret key |
| `ALLOWED_HOSTS` | `*` | Разрешенные хосты |
| `DATABASE_URL` | `postgis://oms2:oms2@oms2-postgres:5432/oms2` | Подключение к PostGIS |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka.oms.svc.cluster.local:9092` | Kafka bootstrap servers |

## Локальный запуск

Нужен PostgreSQL/PostGIS. Пример `DATABASE_URL`:

```text
postgis://oms2:oms2@localhost:5432/oms2
```

Команды:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set DATABASE_URL=postgis://oms2:oms2@localhost:5432/oms2
set SECRET_KEY=dev-secret
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8002
```

Адреса работают после локального запуска Django-командой выше:

```text
http://localhost:8002/admin/
http://localhost:8002/employees/
http://localhost:8002/api/employees/
```

При доступе через Ingress эти URL можно открыть в браузере:

```text
http://oms.local/oms2/admin/
http://oms.local/oms2/employees/
http://oms.local/oms2/api/employees/
http://oms.local/oms2/health/
```

OMS2 - Django-сервис, поэтому встроенного FastAPI Swagger UI по `/docs` у него нет. Для изучения и тестирования общего API-контракта OMS1-OMS5 используйте Swagger UI из корня проекта:

```powershell
docker run --rm `
  --name oms-swagger-ui `
  -p 8088:8080 `
  -e SWAGGER_JSON=/spec/platform/contracts/openapi_oms_microservices.json `
  -v "D:/ProjectsDocker/extrawork:/spec" `
  swaggerapi/swagger-ui:v5.17.14
```

```text
http://localhost:8088
```

## Docker

```bash
docker build -t oms2:latest .
docker run --rm -p 8002:8000 -e SECRET_KEY=dev-secret -e DATABASE_URL=postgis://oms2:oms2@host.docker.internal:5432/oms2 oms2:latest
```

## Kubernetes

```bash
kubectl apply -f ../platform/k8s/namespace.yaml
copy k8s\secret.example.yaml k8s\secret.yaml
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

При установленном Ingress из `platform/k8s/ingress.yaml` HTML/admin/API страницы доступны без port-forward:

```text
http://oms.local/oms2/admin/
http://oms.local/oms2/employees/
http://oms.local/oms2/api/employees/
```

Если Ingress недоступен, для отладки можно использовать `kubectl -n oms port-forward svc/oms2 8002:80` и открыть `http://localhost:8002/admin/`.

## Важно для production

- `OMS2/k8s/postgres.yaml` использует `PersistentVolumeClaim` `oms2-postgres-data`, поэтому данные переживают пересоздание pod.
- Для production лучше использовать управляемую PostgreSQL/PostGIS БД или явно настроенный StorageClass/backup policy.
- Добавить авторизационную middleware-проверку JWT через `OMS1`.
- Добавить полноценные CRUD endpoint-ы и валидацию входных данных.
