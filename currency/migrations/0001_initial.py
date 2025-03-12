# Generated by Django 5.0.1 on 2025-03-11 07:16

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Currency',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='usd', max_length=30, unique=True, verbose_name='Валюта')),
                ('code', models.CharField(max_length=7)),
            ],
            options={
                'verbose_name': 'Валюта',
                'verbose_name_plural': 'Валюты',
            },
        ),
        migrations.CreateModel(
            name='Exchanger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('address', models.CharField(max_length=80)),
                ('address_map', models.CharField(blank=True, max_length=60)),
                ('working_hours', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updatedAt', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Обменик',
                'verbose_name_plural': 'Все Обменики',
            },
        ),
        migrations.CreateModel(
            name='Orders',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('ordersent', 'Отправлен'), ('accepted', 'Принятый'), ('completed', 'Выполнен'), ('new', 'Новый'), ('cancel', 'Сancel')], default='new', max_length=10)),
                ('clients_telephone', models.CharField(help_text='+38096-123-45-67', max_length=16)),
                ('address_exchanger', models.CharField(blank=True, max_length=100, verbose_name='Адрес Брони')),
                ('currency_name', models.CharField(blank=True, max_length=40, verbose_name='Валюта')),
                ('buy_or_sell', models.CharField(blank=True, max_length=8)),
                ('exchange_rate', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Курс')),
                ('order_sum', models.IntegerField(default=100, verbose_name='Сумма заказа')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'verbose_name': 'Заказ',
                'verbose_name_plural': 'Заказы',
                'ordering': ('-id',),
            },
        ),
        migrations.CreateModel(
            name='Users',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chat_id', models.IntegerField(blank=True)),
                ('chat_id_name', models.CharField(blank=True, max_length=150)),
                ('role', models.CharField(choices=[('cashier', 'Касир'), ('moderator', 'Модератор'), ('client', 'Клієнт')], default='user', max_length=10)),
            ],
            options={
                'verbose_name': 'Пользователь',
                'verbose_name_plural': 'Пользователи',
            },
        ),
        migrations.CreateModel(
            name='CartItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('buy', models.DecimalField(blank=True, decimal_places=2, max_digits=10, verbose_name='Покупка')),
                ('sell', models.DecimalField(blank=True, decimal_places=2, max_digits=10, verbose_name='Продажа')),
                ('sum', models.IntegerField(default=100, verbose_name='Сумма от 100 до 10000')),
                ('updatedAt', models.DateTimeField(auto_now=True)),
                ('currency', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='currency.currency')),
                ('exchanger', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='currency.exchanger')),
            ],
            options={
                'verbose_name': 'Курс валют',
                'verbose_name_plural': 'Курсы валют',
            },
        ),
    ]
