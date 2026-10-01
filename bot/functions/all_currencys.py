from requests import post
from os import getenv
from loguru import logger

from functions.all_fun import get_api_data, parser_exchanger


url_currencys = f'{getenv("API")}/api/v1/currencys/?limit=115'
headers = {"Authorization": f"ApiKey {getenv('API_KEY')}"}

all_currencys = [
    {
        "code": "usd",
        "name": "USD - Долар"
    },
    {
        "code": "usb",
        "name": "USD - Синій долар"
    },
    {
        "code": "eur",
        "name": "EUR - Евро"
    },

    {
        "code": "gbp",
        "name": "GBP - Фунт стерлингов"
    },
    {
        "code": "chf",
        "name": "CHF - Швейцарский франк"
    },
    {
        "code": "pln",
        "name": "PLN - Польский злотый"
    },
    {
        "code": "cad",
        "name": "CAD - Канадский доллар"
    },
    {
        "code": "ron",
        "name": "RON - Румынский лей"
    },
    {
        "code": "mdl",
        "name": "MDL - Молдавский лей"
    },
    {
        "code": "bgn",
        "name": "BGN - Болгарский лев"
    },
    {
        "code": "nok",
        "name": "NOK - Норвежская крона"
    },
    {
        "code": "czk",
        "name": "CZK - Чешская крона"
    },
    {
        "code": "cny",
        "name": "CNY - Китайский юань"
    },
    {
        "code": "try",
        "name": "TRY - Турецкая лира"
    },
    {
        "code": "sek",
        "name": "SEK - Шведская крона"
    },
    {
        "code": "dkk",
        "name": "DKK - Датская крона"
    },
    {
        "code": "ils",
        "name": "ILS - Израильский шекель"
    },
    {
        "code": "huf",
        "name": "HUF - Венгерский форинт"
    },
    {
        "code": "aed",
        "name": "AED - Дирхам ОАЭ"
    },
    {
        "code": "aud",
        "name": "AUD - Австралийский доллар"
    },
    {
        "code": "usd-eur",
        "name": "Долар - Евро"
    },
    {
        "code": "chf-usd",
        "name": "Швейцарский франк - Долар"
    },
    {
        "code": "gbp-usd",
        "name": "Фунт стерлингов - Долар"
    }
]

@logger.catch
def add_all_currency():
    data_currency = get_api_data(f'{getenv("API")}/api/v1/currency')

    if data_currency["meta"]["total_count"] == 0:
        for curr in all_currencys:
            x = post(f'{getenv("API")}/api/v1/currency/',
                              json=curr, headers=headers)
            logger.debug(f'{curr} - {x.status_code}')
    try:
        api_currencys = get_api_data(f'{getenv("API")}/api/v1/currencys/')
        if api_currencys["objects"] == []:
            """Если в базе нет курсов"""
            logger.debug(api_currencys)
            add_course()
    except:
        logger.debug(api_currencys)


@logger.catch
def add_course():
    """Добавляем курс на сайт"""
    data_api = get_api_data(f'{getenv("API")}/api/v1/currency?limit=50')
    api_exchangers = get_api_data(f'{getenv("API")}/api/v1/exchangers/')

    logger.debug(api_exchangers)

    currency_id = 1
    for curr in api_exchangers["objects"]:
        exchanger_id = curr["id"]
        logger.debug(curr)
        for curr in parser_exchanger():
            for curr_api in data_api["objects"]:

                if curr['currency'] == curr_api['code']:
                    currency = {
                        "buy": curr['buy'],
                        "currency": f"/api/v1/currency/{curr_api['id']}/",
                        "exchanger": f"/api/v1/exchangers/{exchanger_id}/",
                        "sell": curr['sell'],
                        "sum": 100000
                    }
                    x = post(f'{getenv("API")}/api/v1/currencys/',
                                      json=currency,
                                      headers=headers)
                    logger.debug(x.status_code)
                    logger.debug(
                        f"exchanger_id {exchanger_id } / {curr_api['id']} / {curr_api['code']} buy {curr['buy']}  sell {curr['sell']}")
            currency_id += 1
            logger.debug(curr['currency'])