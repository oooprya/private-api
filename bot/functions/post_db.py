#!/bin/bash
import asyncio
from pytz import timezone
from datetime import datetime, timedelta
from functions.all_fun import update_course, parser_exchanger
import hashlib
from loguru import logger


@logger.catch
async def post_db():
    # Начальная инициализация
    previous_hash = None
    last_post_time = None
    tz = timezone('Europe/Kiev')
    await update_course(parser_exchanger())

    while True:
        # Получаем текущее время
        now = datetime.now(tz)

        # Работаем только с 8:00 до 20:00
        if now.hour >= 8 and now.hour < 20:
            # Получаем данные и хеш
            data = parser_exchanger()
            # Выполняем нужные действия
            # logger.debug(f"Выполняем задачу в {now} {last_post_time} {previous_hash}")

            # Вычисляем текущий хеш
            current_hash = hashlib.md5(
                f"{parser_exchanger()}".encode('utf-8')).hexdigest()


            # Сравниваем с предыдущим хешем
            if previous_hash is not None:
                if current_hash != previous_hash:
                    logger.info(f"Обновление курса! Новый хеш: {current_hash}")
                    await update_course(data)


                    last_post_time = now
                else:
                    logger.debug(f"Курсы не изменились. {previous_hash}")
                    # Если курс не менялся больше minutes=45 — всё равно обновляем
                    if last_post_time is None or (now - last_post_time >= timedelta(minutes=45)):
                        logger.info("Прошел 45 минут без изменений — принудительное обновление.")
                        # await send_msg_currency()
                        last_post_time = now

            # Обновляем предыдущий хеш
            previous_hash = current_hash



            # Ждем 5 минут (300 секунд)
            await asyncio.sleep(300)
        else:
            # Вне рабочего времени — проверяем раз в минуту
            await asyncio.sleep(60)