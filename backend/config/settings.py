"""
CliniQAI Backend Configuration
Centralized settings with environment variable support
HIPAA: No secrets in code. All sensitive values from environment.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, validator
from typing import Optional, List
from functools import lru_cache
import os


class DatabaseSettings(BaseSettings):
    # Primary PostgreSQL + TimescaleDB (hot path)
    POSTGRES_HOST: str = Field(default="localhost", env="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(default=5432, env="POSTGRES_PORT")
    POSTGRES_DB: str = Field(default="cliniqai", env="POSTGRES_DB")
    POSTGRES_USER: str = Field(default="cliniqai", env="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field(default="devpassword", env="POSTGRES_PASSWORD")
    
    # Redis (agent state, session, TTL-based cache)
    REDIS_URL: str = Field(default="redis://localhost:6379", env="REDIS_URL")
    REDIS_AGENT_TTL: int = Field(default=86400, env="REDIS_AGENT_TTL")  # 24 hours
    
    # Qdrant (self-hosted vector store - HIPAA safe)
    QDRANT_HOST: str = Field(default="localhost", env="QDRANT_HOST")
    QDRANT_PORT: int = Field(default=6333, env="QDRANT_PORT")
    QDRANT_COLLECTION: str = Field(default="patient_embeddings", env="QDRANT_COLLECTION")
    
    # S3 (warm path parquet + Glacier archive)
    AWS_REGION: str = Field(default="us-east-1", env="AWS_REGION")
    S3_WARM_BUCKET: str = Field(default="cliniqai-warm-dev", env="S3_WARM_BUCKET")
    S3_ARCHIVE_BUCKET: str = Field(default="cliniqai-archive-dev", env="S3_ARCHIVE_BUCKET")
    
    # Orthanc DICOM server
    ORTHANC_URL: str = Field(default="http://localhost:8042", env="ORTHANC_URL")
    ORTHANC_USER: str = Field(default="orthanc", env="ORTHANC_USER")
    ORTHANC_PASSWORD: str = Field(default="orthanc", env="ORTHANC_PASSWORD")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


class KafkaSettings(BaseSettings):
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        default="localhost:9092", env="KAFKA_BOOTSTRAP_SERVERS"
    )
    KAFKA_SECURITY_PROTOCOL: str = Field(default="SASL_SSL", env="KAFKA_SECURITY_PROTOCOL")
    KAFKA_SASL_MECHANISM: str = Field(default="SCRAM-SHA-512", env="KAFKA_SASL_MECHANISM")
    KAFKA_USERNAME: str = Field(default="cliniqai", env="KAFKA_USERNAME")
    KAFKA_PASSWORD: str = Field(default="dev-kafka-password", env="KAFKA_PASSWORD")
    
    # Topic names
    TOPIC_ICU_VITALS: str = "icu.vitals.raw"
    TOPIC_FHIR_NORMALIZED: str = "fhir.normalized"
    TOPIC_ALERTS: str = "clinical.alerts"
    TOPIC_FEEDBACK: str = "ai.feedback"
    TOPIC_DEAD_LETTER: str = "dlq.failed"
    
    # Consumer settings
    CONSUMER_GROUP_AI: str = "cliniqai-ai-processors"
    CONSUMER_GROUP_AUDIT: str = "cliniqai-audit"
    MAX_POLL_RECORDS: int = 500
    
    # Retry settings
    MAX_RETRY_ATTEMPTS: int = 3
    RETRY_BACKOFF_BASE: float = 2.0  # Exponential: 2^n seconds
    RETRY_MAX_WAIT: int = 60  # Max 60 seconds


class AISettings(BaseSettings):
    # LLM Configuration (vendor-agnostic)
    LLM_PROVIDER: str = Field(default="azure_openai", env="LLM_PROVIDER")  # or "anthropic"
    
    # Azure OpenAI (HIPAA BAA available)
    AZURE_OPENAI_ENDPOINT: str = Field(default="https://your-resource.openai.azure.com/", env="AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_API_KEY: str = Field(default="your-azure-key-here", env="AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_DEPLOYMENT: str = Field(
        default="gpt-4o", env="AZURE_OPENAI_DEPLOYMENT"
    )
    AZURE_API_VERSION: str = Field(default="2024-02-01", env="AZURE_API_VERSION")
    
    # Anthropic (alternative)
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL: str = Field(
        default="claude-sonnet-4-20250514", env="ANTHROPIC_MODEL"
    )
    
    # LLM reasoning settings
    LLM_MAX_TOKENS: int = 4096
    LLM_CONTEXT_WINDOW: int = 128000
    LLM_TEMPERATURE: float = 0.1  # Low temp for clinical consistency
    LLM_MAX_RETRIES: int = 3
    
    # Imaging models
    MONAI_MODEL_PATH: str = Field(
        default="/models/biovil-t", env="MONAI_MODEL_PATH"
    )
    MEDSAM_MODEL_PATH: str = Field(
        default="/models/medsam", env="MEDSAM_MODEL_PATH"
    )
    
    # NLP models
    BIOMEDBERT_MODEL: str = "microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext"
    
    # Vitals model
    TFT_MODEL_PATH: str = Field(default="/models/tft-vitals", env="TFT_MODEL_PATH")
    TFT_MIMIC_PRETRAINED: bool = True
    
    # Agent settings
    AGENT_TIMEOUT_SECONDS: int = 10
    AGENT_CIRCUIT_BREAKER_FAILURES: int = 3
    AGENT_CIRCUIT_BREAKER_WINDOW: int = 300  # 5 minutes
    COORDINATOR_POLL_SECONDS: int = 30
    
    # Alert thresholds (configurable per hospital)
    NEWS2_HIGH_ALERT: int = 5
    DETERIORATION_6H_THRESHOLD: float = 0.70
    SEPSIS_12H_THRESHOLD: float = 0.50
    MORTALITY_24H_THRESHOLD: float = 0.40
    
    # Semantic deduplication
    DEDUP_SIMILARITY_THRESHOLD: float = 0.92


class SecuritySettings(BaseSettings):
    # JWT
    JWT_SECRET_KEY: str = Field(default="dev-secret-CHANGE-IN-PRODUCTION-min-32-chars", env="JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "RS256"  # Asymmetric for production
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Encryption
    ENCRYPTION_KEY_ARN: str = Field(default="arn:aws:kms:us-east-1:000000000000:key/dev", env="KMS_KEY_ARN")  # AWS KMS
    ENCRYPTION_ALGORITHM: str = "AES-256-GCM"
    FERNET_KEY: str = Field(default="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=", env="FERNET_KEY")  # For application-level encryption
    
    # De-identification
    DEIDENT_DATE_SHIFT_MAX_DAYS: int = 90  # ±90 days date shift
    DEIDENT_SALT: str = Field(default="dev-deident-salt-CHANGE-IN-PRODUCTION-32", env="DEIDENT_SALT")  # Per-deployment salt
    
    # MPI mapping store (original↔de-identified)
    MPI_VAULT_URL: str = Field(default="http://localhost:8200", env="MPI_VAULT_URL")  # HashiCorp Vault
    MPI_VAULT_TOKEN: str = Field(default="dev-vault-token", env="MPI_VAULT_TOKEN")
    
    # TLS
    TLS_VERSION: str = "TLSv1.3"
    
    # CORS (restrict to hospital domains in prod)
    ALLOWED_ORIGINS: List[str] = Field(
        default=["http://localhost:3000"], env="ALLOWED_ORIGINS"
    )
    
    # Audit
    AUDIT_LOG_BUCKET: str = Field(default="cliniqai-audit-dev", env="AUDIT_LOG_BUCKET")  # WORM bucket
    AUDIT_RETENTION_YEARS: int = 6  # HIPAA minimum


class ComplianceSettings(BaseSettings):
    # HIPAA
    HIPAA_COVERED_ENTITY: str = Field(default="Dev Hospital", env="HIPAA_COVERED_ENTITY_NAME")
    PHI_IDENTIFIERS_COUNT: int = 18  # Safe Harbor standard
    
    # Data quality thresholds
    QUALITY_SCORE_MINIMUM: float = 0.60
    QUALITY_COMPLETENESS_WEIGHT: float = 0.30
    QUALITY_TIMELINESS_WEIGHT: float = 0.25
    QUALITY_CONSISTENCY_WEIGHT: float = 0.25
    QUALITY_VALIDITY_WEIGHT: float = 0.20
    
    # Breach detection thresholds
    BREACH_BULK_RECORDS_PER_HOUR: int = 50
    BREACH_ALERT_MINUTES: int = 15  # HIPAA: notify within 15 min of detection
    
    # FDA SaMD
    SAMD_CLASS: str = "II"  # Class II → 510k clearance pathway
    FDA_CLEARANCE_STATUS: str = "pending"  # Update when cleared
    
    # Data retention
    AUDIT_LOG_RETENTION_YEARS: int = 6
    PHI_ARCHIVE_RETENTION_YEARS: int = 7
    MODEL_VERSION_RETENTION_YEARS: int = 10


class MonitoringSettings(BaseSettings):
    # Prometheus
    PROMETHEUS_PORT: int = 9090
    METRICS_ENABLED: bool = True
    
    # Sentry (error tracking - NO PHI in error messages)
    SENTRY_DSN: Optional[str] = Field(default=None, env="SENTRY_DSN")
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    
    # Model drift detection
    DRIFT_ACCURACY_DROP_THRESHOLD: float = 0.05  # 5% drop triggers alert
    DRIFT_FP_RATE_INCREASE_THRESHOLD: float = 0.10  # 10% FP increase
    DRIFT_REJECTION_RATE_THRESHOLD: float = 0.30  # 30% rejection rate
    DRIFT_CHECK_INTERVAL_DAYS: int = 7


class Settings(
    DatabaseSettings,
    KafkaSettings,
    AISettings,
    SecuritySettings,
    ComplianceSettings,
    MonitoringSettings,
):
    # Application
    APP_NAME: str = "CliniQAI"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    DEBUG: bool = Field(default=False, env="DEBUG")
    
    # Hospital configuration (per-deployment)
    HOSPITAL_NAME: str = Field(default="Dev Community Hospital", env="HOSPITAL_NAME")
    HOSPITAL_ID: str = Field(default="hospital_dev_001", env="HOSPITAL_ID")
    HOSPITAL_EHR_SYSTEM: str = Field(default="epic", env="HOSPITAL_EHR_SYSTEM")  # epic|cerner|meditech
    HOSPITAL_BED_COUNT: int = Field(default=300, env="HOSPITAL_BED_COUNT")
    
    # Feature flags
    FEATURE_IMAGING_AI: bool = Field(default=True)
    FEATURE_SEPSIS_PREDICTION: bool = Field(default=True)
    FEATURE_PHARMACIST_AGENT: bool = Field(default=True)
    FEATURE_FEDERATED_LEARNING: bool = Field(default=False)  # Enable at multi-hospital
    FEATURE_VOICE_DOCUMENTATION: bool = Field(default=False)  # Phase 2
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @validator("ENVIRONMENT")
    def validate_environment(cls, v):
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}")
        return v


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance. Call this everywhere."""
    return Settings()

# Module-level singleton (convenience import)
settings = get_settings()
