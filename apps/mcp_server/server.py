from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from apps.mcp_server.audit import audited_tool
from packages.knowledge.extraction import (
    apply_validated_extraction_job,
    extraction_status,
    get_extraction_batch,
    release_extraction_job,
    validate_extraction_job,
)
from packages.knowledge.service import (
    get_claims as get_structured_claims,
    get_entity as get_structured_entity,
    get_relations as get_structured_relations,
    get_status_history as get_structured_status_history,
    get_timeline as get_structured_timeline,
    import_knowledge_bundle,
    knowledge_status,
    search_entities as search_structured_entities,
)

from apps.mcp_server.services import (
    WorkMindQueryError,
    exact_search,
    exact_search_attachments,
    get_attachment,
    get_email,
    get_external_document,
    get_thread,
    get_workmind_context,
    list_attachments,
    semantic_search,
    semantic_search_attachments,
    system_status,
    unified_search,
)

mcp = FastMCP(
    "WorkMind",
    instructions=(
        "Read access to the user's local WorkMind archive and controlled "
        "write access to the derived structured-knowledge layer. "
        "Use search_workmind first: it combines semantic and lexical "
        "search across emails, attachments, and imported documents, groups duplicate chunks, "
        "and prioritizes exact identifiers. Use get_workmind_context_bundle "
        "before detailed conclusions to load the source, parent email, "
        "conversation thread, and sibling attachments. Legacy search "
        "tools remain available for narrow diagnostics. Cite every "
        "source_ref and matched chunk_id used in an answer. "
        "Separate direct statements, indirect reports, notifications, "
        "and your "
        "own inferences. Do not present an inference as a source fact. "
        "Use extract_workmind_knowledge only after the user explicitly asks "
        "to save structured knowledge. Validate with apply=false first and "
        "persist with apply=true only after validation succeeds. For queued "
        "automatic extraction, use get_workmind_extraction_batch only when the "
        "user asks to update/process structured knowledge; validate each leased "
        "job before applying it. Every claim, "
        "relation, and event must cite an existing source_ref and chunk_id. "
        "Relations are returned separately as outgoing_relations and "
        "incoming_relations. Follow to_entity_ref/from_entity_ref directly; "
        "do not run another entity search when a relation already supplies "
        "the target entity_ref. Treat dated_claims as observations or validity "
        "dates, not automatically as real-world event dates. For updates to "
        "an existing claim, preserve claim_id or claim_key. Temporal conflicts "
        "use half-open intervals [valid_from, valid_to)."
    ),
)


