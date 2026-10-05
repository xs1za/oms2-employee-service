# OMS2 Backlog

- Для production использовать управляемую PostgreSQL/PostGIS БД или явно настроенный StorageClass, backup policy и restore-процедуру.
- Добавить авторизационную middleware-проверку JWT через `OMS1` для API и admin-доступа.
- Добавить события `employee.updated` и `employee.deactivated` для синхронизации `OMS5.Performer` projection.
- Добавить полноценные CRUD endpoint-ы сотрудников и адресов с валидацией входных данных.
- Добавить аудит изменений master data сотрудников.
