// Парсинг кросс-курса с приоритетом usd
function parseCrossRate(code) {
    if (!code || typeof code !== "string") return null;

    const match = code.match(/^([a-z]{3})-([a-z]{3})$/i);
    if (!match) return null;

    let [_, cur1, cur2] = match;
    cur1 = cur1;
    cur2 = cur2;

    // Если есть usd — делаем его base
    if (cur1 === "usd" || cur2 === "usd") {
        return {
            baseCurrency: "usd",
            quoteCurrency: cur1 === "usd" ? cur2 : cur1
        };
    }

    // Обычный кросс без usd
    return {
        baseCurrency: cur1,
        quoteCurrency: cur2
    };
}

document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById('exchange-form');
    const button = document.getElementById('exchange-submit');
    const submitText = document.getElementById('submit-text');
    const spinner = document.getElementById('submit-spinner');
    const loading = document.getElementById('exchange-loading');

    const currencyField = document.querySelector("[name='currency']");
    const balanceEl = document.getElementById("current-balance");
    const amountInput = document.getElementById("amount");
    const rateInput = document.getElementById("rate");
    const configElement = document.getElementById("exchange-form-config");
    const balanceUrl = configElement?.dataset.balanceUrl || "/admin/wholesale/get-balance/";

     if (!form || !button) {
        return;
    }

    let submitted = false;

    form.addEventListener('submit', function (event) {

        // Защита от двойного клика
        if (submitted) {
            event.preventDefault();
            return;
        }

        submitted = true;

        // Блокируем кнопку
        button.disabled = true;

        // Меняем текст
        submitText.classList.add('hidden');
        spinner.classList.remove('hidden');

        // Показываем overlay
        if (loading) {
            loading.classList.remove('hidden');
        }

    });

    function updateCrossRateUI(currencyId) {
        const selectedOption = currencyField.querySelector(`option[value="${currencyId}"]`);
        const code = selectedOption?.getAttribute('data-code');
        const orderType = document.querySelector('[name="order_type"]').value;

        const crossInfo = code ? parseCrossRate(code) : null;
        const uahEquiv = document.getElementById("uah-equiv");
        const crossEquiv = document.getElementById("cross-equiv");
        const balanceCurrencyLabel = document.getElementById("balance-currency-label");
        const baseCurrencySelectWrapper = document.getElementById("base-currency-select-wrapper");

        if (crossInfo) {
            // Это кроссовый курс
            if (uahEquiv) uahEquiv.style.display = "none";
            if (crossEquiv) crossEquiv.style.display = "block";

            // Обновляем rate input если он существует
            if (rateInput) {
                rateInput.required = false;
                rateInput.value = "";
            }

            // Показываем селект для выбора версии базовой валюты
            if (baseCurrencySelectWrapper) {
                baseCurrencySelectWrapper.style.display = "block";
                populateBaseCurrencySelect(crossInfo.baseCurrency);
            }

            // Обновляем label селекта в зависимости от типа операции
            const baseCurrencyLabel = document.querySelector('#base-currency-select-wrapper label');
            if (baseCurrencyLabel) {
                if (orderType === "buy") {
                    baseCurrencyLabel.innerText = "⬆ Доллар клієнту?";
                } else if (orderType === "sell") {
                    baseCurrencyLabel.innerText = "⬇ Доллар прийшов від клієнта?";
                }
            }

            // Обновляем лейблы кроса
            const baseCurrencyCodeSpan = document.getElementById("base-currency-code");
            if (baseCurrencyCodeSpan) baseCurrencyCodeSpan.innerText = crossInfo.baseCurrency;

            // Показываем правильный баланс в зависимости от типа операции
            if (balanceCurrencyLabel) {
                if (orderType === "buy") {
                    balanceCurrencyLabel.innerText = crossInfo.baseCurrency;
                } else if (orderType === "sell") {
                    balanceCurrencyLabel.innerText = crossInfo.quoteCurrency;
                }
            }
        } else {
            // Обычный курс
            if (uahEquiv) uahEquiv.style.display = "block";
            if (crossEquiv) crossEquiv.style.display = "none";

            if (rateInput) {
                rateInput.required = true;
            }

            if (baseCurrencySelectWrapper) {
                baseCurrencySelectWrapper.style.display = "none";
            }

            // Показываем баланс выбранной валюты
            if (balanceCurrencyLabel) {
                balanceCurrencyLabel.innerText = code || "";
            }
        }
    }

    function populateBaseCurrencySelect(baseCurrency) {
        const baseCurrencySelect = document.getElementById("base_currency");
        if (!baseCurrencySelect) return;

        // Получаем все доступные валюты для этой базовой валюты
        const allOptions = Array.from(currencyField.options).map(opt => ({
            id: opt.value,
            code: opt.getAttribute('data-code'),
            name: opt.textContent
        }));

        // Фильтруем валюты, которые относятся к базовой валюте (USD, USDnew и т.д., но исключаем кроссовые курсы USD-EUR)
        const matchingCurrencies = allOptions.filter(curr => {
            if (!curr.code) return false;
            const code = curr.code.toLowerCase();
            const base = baseCurrency.toLowerCase();

            // Исключаем кроссовые курсы (с дефисом типа USD-EUR)
            if (code.includes('-')) return false;

            // Проверяем совпадение: точное или варианты без дефиса
            return code === base || (code.startsWith(base) && code.length > base.length);
        });

        // Заполняем селект
        baseCurrencySelect.innerHTML = '';
        matchingCurrencies.forEach(curr => {
            const option = document.createElement('option');
            option.value = curr.id;
            option.textContent = curr.name;
            option.setAttribute('data-code', curr.code);
            baseCurrencySelect.appendChild(option);
        });

        // Если есть опции, устанавливаем первую как выбранную
        if (baseCurrencySelect.options.length > 0) {
            baseCurrencySelect.value = baseCurrencySelect.options[0].value;
            baseCurrencySelect.addEventListener('change', function () {
                loadBalance(currencyField.value);
            });
        }
    }

    function loadBalance(currencyId) {
        const selectedOption = currencyField.querySelector(`option[value="${currencyId}"]`);
        const code = selectedOption?.getAttribute('data-code');
        const orderType = document.querySelector('[name="order_type"]').value;

        let currencyParam = currencyId;
        let currencyCodeParam = code;

        // Для крос-курсов используем выбранную версию базовой валюты (для покупки)
        // и всегда передаем оригинальный cross code (USD-EUR) при продаже, чтобы сервер показывал баланс quote (EUR).
        if (code && code.includes('-')) {
            const baseCurrencySelect = document.getElementById("base_currency");

            if (orderType === 'sell') {
                // При продаже отображаем баланс валюты quote (EUR)
                currencyCodeParam = code;
            } else if (baseCurrencySelect && baseCurrencySelect.style.display !== 'none' && baseCurrencySelect.value) {
                currencyParam = baseCurrencySelect.value;
                const selectedBaseCurrency = baseCurrencySelect.querySelector(`option[value="${baseCurrencySelect.value}"]`);
                currencyCodeParam = selectedBaseCurrency?.getAttribute('data-code') || code;
            }
        }

        fetch(`${balanceUrl}?currency=${currencyParam}&currency_code=${currencyCodeParam}&order_type=${orderType}`)
            .then(response => response.json())
            .then(data => {
                console.log(data.balance);
                if (balanceEl) {
                    balanceEl.innerText = Number(data.balance).toLocaleString();
                }

            })


            .catch(err => console.error('Balance load error:', err));

    }

    if (currencyField) {
        currencyField.addEventListener("change", function () {
            loadBalance(this.value);
            updateCrossRateUI(this.value);
            calculate();
        });

        if (currencyField.value) {
            loadBalance(currencyField.value);
            updateCrossRateUI(currencyField.value);
        }
    }

    // Слухаємо зміни order_type для оновлення label селекту базової валюти
    const orderTypeInput = document.querySelector('[name="order_type"]');
    if (orderTypeInput) {
        orderTypeInput.addEventListener("change", function () {
            const currencyId = currencyField.value;
            const selectedOption = currencyField.querySelector(`option[value="${currencyId}"]`);
            const code = selectedOption?.getAttribute('data-code');
            if (code && code.includes('-')) {
                updateCrossRateUI(currencyId);
                loadBalance(currencyId);
                console.log("code", code);
            }
        });
    }

});

