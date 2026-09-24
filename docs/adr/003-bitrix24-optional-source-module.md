# ADR-003: Bitrix24 является опциональным source module

**Status:** Accepted
**Date:** 2026-09-24

## Context

Bitrix24 содержит полезные structured task data, но WorkMind должен работать и в организациях без Bitrix24.

B24 data также может поступать разными способами.

## Decision

Bitrix24 не является зависимостью WorkMind Core.

Поддерживаются два transports:

1. XLSX snapshot importer;
2. ScraplingB24MI.

Оба приводят данные к одному B24 source contract.

## Consequences

- WorkMind работает без B24.
- B24 можно подключить как отдельный module.
- XLSX и Scrapling не создают две task models.
- Bitrix24 Task ID является strong external identity.
- отсутствие задачи в XLSX snapshot не считается удалением.
