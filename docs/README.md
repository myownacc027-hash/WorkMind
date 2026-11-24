# Документация WorkMind

Эта директория является источником истины для технической документации WorkMind.

## Основные документы

- [Архитектура WorkMind](architecture.md)
- [Bitrix24 как источник данных](sources/bitrix24.md)
- [Bitrix24 XLSX Field Mapping](sources/bitrix24-field-mapping.md)
- [Obsidian Projection](projection/obsidian.md)
- [LLM Provider and Knowledge Extraction](architecture/llm-provider-and-knowledge-extraction.md)

## Эксплуатация и сопровождение

- [Исторический импорт: mail-first и безопасное переключение](operations/history-mail-first-upgrade.md)
- [Обязательная проверка и очистка после импорта](operations/post-import-cleanup.md) ([GitHub issue #7](https://github.com/myownacc027-hash/WorkMind/issues/7))
- [Резервное копирование и восстановление](backup-and-restore.md)

## Architecture Decision Records

- [ADR-001: PostgreSQL — source of truth](adr/001-postgresql-source-of-truth.md)
- [ADR-002: Obsidian — derived projection](adr/002-obsidian-derived-projection.md)
- [ADR-003: Bitrix24 — optional source module](adr/003-bitrix24-optional-source-module.md)
- [ADR-004: LLM создаёт candidate bundle](adr/004-llm-candidate-validation.md)
- [ADR-005: Structured source authority](adr/005-structured-source-authority.md)
- [ADR-006: Bitrix24 source identity and snapshot semantics](adr/006-bitrix24-source-identity-and-snapshot-semantics.md)
- [ADR-007: Bitrix24 knowledge identity and provenance](adr/007-bitrix24-knowledge-identity-and-provenance.md)

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
