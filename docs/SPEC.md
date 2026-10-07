# Invoice / Document Processing Automation, v2

Рабочая спецификация. Заменяет `03_invoice_document_automation.md`.

## 1. Цель

Portfolio-проект: автоматическая обработка входящих счетов из Gmail с проверяемой extraction, детерминированной validation и human review.

Проект должен показать две вещи: выходу AI не доверяют без проверки, и качество измерено, а не заявлено.

Сценарий: небольшая компания получает счета поставщиков PDF-вложениями на общий ящик. Сейчас сотрудник вручную переносит данные в таблицу.

## 2. Что изменилось относительно v1

- Self-reported confidence от LLM убран. Маршрутизация идёт по результатам проверок (раздел 7).
- Добавлены тестовый корпус и eval. Все цифры в README берутся только оттуда (раздел 14).
- Проверка сумм учитывает скидки, доставку, несколько ставок налога и допуск на округление.
- Добавлены нормализация vendor и два уровня дубликатов: файл и счёт (раздел 8).
- Определена граница n8n / FastAPI и владелец состояния (раздел 4).
- Review UI включён в стек, с превью PDF и статусом по каждому полю (раздел 13).
- Добавлены tests, CI, auth, status machine, definition of done и этапы с оценкой.

## 3. Принятые решения

Можно изменить до старта Этапа 0. После старта менять только осознанно, с правкой этого файла.

| # | Решение | Выбор | Причина |
|---|---|---|---|
| 1 | Extraction | Два пути. Есть текстовый слой: `pdfplumber`, затем LLM по тексту. Нет текста (скан): рендер страниц через `pypdfium2`, затем vision-модель | Текстовый путь дешевле и проверяем через grounding |
| 2 | LLM provider | Интерфейс `ExtractionProvider`, одна реализация на старте: Groq (free plan). Provider и model id из env. Нужен provider с vision и structured output. На 2026-10-06: текст `openai/gpt-oss-120b`, сканы `qwen/qwen3.8-27b` (единственная vision-модель free plan) | Бесплатно (решение 21.1). Strict json_schema проверен на обеих моделях, для vision только с отключённым reasoning. Model id не хардкодить по памяти, сверять с актуальной документацией |
| 3 | Confidence | Не используется. Только результаты проверок | Самооценка модели не откалибрована |
| 4 | Сканы | В v1 всегда идут в review | Grounding на них не выполняется |
| 5 | Состояние | Postgres. Владеет им FastAPI | n8n не хранит бизнес-состояние |
| 6 | n8n | Только triggers, уведомления, Sheets export, error workflow | Логика, которой нужны тесты, живёт в Python |
| 7 | Очередь | Без Celery и Redis. Статус и attempts в `documents`, worker берёт задачи через `FOR UPDATE SKIP LOCKED` | Минимум инфраструктуры, поведение при сбое видно в БД |
| 8 | DB access | Прямое подключение к Postgres (SQLAlchemy 2.0 + psycopg). SQL-миграции в `supabase/migrations` | Нужны транзакции и блокировки строк |
| 9 | Оригиналы | Supabase Storage, private bucket. Никогда не удаляются | Требование v1 |
| 10 | Review UI | Next.js (App Router), 3 экрана | Уже в стеке |
| 11 | Экспорт | CSV с фиксированными колонками и строка в Google Sheets через n8n | Интеграции с QuickBooks / Xero нет, это ограничение v1 |
| 12 | PDF-библиотеки | `pdfplumber`, `pypdfium2` | Permissive лицензии. PyMuPDF не использовать (AGPL) |
| 13 | Деньги | `Decimal` в коде, `NUMERIC(18,4)` в БД, валюта отдельным полем ISO 4217 | Без float |
| 14 | Окружение | Разработка на локальном стеке Supabase CLI (Postgres, Storage, Auth в Docker). CI: чистый Postgres той же major-версии. Hosted-проекта нет | Бесплатно (решение 21.2). Миграции не ссылаются на схемы `auth` и `storage`, поэтому работают на чистом Postgres |
| 15 | Инструменты | Makefile как единая точка входа, uv (Python 3.13), npm | `make` стандартен для ревьюеров. Рецепты без shell-специфики, работают и под sh, и под Windows cmd |

## 4. Архитектура

