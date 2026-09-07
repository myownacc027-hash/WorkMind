from pathlib import Path

SOURCE = Path(
    "packages/knowledge/extraction.py"
).read_text(encoding="utf-8")
RESOLVER_SOURCE = Path(
    "packages/knowledge/identity_resolution.py"
).read_text(encoding="utf-8")
CONTRACT_SOURCE = SOURCE + RESOLVER_SOURCE


def test_packet_exposes_claim_history_and_events() -> None:
    assert '"recent_claims"' in CONTRACT_SOURCE
    assert '"recent_events"' in CONTRACT_SOURCE
    assert '"supersedes_claim_id"' in CONTRACT_SOURCE
    assert '"superseded_by_claim_id"' in CONTRACT_SOURCE
    assert '"event_id"' in CONTRACT_SOURCE


def test_packet_instructions_cover_temporal_updates() -> None:
    assert "changes the current value" in SOURCE.replace('"\n                    "', "")
    assert "WorkMind will close the previous claim" in SOURCE.replace(
        '"\n                    "',
        "",
    )
    assert "compare recent_events" in SOURCE


def test_packet_separates_identity_context_from_allowed_evidence() -> None:
    assert '"identity_candidates": identity_candidates' in SOURCE
    assert '"existing_knowledge_candidates": identity_candidates' in SOURCE
    assert "identity_candidates are bounded identity" in SOURCE
    assert "They are not evidence" in SOURCE
    assert "_allowed_evidence_from_chunks(chunks)" in SOURCE


def test_packet_contains_ontology_and_v2_contract() -> None:
    assert 'PACKET_CONTRACT_VERSION = "wm-ke-v2"' in SOURCE
    assert '"packet_contract_version": PACKET_CONTRACT_VERSION' in SOURCE
    assert '"runtime_packet_contract_version": PACKET_CONTRACT_VERSION' in SOURCE
    assert '"entity_ontology": ENTITY_ONTOLOGY' in SOURCE
    assert '"system"' in SOURCE
    assert '"process"' in SOURCE
    assert "generic descriptive phrase" in SOURCE
