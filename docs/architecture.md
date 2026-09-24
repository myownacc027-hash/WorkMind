# Архитектура WorkMind

**Статус:** Current
**Редакция:** v2
**Дата актуализации:** 2026-09-24

## 1. Назначение

WorkMind — локальная корпоративная RAG/knowledge-система для накопления, поиска, связывания и анализа рабочих данных.

Система предназначена для работы с:

- письмами Outlook;
- цепочками переписки;
- вложениями;
- PDF, DOCX, XLSX, CSV, TXT, Markdown и PPTX;
- протоколами и расшифровками;
- заметками и внешними документами;
- структурированными корпоративными источниками;
- опционально Bitrix24.

Пользователь должен иметь возможность:

- находить исходные материалы;
- выполнять точный, полнотекстовый и семантический поиск;
- восстанавливать историю рабочего вопроса;
- получать сводки;
- находить связанные задачи и инциденты;
- анализировать изменения статусов;
- выявлять противоречия;
- строить накопленную базу знаний;
- получать человекочитаемое представление знаний через Obsidian;
- работать с системой через MCP.

---

## 2. Основные принципы

### 2.1. Исходные данные не изменяются

WorkMind не изменяет исходные письма, документы и другие первичные материалы.

Для источников, которые могут обновляться, WorkMind хранит собственное локальное состояние, hashes, revisions и provenance.

### 2.2. PostgreSQL — источник истины

PostgreSQL является source of truth для структурированного состояния WorkMind.

В PostgreSQL находятся:

- source metadata;
- письма и threads;
- документы;
- extracted text;
- chunks;
- processing state;
- extraction queue;
- entities;
- claims;
- relations;
- events;
- evidence;
- identity data;
- projection state.

Исходные бинарные файлы при этом сохраняются в файловом архиве.

### 2.3. Qdrant — производный семантический индекс

Qdrant используется для semantic retrieval по chunks.

Он не является source of truth.

Qdrant должен быть полностью перестраиваем из PostgreSQL и сохранённых исходных материалов.

### 2.4. Obsidian — производная проекция

Obsidian не является источником истины.

Vault строится из PostgreSQL и может быть полностью пересоздан.

Направление данных:

~~~text
PostgreSQL
    |
    v
Obsidian
~~~

Автоматическая обратная запись из Obsidian в PostgreSQL не допускается.

### 2.5. LLM не является источником истины

LLM генерирует только candidate knowledge bundle.

Перед сохранением знания проходят:

1. canonical schema;
2. evidence validation;
3. identity checks;
4. ontology checks;
5. duplicate diagnostics;
6. temporal/semantic validation;
7. WorkMind validator.

Успешный `validated` bundle ещё не означает, что данные применены в durable knowledge.

### 2.6. Интеграции не должны становиться зависимостями ядра

WorkMind Core не должен зависеть от:

- Bitrix24;
- Obsidian;
- конкретного LLM provider;
- конкретного AI-клиента.

Внешние системы подключаются через adapters/contracts.

---

## 3. Архитектурные слои

### 3.1. Source Layer

Первичные источники:

- EmailMessage;
- EmailThread;
- Attachment;
- ExternalDocument;
- Spreadsheet;
- Meeting;
- Transcript;
- B24Task;
- будущие source types.

### 3.2. Retrieval Layer

Компоненты поиска:

- DocumentChunk;
- PostgreSQL exact search;
- PostgreSQL full-text search;
- Qdrant semantic search;
- hybrid retrieval.

### 3.3. Knowledge Layer

Нормализованные знания:

- Entity;
- Claim;
- Relation;
- Event;
- Evidence.

### 3.4. Presentation Layer

Человекочитаемые представления:

- Obsidian;
- indexes;
- entity cards;
- local graph;
- будущий web UI.

### 3.5. Access / AI Layer

Контролируемый доступ:

- MCP Server;
- ChatGPT;
- Claude;
- другие MCP clients;
- extraction providers.

---

## 4. Общая схема

~~~text
Outlook ----------------------+
                              |
External documents -----------+----> Source ingestion
                              |            |