```text
Gmail (label Invoices/Inbox)
   ↓  n8n: Gmail Trigger, отдельный запрос на каждое PDF-вложение
POST /api/documents        FastAPI: sha256, сохранить файл, создать documents, ответить 202
   ↓
Worker (очередь в Postgres)
   1. File checks          size, MIME, encrypted, page count, duplicate_file по sha256
   2. Text extraction      pdfplumber; нет текста → render pages
   3. LLM extraction       structured output, значения как напечатаны в документе
   4. Schema validation    Pydantic; одна repair-попытка
   5. Normalization        числа, даты, currency, vendor (детерминированно)
   6. Business checks      арифметика, grounding, даты, дубликаты
   7. Routing              auto_approved | needs_review | skipped | failed
   ↓
outbox_events → n8n webhook → Slack / Google Sheets / Gmail label
   ↓
Review UI (Next.js) → Approve / Edit / Reject → invoices
```

Правило границы: n8n не принимает решений о данных счёта. Он доставляет файл и разносит уведомления.

## 5. Status machine

```text
received → processing → skipped        (skip_reason: not_invoice | unsupported_type | duplicate_file)
                      → failed         (failure_reason, см. раздел 10)
                      → needs_review → approved → exported
                                     → rejected
                      → auto_approved → exported
failed → processing    (reprocess)
failed → approved      (ручной ввод, approval_mode = manual_entry)
```

Каждый переход пишется в `document_events`. Недопустимый переход отклоняется на уровне кода и покрыт тестом.

Очередь: worker берёт документ в статусе `received`, документ в `processing` без `locked_at` (после Reprocess) или документ в `processing`, чей `locked_at` старше `PROCESSING_TIMEOUT` (worker упал). Повторный захват пишет событие `requeued` и увеличивает `attempts`; новый переход статуса для этого не нужен.

## 6. Extraction contract

Принцип: LLM находит значения, код их интерпретирует. Модель возвращает строки в том виде, как они напечатаны. Числа, даты и валюту нормализует код.

```json
{
  "document_type": "invoice",
  "vendor_name_raw": "ACME Supplies Inc.",
  "vendor_tax_id_raw": null,
  "invoice_number_raw": "INV-2026-0142",
  "invoice_date_raw": "03/04/2026",
  "due_date_raw": null,
  "currency_raw": "$",
  "subtotal_raw": "1,200.00",
  "discount_raw": null,
  "shipping_raw": null,
  "tax_lines": [{"label": "VAT 10%", "amount_raw": "120.00"}],
  "tax_inclusive_note_raw": null,
  "total_raw": "1,320.00",
  "line_items": [
    {"description": "Widget", "quantity_raw": "2", "unit_price_raw": "600.00", "amount_raw": "1,200.00"}
  ]
}
```

Правила:

- `document_type`: `invoice | credit_note | other`.
- Поле отсутствует в документе: `null`. Ничего не вычислять и не достраивать.
- `tax_inclusive_note_raw`: дословная фраза из документа о том, что цены включают налог (например, «All prices include VAT at 20%»), иначе `null`. Модель только копирует текст, вывод о формуле делает код (H2).
- Поля confidence в схеме нет.
- Текст документа передаётся как данные в отделённом блоке. У модели нет tools.
- Сырой ответ модели сохраняется в `extractions.raw_output` вместе с provider, model, prompt_version, latency и токенами.

Нормализованная модель (Pydantic): `Decimal` для сумм, `date` для дат, код ISO 4217, `vendor_id` или кандидат.

Текстовый путь: pdfplumber с сохранением раскладки (`layout=True`), чтобы соседние колонки, например «From» и «Bill to», не склеивались в одну строку. До отправки в LLM из текста удаляются невидимые символы: шрифт меньше 5pt или светлый текст (яркость выше 0.9), который не лежит на тёмной заливке. Этот же очищенный текст используется для grounding (H4). Число удалённых символов даёт W9.

Правила нормализации (код, без LLM). Неоднозначность не скрывается, а возвращается как issue для проверок W1, W2, W3:

- Числа: стиль документа (`1,234.56` или `1.234,56`) определяется по всем суммам сразу. Разделитель с 1, 2 или 4+ цифрами после него считается десятичным. Если стиль определить нельзя, а у какой-то суммы есть разделитель с ровно 3 цифрами (`1.600`), разделитель считается разрядным и ставится issue для W2. Скидка берётся по модулю.
- Даты: формат с годом впереди (`2026-03-04`) однозначен. Числовая дата, где одна часть больше 12, однозначна. Если обе части не больше 12, порядок берётся из сохранённого формата vendor, иначе из другой однозначной даты документа, иначе точка означает DMY, иначе USD означает MDY, иначе DMY; при этом ставится issue для W1. Даты с названием месяца (английские и немецкие) однозначны.
- Валюта: код ISO 4217 из списка распространённых валют. Символ без кода (`$`, `€`, `£`) переводится в код (`$` → USD) с issue для W3.
- Vendor и номер счёта: по разделу 8. Налоговый номер: верхний регистр, только буквы и цифры.

