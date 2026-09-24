# ADR-004: LLM создаёт candidate bundle, а validator определяет допустимость

**Status:** Accepted
**Date:** 2026-09-24

## Context

LLM способен извлекать богатую knowledge structure, но может:

- придумывать значения;
- нарушать schema;
- ошибаться в evidence;
- создавать duplicates;
- ошибаться с identity;
- ошибаться со временем события.

## Decision

LLM output никогда не записывается напрямую в durable knowledge.

Pipeline:

~~~text
source packet
    |
    v
provider
    |
    v
candidate bundle
    |
    v
canonical schema
    |
    v
WorkMind validator
    |
    v
validated
    |
    v
controlled apply
~~~

## Consequences

- provider заменяем;
- validator независим от provider;
- auto-apply по умолчанию выключен;
- structural normalization допускается только когда она semantics-preserving.
