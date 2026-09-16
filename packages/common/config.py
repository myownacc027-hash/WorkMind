from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://workmind:change_me@localhost:5432/workmind"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "workmind_chunks"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_service_enabled: bool = True
    embedding_service_url: str = "http://127.0.0.1:8765"
    embedding_service_host: str = "127.0.0.1"
    embedding_service_port: int = Field(default=8765, ge=1024, le=65535)
    embedding_service_timeout_seconds: float = Field(default=300.0, ge=5.0, le=1800.0)
    embedding_service_fallback_local: bool = True
    chunk_max_chars: int = Field(default=500, ge=200, le=20000)
    chunk_overlap_chars: int = Field(default=80, ge=0, le=5000)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    hybrid_search_candidate_limit: int = Field(
        default=250,
        ge=50,
        le=2000,
    )
    hybrid_search_snippets_per_source: int = Field(
        default=3,
        ge=1,
        le=10,
    )
    hybrid_search_snippet_chars: int = Field(
        default=1200,
        ge=200,
        le=5000,
    )
    hybrid_search_rrf_k: int = Field(
        default=60,
        ge=1,
        le=500,
    )
    attachment_max_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1024,
        le=2 * 1024 * 1024 * 1024,
    )
    attachment_max_extracted_chars: int = Field(
        default=5_000_000,
        ge=10_000,
        le=100_000_000,
    )
    attachment_supported_extensions_csv: str = Field(
        default=(
            ".pdf,.docx,.xlsx,.xlsm,.pptx,.txt,.md,.csv,.json,"
            ".xml,.html,.htm,.log"
        ),
        validation_alias="ATTACHMENT_SUPPORTED_EXTENSIONS",
    )
    document_max_bytes: int = Field(
        default=250 * 1024 * 1024,
        ge=1024,
        le=4 * 1024 * 1024 * 1024,
    )
    document_max_extracted_chars: int = Field(
        default=10_000_000,
        ge=10_000,
        le=200_000_000,
    )
    document_supported_extensions_csv: str = Field(
        default=(
            ".pdf,.docx,.xlsx,.xlsm,.pptx,.txt,.md,.csv,.json,"
            ".xml,.html,.htm,.log"
        ),
        validation_alias="DOCUMENT_SUPPORTED_EXTENSIONS",
    )
    document_import_stale_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        validation_alias="DOCUMENT_IMPORT_STALE_MINUTES",
    )
    document_status_recent_error_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        validation_alias="DOCUMENT_STATUS_RECENT_ERROR_LIMIT",
    )

    workmind_home: Path = Path("C:/AIKnowledge")

    outlook_mailbox_name: str = "Corporate Outlook"
    outlook_account_smtp: str = Field(default="", validation_alias="OUTLOOK_ACCOUNT_SMTP")
    outlook_lookback_days: int = Field(default=90, ge=1, le=3650)
    outlook_folders_csv: str = Field(default="Inbox,Sent Items", validation_alias="OUTLOOK_FOLDERS")
    outlook_max_messages_per_folder: int = Field(default=0, ge=0)
    outlook_save_attachments: bool = True
    outlook_save_raw_json: bool = True

    mail_reconciliation_enabled: bool = Field(
        default=True,
        validation_alias="MAIL_RECONCILIATION_ENABLED",
    )
    mail_reconciliation_days: int = Field(
        default=14,
        ge=1,
        le=3650,
        validation_alias="MAIL_RECONCILIATION_DAYS",
    )
    mail_reconciliation_interval_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        validation_alias="MAIL_RECONCILIATION_INTERVAL_HOURS",
    )
    mail_reconciliation_stale_hours: int = Field(
        default=36,
        ge=1,
        le=1440,
        validation_alias="MAIL_RECONCILIATION_STALE_HOURS",
    )
    mail_reconciliation_max_per_folder: int = Field(
        default=0,
        ge=0,
        validation_alias="MAIL_RECONCILIATION_MAX_PER_FOLDER",
    )

    history_primary_store: str = Field(
        default="",
        validation_alias="HISTORY_PRIMARY_STORE",
    )
    history_archive_store: str = Field(
        default="",
        validation_alias="HISTORY_ARCHIVE_STORE",
    )
    history_excluded_folders_csv: str = Field(
        default=(
            "Удаленные,Deleted Items,Нежелательная почта,"
            "Junk E-mail,Спам,Spambox,Ошибки синхронизации,"
            "Sync Issues,Исходящие,Outbox,Черновики,Drafts,"
            "RSS-каналы,RSS Feeds,Conversation Action Settings,"
            "ExternalContacts,Корневая папка Yammer,"
            "Журнал бесед,Conversation History"
        ),
        validation_alias="HISTORY_EXCLUDED_FOLDERS",
    )

    knowledge_extraction_enabled: bool = Field(
        default=True,
        validation_alias="KNOWLEDGE_EXTRACTION_ENABLED",
    )
    knowledge_extraction_auto_queue: bool = Field(
        default=True,
        validation_alias="KNOWLEDGE_EXTRACTION_AUTO_QUEUE",
    )
    knowledge_extraction_version: str = Field(
        default="wm-ke-v2",
        validation_alias="KNOWLEDGE_EXTRACTION_VERSION",
    )
    knowledge_extraction_min_chars: int = Field(
        default=160,
        ge=1,
        le=100_000,
        validation_alias="KNOWLEDGE_EXTRACTION_MIN_CHARS",
    )
    knowledge_extraction_lease_minutes: int = Field(
        default=30,
        ge=5,
        le=1440,
        validation_alias="KNOWLEDGE_EXTRACTION_LEASE_MINUTES",
    )
    knowledge_extraction_max_attempts: int = Field(
        default=5,
        ge=1,
        le=50,
        validation_alias="KNOWLEDGE_EXTRACTION_MAX_ATTEMPTS",
    )
    knowledge_extraction_max_context_chars: int = Field(
        default=60_000,
        ge=5_000,
        le=500_000,
        validation_alias="KNOWLEDGE_EXTRACTION_MAX_CONTEXT_CHARS",
    )
    knowledge_extraction_max_chunks: int = Field(
        default=120,
        ge=5,
        le=500,
        validation_alias="KNOWLEDGE_EXTRACTION_MAX_CHUNKS",
    )
    knowledge_extraction_thread_messages: int = Field(
        default=12,
        ge=1,
        le=100,
        validation_alias="KNOWLEDGE_EXTRACTION_THREAD_MESSAGES",
    )
    knowledge_identity_max_candidates: int = Field(
        default=8,
        ge=1,
        le=30,
        validation_alias="KNOWLEDGE_IDENTITY_MAX_CANDIDATES",
    )
    knowledge_identity_exact_query_limit: int = Field(
        default=60,
        ge=1,
        le=500,
        validation_alias="KNOWLEDGE_IDENTITY_EXACT_QUERY_LIMIT",
    )
    knowledge_identity_exact_phrase_limit: int = Field(
        default=600,
        ge=20,
        le=5000,
        validation_alias="KNOWLEDGE_IDENTITY_EXACT_PHRASE_LIMIT",
    )
    knowledge_identity_semantic_source_limit: int = Field(
        default=20,
        ge=1,
        le=50,
        validation_alias="KNOWLEDGE_IDENTITY_SEMANTIC_SOURCE_LIMIT",
    )
    knowledge_identity_semantic_threshold: float = Field(
        default=0.58,
        ge=0.0,
        le=1.0,
        validation_alias="KNOWLEDGE_IDENTITY_SEMANTIC_THRESHOLD",
    )
    knowledge_identity_lexical_min_score: float = Field(
        default=0.62,
        ge=0.0,
        le=1.0,
        validation_alias="KNOWLEDGE_IDENTITY_LEXICAL_MIN_SCORE",
    )
    knowledge_identity_lexical_scan_limit: int = Field(
        default=300,
        ge=20,
        le=2000,
        validation_alias="KNOWLEDGE_IDENTITY_LEXICAL_SCAN_LIMIT",
    )
    knowledge_identity_semantic_chunk_limit: int = Field(
        default=60,
        ge=1,
        le=500,
        validation_alias="KNOWLEDGE_IDENTITY_SEMANTIC_CHUNK_LIMIT",
    )
    knowledge_identity_semantic_evidence_limit: int = Field(
        default=240,
        ge=1,
        le=2000,
        validation_alias="KNOWLEDGE_IDENTITY_SEMANTIC_EVIDENCE_LIMIT",
    )
    knowledge_identity_semantic_entity_limit: int = Field(
        default=60,
        ge=1,
        le=500,
        validation_alias="KNOWLEDGE_IDENTITY_SEMANTIC_ENTITY_LIMIT",
    )
    knowledge_identity_active_claims_per_entity: int = Field(
        default=60,
        ge=1,
        le=500,
        validation_alias="KNOWLEDGE_IDENTITY_ACTIVE_CLAIMS_PER_ENTITY",
    )
    knowledge_identity_mandatory_claims_safety_cap: int = Field(
        default=200,
        ge=10,
        le=2000,
        validation_alias="KNOWLEDGE_IDENTITY_MANDATORY_CLAIMS_SAFETY_CAP",
    )
    knowledge_identity_recent_claims_per_entity: int = Field(
        default=60,
        ge=1,
        le=500,
        validation_alias="KNOWLEDGE_IDENTITY_RECENT_CLAIMS_PER_ENTITY",
    )
    knowledge_identity_recent_events_per_entity: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias="KNOWLEDGE_IDENTITY_RECENT_EVENTS_PER_ENTITY",
    )
    knowledge_extraction_quality_history_limit: int = Field(
        default=50,
        ge=5,
        le=500,
        validation_alias="KNOWLEDGE_EXTRACTION_QUALITY_HISTORY_LIMIT",
    )
    knowledge_identity_duplicate_risk_threshold: float = Field(
        default=0.78,
        ge=0.0,
        le=1.0,
        validation_alias="KNOWLEDGE_IDENTITY_DUPLICATE_RISK_THRESHOLD",
    )
    knowledge_extraction_provider: str = Field(
        default="agent",
        validation_alias="KNOWLEDGE_EXTRACTION_PROVIDER",
    )
    knowledge_extraction_auto_apply: bool = Field(
        default=False,
        validation_alias="KNOWLEDGE_EXTRACTION_AUTO_APPLY",
    )
    knowledge_extraction_model: str = Field(
        default="gpt-5-mini",
        validation_alias="KNOWLEDGE_EXTRACTION_MODEL",
    )
    knowledge_extraction_api_base: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="KNOWLEDGE_EXTRACTION_API_BASE",
    )
    knowledge_extraction_api_key: str = Field(
        default="",
        validation_alias="KNOWLEDGE_EXTRACTION_API_KEY",
    )
    knowledge_extraction_remote_consent: bool = Field(
        default=False,
        validation_alias="KNOWLEDGE_EXTRACTION_REMOTE_CONSENT",
    )
    knowledge_extraction_timeout_seconds: float = Field(
        default=120.0,
        ge=10.0,
        le=1800.0,
        validation_alias="KNOWLEDGE_EXTRACTION_TIMEOUT_SECONDS",
    )

    log_level: str = "INFO"

    @property
    def outlook_folders(self) -> list[str]:
        return [
            item.strip()
            for item in self.outlook_folders_csv.split(",")
            if item.strip()
        ]

    @property
    def history_excluded_folders(self) -> list[str]:
        return [
            item.strip()
            for item in self.history_excluded_folders_csv.split(",")
            if item.strip()
        ]

    @property
    def attachment_supported_extensions(self) -> set[str]:
        return self._extension_set(
            self.attachment_supported_extensions_csv
        )

    @property
    def document_supported_extensions(self) -> set[str]:
        return self._extension_set(
            self.document_supported_extensions_csv
        )

    @staticmethod
    def _extension_set(value: str) -> set[str]:
        return {
            (
                item.strip().lower()
                if item.strip().startswith(".")
                else f".{item.strip().lower()}"
            )
            for item in value.split(",")
            if item.strip()
        }

    @property
    def embedding_cache_dir(self) -> Path:
        return self.workmind_home / "models" / "fastembed"

    @property
    def run_dir(self) -> Path:
        return self.workmind_home / "run"

    @property
    def embedding_service_pid_file(self) -> Path:
        return self.run_dir / "embedding-service.pid"

    @property
    def embedding_service_log_file(self) -> Path:
        return self.logs_dir / "embedding-service.log"

    @property
    def sync_lock_file(self) -> Path:
        return self.run_dir / "mail-sync.lock"

    @property
    def sync_status_file(self) -> Path:
        return self.run_dir / "mail-sync-status.json"

    @property
    def sync_log_file(self) -> Path:
        return self.logs_dir / "mail-sync.jsonl"


    @property
    def mail_reconciliation_status_file(self) -> Path:
        return self.run_dir / "mail-reconciliation-status.json"

    @property
    def knowledge_extraction_status_file(self) -> Path:
        return self.run_dir / "knowledge-extraction-status.json"

    @property
    def knowledge_extraction_log_file(self) -> Path:
        return self.logs_dir / "knowledge-extraction.jsonl"

    @property
    def outlook_operation_lock_file(self) -> Path:
        return self.run_dir / "outlook-operation.lock"

    @property
    def history_status_file(self) -> Path:
        return self.run_dir / "history-import-status.json"

    @property
    def history_log_file(self) -> Path:
        return self.logs_dir / "history-import.jsonl"

    @property
    def attachment_worker_status_file(self) -> Path:
        return self.run_dir / "attachment-worker-status.json"

    @property
    def attachment_worker_log_file(self) -> Path:
        return self.logs_dir / "attachment-worker.jsonl"

    @property
    def attachment_text_dir(self) -> Path:
        return self.workmind_home / "processed" / "attachments"

    @property
    def document_import_dir(self) -> Path:
        return self.workmind_home / "import"

    @property
    def document_archive_dir(self) -> Path:
        return self.workmind_home / "archive" / "documents"

    @property
    def document_text_dir(self) -> Path:
        return self.workmind_home / "processed" / "documents" / "text"

    @property
    def document_metadata_dir(self) -> Path:
        return self.workmind_home / "processed" / "documents" / "metadata"

    @property
    def document_duplicate_dir(self) -> Path:
        return self.workmind_home / "processed" / "documents" / "duplicates"

    @property
    def document_failed_dir(self) -> Path:
        return self.workmind_home / "failed" / "documents"

    @property
    def document_import_lock_file(self) -> Path:
        return self.run_dir / "document-import.lock"

    @property
    def document_import_status_file(self) -> Path:
        return self.run_dir / "document-import-status.json"

    @property
    def document_import_log_file(self) -> Path:
        return self.logs_dir / "document-import.jsonl"

    @property
    def mail_archive_dir(self) -> Path:
        return self.workmind_home / "archive" / "mail"

    @property
    def attachment_archive_dir(self) -> Path:
        return self.workmind_home / "archive" / "attachments"

    @property
    def logs_dir(self) -> Path:
        return self.workmind_home / "logs"

    def ensure_directories(self) -> None:
        for path in (
            self.workmind_home,
            self.embedding_cache_dir,
            self.run_dir,
            self.mail_archive_dir,
            self.attachment_archive_dir,
            self.attachment_text_dir,
            self.document_import_dir,
            self.document_archive_dir,
            self.document_text_dir,
            self.document_metadata_dir,
            self.document_duplicate_dir,
            self.document_failed_dir,
            self.logs_dir,
            self.workmind_home / "import",
            self.workmind_home / "processed",
            self.workmind_home / "failed",
            self.workmind_home / "obsidian-vault",
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
