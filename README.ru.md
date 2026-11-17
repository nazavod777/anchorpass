<div align="center">

<img src="assets/hero.svg" width="800" alt="Терминал: установка Python-пакета, solve() проходит anchor и reload, возвращает токен"><br><br>

<img src="assets/b-python.svg" height="28" alt="Python"> <img src="assets/b-nodejs.svg" height="28" alt="Node.js"> <img src="assets/b-go.svg" height="28" alt="Go"> <img src="assets/b-rust.svg" height="28" alt="Rust"><br>
<img src="assets/b-api.svg" height="28" alt="sync + async"> <img src="assets/b-proxies.svg" height="28" alt="explicit proxies"> <img src="assets/b-signed.svg" height="28" alt="Ed25519 signed data"> <img src="assets/b-license.svg" height="28" alt="MIT"><br><br>

<a href="README.md">🇬🇧 English</a> · <a href="README.ru.md">🇷🇺 Русский</a> · <a href="README.zh.md">🇨🇳 中文</a>

<br><br>
<a href="https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml"><img src="https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml/badge.svg" alt="CI"></a>

</div>

# ⚓ Anchorpass

> **Решатель reCAPTCHA v3 invisible** — четыре нативных клиента для <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python">, <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js">, <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> и <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust">, с синхронным и асинхронным API, явными прокси и подписанными протокольными данными.

