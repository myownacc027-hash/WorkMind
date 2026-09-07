from pathlib import Path


def test_index_admin_has_safe_check_and_rebuild() -> None:
    source = Path("apps/index_admin/main.py").read_text(encoding="utf-8")
    assert '@app.command("check")' in source
    assert '@app.command("rebuild")' in source
    assert "DROP_WORKMIND_QDRANT" in source
    assert "database_indexed_chunks" in source
    assert "payload_mismatches" in source



def test_qdrant_payload_index_command_is_present() -> None:
    source = Path(
        "apps/index_admin/main.py"
    ).read_text(encoding="utf-8")
    assert '@app.command("payload-indexes")' in source
    assert '"source_type": models.PayloadSchemaType.KEYWORD' in source
    assert "create_payload_index(" in source
    assert "timeout=600" in source
