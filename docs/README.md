# Документация WorkMind

Эта директория является источником истины для технической документации WorkMind.

## Основные документы

- [Архитектура WorkMind](architecture.md)
- [Bitrix24 как источник данных](sources/bitrix24.md)
- [Obsidian Projection](projection/obsidian.md)
- [LLM Provider and Knowledge Extraction](architecture/llm-provider-and-knowledge-extraction.md)

## Architecture Decision Records

- [ADR-001: PostgreSQL — source of truth](adr/001-postgresql-source-of-truth.md)
- [ADR-002: Obsidian — derived projection](adr/002-obsidian-derived-projection.md)
- [ADR-003: Bitrix24 — optional source module](adr/003-bitrix24-optional-source-module.md)
- [ADR-004: LLM создаёт candidate bundle](adr/004-llm-candidate-validation.md)
- [ADR-005: Structured source authority](adr/005-structured-source-authority.md)

## Правило актуальности

Документация в Git является актуальной технической документацией проекта.

DOCX и PDF могут использоваться как исторические материалы или экспортные копии, но не являются параллельным источником архитектурной истины.

Существенное архитектурное изменение считается незавершённым, пока:

1. не обновлён `docs/architecture.md`, если изменение затрагивает общую архитектуру;
2. не обновлена документация соответствующей подсистемы;
3. при необходимости не создан или не изменён ADR.

## Почему Markdown

Markdown выбран потому, что:

- изменения видны в Git diff;
- документация версионируется вместе с кодом;
- её можно читать непосредственно на GitHub;
- её видят coding agents вместе с репозиторием;
- она удобна для локального поиска и RAG;
- из неё при необходимости можно сформировать DOCX/PDF.