// Функция для расчета эквивалента
function calculate() {
    const amountInput = document.getElementById("amount");
    const rateInput = document.getElementById("rate");
    const uahResult = document.getElementById("uah_result");
    const crossResult = document.getElementById("cross_result");
    const currencyField = document.querySelector("[name='currency']");
    const selectedOption = currencyField?.querySelector(`option[value="${currencyField.value}"]`);
    const code = selectedOption?.getAttribute('data-code');

    const crossInfo = code ? parseCrossRate(code) : null;

    if (crossInfo) {
        // Для кроссовых курсов: вычисляем курс и пересчитываем эквивалент
        const amount = parseFloat(amountInput?.value) || 0;
        const rate = parseFloat(rateInput?.value) || 0;
        const total = Math.round(amount * rate);
        if (crossResult) {
            crossResult.innerText = total.toLocaleString('ru-RU', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });
        }
    } else {
        // Для обычных курсов: эквивалент = сумма * курс
        const amount = parseFloat(amountInput?.value) || 0;
        const rate = parseFloat(rateInput?.value) || 0;
        const total = Math.round(amount * rate);
        if (uahResult) {
            uahResult.innerText = total.toLocaleString('ru-RU', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });
        }
    }
}

// Устанавливаем listener для input полей
document.addEventListener("DOMContentLoaded", function () {
    const amountInput = document.getElementById("amount");
    const rateInput = document.getElementById("rate");

    if (amountInput) amountInput.addEventListener("input", calculate);
    if (rateInput) rateInput.addEventListener("input", calculate);
});


// // Клавіатурні комбінації для швидкого вводу сум
// document.addEventListener("keydown", function (e) {
//     const input = document.getElementById("amount");
//     if (!input) return;

//     const tag = document.activeElement.tagName.toLowerCase();
//     const isTyping = tag === "input" || tag === "textarea";

//     const map = {
//         "Digit1": 100,
//         "Digit2": 500,
//         "Digit3": 1000,
//         "Digit4": 5000,
//         "Digit5": 10000,
//     };

//     if (map[e.code]) {
//         e.preventDefault();
//         if (isTyping && input.value) {
//             input.value = parseFloat(input.value) + map[e.code];
//         } else {
//             input.value = map[e.code];
//         }
//         input.dispatchEvent(new Event("input"));
//     }

//     if (e.code === "Escape") {
//         input.value = "";
//         input.dispatchEvent(new Event("input"));
//     }
// });