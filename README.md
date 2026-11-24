# WorkMind

WorkMind — локальная ИИ-система и база знаний для Outlook и рабочих документов. Она собирает почту и вложения, извлекает и индексирует содержимое, объединяет точный и векторный поиск и формирует проверяемое структурированное знание с привязкой к исходным фрагментам.

Проект рассчитан на приватную работу: PostgreSQL, Qdrant, файлы и embedding-модель могут находиться на компьютере пользователя или в его локальной инфраструктуре. Код репозитория не содержит почтового архива, документов, векторной базы, секретов или рабочей конфигурации.

## Возможности

- синхронизация папок classic Outlook и исторический импорт почты;
- сохранение писем, цепочек, получателей и вложений в PostgreSQL;
- content-addressed storage (CAS) для дедупликации бинарных вложений по SHA-256;
- извлечение текста из PDF, DOCX, XLSX/XLSM, PPTX и текстовых форматов;
- локальные многоязычные embeddings и индекс в Qdrant;
- гибридный поиск: PostgreSQL full-text/lexical + Qdrant semantic + RRF ranking;
- импорт отдельных рабочих документов вне Outlook;
- MCP-инструменты для поиска, получения цепочек и сборки контекста;
- structured knowledge pipeline: сущности, утверждения, связи, события, временная история и evidence-ссылки;
- очереди, фоновые worker-процессы, проверки целостности и Windows Scheduled Tasks.

## Текущие операционные задачи

- [Очистка после импорта, включая резервные копии — issue #7](https://github.com/myownacc027-hash/WorkMind/issues/7) — открытая задача до завершения всех этапов, проверки восстановления и согласования удаления.
- [Чек-лист безопасной очистки и хранения бэкапов](docs/operations/post-import-cleanup.md).
- [Правила для разработчиков и AI-агентов](AGENTS.md).

**До закрытия issue #7 не удалять резервные точки, исходные данные и каталоги активной загрузки.**

## Архитектура

```text
Outlook / рабочие документы
            |
            v
  collectors and importers
            |
     +------+------+
     |             |
     v             v
PostgreSQL    файловое хранилище / CAS
     |             |
     +------+------+
            v
  extraction and chunking
            |
     +------+------+
     |             |
     v             v
PostgreSQL FTS   local embeddings -> Qdrant
     |             |
     +------+------+
            v
       hybrid search
            |
     +------+------+
     |             |
     v             v
           MCP   structured knowledge
```

PostgreSQL является системой учёта метаданных, очередей, полнотекстового поиска и структурированного знания. Qdrant хранит векторный индекс. Файлы вложений хранятся отдельно; начиная с миграции `0011_content_addressed_storage`, новые поддерживаемые вложения связываются с CAS-объектами.

## Stable baseline

Текущая зафиксированная точка: **WorkMind 0.12.1, pre-STORAGE-02, 2026-09-07**.

- Alembic head: `0011_content_addressed_storage`;
- полный набор тестов: `236 passed`;
- Outlook -> CAS: подтверждено;
- Windows Scheduled Task: `LastTaskResult=0`;
- CAS integrity: подтверждена.

Подробности, измерения и незавершённые задачи: [`docs/releases/stable-pre-storage02-2026-09-07.md`](docs/releases/stable-pre-storage02-2026-09-07.md).

## Внешние зависимости

- Windows 10/11 и PowerShell;
- classic Outlook, настроенный в той же пользовательской сессии, где запускается collector;
- Python 3.11–3.13;
- PostgreSQL 17 (текущий Compose-профиль использует `postgres:17-alpine`);
- Qdrant 1.18 (текущий Compose-профиль использует `qdrant/qdrant:v1.18.0`);
- локальная embedding-модель `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`;
- локальный каталог хранения, заданный через `WORKMIND_HOME`;
- Windows Task Scheduler для автоматической синхронизации и фоновых worker-процессов.

Docker Desktop удобен для локального PostgreSQL и Qdrant, но сами сервисы могут быть развёрнуты и другим способом. Для optional knowledge extraction provider может потребоваться отдельная конфигурация и явное согласие на удалённую обработку; по умолчанию секреты не хранятся в репозитории.

## Быстрый старт для разработки

1. Установите поддерживаемую версию Python и Docker Desktop.
2. Скопируйте `.env.example` в `.env` и замените демонстрационные значения своими.
3. Не добавляйте `.env` в Git.
4. Запустите bootstrap:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
```

5. Примените миграции и проверьте окружение:

```powershell
.\.venv\Scripts\Activate.ps1
alembic upgrade head
workmind-mcp-check
python -m pytest -q
```

6. После настройки Outlook выполните ограниченную тестовую синхронизацию:

```powershell
workmind-outlook sync --lookback-days 7 --max-per-folder 50
```

Перед массовым импортом проверьте выбранный mailbox, папки, каталог хранения и резервное копирование. Полноценный deployment/bootstrap guide запланирован в `DEPLOY-01` после стабилизации storage-контура.

## Основные команды

```text
workmind-sync-now                    принудительная синхронизация Outlook
workmind-sync-status                 состояние синхронизации
workmind-attachments-worker          обработка вложений
workmind-documents-import            импорт рабочих документов
workmind-search                      гибридный поиск
workmind-mcp                         MCP server
workmind-mcp-check                   проверка MCP и инфраструктуры
workmind-knowledge-extraction        управление extraction queue
workmind-knowledge-extraction-status состояние extraction pipeline
```

Полный список entry points находится в `pyproject.toml`.

## Конфиденциальность и границы репозитория

В Git нельзя добавлять:

- `.env`, API keys, пароли, connection strings и токены;
- реальные адреса почтовых ящиков и имена корпоративных Outlook stores;
- письма, вложения, извлечённый текст и пользовательские документы;
- дампы PostgreSQL, Qdrant storage, логи, диагностические отчёты и локальные backup-файлы;
- пользовательские абсолютные пути и сведения о рабочей инфраструктуре.

`.gitignore` закрывает стандартные локальные артефакты, но перед каждой публикацией всё равно требуется просматривать `git diff --cached`.

## Статус проекта

WorkMind находится в активной разработке. Baseline `pre-STORAGE-02` предназначен как воспроизводимая контрольная точка перед массовым переносом legacy-вложений в CAS. Это не готовый универсальный установщик и не заменяет резервное копирование рабочих данных.

## Резервное копирование и восстановление

Полное руководство — [`docs/backup-and-restore.md`](docs/backup-and-restore.md).

Краткое начало работы:

```powershell
# Создать backup
.\scripts\backup\backup_workmind.ps1 -BackupRoot "D:\WorkMindBackups"

# Проверить backup
.\scripts\backup\verify_backup.ps1 -BackupPath "D:\WorkMindBackups\WorkMindBackup-..."

# Тест восстановления (изолированный, без затрагивания production)
.\scripts\backup\restore_workmind.ps1 -BackupPath "D:\WorkMindBackups\WorkMindBackup-..."
```

**Source of truth: PostgreSQL + CAS.** Qdrant — производный индекс, перестраивается из source of truth.

> **Важно:** backup на том же диске `C:` — это локальная recovery-копия, не защита от физического отказа диска.

## Architecture

Актуальная техническая документация WorkMind:

- [Архитектура WorkMind](docs/architecture.md)
- [Документация проекта](docs/README.md)
