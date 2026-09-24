# ADR-005: Structured source authority имеет приоритет над LLM extraction

**Status:** Accepted
**Date:** 2026-09-24

## Context

Один факт может одновременно присутствовать:

- как structured field корпоративной системы;
- в тексте письма;
- в description;
- как LLM inference.

Повторное угадывание structured field создаёт ненужные conflicts.

## Decision

Приоритет источников:

~~~text
structured authoritative source
    >
explicit source statement
    >
indirect report
    >
inference
~~~

Для Bitrix24 непосредственно из structured fields определяются, например:

- Task ID;
- title;
- status;
- stage;
- assignee;
- creator;
- deadline;
- parent task.

LLM используется для semantic extraction свободного текста.

## Consequences

- меньше ложных claims;
- проще reconciliation;
- меньше duplicates;
- provenance понятнее;
- structured source values нельзя молча переопределять inference.
