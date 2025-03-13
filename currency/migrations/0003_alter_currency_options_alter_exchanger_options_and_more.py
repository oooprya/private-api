# Generated by Django 5.0.1 on 2025-03-13 11:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('currency', '0002_alter_cartitem_buy_alter_cartitem_sell'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='currency',
            options={'ordering': ('code_id',), 'verbose_name': 'Валюта', 'verbose_name_plural': 'Валюты'},
        ),
        migrations.AlterModelOptions(
            name='exchanger',
            options={'ordering': ('id',), 'verbose_name': 'Обменик', 'verbose_name_plural': 'Все Обменики'},
        ),
        migrations.AlterModelOptions(
            name='orders',
            options={'ordering': ('-created_at',), 'verbose_name': 'Заказ', 'verbose_name_plural': 'Заказы'},
        ),
        migrations.AddField(
            model_name='currency',
            name='code_id',
            field=models.ImageField(default=0, upload_to=''),
        ),
    ]
