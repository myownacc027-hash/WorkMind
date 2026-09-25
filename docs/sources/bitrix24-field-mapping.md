
# Bitrix24 XLSX Field Mapping

**Contract:** `b24-source-v1`
**Snapshot audited:** `tasks_2026-09-24_11_01_21.xlsx`
**Date:** 2026-09-25

Back to:

[Bitrix24 Source Contract v1](bitrix24.md).

## 1. Audit facts

Физический XLSX содержит 55 columns.

Служебный `index`, который может появляться в текстовом представлении
spreadsheet parser, не является Excel column.

Snapshot содержит:

- 37 task rows;
- 37 unique Task IDs;
- одну summary row без Task ID;
- 85 hyperlinks;
- 0 formulas.

24 задачи имеют parent ID.

Используется 9 distinct parent IDs:

- 4 parent tasks находятся в этом snapshot;
- 5 parent tasks находятся вне snapshot.

Описание присутствует у всех 37 задач.

Observed description length:

~~~text
minimum = 55
median  = 2506
maximum = 14862
~~~

## 2. Mapping classes

`canonical`
Поле имеет определённую normalized semantics.

`label`
Authoritative display value, но не stable entity identity.

`reference`
Stable external reference.

`opaque`
Значение сохраняется, но WorkMind пока не интерпретирует business semantics.

`derived`
Производный snapshot indicator, не используемый как durable source truth.

`raw/future`
Поле сохраняется в raw representation и может получить semantics позже.

## 3. Full 55-column mapping

| # | XLSX field | Filled in audit | Normalized field | Type | Class / rule |
|---:|---|---:|---|---|---|
| 1 | ID | 37/37 | `external_id` | string ID | **reference**; required; Excel integer-like numeric -> decimal string |
| 2 | Название | 37/37 | `title` | string | **canonical** authoritative |
| 3 | Описание | 37/37 | `description` | string | **canonical** authoritative; primary semantic extraction text |
| 4 | Последняя активность | 37/37 | `last_activity_at` | datetime | **canonical**; apply `source_timezone` |
| 5 | Крайний срок | 37/37 | `deadline_at` | datetime | **canonical**; apply `source_timezone` |
| 6 | Постановщик | 37/37 | `creator_label` | string | **label**; not Person identity |
| 7 | Исполнитель | 37/37 | `assignee_label` | string | **label**; not Person identity |
| 8 | Соисполнители | 0/37 | `coassignee_labels[]` | list[string] | **label**; multivalue |
| 9 | Наблюдатели | 37/37 | `observer_labels[]` | list[string] | **label**; multivalue |
| 10 | Статус | 37/37 | `status` | string | **canonical** authoritative |
| 11 | Проект | 37/37 | `project_name` | string | **canonical label**; not project identity by itself |
| 12 | Дата создания | 37/37 | `created_at` | datetime | **canonical** |
| 13 | Дата начала работы | 6/37 | `started_at` | datetime nullable | **canonical** |
| 14 | Дата изменения | 37/37 | `updated_at` | datetime | **canonical** |
| 15 | Дата закрытия | 0/37 | `closed_at` | datetime nullable | **canonical** |
| 16 | Планируемая длительность | 10/37 | `planned_duration_minutes` | integer minutes | **canonical**; elapsed duration, not clock time |
| 17 | Учитывать рабочее время | 37/37 | `track_work_time` | bool | **canonical**; `Y -> true` |
| 18 | Затрачено | 12/37 | `spent_minutes` | integer minutes | **canonical**; elapsed duration |
| 19 | Теги | 35/37 | `tags[]` | list[string] | **canonical** multivalue; raw value retained |
| 20 | Стадия | 37/37 | `stage` | string | **canonical** authoritative |
| 21 | Лид | 0/37 | — | raw | **raw/future** CRM reference |
| 22 | Контакт | 0/37 | — | raw | **raw/future** CRM reference |
| 23 | Компания | 0/37 | — | raw | **raw/future** CRM reference |
| 24 | Сделка | 0/37 | — | raw | **raw/future** CRM reference |
| 25 | CRM | 10/37 | `crm_context_raw` | string nullable | **opaque**; do not parse sprint semantics in v1 |
| 26 | ID базовой задачи | 24/37 | `parent_external_id` | string ID nullable | **reference**; parent may be outside snapshot |
| 27 | Название базовой задачи | 24/37 | `parent_title_snapshot` | string nullable | denormalized snapshot label |
| 28 | Поток | 0/37 | `flow_raw` | raw nullable | **opaque** until semantics confirmed |
| 29 | Приоритет | 1/37 | `priority_raw` | raw nullable | **opaque**; observed numeric code, semantics not guessed |
| 30 | Номер заявки в helpdesk | 0/37 | `helpdesk_number` | string nullable | **canonical/future** |
| 31 | % Готовности | 0/37 | `progress_percent` | decimal nullable | **canonical/future**; validate range before use |
| 32 | Направление бизнеса | 37/37 | `business_direction` | string | **canonical** authoritative |
| 33 | Тип заявки | 34/37 | `request_type` | string nullable | **canonical** authoritative |
| 34 | Плановая дата | 0/37 | `planned_at` | datetime nullable | **canonical/future** |
| 35 | Вид задачи | 2/37 | `task_kind` | string nullable | **canonical** source label |
| 36 | Запуск формы | 0/37 | — | raw | **raw/future** custom field |
| 37 | Продукт | 35/37 | `product` | string nullable | **canonical**; preserve source spelling |
| 38 | Задача стандарта | 0/37 | — | raw | **raw/future** custom field |
| 39 | Бизнес-процесс | 0/37 | — | raw | **raw/future** custom field |
| 40 | Объект ГК МИ | 0/37 | — | raw | **raw/future** custom field |
| 41 | Плановая дата релиза | 2/37 | `planned_release_at` | datetime nullable | **canonical** |
| 42 | Фактическая дата релиза | 0/37 | `actual_release_at` | datetime nullable | **canonical** |
| 43 | Запрос переноса сроков | 3/37 | `deadline_change_request_raw` | raw nullable | **opaque**; observed value `1257`; do not treat as bool |
| 44 | В процессе смена крайнего срока | 30/37 | `deadline_change_in_progress` | bool nullable | **canonical**; `Да/Нет` |
| 45 | Время для согласования переноса крайнего срока | 0/37 | — | raw nullable | **raw/future**; type not inferred without sample |
| 46 | Причина переноса крайнего срока | 0/37 | `deadline_change_reason` | string nullable | **canonical/future** |
| 47 | Использовался ИИ | 1/37 | `ai_used` | bool nullable | **canonical**; `Да/Нет` |
| 48 | Комментарий к согласованию | 0/37 | `approval_comment` | string nullable | **canonical/future** |
| 49 | Итог согласования | 0/37 | `approval_result` | string nullable | **canonical/future** |
| 50 | Кол-во переносов сроков | 37/37 | `deadline_change_count` | integer | **canonical** |
| 51 | Дополнительные сотрудники | 0/37 | `additional_employee_labels[]` | list[string] | **label**; not Person identity |
| 52 | Дней с даты постановки | 37/37 | — | number | **derived**; raw only; excluded from extraction hash |
| 53 | Создано | 0/37 | — | raw | **opaque**; semantics not inferred |
| 54 | Дней ждет контроля | 37/37 | — | number | **derived**; raw only; excluded from extraction hash |
| 55 | Стадия базовой задачи | 24/37 | `parent_stage_snapshot` | string nullable | denormalized parent snapshot |

