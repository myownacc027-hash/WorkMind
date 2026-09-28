
# ADR-007: Bitrix24 knowledge identity and provenance

**Status:** Accepted

## Context

Bitrix24 tasks are mutable source records.

WorkMind now stores:

- current B24 task state in `b24_tasks`;
- immutable transport observations in `b24_task_observations`.

The current knowledge layer already contains a task with Bitrix24 Task ID
`189422`.

The deterministic B24 projection therefore must not create another entity for
the same task.

## Decision

### Task entity identity

A projected B24 task uses:

~~~text
entity_type = task
external_id = <Bitrix24 Task ID>
~~~

The source namespace is stored explicitly in:

~~~text
attributes_json.b24.source_namespace = b24_task
attributes_json.b24.task_id = <Bitrix24 Task ID>
~~~

Projection identity resolution uses the B24 attributes namespace first.

For legacy entities created before this projection exists, exact matching by
`entity_type=task` plus the existing raw Bitrix24 Task ID in `external_id` is
allowed as a compatibility fallback.

No fuzzy title match is allowed for automatic identity merge.

If the desired normalized task name collides with a different task entity and
there is no source identity match, projection must stop with a collision.

### Canonical name

The human-facing name is:

~~~text
Б24 №<Task ID> «<clean title>»
~~~

Only the known leading classifier `[ЗнИ]:` is removed from the human-facing
title.

The exact source title is preserved in:

~~~text
attributes_json.b24.source_title
~~~

Unknown prefixes are not removed.

### Description and structured state

The authoritative current B24 description is the deterministic entity
description.

Authoritative B24 structured fields are stored under:

~~~text
attributes_json.b24
~~~

The projection must preserve unrelated existing entity attributes.

### Participant identity

Creator, assignee, coassignee, observer and additional-employee values from the
XLSX remain labels.

They do not automatically create `person` entities.

Stable person relations require a separate stable B24 user identity or an
explicit local identity mapping.

### Evidence provenance

Knowledge evidence generated from deterministic B24 source state must point to
the immutable observation:

~~~text
source_type = b24_observation
source_ref = workmind://b24_observation/<observation UUID>
~~~

It must not use mutable `b24_tasks` as historical evidence.

### Parent task references

When the parent Task ID exists in the imported B24 source layer, the projection
may produce a deterministic task-to-task relation candidate.

When the parent is absent, WorkMind stores the external parent reference but
does not invent a synthetic KnowledgeEntity.

### Dry-run before apply

The first implementation produces and verifies a complete projection plan
without mutating Knowledge tables.

Applying the plan is a separate stage.

## Consequences

- B24 Task `189422` reuses its existing KnowledgeEntity.
- New B24 tasks receive deterministic identities.
- source titles remain auditable while human names stay readable;
- participant labels cannot create accidental duplicate people;
- historical evidence remains bound to immutable source observations;
- partial B24 snapshots cannot manufacture missing parent entities;
- knowledge mutation can be reviewed separately from projection planning.