## 7. Проверки и маршрутизация

Результат каждой проверки сохраняется в `check_results` (check_id, severity, status: pass | fail | not_run, field, message) и показывается в UI рядом с полем.

Hard checks. Провал любой отправляет документ в `needs_review`:

| ID | Проверка | Правило |
|---|---|---|
| H1 | Обязательные поля | vendor_name, invoice_number, invoice_date, total, currency заполнены и нормализуются. Также падает, если любое извлечённое значение не удалось нормализовать |
| H2 | Итог | subtotal - discount + shipping + sum(tax_lines) = total, допуск `AMOUNT_TOLERANCE` (по умолчанию 0.02). Если задан `tax_inclusive_note_raw`, налог уже внутри сумм: subtotal - discount + shipping = total |
| H3 | Line items | sum(line_items.amount) = subtotal с тем же допуском, если line items есть. Без line items считается пройденной |
| H4 | Grounding (текстовый путь) | raw-значения invoice_number, total, invoice_date и, если задан, tax_inclusive_note_raw дословно присутствуют в извлечённом тексте после нормализации пробелов |
| H5 | Дубликат счёта | (vendor_id, invoice_number_normalized) уже есть среди одобренных счетов или среди документов в `needs_review`. В review со ссылкой на существующую запись |
| H6 | Даты | invoice_date не позже даты получения письма плюс 1 день, due_date не раньше invoice_date |

Warnings. Тоже отправляют в `needs_review`, поле подсвечивается:

| ID | Проверка |
|---|---|
| W1 | Неоднозначная дата: числовой формат, день и месяц оба не больше 12, у vendor не сохранён формат. Формат с годом впереди (ISO) не считается неоднозначным |
| W2 | Неоднозначный формат числа. Формат определяется по всем суммам документа, warning только если определить нельзя |
| W3 | Валюта указана только символом без кода |
| W4 | Vendor не найден точным совпадением (новый или fuzzy-кандидат). Fuzzy: rapidfuzz `token_sort_ratio` по нормализованным именам и alias, порог `VENDOR_FUZZY_THRESHOLD`, до 3 кандидатов |
| W5 | Документ прошёл через vision-путь, grounding не выполнялся |
| W6 | total выше `AUTO_APPROVE_MAX_TOTAL` |
| W7 | H2 или H3 нельзя выполнить: в документе нет subtotal, total или налога. Счёт без налоговых строк всегда идёт к человеку (решение 2026-10-06: считать налог нулём рискованно, если модель пропустила строку налога и ошиблась в subtotal) |
| W8 | Возможный дубликат: vendor неизвестен, но совпали номер, сумма и дата с существующим счётом |
| W9 | В документе найден и удалён невидимый текст: шрифт меньше 5pt или светлый текст не на тёмном фоне (возможная prompt injection) |

Routing:

- `auto_approved`: `AUTO_APPROVE_ENABLED=true`, все hard checks в статусе pass, warnings нет.
- Иначе `needs_review`.
- По умолчанию в `.env.example` стоит `AUTO_APPROVE_ENABLED=false`.

Следствие: первый счёт от любого нового vendor всегда проходит через человека (W4).

Каждая проверка всегда пишет хотя бы одну строку в `check_results` (pass, fail или not_run), чтобы UI мог показать состояние рядом с полем. `not_run` у hard check тоже блокирует auto-approve. Если при auto-approve сработал уникальный индекс `invoices` (тот же счёт одобрен параллельно), документ уходит в review с H5.

При Approve с правками сервер заново запускает проверки на итоговых значениях. Если hard check всё ещё не проходит (например, в самом счёте не сходятся суммы), одобрить можно только с явным override и обязательным комментарием. Это пишется в `document_events`, `approval_mode = human_override`.

## 8. Vendor и дубликаты

Нормализация имени vendor: NFKC, lowercase, удалить пунктуацию, схлопнуть пробелы, убрать юридические суффиксы (inc, llc, ltd, gmbh, corp, co, sarl, bv, pty).

Порядок сопоставления:

1. `tax_id`, точное совпадение.
2. Нормализованное имя, точное совпадение по `vendors` или `vendor_aliases`.
3. Fuzzy (`rapidfuzz`, порог в env). Только как предложение для reviewer, без автоматического слияния.

