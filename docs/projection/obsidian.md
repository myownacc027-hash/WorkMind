# Obsidian Projection

**Статус:** Foundation implemented
**Human presentation:** v2 planned

## 1. Назначение

Obsidian является человекочитаемой проекцией structured knowledge из PostgreSQL.

Он не является source of truth.

~~~text
PostgreSQL -> Obsidian
~~~

Автоматический write-back запрещён.

## 2. Свойства projection

Projection должна быть:

- deterministic;
- rebuildable;
- safe;
- based on stable WorkMind UUID;
- compatible with human-readable filenames.

WorkMind управляет только своими managed Markdown-файлами.

Пользовательская `.obsidian` конфигурация не принадлежит WorkMind.

## 3. Реализованный foundation

Поддерживаются:

- entity pages;
- navigation indexes;
- wikilinks;
- relations;
- safe apply;
- managed-file checks;
- stale managed-file pruning;
- manifests;
- fingerprints;
- status;
- drift detection;
- human-readable paths.

## 4. Проблема текущего presentation

Технически корректная карточка может быть неудобной человеку.

Не должны доминировать:

- UUID;
- source_ref;
- chunk_id;
- hashes;
- machine predicates;
- пустые разделы.

## 5. Human-readable presentation v2

Карточка должна отвечать на вопросы:

- что это;
- что произошло;
- каков статус;
- с чем связано;
- кто участвует;
- как менялось состояние;
- чем вывод подтверждён.

## 6. Incident card

~~~text
# Инцидент <номер> — <суть>

Краткое резюме
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

## 7. Task card

~~~text
# <ID> — <название>

Статус / стадия
Исполнитель / постановщик
Срок
Продукт / направление

Суть
Проблема
Цель
Требования
Связанные задачи
Связанные инциденты
Участники
Хронология
Источники
Технические данные
~~~

## 8. Human labels

Machine predicates сохраняются в Knowledge Layer.

Например:

~~~text
has_incident
assigned_to
part_of
works_at
~~~

Presentation layer может отображать их как:

~~~text
Связан с инцидентом
Ответственный
Входит в
Работает в
~~~

Это изменение представления, а не изменение knowledge.

## 9. Пустые разделы

Пустые разделы для пользователя не выводятся.

Вместо:

~~~text
Claims: None
Events: None
~~~

раздел отсутствует.

## 10. Evidence presentation

Основное представление должно показывать:

- понятный источник;
- дату;
- source title;
- при необходимости excerpt.

Внутренние UUID/chunk IDs/source refs остаются в техническом блоке.

## 11. Graph View

Глобальный Obsidian graph не должен визуально приравнивать navigation pages к business entities.

Рабочий graph view должен уметь исключать:

- Home;
- Index;
- calendar notes;
- Canvas;
- служебные файлы.

Основной knowledge graph строится вокруг `Entities/**`.

Для конкретной сущности предпочтителен local graph на 1–2 hops.
