# LLM Provider and Knowledge Extraction Architecture

## Status and scope

This document records the WorkMind 0.12.0 extraction architecture. It separates
features implemented in 0.12.0 from future provider work.

## Implemented in 0.12.0

The active provider is `agent`: Codex obtains leased jobs and submits proposed
JSON bundles through WorkMind MCP. `auto_apply` remains `false`.

The extractor contract is `wm-ke-v2`. Existing `wm-ke-v1` jobs remain valid and
receive `packet_contract_version=wm-ke-v2` from the current safe packet builder
when leased. Their persisted `extractor_version` remains `wm-ke-v1`, while the
packet explicitly reports `job.runtime_packet_contract_version=wm-ke-v2`.
Queue insertion checks an existing source/content hash across extractor versions,
so upgrading does not silently create a second active job for the same source.

Terminal `completed`, `skipped`, or `error` v1 jobs do not permanently block a
deliberate v2 retry. An operator can preview and then schedule exactly one
controlled current-version job:

```powershell
workmind-knowledge-extraction reextract-job <job-id> `
  --reason "controlled 0.12 re-extraction"
workmind-knowledge-extraction reextract-job <job-id> `
  --reason "controlled 0.12 re-extraction" `
  --apply
```

The first command is read-only. The second creates or requeues the unique v2 job.
It refuses to create another job while any same-source/content job is active.
A controlled re-extraction is a new extraction cycle: an exhausted terminal job
returns to `pending` with `attempts=0`, while the prior cycle's quality history
and attempt offset remain available for audit. There is no automatic mass
requeue or backfill on upgrade.

### Provider invariant

An LLM never writes structured knowledge directly. It returns only a proposed
JSON bundle containing `entities`, `claims`, `relations`, and `events`.

WorkMind always owns:

- queue state and leasing;
- construction of the evidence packet;
- bounded identity resolution;
- the exact `allowed_evidence` scope;
- temporal claim and recent-event context;
- entity, claim, and event ID reuse rules;
- validation and semantic dry-run;
- bundle SHA-256 fingerprinting;
- transaction boundaries and apply policy;
- audit, retry, requeue, and failure handling.

### Evidence and identity context

`allowed_evidence` contains the only `source_ref` and `chunk_id` pairs that may
support newly asserted claims, relations, and events.

`identity_candidates` contains existing structured knowledge used only to:

- resolve entity identity;
- prevent duplicates;
- reuse exact entity, claim, or event IDs;
- inspect active/recent claims and recent events for temporal and deduplication
  decisions.

Historical chunks found by semantic identity resolution never enter
`allowed_evidence`. Identity candidates are not evidence for a new fact.
`existing_knowledge_candidates` is retained as a compatibility alias generated
from the same candidate list; it is not a second resolver.

### Candidate resolution

WorkMind resolves candidates before provider extraction:

1. Stage A extracts a bounded set of normalized phrases before SQL, then uses
   equality/`IN` lookups on indexed `external_id` and `normalized_key` fields.
   It never compares arbitrary source text with every entity row. Dates, years,
   times, and bare numbers are not external IDs. Type context such as
   `task 189422` constrains the entity type through every resolution channel.
   This stage is independent of lexical scan ordering and preserves known short
   names such as `WMS` and `ДСЛ` regardless of source casing.
2. Semantic matching reuses the existing hybrid/Qdrant search over
   `DocumentChunk`. Similar chunks are score-ordered before fan-out caps are
   applied, then mapped through `KnowledgeEvidence` to `KnowledgeClaim` or
   `KnowledgeEvent`, and finally to `KnowledgeEntity`. Duplicate chunks and
   entities retain their strongest score.
3. Lexical matching scores canonical name, normalized key, description, and
   external ID using significant token coverage and technical identifiers. A
   typed external-ID conflict cannot become an equivalent lexical match.
4. Results are merged by entity ID, methods and reasons are combined, candidates
   are ranked, and the list is bounded. Semantic evidence may retain a related
   cross-type candidate as `semantic_only`, but it cannot replace the typed
   exact result or drive duplicate-risk diagnostics. An untyped marker such as
   `#189422` conservatively keeps matching entity types and emits an explicit
   ambiguity diagnostic.

Every intermediate channel is bounded. Conservative defaults are:

- 60 exact entity rows and 600 normalized source phrases;
- 20 diversified semantic source results, 60 semantic chunks, 240
  `KnowledgeEvidence` rows, and 60 mapped entities;
- 300 lexical entity rows;
- 8 merged final candidates;
- per candidate: all mandatory active temporal claims up to a fail-safe cap of
  200, 60 non-temporal active claims, 60 recent historical claims, and 30 events.

`KNOWLEDGE_IDENTITY_SEMANTIC_SOURCE_LIMIT` is the number of diversified sources
returned by `search_hybrid`, not the physical Qdrant point count. Semantic search
internally owns its point fan-out. Resolver-side chunk, evidence, and entity caps
bound the later mapping stages independently.

