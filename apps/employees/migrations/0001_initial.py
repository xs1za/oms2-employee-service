import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Address",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("full_address", models.CharField(max_length=1000, verbose_name="Адрес полный")),
                ("postal_code", models.CharField(blank=True, max_length=32, verbose_name="Почтовый индекс")),
                ("country", models.CharField(blank=True, max_length=250, verbose_name="Страна")),
                ("federal_district", models.CharField(blank=True, max_length=250, verbose_name="Федеральный округ")),
                ("region", models.CharField(blank=True, max_length=250, verbose_name="Регион")),
                ("city", models.CharField(blank=True, max_length=250, verbose_name="Город")),
                ("settlement", models.CharField(blank=True, max_length=250, verbose_name="Населенный пункт")),
                ("street", models.CharField(blank=True, max_length=250, verbose_name="Улица")),
                ("house", models.CharField(blank=True, max_length=250, verbose_name="Дом")),
                ("block", models.CharField(blank=True, max_length=250, verbose_name="Корпус/строение")),
                ("flat", models.CharField(blank=True, max_length=250, verbose_name="Квартира")),
                ("main_address", models.BooleanField(default=False, verbose_name="Основной адрес")),
                ("location", django.contrib.gis.db.models.fields.PointField(blank=True, null=True, srid=4326, verbose_name="Координаты")),
                ("qc", models.IntegerField(blank=True, null=True, verbose_name="Качество распознавания")),
                ("is_auto", models.BooleanField(default=False, verbose_name="Получен автоматически")),
            ],
            options={"verbose_name_plural": "Адреса", "verbose_name": "Адрес"},
        ),
        migrations.CreateModel(
            name="Employeepersinfo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("surname", models.CharField(max_length=50, verbose_name="Фамилия")),
                ("firstname", models.CharField(max_length=50, verbose_name="Имя")),
                ("patronymic", models.CharField(blank=True, max_length=50, verbose_name="Отчество")),
                ("personnel_number", models.CharField(blank=True, max_length=250, verbose_name="Перс №")),
                ("gender", models.CharField(max_length=6, verbose_name="Пол")),
                ("created_at", models.DateTimeField(auto_now_add=True, null=True, verbose_name="создано")),
                ("updated_at", models.DateTimeField(auto_now=True, null=True, verbose_name="изменено")),
                ("address", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="employees.address", verbose_name="Адрес")),
            ],
            options={"verbose_name_plural": "Сотрудники", "verbose_name": "Сотрудник", "ordering": ("-personnel_number",)},
        ),
    ]
