# ADR-002: Obsidian — производная проекция

**Status:** Accepted
**Date:** 2026-09-24

## Context

Obsidian удобен человеку как Wiki и graph UI, но Markdown не должен быть authoritative storage для identity, temporal state и provenance.

## Decision

Obsidian используется как deterministic derived projection Knowledge Layer.

~~~text
PostgreSQL -> Obsidian
~~~

Автоматический write-back запрещён.

## Consequences

- Vault может быть перестроен.
- WorkMind управляет только managed files.
- `.obsidian` принадлежит пользователю.
- Human-readable presentation может развиваться независимо от knowledge schema.