Mandatory active temporal/exclusive predicates (`current_status`, `status`,
`owner`, `assignee`, `deadline`, and `decision`) are loaded by a dedicated query.
Non-temporal active claims, historical claims, and events have independent
per-entity bounds, so noisy history cannot displace a current status. Mandatory
claims are never silently truncated: exceeding
`KNOWLEDGE_IDENTITY_MANDATORY_CLAIMS_SAFETY_CAP` marks the packet blocked,
records an `incomplete_identity_context` diagnostic, and makes validation fail
until the data anomaly is resolved. Other bounded-channel truncation remains
visible in context diagnostics.

The scoring defaults remain semantic 0.58, lexical 0.62, and duplicate risk 0.78.
The deterministic matrix behind those defaults treats exact names/IDs and stable
technical IDs as strong, two informative name tokens as potentially strong,
weak semantic scores and one generic token as insufficient, and a common acronym
alone as weak. Semantic 0.58 is only the historical-evidence admission floor;
duplicate risk still requires identity similarity rather than candidate score
alone.

### Ontology and duplicate risk

The packet provides explicit definitions for `system`, `process`, `task`,
`project`, `incident`, `decision`, `person`, `organization`, `document`, and
`other`. In particular, a generic phrase containing "processing" is not by
itself a stable system identity.

During validation, WorkMind compares proposed new entities with strong identity
candidates separately for every new bundle entity. Reusing a candidate for one
bundle entry does not exempt a second proposed entity from duplicate checks.
Matching external IDs, exact normalized names, multiple informative name-token
roots, or stable technical identifiers can establish risk. Different external
IDs plus unrelated names do not. A common acronym such as `ДИТ` alone is not a
strong identity signal.

The model does not set these diagnostics. `duplicate_risk` is visible but does
not automatically reject a bundle because a genuinely distinct entity may still
be correct. Non-blocking `ontology_diagnostics` additionally reports close
cross-type `system`/`process` ambiguity and generic new identities that have no
stable identifier or strong same-type candidate.

### Quality diagnostics

Candidate-resolution and validation diagnostics are stored in the existing
`KnowledgeExtractionJob.validation_json`. No schema migration is needed. Each
job keeps a bounded `quality_history` of attempt events keyed by
`attempt:event_type`. Invalid JSON, lease mismatch, packet-build failure, source
or bundle fingerprint mismatch, preflight failure, semantic validation failure,
and apply failure survive retry/requeue. Repeating the same event in one attempt
does not double-count it.

Status reports aggregate exact, semantic, lexical and merged candidate hits,
reused and new entities, duplicate risks, ontology warnings, validation-failure
attempts, skipped jobs, completed jobs, and validated jobs while retaining all
pre-0.12.0 status fields.

### Known limitations

- `quality_history` is intentionally bounded. When enough old events are
  truncated, their failure count is not yet rolled into the cumulative baseline.
- Untyped external-ID ambiguity is calculated from the bounded final candidate
  set. A cross-type collision beyond that cap may not appear in the diagnostic.

### Preserved safety contract

WorkMind retains full semantic dry-run, validate/apply parity, bundle SHA-256,
UTF-8-safe file validation, encoding-loss detection, exact entity ID reuse,
claim ID/key validation, event ID reuse, temporal supersession, evidence scope,
safe apply failure/requeue, and `auto_apply=false`.

## Planned and future

Later phases may add:

- a local model provider;
- OpenAI or another online provider;
- an OpenAI-compatible provider;
- hybrid local-first extraction with a controlled remote fallback.

Local model integration is explicitly out of scope for WorkMind 0.12.0. Every
future provider must consume the same provider-neutral packet and return the
same proposed bundle contract; it must not bypass WorkMind validation or apply
policy.


## WorkMind 0.12.1: source metadata evidence

WorkMind distinguishes two evidence channels:

- **chunk evidence**: `source_ref + chunk_id`, used for claims, relations, and
  events and preserved unchanged from earlier versions;
- **source metadata evidence**: a bounded `source_ref + metadata` assertion used
  only as entity identity evidence when the source has no text chunk.

The extraction packet publishes `allowed_source_metadata_evidence`. For
attachments this may contain only WorkMind-verified `filename`, `content_hash`,
`size_bytes`, `mime_type`, and `parent_source_ref`; for external documents it may
contain `filename`, `content_hash`, `size_bytes`, `mime_type`, and `source_date`.
The model cannot introduce other fields or reference a source outside this scope.
Validation re-reads the source row and compares every proposed metadata value to
the database. A mismatch is a semantic validation failure.

`KnowledgeEvidence` now supports exactly one parent among entity, claim, and
event, and an `evidence_kind` of either `chunk` or `source_metadata`. Chunk
evidence requires a real `DocumentChunk`; source metadata evidence requires
`chunk_id = NULL` and stores only the verified metadata snapshot. This avoids
creating synthetic chunks for `.epf` and other binary artifacts.

Source metadata is an **identity** channel, not a factual shortcut. A filename
can establish the stable technical identity of a software artifact, but a rule
such as "date relevance is set to package creation date" still requires a
current allowed email/document chunk. Validate/apply parity and bundle SHA-256
continue to cover the complete bundle, including entity identity evidence.
