
# ADR-006: Bitrix24 source identity and snapshot semantics

**Status:** Accepted
**Date:** 2026-09-25

## Context

Bitrix24 tasks могут поступать в WorkMind как минимум двумя transports:

- XLSX export;
- ScraplingB24MI.

XLSX export может:

- содержать только часть задач;
- содержать parent references на задачи вне export;
- представлять IDs как Excel numeric values;
- содержать только display names пользователей;
- содержать custom fields неизвестной semantics;
- включать derived fields, меняющиеся со временем;
- включать hyperlinks с дополнительной system identity.

Если не определить source identity и snapshot semantics заранее, importer может:

- создавать duplicate tasks;
- ошибочно удалять задачи;
- создавать duplicate Persons;
- терять parent links;
- запускать LLM extraction каждый день из-за derived counters;
- путать project name с project identity.

## Decision

### 1. Task identity

Logical identity:

~~~text
source_type = b24_task
external_id = Bitrix24 Task ID
~~~

Task ID canonicalized to string.

### 2. One logical model

XLSX и ScraplingB24MI используют один B24 source contract.

Transport не определяет новую task identity.

### 3. Partial snapshot does not imply deletion

Отсутствие Task ID в конкретном XLSX snapshot не означает:

- deletion;
- closing;
- deactivation;
- loss of access.

Для удаления необходим отдельный authoritative signal.

### 4. Missing column and empty cell are different

~~~text
column present + empty cell
    -> NULL

column absent
    -> preserve previous field
~~~

Importer хранит `present_columns`.

### 5. Participant display labels are not Person identities

XLSX names сохраняются как participant labels.

Person entity/relation требует strong identity:

- B24 user ID;
- другой stable external ID;
- confirmed local mapping.

### 6. Parent reference may remain unresolved

`parent_external_id` сохраняется независимо от presence parent row.

Отсутствующий parent не создаёт выдуманную полную Task entity.

### 7. Three fingerprints

~~~text
raw_hash
canonical_hash
extraction_input_hash
~~~

имеют разные назначения.

Derived counters не должны заставлять WorkMind повторно выполнять semantic
extraction.

### 8. Hyperlinks are source data

Task URL и workgroup/project IDs сохраняются отдельно от display fields.

Project name не является единственным identity key.

### 9. Source timezone is explicit

Naive XLSX datetime не считается UTC.

Connector/import обязан знать `source_timezone`.

### 10. Structured fields outrank LLM inference

Authoritative B24 fields применяются deterministic path.

LLM анализирует semantic free text и не заменяет structured values.

## Consequences

### Positive

- idempotent reimport;
- меньше duplicate tasks;
- меньше duplicate Persons;
- безопасная работа с partial exports;
- parent links не теряются;
- semantic extraction запускается только при значимом изменении;
- XLSX и Scrapling могут сосуществовать;
- raw custom fields остаются доступными для будущей нормализации.

### Costs

- source model хранит больше metadata;
- требуется отдельный normalization layer;
- participant resolution откладывается до strong identity;
- importer должен различать missing column и empty value;
- необходимо поддерживать несколько fingerprints.

## Related documentation

- [WorkMind architecture](../architecture.md)
- [Bitrix24 Source Contract v1](../sources/bitrix24.md)
- [Bitrix24 XLSX Field Mapping](../sources/bitrix24-field-mapping.md)
- [ADR-003: Bitrix24 optional source module](003-bitrix24-optional-source-module.md)
- [ADR-005: Structured source authority](005-structured-source-authority.md)