Bitrix24                      |            +----> PostgreSQL
   |                          |            |
   +--> XLSX importer --------+            +----> File archive
   |                                       |
   +--> ScraplingB24MI -------+            v
                                      Document chunks
                                           |
                                           +----> Qdrant
                                           |
                                           v
                                  wm-ke-v2 packet
                                           |
                                           v
                                    LLM Provider
                                           |
                                           v
                                    Candidate bundle
                                           |
                                           v
                                   Canonical schema
                                           |
                                           v
                                  WorkMind validator
                                           |
                                           v
                                      validated
                                           |
                                           v
                                   controlled apply
                                           |
                                           v
                                Knowledge in PostgreSQL
                                           |
                      +--------------------+------------------+
                      |                                       |
                      v                                       v
                 MCP Server                       Obsidian Projection
                      |                                       |
                      v                                       v
                AI clients                              Human view
~~~

---

## 5. Source Layer

### 5.1. Outlook

Outlook Collector работает в пользовательской Windows-сессии.

Он получает:

- Inbox;
- Sent Items;
- выбранные рабочие папки;
- тему;
- отправителя;
- получателей;
- дату;
- Conversation ID;
- body;
- attachments;
- folder path.

Collector работает только на чтение.

Повторная синхронизация не должна создавать дубли.

### 5.2. Внешние документы

Поддерживаемые источники включают:

- PDF;
- DOCX;
- XLSX;
- CSV;
- TXT;
- Markdown;
- PPTX;
- протоколы;
- расшифровки.

Generic document ingestion используется для файлов как документов.

Если внешняя система имеет собственную устойчивую объектную модель, рекомендуется отдельный source adapter.

### 5.3. Bitrix24

Bitrix24 является optional source module.

WorkMind должен работать и без Bitrix24.

Поддерживаются два транспорта:

1. XLSX snapshot importer;
2. ScraplingB24MI.

Оба транспорта должны приводить данные к одному внутреннему B24 source contract.

Базовая identity задачи:

~~~text
source_type = b24_task
external_id = Bitrix24 Task ID
~~~

Повторная загрузка того же Task ID должна обновлять существующий logical source object, а не создавать новый.

Отсутствие задачи в следующей XLSX-выгрузке не означает удаление задачи из Bitrix24.

Подробности:

[sources/bitrix24.md](sources/bitrix24.md)

---

## 6. Хранилища

### 6.1. PostgreSQL

Используется PostgreSQL 17.

PostgreSQL хранит:

- source objects;
- metadata;
- parsed text;
- chunks;
- extraction jobs;
- structured knowledge;
- identity information;
- evidence;
- revisions;
- processing states.

Система должна быть способна восстановить Qdrant и Obsidian из PostgreSQL и сохранённых первичных источников.

### 6.2. Qdrant

Qdrant 1.15 используется для semantic search.

Типичные сценарии:

- поиск похожих инцидентов;
- поиск переписки по смыслу;
- поиск аналогичных решений;
- подбор context для AI;
- identity assistance.

### 6.3. Файловый архив

Основная структура:

~~~text
C:\AIKnowledge\
|-- import\
|-- archive\
|-- processed\
|-- failed\
|-- obsidian-vault\
`-- logs\
~~~

`archive` хранит неизменяемые оригиналы.

`processed` содержит производные технические материалы.

---

## 7. Retrieval Layer

WorkMind сочетает:

### Точный поиск

PostgreSQL используется для:

- ID;
- email;
- task numbers;
- кодов;
- дат;
- sender;
- точных строк;
- metadata filters.

### Семантический поиск

Qdrant используется для поиска по смыслу.

### Гибридный поиск

Результаты PostgreSQL и Qdrant объединяются с учётом:

- релевантности;
- типа источника;
- дат;
- duplicate suppression;
- дополнительных filters.

---

## 8. Knowledge Layer

Source Layer и Knowledge Layer являются разными уровнями.

Например:

~~~text
B24Task #189422
~~~

является source object.

А:

~~~text
Incident X --affects--> Task #189422
~~~

является knowledge relation.

### 8.1. Entity

Долгоживущая сущность.

Примеры типов:

- person;
- organization;
- system;
- task;
- incident;
- document;
- process;
- other.

Entity имеет стабильный WorkMind UUID.

### 8.2. Claim

Утверждение о сущности.

Примеры:

~~~text
Task 189422 current_status = "Возвращена на доработку"

Person X job_title = "Старший администратор"
~~~

### 8.3. Relation

Связь между entities.

Примеры:

~~~text
Person --works_at--> Organization

Task --part_of--> Task

Incident --affects--> System
~~~

### 8.4. Event

Событие с подтверждённым occurrence time.

Время получения письма или наблюдения факта нельзя автоматически считать временем самого события.

### 8.5. Evidence

Каждый значимый факт должен иметь provenance/evidence.

Текущие evidence types:

- `direct_statement`;
- `indirect_report`;
- `inference`;
- `notification`.

Evidence должен ссылаться на допустимый source/chunk.

---

## 9. Knowledge Extraction

Detailed subsystem specification: [LLM Provider and Knowledge Extraction Architecture](architecture/llm-provider-and-knowledge-extraction.md).

### 9.1. Extraction queue

Индексированный source может быть поставлен в очередь knowledge extraction.

Job хранит:

- source identity;
- extractor version;
- attempts;
- lease;
- status;
- candidate bundle;
- validation result;
- apply result.

### 9.2. Extraction contract

Текущий packet contract:

~~~text
wm-ke-v2
~~~

Packet включает:

- source context;
- chunks;
- allowed evidence;
- identity candidates;
- ontology context;
- diagnostics.

### 9.3. Providers

Provider abstraction не должна зависеть от одного LLM.

Поддерживаются:

- Agent через MCP;
- OpenAI provider;
- DeepSeek provider;
- будущие providers.

### 9.4. DeepSeek

DeepSeek является provider adapter, а не частью core architecture.

Текущая рабочая конфигурация:

~~~text
model = deepseek-flash
reasoning_effort = none
~~~

Provider выполняет remote extraction только при явном remote consent.

### 9.5. Candidate bundle

Provider возвращает:

- entities;
- claims;
- relations;
- events.

Provider output не является durable knowledge.

### 9.6. Canonical schema

Candidate bundle проверяется canonical JSON Schema.

Допускается только semantics-preserving normalization.

Допустимые примеры:

- добавить отсутствующую пустую top-level секцию как `[]`;
- удалить provider-only поле event confidence, если WorkMind event model его не поддерживает.

Не допускается:

- придумывать факты;
- исправлять неизвестные identities;
- генерировать evidence;
- угадывать отношения;
- скрывать произвольные структурные ошибки.

### 9.7. Validation

WorkMind validator является локальной авторитетной точкой проверки.

### 9.8. Apply

`validated` и `applied` — разные состояния.

По умолчанию:

~~~text
auto_apply = false
~~~

Применение knowledge выполняется отдельно и контролируемо.

---

## 10. Source Authority

Для фактов из структурированного авторитетного источника LLM не должен быть основным способом определения значения.

Приоритет:

~~~text
structured authoritative source
    >
explicit source statement
    >
indirect report
    >
inference
~~~

Для Bitrix24 напрямую используются:

- Task ID;
- title;
- status;
- stage;
- creator;
- assignee;
- co-assignees;
- observers;
- deadline;
- parent/base task;
- project;
- product;
- structured custom fields.

LLM используется для извлечения из свободного текста:

- проблемы;
- цели;
- требований;
- ограничений;
- причин;
- решений;
- затронутых процессов;
- смысловых связей.

---

## 11. Identity Resolution

Перед созданием новой Entity WorkMind должен пытаться переиспользовать существующую.

Сильные identity signals:

- WorkMind UUID;
- external ID;
- Bitrix24 Task ID;
- email address;
- другие устойчивые system IDs.

Дополнительно используются:

- lexical identity;
- semantic candidates;
- ontology compatibility;
- source context.

Цель — не создавать дубли вроде:

~~~text
ООО «МИР ИНСТРУМЕНТА»
ООО "МИР ИНСТРУМЕНТА"
Мир Инструмента
~~~

если evidence подтверждает одну организацию.

---

## 12. Bitrix24 Integration

### 12.1. XLSX importer

Используется для:

- первичного наполнения;
- snapshots;
- разработки;
- ручного обновления.

Importer должен сохранять:

1. normalized canonical fields;
2. полный raw record;
3. source hash/revision;
4. происхождение данных.

### 12.2. ScraplingB24MI

Используется для автоматизированного чтения B24 через существующую авторизованную browser session.

Может дополнять XLSX данными, которые отсутствуют в export:

- comments;
- checklist;
- attachments;
- history/change log, если доступен;
- дополнительные поля.

### 12.3. Один source contract

XLSX importer и ScraplingB24MI не создают две независимые модели.

Они являются двумя transport adapters одного logical source:

~~~text
Bitrix24
   |
   +--> XLSX Importer -----+
   |                       |
   +--> ScraplingB24MI ----+--> B24 Source Contract
                                   |
                                   v
                               PostgreSQL
~~~

---

## 13. Obsidian Projection

Obsidian — derived presentation layer.

Подробности:

[projection/obsidian.md](projection/obsidian.md)

### 13.1. Основные свойства

Projection должна быть:

- deterministic;
- rebuildable;
- based on stable UUID;
- безопасной при apply;
- tolerant к human-readable filenames.

WorkMind не управляет пользовательской `.obsidian` конфигурацией.

### 13.2. Human-readable cards

Карточка должна начинаться с полезной информации для человека.

Для incident:

~~~text
Название
Краткая суть
Статус
Что произошло
Затронутые системы / склады
Связанные задачи
Связанные инциденты
Участники
Хронология
Решение / текущее состояние
Источники
Технические данные
~~~

Для task:

~~~text
Название
Статус / стадия
Исполнитель
Постановщик
Срок
Продукт / направление
Суть
Проблема
Цель
Требования
Связанные задачи
Связанные инциденты
Хронология
Источники
Технические данные
~~~

Пустые пользовательские разделы не должны выводиться.

UUID, chunk IDs, source refs и hashes должны находиться в техническом блоке.

---

## 14. MCP Server

AI client не получает прямой произвольный доступ к PostgreSQL, Qdrant или файловой системе.

Контролируемый доступ выполняется через MCP.

MCP предоставляет инструменты для:

- exact search;
- semantic search;
- hybrid search;
- source reading;
- knowledge entities;
- claims;
- relations;
- timeline;
- extraction;
- validation;
- controlled apply;
- diagnostics.

Все чувствительные действия должны журналироваться.

---

## 15. Безопасность

Основные правила:

- PostgreSQL не публикуется наружу;
- Qdrant не является публичным сервисом;
- локальные сервисы используют loopback, где это возможно;
- API keys не хранятся в Git;
- credentials не должны попадать в logs и bundles;
- credential-like данные проходят redaction;
- исходные письма не изменяются;
- remote LLM используется только при явно разрешённом remote consent;
- provider output считается недоверенным входом;
- финальная проверка выполняется локально.

---

## 16. Revisions и актуальность

Для источников должны поддерживаться:

- content hash;
- source revision;
- extracted revision;
- indexed revision;
- processing status.

Повторная обработка не должна создавать logical duplicates.

Для snapshot imports действует правило:

~~~text
object missing from snapshot
    !=
object deleted from source system
~~~

---

## 17. Технологический стек

### Host

- Windows 11;
- Docker Desktop;
- WSL2;
- Python 3.11.

### Storage

- PostgreSQL 17;
- Qdrant 1.15;
- локальный файловый архив.

### Python ecosystem

Используются в том числе:

- SQLAlchemy;
- psycopg;
- FastAPI;
- qdrant-client;
- Pydantic;
- pywin32;
- openpyxl;
- python-docx;
- pypdf;
- python-pptx;
- BeautifulSoup;
- Scrapling;
- Playwright/CDP для отдельных connectors.

### User interfaces

- ChatGPT;
- Obsidian;
- MCP-compatible clients;
- собственные инструменты проекта.

---

## 18. Architecture Decision Records

Решения, которые сложно восстановить только из кода, фиксируются ADR.

Текущие решения:

1. PostgreSQL является source of truth.
2. Obsidian является derived projection.
3. Bitrix24 является optional source module.
4. LLM генерирует candidate bundle; WorkMind validator определяет допустимость.
5. Structured source authority имеет приоритет над LLM extraction.

---

## 19. Основные изменения относительно v1

Architecture v2 фиксирует уже реализованные и согласованные решения:

- разделение Source Layer и Knowledge Layer;
- structured knowledge graph;
- Entity / Claim / Relation / Event / Evidence;
- extraction queue;
- `wm-ke-v2`;
- provider abstraction;
- DeepSeek;
- canonical schema;
- WorkMind validator;
- controlled apply;
- identity resolution;
- Obsidian как derived projection;
- human-readable presentation layer;
- Bitrix24 как optional source module;
- XLSX и ScraplingB24MI как transports одного B24 contract;
- source authority;
- Git/Markdown как источник технической документации.

---

## 20. Правило развития

Новый source adapter проектируется в последовательности:

~~~text
Source contract
    |
    v
Raw ingestion
    |
    v
Normalized source representation
    |
    v
Chunking / retrieval
    |
    v
Identity context
    |
    v
Knowledge extraction
    |
    v
Validation
    |
    v
Controlled apply
    |
    v
Presentation
~~~

Не следует начинать новую интеграцию с Obsidian Markdown или LLM prompt.

Сначала определяются:

- source identity;
- lifecycle;
- raw representation;
- canonical fields;
- authority rules.
