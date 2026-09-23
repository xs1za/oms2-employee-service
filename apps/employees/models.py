from django.contrib.gis.db import models as geomodels
from django.db import models


class Employeepersinfo(models.Model):
    surname = models.CharField(max_length=50, blank=False, verbose_name="Фамилия")
    firstname = models.CharField(max_length=50, blank=False, verbose_name="Имя")
    patronymic = models.CharField(max_length=50, blank=True, verbose_name="Отчество")
    personnel_number = models.CharField(max_length=250, blank=True, verbose_name="Перс №")
    gender = models.CharField(max_length=6, verbose_name="Пол")
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True, verbose_name="создано")
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True, verbose_name="изменено")
    address = models.ForeignKey("Address", blank=True, null=True, on_delete=models.PROTECT, verbose_name="Адрес")

    class Meta:
        verbose_name_plural = "Сотрудники"
        verbose_name = "Сотрудник"
        ordering = ("-personnel_number",)

    def __str__(self) -> str:
        return f"{self.firstname} {self.surname}"


class Address(models.Model):
    full_address = models.CharField(max_length=1000, null=False, verbose_name="Адрес полный")
    postal_code = models.CharField(max_length=32, blank=True, verbose_name="Почтовый индекс")
    country = models.CharField(max_length=250, blank=True, verbose_name="Страна")
    federal_district = models.CharField(max_length=250, blank=True, verbose_name="Федеральный округ")
    region = models.CharField(max_length=250, blank=True, verbose_name="Регион")
    city = models.CharField(max_length=250, blank=True, verbose_name="Город")
    settlement = models.CharField(max_length=250, blank=True, verbose_name="Населенный пункт")
    street = models.CharField(max_length=250, blank=True, verbose_name="Улица")
    house = models.CharField(max_length=250, blank=True, verbose_name="Дом")
    block = models.CharField(max_length=250, blank=True, verbose_name="Корпус/строение")
    flat = models.CharField(max_length=250, blank=True, verbose_name="Квартира")
    main_address = models.BooleanField(default=False, verbose_name="Основной адрес")
    location = geomodels.PointField(blank=True, null=True, verbose_name="Координаты")
    qc = models.IntegerField(verbose_name="Качество распознавания", null=True, blank=True)
    is_auto = models.BooleanField(verbose_name="Получен автоматически", default=False)

    class Meta:
        verbose_name_plural = "Адреса"
        verbose_name = "Адрес"

    def __str__(self) -> str:
        return f"{self.country} {self.full_address}"