Anchorpass воспроизводит HTTP-поток invisible reCAPTCHA v3 — эндпоинты `anchor` и `reload` — по обычному HTTPS. Без браузера, вебдрайвера и стека фингерпринтинга. Протокольные константы распространяются как Ed25519-подписанные конверты с проверкой срока и откатов. Логика продолжает [xHossein/PyPasser](https://github.com/xHossein/PyPasser) и [invisible_captcha_bypasser](https://github.com/nazavod777/invisible_captcha_bypasser) автора; каждый пакет здесь — новая самостоятельная реализация одного общего [`spec/`](spec/protocol.md), а не обёртка над другим языком.

> [!WARNING]
> Автоматическое решение CAPTCHA нарушает Terms of Service Google. Используй только против своих приложений или тех, на которые у тебя есть явное разрешение. Полученный токен не гарантирует acceptance и score — твоё приложение всё равно проверяет его на своей стороне.

## ✨ Возможности

| Возможность | Что это значит |
|---|---|
| 🧩 **Четыре нативных пакета** | реализации на Python, Node.js, Go и Rust по одной общей спеке; без FFI между ними |
| ⚡ **Sync + async** | в каждом языке блокирующий и нативно-асинхронный клиент с единым контрактом поведения |
| 🔀 **Явная маршрутизация прокси** | `http`, `https`, `socks5`, `socks5h`; переменные окружения игнорируются, упавший прокси никогда не заменяется прямым заходом |
| 🔐 **Подписанные протокольные данные** | Ed25519-конверты со сроком действия и полом отката; канала удалённых обновлений по умолчанию нет |
| 🍪 **Изоляция cookie на solve** | каждый solve имеет свою cookie-банку с правилами domain/path/Secure/expiry и ограниченными бюджетами |
| 📏 **Ограничено по умолчанию** | общие дедлайны, границы connect/idle, лимиты тела и потолок параллельности; перегрузка отклоняется, а не копится в очереди |
| 🧪 **Общие тест-векторы** | одна спека с протокольными, прокси и негативными векторами гоняет все четыре пакета; CI прогоняет всю матрицу |

## 🎯 Что решает

Ровно **одну** вещь — получение токена reCAPTCHA v3 invisible через обычный HTTP-поток, начиная с anchor-URL интегрируемого приложения:

> `https://www.google.com/recaptcha/api2/anchor?k=<site-key>&co=<encoded-origin>`

| ✅ Решает | ❌ Не решает |
|---|---|
| поток anchor → reload, семейства `api2` и `enterprise` (локальная конформность обоих) | v2-чекбоксы, челленджи с картинками и аудио |
| строгую валидацию anchor-URL, обработку `bg` / `action`, подписанные обновления данных | live-квалификацию Enterprise — записанные Enterprise-ответы не содержали поля action |
| явную маршрутизацию прокси на весь поток | гарантированный score или acceptance — решает наблюдающий сайт |
| | серверную проверку токена — это должно делать твоё приложение |
| | публичный релиз Go — ещё не опубликован |

## 🚀 Установка

`0.1.1` доступна в [<img src="assets/c-pypi.svg" height="20" align="absmiddle" alt="PyPI">](https://pypi.org/project/anchorpass/0.1.1/), [<img src="assets/c-npm.svg" height="20" align="absmiddle" alt="npm">](https://www.npmjs.com/package/anchorpass/v/0.1.1) и [<img src="assets/c-cratesio.svg" height="20" align="absmiddle" alt="crates.io">](https://crates.io/crates/anchorpass/0.1.1). Тег версии Go для <img src="assets/c-goproxy.svg" height="20" align="absmiddle" alt="Go proxy"> пока не опубликован; для Go используй локальный чекаут.

<table>
<tr><td width="260"><img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"><br><sub>рекомендуется виртуальное окружение</sub></td><td>

```bash
python -m pip install anchorpass==0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><br><sub>устанавливай из каталога своего приложения</sub></td><td>

```bash
npm install anchorpass@0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"><br><sub>путь модуля + локальный replace до публикации</sub></td><td>

```bash
go mod edit -replace github.com/nazavod777/anchorpass/go=/absolute/path/to/anchorpass/go
go get github.com/nazavod777/anchorpass/go
```

</td></tr>
<tr><td width="260"><img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"><br><sub>точная версия; фича по умолчанию — `async`</sub></td><td>

```toml
[dependencies]
anchorpass = "=0.1.1"
# блокирующий клиент: добавь features = ["blocking"]
```

</td></tr>
</table>

> [!NOTE]
> Python, Node.js и Rust устанавливаются из публичных реестров, Go — из локального чекаута. `make prepare-release` запечатывает самостоятельные артефакты (wheel, sdist, npm-тарболл, крейт, Go-архив) с манифестом контрольных сумм; [релизные операции](docs/release.md) описывают установку точными байтами, публикацию и подпись канала.

**Что даёт установка:** самодостаточный пакет на язык с зашитым внутрь подписанным протокольным бандлом → импорт и конструирование клиента **не выполняют сетевых запросов** → без post-install скриптов и фоновых циклов.

### 📋 Требования

- <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> **3.11+** (проверено на 3.11.18 и 3.13.15)
- <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> **22** (проверено на 22.0.0 и 22.22.1)
- <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> **1.23+** (проверено на 1.23.0 и 1.25.5)
- <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> **1.92+** (проверено на тулчейнах 1.92.0 и 1.96)
- <img src="assets/c-linux.svg" height="20" align="absmiddle" alt="Linux"> x86_64 — проверенная платформа; другие ОС и архитектуры не квалифицированы
- 🌐 Без браузера, дисплея и системных сервисов; выбранные транспорты: Python PycURL (sync) и aiohttp (async), Node.js Undici, Go `net/http`, Rust reqwest

## ⚙️ Как это работает

```mermaid
flowchart LR
    A([👤 твоё приложение]) --> C{{"🧩 клиент anchorpass"}}
    C -->|"один явный маршрут<br/>http · https · socks5 · socks5h"| P["🛰️ прокси — опционально"]
    P --> V["🔎 текущая версия api.js"]
    V --> N["⚓ anchor GET"]
    N --> R["🔁 reload POST"]
    R --> T["🎫 токен"]
    T --> A
```

- каждый solve строго валидирует anchor-URL (HTTPS, `google.com`, `api2/anchor` или `enterprise/anchor`, непустые `k` и `co`) до любого сетевого запроса
- текущая версия протокола запрашивается заново на каждый solve, даже если URL несёт `v=`
- каждый solve имеет изолированную cookie-банку; соединения из пула переиспользуются, но cookie не утекают между solve и в запросы обновлений
- `bg` берётся из проверенного подписанного бандла; приоритет действия: явный аргумент → `sa` из URL → не отправлять — библиотека никогда не выдумывает site action
- редиректы отклоняются, а упавший прокси — это ошибка, но никогда не прямой ретрай

## 🧩 Использование

Бери anchor-URL из интегрируемого приложения — форма плейсхолдера не является рабочей конфигурацией сайта:

> `anchor_url = "https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin"`

Один и тот же контракт на каждом языке:

<table>
<tr><td width="120"><img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"></td><td>

```python
from anchorpass import AsyncClient, Client

with Client(proxy="http://user:pass@127.0.0.1:8080") as client:
    token = client.solve(anchor_url, action="homepage").token

async with AsyncClient() as client:
    token = (await client.solve(anchor_url)).token
```

</td></tr>
<tr><td width="120"><img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"></td><td>

```js
import { Client, SyncClient } from 'anchorpass';

const client = new Client({ proxy: 'http://user:pass@127.0.0.1:8080' });
const token = (await client.solve(anchorUrl, { action: 'homepage' })).token;
await client.closeAsync();

const sync = new SyncClient();
const token2 = sync.solveSync(anchorUrl).token;
```

</td></tr>
<tr><td width="120"><img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"></td><td>

```go
client, err := anchorpass.NewClient(
	anchorpass.WithProxy("http://user:pass@127.0.0.1:8080"))
if err != nil {
	log.Fatal(err)
}
defer client.Close()

result, err := client.Solve(ctx, anchorURL,
	anchorpass.WithAction("homepage"))
token := result.Token // one-shot: anchorpass.Solve(ctx, anchorURL)
```

</td></tr>
<tr><td width="120"><img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"></td><td>

```rust
let client = Client::builder()
    .proxy("socks5h://user:pass@127.0.0.1:1080")
    .build()?;
let token = client.solve(&anchor).await?.token;

// blocking: только обычные потоки, никогда внутри Tokio runtime
let token = anchorpass::blocking::Client::builder()
    .build()?
    .solve(&anchor)?
    .token;
```

</td></tr>
</table>

`extract_action` / `extractAction` / `ExtractAction` разбирают `sa` из URL без всякого I/O; `load_bg` / `loadBg` / `LoadBG` / `load_bg` возвращают `bg` проверенного бандла без captcha-запроса. Полные запускаемые скрипты, читающие anchor-URL и опциональный прокси из аргументов командной строки: [examples/](examples/README.md).

Одноразовые хелперы сами создают и закрывают клиентов. Переиспользуй клиента, чтобы переиспользовать соединения. Детали по языкам:

| Пакет | Клиенты | Одноразовые хелперы | Заметки по языку |
| --- | --- | --- | --- |
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `Client`, `AsyncClient` | `solve`, `asolve`, `load_bg`, `aload_bg` | [python/README.md](python/README.md) |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `Client`, `SyncClient` | `solve`, `solveSync`, `loadBg`, `loadBgSync` | [js/README.md](js/README.md) |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `NewClient(options...)` | top-level `Solve`, `LoadBG` | [go/README.md](go/README.md) |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `Client::builder()`, `blocking::Client` | методы клиента | [rust/README.md](rust/README.md) |

Поля на вызов — `bg`, `action`, User-Agent и `hostname` — опциональные параметры одного solve:

| Пакет | Хелпер бандла `bg` | Хелпер действия URL | Оверрайд на вызов |
| --- | --- | --- | --- |
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `load_bg`, `aload_bg`, метод `load_bg` клиента | `extract_action` | `solve(..., bg=..., action=..., user_agent=..., hostname=...)` |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `loadBg`, `loadBgSync`, соотв. методы клиента | `extractAction` | опции `solve`/`solveSync` `{ bg, action, userAgent, hostname }` |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `LoadBG(ctx, options...)`, `Client.LoadBG(ctx)` | `ExtractAction` | `WithBG`, `WithAction`, `WithUserAgent`, `WithHostname` в `Solve` |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | метод `load_bg` клиента | `extract_action` | `solve_with_options` с `SolveOptions` |

Явное действие побеждает `sa` из anchor-URL; иначе `sa` сохраняется; если нет ни того ни другого — действие не отправляется. Выбранное действие уходит и в anchor, и в reload. Оверрайды заменяют только исходящие поля одного вызова — они не меняют бандл, кэш и последующие вызовы.


`user_agent` (Python/Rust), `userAgent` (Node.js) или `WithUserAgent` (Go) задаёт одинаковый заголовок на шагах version, anchor и reload, не меняя обновления и следующие вызовы. `hostname` / `WithHostname` заменяет только домен приложения в `co`, сохраняя схему и порт. Принимается ASCII-домен или IP, без URL и порта; ограничения домена ключа Google сохраняются. Без этих параметров исходные заголовки и `co` не меняются. Правила и примеры — в [контракте API](spec/api.md).

## 🔀 Маршрутизация и обновления

`proxy` принимает абсолютный URL `http`, `https`, `socks5` или `socks5h` с явными хостом и портом. `socks5` резолвит цель локально; `socks5h` — на прокси. Прокси-переменные окружения игнорируются. Клиент держит один маршрут на все шаги captcha; для смены создай нового клиента. Отдельный запрос проверки токена в твоём приложении должен настраивать свой маршрут, а ротирующий провайдер может менять выходной IP при неизменном URL.

| Опция | По умолчанию | Назначение | Пакеты |
|---|---|---|---|
| proxy | нет (напрямую) | маршрут всех шагов captcha | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| маршрут обновлений | наследует proxy | отдельный маршрут запросов обновлений; явный прямой доступен | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| общий таймаут | 30 с | дедлайн операции на все стадии | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| потолок параллельности | 16 | активные solve; лишнее отклоняется (`ResourceLimit`), очереди нет | <img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| кэш | память | хранилище подписанных конвертов; путь включает постоянный кэш с историей отката | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| канал обновлений | нет | `bundleUrl` + `trustedKeys`; канала по умолчанию и фонового цикла нет | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| `offline` | выкл | отключает сеть обновлений, но не сеть captcha | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| `pin` | нет | точная ревизия; не обходит проверки подписи, срока и отката | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |

Прочитай [updates](docs/updates.md) перед настройкой канала.

## 🔐 Подписанные данные и доверие

- 📝 конверты подписаны Ed25519 по точным байтам payload с домен-разделённым префиксом (`anchorpass.bundle.v1`); дубликаты JSON-ключей, неизвестные поля, чужие семейства, кривые ревизии, негабаритные поля и просроченные данные отклоняются
- 🔑 публичный ключ `production-1` проверяет [канал данных на GitHub](docs/channel-operations.md); приватный ключ хранится в секрете GitHub Actions
- 🚫 канал GitHub подключается явным указанием URL; импорт и конструирование клиента не делают сетевых запросов
- ↩️ аутентифицированная более высокая ревизия становится полом отката — в памяти, а при настроенном пути и на диске — до проверок срока и pin
- 🧱 запросы обновлений идут через собственный ограниченный пул соединений без captcha-cookie, заголовков и параметров; сертификаты цели и HTTPS-прокси проверяются независимо
- 🕶️ сообщения об ошибках и диагностика не содержат токенов и учётных данных прокси

## 🔧 Разработка

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
npm --prefix js ci
make check                 # целостность контрактов/данных + все языковые гейты
make test-package-install  # собранные артефакты вне дерева исходников
```

- `make test-benchmark-adapters`, `make test-pressure-adapters` и `make test-live-helpers` проверяют локальные харнессы без обращения к публичным CAPTCHA-эндпоинтам; [инструкции бенчмарков](benchmarks/README.md) описывают последовательный матричный раннер
- `make test-live` явно обращается к публичным демо-верификаторам; его [инструкции](tests/live/README.md) объясняют выбор сайтов, маршрутизацию прокси и негативные контроли — обычный CI внешние acceptance-запросы не выполняет
- `make prepare-release` запечатывает артефакты и манифест контрольных сумм; [релизные операции](docs/release.md) покрывают публикацию, подпись канала и восстановление
- проверкам установки Go-архива нужен предварительный `make check` для наполнения кэша зависимостей тулчейна; сам модуль всегда берётся из проверяемого архива
- каждый push и pull request запускает [verify workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml); требуй зелёный прогон точного коммита, выбранного для релиза

## 📚 Документация

| Путь | Начни отсюда |
|---|---|
| 🗺️ [Индекс документации](docs/README.md) | все документы с маршрутами чтения по ролям |
| 🔐 [Подписанные протокольные данные](docs/updates.md) | хранение ключей, каналы и политика срока |
| 🚢 [Релизные операции](docs/release.md) | публикация точными байтами и восстановление |
| 🧭 [Статус реализации](docs/implementation-status.md) | локальные доказательства против внешнего acceptance |
| 🧬 [Совместимость](docs/compatibility.md) | проверенные рантаймы и платформы |

Общий [`spec/`](spec/protocol.md) — источник истины для всех четырёх реализаций.

## ⚠️ Ограничения

- <img src="assets/c-linux.svg" height="20" align="absmiddle" alt="Linux"> проверено только на x86_64; другие платформы и архитектуры не квалифицированы ([матрица совместимости](docs/compatibility.md))
- 🔑 встроенные данные истекают; подключи [канал GitHub](docs/channel-operations.md) для продления подписанных данных или заранее предоставь свежий доверенный конверт
- 🧬 семейство `enterprise` имеет только локальную конформность; в live-записи Enterprise-ответы не содержали action
- <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> асинхронная отмена не может принудительно прервать уже идущий системный вызов DNS (reqwest использует системный резолвер)
- 🎫 полученный токен не подтверждает acceptance и score; приложения обязаны проверять его и не логировать без разбора
- 📦 `0.1.1` опубликована в PyPI, npm и crates.io; публикация Go ещё впереди

## ✅ Статус проверок

[Verify workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml) прошёл по всем задачам на `edd3797`; для своего коммита требуй зелёный прогон:

| 🔍 check | <img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"> go-minimum | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"> python-minimum | <img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> node-minimum | <img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> rust-current |
|:---:|:---:|:---:|:---:|:---:|
| [✅ гейты](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791929) | [✅ Go 1.23](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791730) | [✅ Python 3.11](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791979) | [✅ Node 22.0](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791171) | [✅ Rust 1.92](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791747) |

Живая [мультисайтовая запись](docs/live-acceptance-multisite.json) содержит 28 кейсов на четырёх публичных демо и четыре негативных контроля с инвалидными токенами: Google и 2Captcha v3 прошли строгие acceptance-проверки; семь Enterprise-кейсов приняты без поля action, семь кейсов ZennoLab вернули квотную диагностику — матрица намеренно выходит с ненулевым кодом, потому что такие ограниченные ответы не являются чистыми прохождениями. Эти наблюдения описывают записанные демо, маршрут и время; они не устанавливают стабильный score или acceptance на произвольных приложениях. Локальная конформность покрывает оба семейства `api2` и `enterprise` на всех четырёх языках. Полные доказательства: [implementation status](docs/implementation-status.md), [transport decisions](docs/transport-decisions.md), [pressure results](docs/pressure-results.md).

## 🗂️ Структура

| | Путь | Содержимое |
|:---:|---|---|
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `python/` | Python-пакет (PycURL sync, aiohttp async) |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `js/` | Node.js-пакет (Undici) |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `go/` | Go-модуль (net/http) |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `rust/` | Rust-крейт (reqwest + Hyper/rustls-адаптер для HTTPS-прокси) |
| 📐 | `spec/` | общие протокольные константы и тест-векторы — источник истины |
| 📚 | `docs/` | статус, обновления, решения по транспортам, релизный ранбук |
| 🎬 | `examples/` | запускаемые скрипты solve на каждом языке |
| 📊 | `benchmarks/` | матричный раннер транспортов и отчёты |
| 🧪 | `tests/` | общие фикстуры и опциональные live-проверки |
| 🔧 | `Makefile` | гейты check, package-install, бенчмарки и релиз |

## 🤝 Благодарности

| Роль | Кто |
|---|---|
| 👤 **Автор и мейнтейнер** | [**nazavod777**](https://github.com/nazavod777) |
| 📖 **Исходная логика** | [**xHossein/PyPasser**](https://github.com/xHossein/PyPasser) (MIT) · [invisible_captcha_bypasser](https://github.com/nazavod777/invisible_captcha_bypasser) (основа) |
| 🤖 **Собрано с AI-инструментами** | <img src="assets/c-claude.svg" height="20" align="absmiddle" alt="Claude Code"> (Anthropic) · модели <img src="assets/c-zai.svg" height="20" align="absmiddle" alt="GLM · Z.ai"> · <img src="assets/c-t3code.svg" height="20" align="absmiddle" alt="T3 Code"> (терминал) |

AI-инструменты выше использовались как ассистенты разработки — спасибо их командам. Решения по дизайну, ревью и ответственность за код остаются за автором.

## 📄 Лицензия

[MIT](LICENSE) · протокольные данные и элементы дизайна произведены от PyPasser — см. [NOTICE](NOTICE)

---

<div align="center"><sub>Нравится проект? Поддержи автора ☕</sub></div>

<div align="center">

<a href="https://nazavod.dev"><img src="assets/site.svg" height="28" alt="nazavod.dev"></a> <a href="https://t.me/bio_nazavod"><img src="assets/telegram.svg" height="28" alt="Telegram"></a> <a href="https://github.com/nazavod777"><img src="assets/github.svg" height="28" alt="GitHub"></a> <a href="https://x.com/nazavod777"><img src="assets/x.svg" height="28" alt="X"></a> <a href="https://antidrain.me"><img src="assets/antidrain.svg" height="28" alt="AntiDrain"></a>

<br><br>

<a href="https://nazavod.dev/coffee/"><img src="assets/coffee.svg" width="300" height="54" alt="Buy me a coffee"></a>

</div>
