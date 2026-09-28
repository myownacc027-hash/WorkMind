
# Bitrix24 Source Contract v1

**Статус:** Accepted
**Версия контракта:** `b24-source-v1`
**Тип интеграции:** optional source module
**Дата:** 2026-09-25

## 1. Назначение

Bitrix24 предоставляет WorkMind структурированные данные о рабочих задачах.

Bitrix24 не является зависимостью WorkMind Core.

WorkMind должен продолжать работать при полном отсутствии B24 integration.

Текущий контракт определяет единое source representation для двух transports:

- XLSX snapshot importer;
- ScraplingB24MI.

Подробный mapping XLSX:

[Bitrix24 XLSX Field Mapping](bitrix24-field-mapping.md).

Архитектурное решение:

[ADR-006: Bitrix24 source identity and snapshot semantics](../adr/006-bitrix24-source-identity-and-snapshot-semantics.md).

---

## 2. Logical source identity

Для задачи используется:

~~~text
source_type = b24_task
external_id = <Bitrix24 Task ID>
~~~

`external_id` является строковым значением.

Если XLSX хранит ID как Excel number, например:

~~~text
221516.0
~~~

normalizer обязан получить:

~~~text
"221516"
~~~

Fractional Task ID недопустим.

Повторное получение одного Task ID обновляет существующий source object и не
создаёт новую logical task.

---

## 3. Transport architecture

~~~text
                Bitrix24
                   |
        +----------+----------+
        |                     |
       XLSX              ScraplingB24MI
        |                     |
        +----------+----------+
                   |
                   v
             B24 Adapter
                   |
        +----------+-----------+
        |                      |
        v                      v
   Raw Snapshot          Normalized B24Task
                               |
                 +-------------+-------------+
                 |                           |
                 v                           v
       deterministic facts           Description/chunks
                 |                           |
                 |                       LLM provider
                 |                           |
                 +-------------+-------------+
                               |
                               v
                          validation
                               |
                               v
                         Knowledge Layer
~~~

XLSX и ScraplingB24MI не должны иметь две независимые domain models.

Оба transport adapter обновляют один logical B24 source object.

---

## 4. Source envelope

Минимальный source envelope:

~~~text
B24TaskSource
├── source_type
├── external_id
├── source_url
├── project_external_id
├── transport
├── imported_at
├── snapshot_id
├── source_timezone
├── present_columns
├── raw_row
├── raw_hyperlinks
├── raw_hash
├── canonical_hash
└── extraction_input_hash
~~~

### `source_type`

Для задач:

~~~text
b24_task
~~~

### `external_id`

Stable Bitrix24 Task ID.

### `source_url`

Исходная ссылка на task, если transport её предоставляет.

### `project_external_id`

Bitrix24 workgroup/project ID, если он может быть достоверно извлечён из
source hyperlink или live B24 object.

### `transport`

Допустимые значения v1:

~~~text
xlsx
scrapling
~~~

### `snapshot_id`

Идентификатор конкретного импорта/snapshot.

### `source_timezone`

Timezone исходной B24 installation/import configuration.

### `present_columns`

Набор колонок, реально присутствовавших в конкретном export.

Это необходимо отличать от пустых значений.

### `raw_row`

Полное transport-level представление source row.

Неизвестные и пока не используемые поля не выбрасываются.

### `raw_hyperlinks`

Hyperlinks хранятся отдельно от display values.

---

## 5. Normalized B24Task

Основные canonical fields:

~~~text
external_id
title
description

last_activity_at
deadline_at
created_at
started_at
updated_at
closed_at

creator_label
assignee_label
coassignee_labels
observer_labels
additional_employee_labels

status
stage

project_external_id
project_name

parent_external_id
parent_title_snapshot
parent_stage_snapshot

planned_duration_minutes
spent_minutes
track_work_time

tags

business_direction
request_type
task_kind
product

planned_release_at
actual_release_at

deadline_change_in_progress
deadline_change_count

helpdesk_number
progress_percent

crm_context_raw
priority_raw
deadline_change_request_raw
~~~

Полный mapping находится в
[bitrix24-field-mapping.md](bitrix24-field-mapping.md).

---

## 6. Raw fields are never silently discarded

XLSX содержит custom fields, часть которых может быть:

- временно пустой;
- специфичной для одной организации;
- плохо документированной;
- добавленной позже;
- удалённой из будущего export.

Поэтому source adapter обязан сохранять полный `raw_row`.

Наличие canonical mapping не означает, что original value можно удалить.

---

## 7. Three independent fingerprints

### 7.1. raw_hash

`raw_hash` отвечает на вопрос:

