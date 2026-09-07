from packages.knowledge.openai_provider import _output_text


def test_output_text_reads_responses_message_content() -> None:
    response = {
        "id": "resp_1",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"entities":[],"claims":[],"relations":[],"events":[]}',
                    }
                ],
            }
        ],
    }
    assert _output_text(response).startswith('{"entities"')


def test_output_text_prefers_direct_field_when_available() -> None:
    assert _output_text({"output_text": "{}", "output": []}) == "{}"


def test_remote_provider_refuses_without_explicit_consent() -> None:
    from types import SimpleNamespace
    from packages.knowledge.openai_provider import (
        KnowledgeProviderError,
        extract_with_openai,
    )

    settings = SimpleNamespace(
        knowledge_extraction_remote_consent=False,
        knowledge_extraction_api_key="secret",
    )
    try:
        extract_with_openai({}, settings=settings)
    except KnowledgeProviderError as exc:
        assert "Remote extraction is not authorized" in str(exc)
    else:
        raise AssertionError("provider should refuse without consent")
