#!/usr/bin/env bash
# exit on error

# set -e

# if [ "$DATABASE" = "postgres" ]
# then
#     echo "Waiting for postgres..."

#     while ! nc -z $SQL_HOST $SQL_PORT; do
#       sleep 0.1
#     done

#     echo "PostgreSQL started"
# fi

# Выполняем миграции
echo "Migrating..."

# python manage.py migrate --no-input
python manage.py collectstatic --no-input
python manage.py makemigrations
python manage.py migrate

# Запускаем сервер
exec gunicorn base.wsgi:application --bind 0.0.0.0:8000