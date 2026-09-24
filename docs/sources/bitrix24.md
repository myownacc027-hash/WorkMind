# Bitrix24 Source Module

**Статус:** Draft
**Тип:** optional integration

## 1. Назначение

Bitrix24 предоставляет WorkMind структурированные данные о рабочих задачах.

Интеграция B24 не является обязательной частью WorkMind Core.

## 2. Транспорты

Поддерживаются два подхода.

### XLSX snapshot importer

Используется для:

- первичной загрузки;
- ручных snapshots;
- разработки и тестирования;
- environments без live connector.

### ScraplingB24MI

Используется для автоматизированного чтения доступных данных через авторизованную B24 browser session.

Оба транспорта должны использовать один внутренний source contract.

## 3. Identity

Для задачи:

~~~text
source_type = b24_task
external_id = Bitrix24 Task ID
~~~

Task ID является сильным external identity.

Повторная загрузка того же ID обновляет существующий logical source object.

## 4. Raw и normalized representation

Importer сохраняет два представления.

### Normalized fields

Планируемые canonical поля:

- ID;
- title;
- description;
- status;
- stage;
- creator;
- assignee;
- co-assignees;
- observers;
- project;
- tags;
- created_at;
- started_at;
- updated_at;
- closed_at;
- deadline;
- parent/base task ID;
- priority;
- business direction;
- request type;
- product;
- release dates;
- другие поля после отдельного field audit.

### Raw record

Полный исходный row должен сохраняться без потери неизвестных колонок.

Это позволяет:

- повторно нормализовать старый snapshot;
- поддержать новые B24 fields позже;
- проводить аудит;
- не терять custom fields.

## 5. Связанные ресурсы

В дальнейшем отдельно моделируются:

- B24Comment;
- B24ChecklistItem;
- B24Attachment;
- B24ChangeEvent, если история доступна.

Они не должны сводиться к одному текстовому полю задачи.

## 6. Source authority

Из структурированных B24 fields напрямую берутся:

- ID;
- title;
- status;
- stage;
- creator;
- assignee;
- deadline;
- parent task;
- project;
- product;
- structured custom fields.

LLM не должен переопределять эти значения по письмам.

## 7. Semantic extraction

LLM может анализировать свободный description для извлечения:

- проблемы;
- цели;
- требований;
- критериев;
- ограничений;
- систем;
- процессов;
- причин;
- решений;
- дополнительных relations.

## 8. Snapshot semantics

XLSX export может быть неполным.

Поэтому:

~~~text
task missing from XLSX snapshot
    !=
task deleted from Bitrix24
~~~

Удаление или деактивация требует отдельного достоверного сигнала.

## 9. Связь с Knowledge Layer

B24Task является source object.

WorkMind Entity с типом `task` является knowledge representation.

~~~text
B24Task #189422
       |
       v
Identity resolution
       |
       v
WorkMind Entity(task)
       |
       +--> claims
       +--> relations
       `--> events
~~~

## 10. Следующий этап

Перед созданием importer выполняется field audit реального XLSX:

- список колонок;
- data types;
- заполненность;
- hyperlinks;
- parent-child structure;
- multivalue fields;
- custom fields;
- timestamps;
- authority category;
- normalization rules.

После этого этот документ дополняется точным field mapping.
