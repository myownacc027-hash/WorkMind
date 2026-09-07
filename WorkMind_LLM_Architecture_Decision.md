# WorkMind LLM Architecture Decision

**Status:** accepted
**Decision date:** 2026-08-14
**Current release:** WorkMind 0.12.0

This root file is the historical ADR for the original provider-neutral Knowledge
Extraction decision. The maintained architecture and operational contract live
in:

[docs/architecture/llm-provider-and-knowledge-extraction.md](docs/architecture/llm-provider-and-knowledge-extraction.md)

The invariant remains unchanged: an LLM provider may only propose an
evidence-backed JSON bundle. WorkMind owns evidence scope, identity resolution,
temporal safety, validation, bundle fingerprinting, transaction boundaries,
retry history, and apply policy. No provider writes structured knowledge or
PostgreSQL rows directly.

WorkMind 0.12.0 uses the connected Codex agent under this boundary. Local,
online, and hybrid providers remain future implementations of the same contract.