Новый vendor создаётся только при одобрении человеком. Если reviewer выбрал существующего vendor для нового написания, оно сохраняется как alias. Если reviewer разрешил неоднозначную дату, формат сохраняется в `vendors.date_format`, и W1 для этого vendor больше не срабатывает.

Это детерминированная память правок. Не называть её обучением или machine learning.

Нормализация номера счёта: uppercase, удалить пробелы и разделители. Ведущие нули не трогать.

Два уровня дубликатов:

- Файл: тот же sha256. Статус `skipped: duplicate_file`, ссылка на оригинал, LLM не вызывается.
- Счёт: H5 и W8. Всегда в review, никогда не отбрасывается молча.

Гонка при одновременном одобрении закрыта уникальным индексом `invoices (vendor_id, invoice_number_normalized)`. Конфликт возвращает 409 со ссылкой на существующий счёт.

## 9. Модель данных

| Таблица | Ключевые поля |
|---|---|
| `documents` | id, gmail_message_id, attachment_id, filename, sha256, storage_path, sender, subject, received_at, status, skip_reason, failure_reason, duplicate_of_document_id, extraction_path (text, vision), attempts, next_attempt_at, locked_at, last_error. UNIQUE (gmail_message_id, sha256) |
| `extractions` | document_id, provider, model, prompt_version, raw_output JSONB (все ответы модели, включая repair), normalized JSONB (document_type, нормализованный счёт, результат поиска vendor, найденные дубликаты), latency_ms, input_tokens, output_tokens |
| `check_results` | extraction_id, check_id, severity, status, field, message |
| `vendors` | canonical_name, normalized_name UNIQUE, tax_id UNIQUE (если задан), date_format (DMY, MDY) |
| `vendor_aliases` | vendor_id, normalized_alias UNIQUE |
| `invoices` | document_id UNIQUE, vendor_id, invoice_number, invoice_number_normalized, invoice_date, due_date, currency, subtotal, discount, shipping, tax_total, total, approval_mode (auto, human, human_override, manual_entry), approved_by, approved_at, exported_at. UNIQUE (vendor_id, invoice_number_normalized) |
| `invoice_line_items` | invoice_id, position, description, quantity, unit_price, amount |
| `review_edits` | document_id, field, extracted_value, final_value, edited_by |
| `document_events` | document_id, event_type, actor, payload JSONB, created_at |
| `outbox_events` | document_id, event_type, payload JSONB, attempts, next_attempt_at, delivered_at, last_error |

Ключ идемпотентности ingest: (gmail_message_id, sha256). Gmail API выдаёт новый `attachmentId` при каждом получении письма, поэтому `attachment_id` хранится только для справки. sha256 считается в API при приёме.

Соглашения: PK типа UUID, статусы и причины как `text` с `CHECK` (не ENUM), все времена `timestamptz`, `created_at` на всех таблицах и `updated_at` там, где строка меняется. Все FK с `ON DELETE RESTRICT`: оригиналы и история не удаляются каскадом. `last_error` хранит текст последней ошибки без содержимого документа.

RLS включён на всех таблицах, политик нет: роли Data API (anon, authenticated) не видят данных, backend подключается к Postgres напрямую владельцем таблиц. Secret key Supabase (`sb_secret_...`, замена устаревшего service role key) используется только на backend.

## 10. Failure handling