def _error(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


@mcp.tool()
@audited_tool("search_workmind")
def search_workmind(
    query: str,
    limit: int = 10,
    mode: str = "hybrid",
    source_type: str = "all",
    min_score: float = 0.0,
    sender: str | None = None,
    filename: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    snippets_per_source: int = 3,
) -> dict[str, Any]:
    """Unified hybrid search across emails and extracted attachments.

    Args:
        query: Natural-language question, phrase, task number, code, or identifier.
        limit: Maximum number of grouped sources, from 1 to 50.
        mode: hybrid, semantic, or lexical. Use hybrid by default.
        source_type: all, email, attachment, or document.
        min_score: Optional Qdrant cosine threshold for the semantic channel.
        sender: Optional sender name or email substring.
        filename: Optional attachment filename substring.
        date_from: Optional lower date bound, YYYY-MM-DD or ISO 8601.
        date_to: Optional inclusive upper date bound.
        snippets_per_source: Best matching chunks returned per source, 1 to 10.
    """
    try:
        return unified_search(
            query,
            limit=limit,
            mode=mode,
            source_type=source_type,
            min_score=min_score,
            sender=sender,
            filename=filename,
            date_from=date_from,
            date_to=date_to,
            snippets_per_source=snippets_per_source,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_context_bundle")
def get_workmind_context_bundle(
    source_ref_or_id: str,
    source_type: str | None = None,
    include_thread: bool = True,
    max_body_chars: int = 6_000,
    max_attachment_text_chars: int = 12_000,
    max_body_chars_per_thread_message: int = 2_000,
    max_thread_messages: int = 8,
    query: str | None = None,
    evidence_limit: int = 5,
) -> dict[str, Any]:
    """Load a compact source context with bounded thread and provenance.

    Pass the original search query so evidence_candidates contains
    the most relevant chunk IDs and excerpts for verification.

    The context bundle is intentionally compact. For larger/raw content
    use get_email_by_id, get_email_thread, get_attachment_by_id, or
    get_document_by_id.

    Args:
        source_ref_or_id: WorkMind source_ref or source UUID.
        source_type: email, attachment, document, or auto-detect.
        include_thread: Include bounded conversation context.
        max_body_chars: Requested source-email ceiling; hard-capped at 6000.
        max_attachment_text_chars: Requested attachment/document ceiling;
            hard-capped at 12000.
        max_body_chars_per_thread_message: Requested per-message ceiling;
            hard-capped at 2000.
        max_thread_messages: Requested thread window; hard-capped at 8.
        query: Original search query used to rank evidence excerpts.
        evidence_limit: Maximum target-source evidence candidates.
    """
    try:
        return get_workmind_context(
            source_ref_or_id,
            source_type=source_type,
            include_thread=include_thread,
            max_body_chars=max_body_chars,
            max_attachment_text_chars=(
                max_attachment_text_chars
            ),
            max_body_chars_per_thread_message=(
                max_body_chars_per_thread_message
            ),
            max_thread_messages=(
                max_thread_messages
            ),
            query=query,
            evidence_limit=evidence_limit,
        )
    except Exception as exc:
        return _error(exc)



@mcp.tool()
@audited_tool("extract_workmind_knowledge")
def extract_workmind_knowledge(
    bundle_json: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Validate or persist entities, claims, relations, events, and evidence.

    Call with apply=false first. Each claim, relation, and event must contain
    evidence with an existing source_ref and chunk_id. Existing claims may
    be updated by claim_id or stable claim_key, including valid_from, valid_to,
    observed_at, status, and supersession links. Use apply=true only after
    explicit user authorization and successful validation.
    """
    try:
        return import_knowledge_bundle(
            bundle_json,
            apply=apply,
            created_by="mcp",
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_extraction_batch")
def get_workmind_extraction_batch(
    limit: int = 3,
    lease_minutes: int | None = None,
) -> dict[str, Any]:
    """Lease pending source packets for structured-knowledge extraction.

    Each packet contains exact source_ref/chunk_id evidence that may be cited.
    Do not invent evidence outside the packet. Validate a produced bundle with
    validate_workmind_extraction_job before any apply operation.
    """
    try:
        return get_extraction_batch(
            limit=limit,
            lease_minutes=lease_minutes,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("validate_workmind_extraction_job")
def validate_workmind_extraction_job(
    job_id: str,
    lease_token: str,
    bundle_json: str,
) -> dict[str, Any]:
    """Validate a leased extraction bundle against evidence and PostgreSQL.

    Successful validation changes the job to `validated` and releases the
    lease. It does not persist entities/claims/events yet.
    """
    try:
        return validate_extraction_job(
            job_id,
            lease_token,
            bundle_json,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("apply_workmind_extraction_job")
def apply_workmind_extraction_job(
    job_id: str,
) -> dict[str, Any]:
    """Persist a previously validated extraction job.

    Call only after the user authorized saving structured knowledge and the
    validation step succeeded.
    """
    try:
        return apply_validated_extraction_job(job_id)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("release_workmind_extraction_job")
def release_workmind_extraction_job(
    job_id: str,
    lease_token: str,
    reason: str = "released by agent",
    retry: bool = True,
) -> dict[str, Any]:
    """Release a leased extraction job back to pending or skip it."""
    try:
        return release_extraction_job(
            job_id,
            lease_token,
            reason=reason,
            retry=retry,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_extraction_status")
def get_workmind_extraction_status() -> dict[str, Any]:
    """Return structured-knowledge extraction queue health and counts."""
    try:
        return extraction_status()
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("search_workmind_entities")
def search_workmind_entities(
    query: str,
    entity_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search derived entities by canonical name, external ID, or key."""
    try:
        return search_structured_entities(
            query,
            entity_type=entity_type,
            limit=limit,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_entity")
def get_workmind_entity(
    entity_ref_or_id: str,
    entity_type: str | None = None,
    include_claims: bool = True,
    include_relations: bool = True,
    include_timeline: bool = True,
    include_evidence: bool = True,
    include_excerpt: bool = False,
) -> dict[str, Any]:
    """Return a navigable entity card.

    The response separates ordinary claims, outgoing_relations,
    incoming_relations, real events, and dated_claims. Follow
    to_entity_ref/from_entity_ref from relation objects directly.
    dated_claims contain observation/validity dates and are not
    automatically real-world events.
    """
    try:
        return get_structured_entity(
            entity_ref_or_id,
            entity_type=entity_type,
            include_claims=include_claims,
            include_relations=include_relations,
            include_timeline=include_timeline,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_claims")
def get_workmind_claims(
    entity_ref_or_id: str,
    entity_type: str | None = None,
    predicate: str | None = None,
    evidence_type: str | None = None,
    status: str | None = None,
    include_conflicts: bool = True,
    include_relations: bool = False,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Return ordinary evidence-backed claims.

    Relations are excluded by default. Set include_relations=true only
    for backward-compatible diagnostics; prefer get_workmind_relations.
    Full excerpts are omitted by default to keep MCP responses compact.
    """
    try:
        return get_structured_claims(
            entity_ref_or_id,
            entity_type=entity_type,
            predicate=predicate,
            evidence_type=evidence_type,
            status=status,
            include_conflicts=include_conflicts,
            include_relations=include_relations,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
            limit=limit,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_relations")
def get_workmind_relations(
    entity_ref_or_id: str,
    entity_type: str | None = None,
    direction: str = "both",
    relation_type: str | None = None,
    status: str | None = None,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Return navigable outgoing and incoming entity relations.

    direction is outgoing, incoming, or both. Each relation includes
    from_entity_ref and to_entity_ref, so the connected entity can be
    opened directly without another search.
    """
    try:
        return get_structured_relations(
            entity_ref_or_id,
            entity_type=entity_type,
            direction=direction,
            relation_type=relation_type,
            status=status,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
            limit=limit,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_timeline")
def get_workmind_timeline(
    entity_ref_or_id: str,
    entity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """Return separate real events and dated claims.

    occurred_at identifies an asserted event time. A dated_claim uses
    observed_at or valid_from and must not be described as an event
    unless an event record also exists.
    """
    try:
        return get_structured_timeline(
            entity_ref_or_id,
            entity_type=entity_type,
            date_from=date_from,
            date_to=date_to,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
            limit=limit,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_status_history")
def get_workmind_status_history(
    entity_ref_or_id: str,
    entity_type: str | None = None,
    predicate: str = "current_status",
    as_of: str | None = None,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Return status history, current value, and temporal conflicts.

    Temporal intervals use [valid_from, valid_to). Two different active
    exclusive values conflict only when their intervals overlap. A
    superseded record remains available as historical knowledge.
    """
    try:
        return get_structured_status_history(
            entity_ref_or_id,
            entity_type=entity_type,
            predicate=predicate,
            as_of=as_of,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
            limit=limit,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_knowledge_status")
def get_workmind_knowledge_status() -> dict[str, Any]:
    """Return structured-knowledge counts and conflict health."""
    try:
        return knowledge_status()
    except Exception as exc:
        return _error(exc)

@mcp.tool()
@audited_tool("search_emails")
def search_emails(
    query: str,
    limit: int = 10,
    min_score: float = 0.0,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Search archived emails by meaning.

    Args:
        query: Natural-language description of the needed information.
        limit: Maximum number of unique emails, from 1 to 50.
        min_score: Optional cosine similarity threshold from -1 to 1.
        sender: Optional sender email or name substring.
        date_from: Optional lower date bound, YYYY-MM-DD or ISO 8601.
        date_to: Optional inclusive upper date, YYYY-MM-DD or ISO 8601.
    """
    try:
        return semantic_search(
            query,
            limit=limit,
            min_score=min_score,
            sender=sender,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("search_exact")
def search_exact(
    query: str,
    limit: int = 20,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    subject_only: bool = False,
) -> dict[str, Any]:
    """Find literal text, addresses, task numbers, or identifiers in emails.

    Args:
        query: Literal substring to find, case-insensitively.
        limit: Maximum number of emails, from 1 to 100.
        sender: Optional sender email or name substring.
        date_from: Optional lower date bound, YYYY-MM-DD or ISO 8601.
        date_to: Optional inclusive upper date, YYYY-MM-DD or ISO 8601.
        subject_only: Search only in email subjects when true.
    """
    try:
        return exact_search(
            query,
            limit=limit,
            sender=sender,
            date_from=date_from,
            date_to=date_to,
            subject_only=subject_only,
        )
    except Exception as exc:
        return _error(exc)

@mcp.tool()
@audited_tool("search_attachments")
def search_attachments(
    query: str,
    limit: int = 10,
    min_score: float = 0.0,
    filename: str | None = None,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Search extracted attachment text by meaning."""
    try:
        return semantic_search_attachments(
            query,
            limit=limit,
            min_score=min_score,
            filename=filename,
            sender=sender,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("search_attachments_exact")
def search_attachments_exact(
    query: str,
    limit: int = 20,
    filename: str | None = None,
) -> dict[str, Any]:
    """Find a literal phrase or identifier inside extracted attachments."""
    try:
        return exact_search_attachments(
            query,
            limit=limit,
            filename=filename,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_attachment_by_id")
def get_attachment_by_id(
    source_id: str,
    max_text_chars: int = 100_000,
) -> dict[str, Any]:
    """Return extracted attachment text and its parent email metadata."""
    try:
        return get_attachment(
            source_id,
            max_text_chars=max_text_chars,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_document_by_id")
def get_document_by_id(
    source_id: str,
    max_text_chars: int = 100_000,
) -> dict[str, Any]:
    """Return one imported standalone document and extracted text."""
    try:
        return get_external_document(
            source_id,
            max_text_chars=max_text_chars,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_email_by_id")
def get_email_by_id(
    source_id: str,
    max_body_chars: int = 50_000,
    include_html: bool = False,
) -> dict[str, Any]:
    """Return one complete archived email by source_id.

    Args:
        source_id: UUID returned by a search tool.
        max_body_chars: Maximum number of text/HTML characters returned.
        include_html: Include raw HTML body when true.
    """
    try:
        return get_email(
            source_id,
            max_body_chars=max_body_chars,
            include_html=include_html,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_email_thread")
def get_email_thread(
    source_id: str,
    max_body_chars_per_message: int = 20_000,
) -> dict[str, Any]:
    """Return the chronological conversation containing an email.

    Args:
        source_id: UUID of any email in the conversation.
        max_body_chars_per_message: Per-message text limit.
    """
    try:
        return get_thread(
            source_id,
            max_body_chars_per_message=max_body_chars_per_message,
        )
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_attachment_list")
def get_attachment_list(source_id: str) -> dict[str, Any]:
    """Return filenames, sizes, extraction status, and local paths."""
    try:
        return list_attachments(source_id)
    except Exception as exc:
        return _error(exc)


@mcp.tool()
@audited_tool("get_workmind_status")
def get_workmind_status() -> dict[str, Any]:
    """Return database, processing queue, index, and Qdrant health."""
    try:
        return system_status()
    except Exception as exc:
        return _error(exc)


def main() -> None:
    # STDIO JSON-RPC owns stdout. Do not configure WorkMind's stdout logger here.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