> Изменилось ли вообще transport-level представление source?

Fingerprint включает как минимум:

- набор present columns;
- raw values;
- raw hyperlinks.

Неизвестное поле также влияет на `raw_hash`.

### 7.2. canonical_hash

`canonical_hash` отвечает на вопрос:

> Изменился ли normalized B24Task?

Он строится по canonical source state после deterministic normalization.

Transport metadata вроде имени XLSX-файла не должна сама по себе менять
`canonical_hash`.

### 7.3. extraction_input_hash

`extraction_input_hash` отвечает на вопрос:

> Изменился ли semantic input настолько, что knowledge extraction требуется
> выполнить повторно?

Минимальный extraction input:

~~~text
external_id
title
description
parent_external_id
project
product
request_type
selected semantic context
~~~

Поля вроде:

~~~text
Дней с даты постановки
Дней ждет контроля
~~~

не должны самостоятельно менять `extraction_input_hash`.

Это предотвращает бессмысленный повторный вызов LLM каждый день.

---

## 8. Snapshot semantics

XLSX export считается snapshot transport, но не authoritative complete inventory.

### Case 1

Task присутствует.

Column присутствует.

Value присутствует.

~~~text
-> canonical field is updated
~~~

### Case 2

Task присутствует.

Column присутствует.

Cell пустая.

~~~text
-> canonical field becomes NULL
~~~

### Case 3

Task присутствует.

Column отсутствует из export полностью.

~~~text
-> previous canonical value is not cleared
~~~

### Case 4

Task отсутствует из snapshot.

~~~text
-> no deletion
-> no automatic close
-> no deactivation
~~~

Отсутствие объекта из частичной XLSX-выгрузки не является evidence удаления
объекта в B24.

---

## 9. Summary and non-task rows

Task source object должен иметь валидный Task ID.

Правило XLSX importer:

~~~text
missing Task ID
    ->
row is not B24Task
~~~

Строки итогов вроде:

~~~text
Сумма:
~~~

не импортируются как tasks.

Они могут при необходимости храниться как snapshot metadata.

Importer не должен зависеть от позиции строки в конце файла.

---

## 10. Datetime semantics

В XLSX даты представлены как naive Excel datetimes без timezone offset.

Запрещено автоматически интерпретировать их как UTC.

Каждый source connector/import должен иметь явную настройку:

~~~text
source_timezone
~~~

Pipeline:

~~~text
B24 local datetime
    |
    v
source_timezone
    |
    v
canonical timezone-aware datetime
    |
    v
PostgreSQL
~~~

---

## 11. Participant labels are not Person identity

XLSX поля:

- Постановщик;
- Исполнитель;
- Соисполнители;
- Наблюдатели;
- Дополнительные сотрудники

содержат labels.

Label является source truth о том, что было показано B24 export.

Label не является достаточным strong identity для `Person`.

Причины:

- ФИО может иметь разный порядок слов;
- разные exports могут использовать разные display names;
- значение может быть организационной ролью;
- значение может быть технической очередью;
- совпадение строки имени не гарантирует identity.

Примеры классов значений:

~~~text
Person-like label
Role-like label
Queue-like label
Unknown participant label
~~~

XLSX importer не создаёт Person entity только на основании label.

Strong identity появляется при наличии:

- B24 user ID;
- другого устойчивого external user ID;
- подтверждённого local mapping.

---

## 12. Parent tasks

`ID базовой задачи` нормализуется в:

~~~text
parent_external_id
~~~

Parent task может отсутствовать в конкретном snapshot.

Дополнительно сохраняются:

~~~text
parent_title_snapshot
parent_stage_snapshot
~~~

Если parent ещё не импортирован:

~~~text
child
  |
  +--> unresolved external reference: b24_task:<parent_id>
~~~

Не создаётся выдуманная полная parent Task entity.

После появления parent source object reference может разрешиться
детерминированно.

---

## 13. Hyperlinks and project identity

Hyperlink поля являются source data.

Основной task hyperlink может иметь вид:

~~~text
.../workgroups/group/<group_id>/tasks/task/view/<task_id>/
~~~

Из него при строгом соответствии формату разрешено извлекать:

~~~text
project_external_id
external_id
source_url
~~~

Название проекта:

~~~text
project_name
~~~

хранится отдельно.

`project_name` не используется как единственный identity key.

Если URL не соответствует ожидаемому формату, raw hyperlink сохраняется,
но IDs не угадываются.

---

## 14. Multivalue fields

К multivalue source fields относятся:

- Соисполнители;
- Наблюдатели;
- Теги;
- Дополнительные сотрудники.

