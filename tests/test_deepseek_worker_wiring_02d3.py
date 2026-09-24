from pathlib import Path


def test_worker_supports_deepseek_provider() -> None:
    source = Path(
        "apps/knowledge_extraction_cli/worker.py"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "from packages.knowledge.deepseek_provider "
        "import extract_with_deepseek"
        in source
    )

    assert (
        'provider not in {"openai", "deepseek"}'
        in source
    )

    assert (
        'if provider == "deepseek"'
        in source
    )

    assert (
        "extract_with_deepseek"
        in source
    )

    assert (
        "settings.deepseek_model"
        in source
    )


def test_deepseek_settings_are_configurable() -> None:
    source = Path(
        "packages/common/config.py"
    ).read_text(
        encoding="utf-8"
    )

    required = (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_REASONING_EFFORT",
        "DEEPSEEK_MAX_OUTPUT_TOKENS",
        "DEEPSEEK_TIMEOUT_SECONDS",
    )

    for value in required:
        assert value in source
