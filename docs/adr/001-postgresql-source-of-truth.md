# ADR-001: PostgreSQL — источник истины структурированного состояния

**Status:** Accepted
**Date:** 2026-09-24

## Context

WorkMind использует PostgreSQL, Qdrant, файловый архив и Obsidian.

Без явного authority возможно расхождение данных.

## Decision

PostgreSQL является source of truth для структурированного состояния WorkMind и Knowledge Layer.

Файловый архив хранит оригинальные бинарные sources.

Qdrant и Obsidian являются производными.

## Consequences

- Qdrant должен быть rebuildable.
- Obsidian должен быть rebuildable.
- Obsidian write-back не является допустимым механизмом обновления knowledge.
- Durable knowledge changes проходят через PostgreSQL.