Transport-specific parser может формировать normalized list.

При этом всегда сохраняется original raw cell.

Нормализация включает:

- trim surrounding whitespace;
- сохранение source order;
- удаление пустых элементов;
- отсутствие автоматического Person resolution.

---

## 15. Duration fields

Поля:

~~~text
Планируемая длительность
Затрачено
~~~

в исследованном XLSX экспортируются как elapsed duration strings вида:

~~~text
00:05
08:00
13:05
~~~

Normalized representation:

~~~text
planned_duration_minutes
spent_minutes
~~~

Parser должен поддерживать duration больше 24 часов.

Такие значения нельзя трактовать как time-of-day.

---

## 16. Boolean fields

Известные transport mappings v1:

~~~text
Учитывать рабочее время:
Y -> true

В процессе смена крайнего срока:
Да  -> true
Нет -> false

Использовался ИИ:
Да  -> true
Нет -> false
~~~

Пустая ячейка:

~~~text
NULL
~~~

Неизвестное значение должно приводить к controlled parse diagnostic,
а не к произвольному bool.

---

## 17. Opaque and derived fields

Некоторые поля пока не имеют достаточно надёжной business semantics.

Например:

~~~text
CRM
Приоритет
Запрос переноса сроков
Создано
~~~

Они сохраняются в raw representation.

Если для поля определён canonical placeholder вида `*_raw`, его значение
переносится без semantic reinterpretation.

Производные snapshot fields:

~~~text
Дней с даты постановки
Дней ждет контроля
~~~

не являются source-of-truth timestamps и не должны создавать самостоятельные
durable claims.

---

## 18. Source authority

Для structured fields действует приоритет:

~~~text
B24 structured field
    >
explicit source statement
    >
indirect report
    >
LLM inference
~~~

LLM не должен переопределять структурированный статус задачи, потому что в
description встретилось старое значение статуса.

---

## 19. Deterministic Knowledge Layer projection

Без LLM можно детерминированно создавать/обновлять task identity:

~~~text
Entity:
type = task
external_source = bitrix24
external_id = <Task ID>
~~~

и structured claims, например:

~~~text
title
status
stage
deadline
project
business_direction
request_type
product
planned_release_at
actual_release_at
~~~

Каждый такой факт сохраняет B24 provenance.

---

## 20. Participant relations

Relations вроде:

~~~text
task --assigned_to--> Person
task --created_by--> Person
~~~

не создаются только по XLSX display label.

До identity resolution source хранит participant label.

После получения strong user identity relation может быть создана или
разрешена отдельно.

---

## 21. Semantic extraction boundary

LLM получает только данные, полезные для semantic knowledge extraction.

Основное содержимое:

~~~text
Task ID
Title
Description
~~~

Допустимый structured context:

~~~text
parent task
project
product
request type
status
stage
~~~

`status` и `stage` передаются только как context.

LLM не должен заново извлекать их как authoritative values.

Semantic extraction используется для:

- business problem;
- goal;
- requirements;
- acceptance criteria;
- restrictions;
- causes;
- decisions;
- affected systems;
- affected processes;
- additional semantic relations.

LLM output остаётся candidate bundle и проходит стандартные:

- canonical schema validation;
- evidence validation;
- identity validation;
- semantic validation;
- controlled apply.

---

## 22. XLSX audit baseline

Контракт v1 разработан по snapshot:

~~~text
tasks_2026-09-24_11_01_21.xlsx
~~~

Наблюдения sample:

- 55 физических Excel columns;
- 37 task rows с Task ID;
- 1 non-task summary row;
- 37 уникальных Task ID;
- 37 descriptions;
- median description length около 2506 символов;
- maximum description length 14862 символа;
- 24 tasks имеют parent ID;
- 9 distinct parent IDs;
- 4 parent tasks присутствуют в snapshot;
- 5 parent tasks отсутствуют в snapshot;
- 85 hyperlinks;
- formulas отсутствуют;
- 21 поле полностью пустое в этом snapshot.

Эти числа являются audit observations конкретного snapshot, а не
ограничениями source contract.

---

## 23. Current scope

Source Contract v1 покрывает:

- B24 Task;
- XLSX snapshot import;
- future Scrapling task synchronization;
- raw preservation;
- canonical normalization;
- source identity;
- parent references;
- participant labels;
- hashing;
- timezone;
- deterministic/LLM boundary.

---

## 24. Future child sources

Отдельными source objects в дальнейшем могут стать:

~~~text
B24Comment
B24ChecklistItem
B24Attachment
B24ChangeEvent
~~~