| Ситуация | Поведение |
|---|---|
| Письмо без PDF | n8n: Slack notice, label `Invoices/No-attachment`. Document не создаётся |
| Сбой при ingest (API, Storage, DB недоступны) | HTTP node: до 3 попыток. Затем error workflow, Slack alert, label `Invoices/Ingest-failed` |
| Повторная доставка того же вложения | Вернуть существующий document, ничего не создавать |
| Тот же файл в другом письме | `skipped: duplicate_file` |
| Файл больше `INGEST_MAX_FILE_MB` (по умолчанию 50) | API отвечает 413, document не создаётся, retry нет. n8n: error workflow, Slack alert, label `Invoices/Ingest-failed`. Оригинал остаётся в Gmail |
| Файл больше `MAX_FILE_MB` или `MAX_PAGES` | Оригинал сохраняется, `failed: too_large`, LLM не вызывается |
| Повреждённый PDF | `failed: unreadable_pdf` |
| PDF с паролем | `failed: encrypted_pdf` |
| Не invoice | `skipped: not_invoice` |
| Credit note | `skipped: unsupported_type`, Slack notice |
| LLM timeout, 429, 5xx | До 3 попыток, exponential backoff с jitter, для 429 пауза из `retry-after`. Затем `failed: llm_unavailable` |
| 429 с ожиданием дольше `LLM_MAX_WAIT_SECONDS` (дневной лимит) или неверный API key | Без ожидания `failed: llm_unavailable`. Eval останавливается и ничего не сохраняет |
| Скан длиннее `LLM_MAX_VISION_PAGES` (у Groq 3 изображения на запрос) | `failed: too_large`, LLM не вызывается |
| Malformed JSON или schema не прошла (в том числе `json_validate_failed` от Groq) | Одна repair-попытка с текстом ошибок. Затем `failed: invalid_extraction` |
| Worker упал во время processing (или Storage недоступен) | `locked_at` старше `PROCESSING_TIMEOUT`: задача возвращается в очередь. После `WORKER_MAX_ATTEMPTS` попыток `failed: processing_timeout` |
| Provider отклоняет все запросы (ключ, дневной лимит) | Текущий документ `failed: llm_unavailable`, worker делает паузу 10 минут, чтобы не провалить всю очередь подряд |
| n8n webhook или Slack недоступны | Outbox: до 5 попыток с паузой 30 с × 2^попытка. Статус документа не меняется. Недоставленные события видны в stats |
| Google Sheets не подключён (`GOOGLE_SHEET_ID` пуст) | n8n отвечает `{"exported": false}`: событие считается доставленным, но документ не переходит в `exported` |

Общие правила: ни один retry не бесконечен. Оригинал не удаляется ни при каком исходе. Любой `failed` виден в UI с действиями Reprocess и Enter manually.

## 11. API (FastAPI)

| Метод | Назначение |
|---|---|
| `POST /api/documents` | Multipart: файл и метаданные письма. Auth: shared secret в заголовке `X-Ingest-Secret`. Идемпотентен: 202 для нового документа, 200 для повторной доставки. 413 выше `INGEST_MAX_FILE_MB`, 503 если Storage или БД недоступны (n8n повторит). Оригинал хранится по пути `originals/<sha256[:2]>/<sha256>`, один объект на одинаковое содержимое |
| `GET /api/documents?status=` | Список для очереди. `status` можно повторять (`?status=approved&status=exported`) |
| `GET /api/documents/{id}` | Extraction (raw-значения из ответа модели и нормализованные), проверки, события, signed URL на PDF |
| `GET /api/vendors` | Справочник vendors для выбора vendor в форме |
| `POST /api/documents/{id}/approve` | Итоговые значения полей, опционально override и комментарий |
| `POST /api/documents/{id}/reject` | Причина обязательна |
| `POST /api/documents/{id}/reprocess` | Только из `failed` |
| `GET /api/invoices/export.csv` | Фильтр по датам |
| `GET /api/stats` | Данные для экрана Stats: документы по статусам, review rate по переходам `processing → needs_review/auto_approved`, edit rate по полям среди одобренных человеком, failures по причинам, outbox pending и undeliverable. Доли как hits/total |
| `GET /health` | Проверка API и БД |

## 12. n8n workflows

Экспортируются в `n8n/` в виде JSON без credentials. n8n 2.42.3 работает локально в docker compose; `make n8n-import` загружает workflows и публикует `invoice_events`. Gmail и Sheets credentials создаются в UI n8n (см. `docs/integrations.md`). Workflows берут адреса и секреты из env контейнера n8n: `API_URL`, `INGEST_SHARED_SECRET`, `N8N_WEBHOOK_SECRET`, `SLACK_WEBHOOK_URL`, `GOOGLE_SHEET_ID`. Если `SLACK_WEBHOOK_URL` пуст, сообщения в Slack пропускаются; если пуст `GOOGLE_SHEET_ID`, строка в Sheets не пишется.

1. `invoice_ingest`: Gmail Trigger (label `Invoices/Inbox`, с вложениями) → разбить по вложениям → оставить PDF → `POST /api/documents` → label `Invoices/Received`.
2. `invoice_events`: Webhook с проверкой секрета → switch по типу события. `needs_review`: Slack со ссылкой на экран документа. `approved` и `auto_approved`: Google Sheets (append or update по invoice id) и Slack. `failed`: Slack alert.
3. `error_handler`: Error Trigger → Slack.

Запись в Sheets идёт через append or update по `invoice_id`, чтобы повторная доставка события не создала вторую строку. `invoice_events` отвечает `{"exported": true}` только после записи строки; только тогда API переводит одобренный документ в `exported` и ставит `invoices.exported_at`.