## 4. Completely empty fields in audited snapshot

21 fields были полностью пустыми:

- Соисполнители;
- Дата закрытия;
- Лид;
- Контакт;
- Компания;
- Сделка;
- Поток;
- Номер заявки в helpdesk;
- % Готовности;
- Плановая дата;
- Запуск формы;
- Задача стандарта;
- Бизнес-процесс;
- Объект ГК МИ;
- Фактическая дата релиза;
- Время для согласования переноса крайнего срока;
- Причина переноса крайнего срока;
- Комментарий к согласованию;
- Итог согласования;
- Дополнительные сотрудники;
- Создано.

Пустота в одном snapshot не является основанием удалить поддержку поля.

## 5. Hyperlinks

Observed hyperlink counts:

| Field | Hyperlinks |
|---|---:|
| ID | 37 |
| ID базовой задачи | 24 |
| Название базовой задачи | 24 |
| **Total** | **85** |

Task ID hyperlink является полезным source URL.

Parent ID hyperlink может использоваться для parent external reference.

Hyperlink на parent title сохраняется raw, но его presence не делает title
strong identity.

## 6. Observed project/workgroup mapping

В audited sample task hyperlinks содержали два workgroup IDs:

~~~text
group 13 -> 001 Автоматизация/Разработка
group 49 -> ДСЛ. Операционные задачи
~~~

Это audit observation, а не hard-coded mapping.

Production importer извлекает group ID из URL и хранит отдельно от display name.

## 7. Parent coverage

24/37 tasks имеют parent ID.

9 distinct parent IDs.

Parents present in same snapshot:

~~~text
94104
175020
185038
199109
~~~

Parents absent from snapshot:

~~~text
43033
58730
58771
156043
156051
~~~

Это подтверждает, что parent resolution не может зависеть от completeness
текущего XLSX.

## 8. Special normalization notes

### Numeric IDs

Excel stores Task IDs как numeric cells.

Нормализация:

~~~text
55899.0 -> "55899"
~~~

Fractional value:

~~~text
55899.5
~~~

является parse error.

### Product spelling

Source spelling сохраняется.

Например разные systems могут использовать:

~~~text
1С: WMS
1C: ERP
~~~

Importer не выполняет silent spelling correction или ontology mapping.

Ontology normalization выполняется отдельным controlled layer.

### CRM

Observed CRM values содержат semi-structured sprint labels.

v1 сохраняет значение как:

~~~text
crm_context_raw
~~~

без попытки превращать строку в отдельные Sprint entities.

### Priority

В sample имеется только одно непустое numeric value.

Без документации B24/custom-field semantics WorkMind не присваивает ему
значение вроде `high` или `normal`.

### Deadline change request

Observed непустое значение:

~~~text
1257
~~~

Это не интерпретируется как boolean.

Поле хранится как opaque raw value до подтверждения semantics.

## 9. Extraction eligibility

Изменение поля не обязательно должно запускать LLM extraction.

### Обычно влияет на extraction input

- Название;
- Описание;
- ID базовой задачи;
- Название базовой задачи;
- Проект;
- Направление бизнеса;
- Тип заявки;
- Продукт.

### Обычно является structured context/fact

- Статус;
- Стадия;
- deadline;
- release dates.

### Не должно самостоятельно запускать extraction

- Дней с даты постановки;
- Дней ждет контроля;
- Кол-во переносов сроков;
- Последняя активность, если semantic content не изменился.

Окончательный `extraction_input_hash` определяется canonical extraction
payload, а не raw row целиком.