Они не должны упаковываться в одно поле `description`.

Наличие этих sources зависит от реальной доступности данных через B24 transport.

---

## 25. Implementation order

После принятия Source Contract v1:

~~~text
XLSX reader
    |
    v
snapshot parser
    |
    v
raw B24TaskSource
    |
    v
normalizer
    |
    v
normalized B24Task
    |
    v
idempotent persistence
    |
    +--> deterministic knowledge
    |
    +--> extraction queue when extraction_input_hash changed
~~~

Importer не должен начинаться с LLM prompt или Obsidian rendering.

## 26. Persistence model

The first persistence implementation uses three tables.

### `b24_import_snapshots`

One row describes one transport-level import attempt/snapshot.

It stores:

- transport;
- source name/hash when available;
- source timezone;
- present columns;
- import counters;
- transport metadata.

A snapshot is not interpreted as a complete inventory of Bitrix24 tasks.

### `b24_tasks`

One row represents the current normalized state of one logical Bitrix24 task.

Stable identity remains:

~~~text
source_type = b24_task
external_id = <Bitrix24 Task ID>
~~~

The row contains:

- current canonical fields;
- current raw row and hyperlinks;
- the three fingerprints;
- last snapshot reference.

This table is optimized for current-state reads.

### `b24_task_observations`

An observation records what WorkMind actually received and normalized in a
specific snapshot.

It preserves:

~~~text
raw_row
raw_hyperlinks
present_columns
canonical_json
extraction_input_json
raw_hash
canonical_hash
extraction_input_hash
contract_version
normalizer_version
~~~

Observations are historical audit records and must not be rewritten to look
like a newer source state.

### Why current state and observations are separate

Bitrix24 tasks are mutable.

A task can change status, description, deadline, participants, product, parent
or other fields while keeping the same Task ID.

WorkMind therefore needs both:

~~~text
current state
+
historical source observation
~~~

Using only `b24_tasks` would lose historical provenance.

Using only immutable observations would make current-state access and
idempotent synchronization unnecessarily expensive.

## 27. Evidence versioning constraint

Existing WorkMind extraction is based on `DocumentChunk` evidence.

For mutable B24 tasks, a historical claim must never silently start pointing
to newer text after the task changes.

Therefore semantic extraction integration must preserve this invariant:

~~~text
evidence source version is immutable
~~~

Migration `0012_bitrix24_sources` does not yet connect B24 tasks to
`DocumentChunk`, Qdrant, or `KnowledgeExtractionJob`.

That wiring is intentionally deferred until the XLSX normalizer and
observation persistence are verified.

When extraction is connected, `extraction_input_hash` is the B24 semantic
version fingerprint.

`raw_hash` and `canonical_hash` must not by themselves trigger another LLM
extraction.

## 28. XLSX parser and normalizer invariants

The first B24 transport implementation is a read-only XLSX parser.

Before persistence, the parser verifies the source snapshot and produces a
normalized in-memory representation.

The parser:

- preserves every present XLSX column in `raw_row`;
- preserves hyperlinks separately in `raw_hyperlinks`;
- rejects duplicate headers;
- requires Task ID for a row to become a B24 task;
- skips summary/non-task rows without Task ID;
- rejects fractional Task IDs;
- preserves unresolved parent references;
- validates task IDs embedded in hyperlinks;
- extracts workgroup/project ID from task hyperlinks;
- converts naive datetimes through explicit `source_timezone`;
- supports `Europe/Moscow` on Windows even when the system tzdata package is
  unavailable;
- treats duration fields as elapsed durations;
- supports durations greater than 24 hours;
- splits participant lists used by the audited B24 XLSX on comma, semicolon or
  newline boundaries;
- preserves participant values only as labels;
- performs no database writes during dry-run.

Audit description-length metrics use the raw XLSX cell text. Normalized
description-length metrics are reported separately because normalization trims
surrounding whitespace, including non-breaking spaces.

### Fingerprint boundaries

`raw_hash` includes:

~~~text
present_columns
raw_row
raw_hyperlinks
~~~

`canonical_hash` contains normalized source state.

`extraction_input_hash` contains only semantic extraction input.

Consequently:

~~~text
derived counter change
    -> raw_hash changes
    -> canonical_hash unchanged
    -> extraction_input_hash unchanged

status change
    -> raw_hash changes
    -> canonical_hash changes
    -> extraction_input_hash unchanged

description change
    -> raw_hash changes
    -> canonical_hash changes
    -> extraction_input_hash changes
~~~

This behavior is verified before B24 data is connected to PostgreSQL
persistence or the WorkMind extraction queue.
