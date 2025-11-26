
# """Добавить Обменники"""
# exchangers = [
#     {
#         "address": "вулиця Успенська, 41",
#         "address_map": "https://maps.app.goo.gl/vp4BjybpC3XAamiRA",
#         "created_at": "2025-03-20T10:54:42",
#         "id": 1,
#         "resource_uri": "/api/v1/exchangers/1/",
#         "updatedAt": "2025-03-25T16:40:47.272295",
#         "working_hours": "9:00 - 19:30"
#     },
#     {
#         "address": "вулиця Мала Арнаутська, 49",
#         "address_map": "https://maps.app.goo.gl/t9e5uvLYuSzwLYK58",
#         "created_at": "2025-03-20T10:56:03",
#         "id": 2,
#         "resource_uri": "/api/v1/exchangers/2/",
#         "updatedAt": "2025-03-21T12:27:08.928182",
#         "working_hours": "9:00 - 19:00"
#     },
#     {
#         "address": "Бізнес центр Грецька площа, 1",
#         "address_map": "https://maps.app.goo.gl/BqNwrCnnkpJnsAwBA",
#         "created_at": "2025-03-20T10:55:41",
#         "id": 3,
#         "resource_uri": "/api/v1/exchangers/3/",
#         "updatedAt": "2025-03-20T12:48:21.795955",
#         "working_hours": "9:00 - 19:00"
#     },
#     {
#         "address": "вулиця Олексія Маркевича, 32 (Новий ринок)",
#         "address_map": "https://maps.app.goo.gl/EH33J7apVWWXhg398",
#         "created_at": "2025-03-20T10:55:45",
#         "id": 4,
#         "resource_uri": "/api/v1/exchangers/4/",
#         "updatedAt": "2025-03-25T09:46:43.421242",
#         "working_hours": "8:00 - 18:00"
#     },
#     {
#         "address": "вул. Мельницька, 1 - (Таврія В)",
#         "address_map": "https://maps.app.goo.gl/MTTDKNQWRFbjGFx46",
#         "created_at": "2025-03-21T15:31:00",
#         "id": 5,
#         "resource_uri": "/api/v1/exchangers/5/",
#         "updatedAt": "2025-03-25T16:25:31.800175",
#         "working_hours": "8:00 - 20:00"
#     }
# ]


# @logger.catch
# def add_all_exchangers():
#     data_exchangers = get_api_data(f'{getenv("API")}/api/v1/exchangers')

#     if data_exchangers["meta"]["total_count"] == 0:
#         for exchanger in exchangers:
#             x = requests.post(f'{getenv("API")}/api/v1/exchangers/',
#                               json=exchanger, headers=headers)
#             logger.debug(f'{exchanger} - {x.status_code}')