Для локальной работы без почты `make demo-send` отправляет корпус в `POST /api/documents` так же, как это делает n8n. Для демо с Gmail `scripts/gmail_seed.py` кладёт письма с корпусом в demo-ящик через Gmail API (`messages.insert`), отправлять письма никому не нужно.

## 13. Review UI (Next.js)

1. **Queue.** Вкладки Needs review, Failed, Skipped, Approved. Колонки: vendor, номер, сумма, дата получения, число флагов.
2. **Document.** Слева PDF (signed URL). Справа форма: у каждого поля статус (ok, warning, error), текст проверки и raw-значение рядом с нормализованным. Таблица line items. Баннер дубликата со ссылкой. Кнопки Approve, Reject, Reprocess. Override с обязательным комментарием. Внизу timeline событий.
3. **Stats.** Количество по статусам, review rate, edit rate по полям, failures по причинам, недоставленные outbox-события.

Решения Этапа 5:

- Интерфейс на английском, светлая и тёмная тема по настройке системы. Экран Stats делается на Этапе 6.
- Вкладка Approved показывает `approved`, `auto_approved` и `exported`.
- Суммы в форме и в очереди приходят из API строками и не проходят через float. Форма проверяет формат до отправки (decimal, ISO-дата, код валюты из трёх букв), окончательную проверку делает сервер.
- Vendor выбирается из совпадения, кандидатов или справочника (`GET /api/vendors`), либо вводится как новый.
- При W1 форма показывает оба прочтения даты (DMY и MDY), человек выбирает одно. Выбор уходит в `date_format` и сохраняется у vendor.
- Override не предлагается заранее. Approve сначала отправляется без override; если сервер отвечает 422 со списком непройденных hard checks, форма показывает их и даёт подтвердить override с обязательным комментарием.
- У документа в `failed` форма пустая, кнопка Approve manual entry.
- Если vendor с таким именем появился в справочнике после обработки, approve нового vendor получает 409 с `vendor_id`. Форма выбирает этого vendor и просит проверить и нажать Approve ещё раз; повторно сама не отправляет.
- Статус поля не показывается, если проверки не запускались (нет extraction) или если поле пустое и ни одна проверка его не отметила: «ok» у пустого поля читалось бы как подтверждённое значение.
- У пропущенного документа без extraction форма не показывается, только баннер, PDF и timeline.
- У одобренного счёта Tax ID берётся из записи vendor (`invoice.vendor_tax_id` в `GET /api/documents/{id}`), в `invoices` он не хранится.
- Даты со временем показываются с названием месяца (6 Oct 2026, 23:38), чтобы день и месяц нельзя было перепутать.

Auth: Supabase Auth. Все review endpoints требуют JWT. API проверяет подпись по опубликованным ключам проекта (JWKS, ES256 или RS256), audience `authenticated` и issuer `SUPABASE_PUBLIC_URL/auth/v1`. Регистрация из UI выключена, рецензентов создают в Supabase Studio.

Approve принимает итоговые значения полей. Сервер заново прогоняет H1, H2, H3, H5 (только среди уже одобренных счетов) и H6; grounding и warnings к значениям, введённым человеком, не применяются. Новый vendor создаётся при одобрении, новое написание выбранного vendor сохраняется как alias, выбранный формат даты сохраняется у vendor. Изменённые поля пишутся в `review_edits`. Approve документа в `failed` означает ручной ввод (`manual_entry`). Reprocess сбрасывает attempts.

## 14. Тестовый корпус и eval

Корпус генерируется скриптом из ground truth JSON, поэтому разметка верна по построению. Вымышленные компании, реальных данных нет.

Состав, 35 документов:

- 13 чистых счетов с текстовым слоем, 5 разных layouts (один с ISO-датами, где день и месяц не больше 12)
- 4 со скидкой, доставкой, несколькими ставками налога, tax-inclusive ценами
- 3 с европейским форматом чисел и дат
- 2 многостраничных
- 4 скана (растр, шум, небольшой поворот)
- 2 дубликата: один побайтно идентичный, один перерендеренный
- 3 не-счёта (договор, прайс-лист, чек)
- 1 повреждённый PDF, 1 с паролем
- 1 с внедрённой инструкцией в тексте (prompt injection)
- 1 с арифметической ошибкой в самом документе

Устройство корпуса:

- Описания документов в коде (`corpus/definitions.py`) → `corpus/ground_truth/*.json` → PDF в `corpus/documents/` рендерятся из JSON. `make corpus` пересоздаёт всё детерминированно.
- PDF коммитятся в репозиторий, чтобы у всех были одинаковые входные файлы.
- Каждый ground truth хранит значения как напечатаны (ожидаемый ответ LLM), нормализованные значения (для exact match) и ожидаемый маршрут: статус, причину и флаги проверок.
- Счета на английском, 3 европейских на немецком. Налоговые номера с несуществующим префиксом `ZZ`, e-mail на домене `.example`.
- `corpus/manifest.json`: порядок обработки (дубликаты после оригиналов) и sha256 файлов.

Контекст прогона eval: полный pipeline на временной чистой БД, известные vendors заранее загружаются из `corpus/vendors_seed.json`, документы идут в порядке manifest, `AUTO_APPROVE_ENABLED=true` только внутри eval. Без известных vendors все документы ушли бы в review из-за W4.

`eval/run_eval.py` считает:

- точность по каждому полю после нормализации (exact match)
- line items: совпадение количества строк и сумм
- точность определения типа документа
- review rate: needs_review / (needs_review + auto_approved); skipped и failed не входят
- auto-approve precision: доля авто-одобренных, где все ключевые поля верны. Ключевые поля = поля H1: vendor, номер, дата, total, валюта
- долю документов с ошибкой в поле, которые проверки отправили в review
- latency и токены на документ

Доли хранятся как пары hits/total, без float.

Результат сохраняется в `eval/results/` с датой, provider и model (JSON и Markdown, существующий файл не перезаписывается). Раздел Results в README берётся только оттуда, с указанием размера корпуса и того, что он синтетический.

`make eval-oracle` проверяет сам каркас eval: ответы берутся прямо из ground truth, все метрики точности должны быть 100%. Его результаты никогда не сохраняются.

`make eval-smoke` гоняет настоящую LLM на 3 документах (текстовый счёт, скан, не-счёт), чтобы проверить настройку, не тратя дневной лимит. Результаты не сохраняются. Перед полным `make eval` запускать его.

С Этапа 3 eval гоняет настоящий worker: документы по одному в порядке manifest попадают во временную БД, обрабатываются полностью, затем читаются статус, флаги проверок и нормализованные значения. В результатах для каждого документа хранятся ожидаемый и полученный маршрут, флаги и сырой ответ модели. Интеграционный тест прогоняет весь корпус через этот же путь с записанными ответами модели из прогона 2026-10-06 (`api/tests/fixtures/recorded_answers.json`) и требует, чтобы маршрут каждого документа совпал с ground truth.

Реальная LLM вызывается только в eval (`make eval`). В CI её нет.

## 15. Security

- Shared secret в обе стороны: n8n → API (`X-Ingest-Secret`, сравнение constant-time в API) и API → n8n webhook (`X-Webhook-Secret`, проверка в первом узле workflow). Без настроенного секрета приём отвечает 503.
- Storage private. Превью PDF только через короткоживущий signed URL.
- Лимиты размера и числа страниц. Тип файла проверяется по magic bytes.
- Prompt injection: текст документа идёт в отделённом блоке данных, у модели нет tools, выход ограничен схемой. Невидимый текст удаляется до LLM, такой документ всегда идёт в review (W9). Искажённые поля ловятся H2, H3, H4. Сценарий есть в корпусе. Ограничение: текст, спрятанный под картинкой или белым прямоугольником, этим правилом не находится.
- В логах нет полного текста документов. Секреты только в env, в репо `.env.example`.

## 16. Tests, CI, запуск

- `pytest`. Unit: нормализация чисел и дат (table-driven), vendor, проверки H и W, routing, переходы статусов, идемпотентный ingest, дубликаты, retry policy.
- Integration: pipeline на всём корпусе с fake provider и записанными ответами модели; worker на временной БД (очередь, повторы, дубликаты, блокировки).
- CI (GitHub Actions): `ruff`, `pytest`, для web `eslint`, `tsc` и `vitest` (чистая логика формы и отображения, без браузера).
- Локальный запуск одной командой: `make up` поднимает локальный Supabase и через docker compose api, worker, web и n8n. `.env.example` полный.

Структура репозитория:

```text
api/                  FastAPI, worker, domain logic, tests
web/                  Next.js review UI
n8n/                  exported workflows
supabase/migrations/  SQL migrations
corpus/               generator, templates, ground truth
eval/                 run_eval.py, results/
docs/                 SPEC.md, architecture, screenshots
```

## 17. Вне scope (v1)

- Credit notes, несколько счетов в одном PDF, вложения не в PDF
- Интеграции с QuickBooks, Xero и другими accounting-системами
- Проверка банковских реквизитов, оплата
- Auto-approve для сканов
- Multi-tenant, роли пользователей
- Fine-tuning

Ограничения v1:

- Groq free plan (на 2026-10-05): 30 RPM, 8K TPM, 200K TPD на модель, не больше 3 изображений в запросе. Eval делает паузы между вызовами, дневной лимит токенов ограничивает число полных прогонов в сутки.
- `AUTO_APPROVE_MAX_TOTAL` одно число, сравнивается с total без учёта валюты.
- Vision-модель `qwen/qwen3.8-27b` у Groq в статусе preview и может быть отключена. Сканы длиннее `LLM_MAX_VISION_PAGES` не обрабатываются автоматически (`failed: too_large`, ручной ввод). Для другой модели без этого ограничения достаточно поменять значение в env.
- Список кодов валют ограничен распространёнными валютами. Неизвестный код означает, что нормализация не прошла, и счёт идёт в review.

Всё это перечисляется в разделе Limitations в README.

## 18. Этапы

Оценки грубые. Порядок важен: корпус и eval идут до extraction, чтобы каждый следующий этап был измерим.

| Этап | Содержание | Часы |
|---|---|---|
| 0 | Каркас репо, config, миграции, CI, локальный запуск | 4-5 |
| 1 | Генератор корпуса, ground truth, каркас eval | 6-8 |
| 2 | Extraction: PDF text, vision-путь, provider interface, schema, нормализация | 10-12 |
| 3 | Проверки, vendor, дубликаты, routing, status machine, worker и retries | 10-12 |
| 4 | API, outbox, n8n workflows | 8-10 |
| 5 | Review UI | 10-12 |
| 6 | Stats, прогон eval, README, скриншоты, demo video | 8-10 |
| | Итого | 56-69 |

Если времени не хватает, резать в таком порядке: экран Stats (таблица eval в README остаётся), Google Sheets export, сохранение `vendors.date_format`.

## 19. Definition of done

- [ ] Локальный запуск одной командой по README, `.env.example` полный
- [ ] `make eval` воспроизводит таблицу Results
- [ ] Полный путь работает на demo-ящике: Gmail → review → approve → Sheets и Slack
- [ ] Каждая строка раздела 10 покрыта тестом или показана в demo
- [ ] CI зелёный
- [ ] README на английском: Problem, Solution, Architecture, Extraction schema, Validation rules, Human review, Failure handling, Results, Demo, Tech stack, Limitations
- [ ] В README нет ни одной цифры, которой нет в `eval/results/`
- [ ] Demo video на 60-90 секунд

## 20. Demo-сценарий

1. Письмо со счётом известного vendor: auto-approve, строка в Sheets, Slack.
2. Счёт нового vendor: review, PDF рядом с полями, Approve.
3. Счёт с неоднозначной датой: warning, правка, сохранение формата у vendor.
4. Повторная отправка того же счёта: баннер дубликата.
5. Счёт, где не сходятся суммы: hard check, override с комментарием.
6. Повреждённый PDF: failed, оригинал на месте, Slack alert.
7. Таблица Results из eval.

## 21. Открытые вопросы

Вопросы 1-3 закрыты перед Этапом 0 (2026-10-05).

1. Какой LLM provider использовать первым. **Решено:** Groq, free plan. Требование: бесплатный provider с vision и structured output. Model id выбираются на Этапе 2 по актуальной документации.
2. Supabase: hosted-проект или локальный стек через Supabase CLI для разработки. **Решено:** локальный стек, CI на чистом Postgres, hosted-проекта нет (решение 14).
3. Нужен ли публичный live demo. **Решено:** нет, только video, скриншоты и воспроизводимый локальный запуск.
4. Vision-путь: Groq принимает не больше 3 изображений в запросе. Что делать со сканами длиннее 3 страниц. **Решено на Этапе 2:** лимит задаётся настройкой `LLM_MAX_VISION_PAGES` (для Groq 3), более длинные сканы получают `failed: too_large` и вводятся вручную. Склейка ответов по частям отклонена: ошибки на стыках страниц.
5. Скрытый текст и prompt injection (найдено на Этапе 3). Подставные значения, напечатанные невидимым шрифтом, проходили grounding (H4). **Решено на Этапе 4:** невидимый текст удаляется до LLM и до H4, документ уходит в review с W9.
