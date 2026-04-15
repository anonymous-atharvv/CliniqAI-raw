"""
Database Connection Manager
Handles: PostgreSQL+TimescaleDB, Redis, Qdrant vector store
All connections are async, connection-pooled, and monitored.
"""

import logging
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool, QueuePool
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ─── Global connection objects (initialized at startup) ───────────────────────
_engine = None
_session_factory = None
_redis_client: Optional[aioredis.Redis] = None
_qdrant_client = None


async def init_postgres(dsn: str, pool_size: int = 10, echo: bool = False):
    """Initialize PostgreSQL async engine with connection pool."""
    global _engine, _session_factory
    _engine = create_async_engine(
        dsn,
        echo=echo,
        pool_size=pool_size,
        max_overflow=20,
        pool_pre_ping=True,          # Verify connections before use
        pool_recycle=3600,           # Recycle connections every hour
        connect_args={
            "server_settings": {
                "application_name": "cliniqai_backend",
                "jit": "off",        # Disable JIT for short queries (healthcare latency)
            }
        },
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=True,
        autocommit=False,
    )
    logger.info("PostgreSQL connection pool initialized")


async def init_redis(url: str, password: Optional[str] = None):
    """Initialize Redis async client."""
    global _redis_client
    _redis_client = await aioredis.from_url(
        url,
        password=password,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )
    await _redis_client.ping()
    logger.info("Redis connection initialized")


async def init_qdrant(host: str, port: int = 6333):
    """Initialize Qdrant vector store client."""
    global _qdrant_client
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Distance, VectorParams
        _qdrant_client = AsyncQdrantClient(host=host, port=port)

        # Ensure patient embeddings collection exists
        collections = await _qdrant_client.get_collections()
        collection_names = [c.name for c in collections.collections]

        if "patient_embeddings" not in collection_names:
            await _qdrant_client.create_collection(
                collection_name="patient_embeddings",
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: patient_embeddings")
        logger.info("Qdrant connection initialized")
    except ImportError:
        logger.warning("qdrant_client not installed — vector store unavailable")
    except Exception as e:
        logger.error(f"Qdrant init failed: {e} — vector features disabled")


async def close_all():
    """Graceful shutdown of all connections."""
    global _engine, _redis_client, _qdrant_client
    if _engine:
        await _engine.dispose()
        logger.info("PostgreSQL pool closed")
    if _redis_client:
        await _redis_client.aclose()
        logger.info("Redis connection closed")
    if _qdrant_client:
        await _qdrant_client.close()
        logger.info("Qdrant connection closed")


# ─── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: async database session.
    Usage:
        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_postgres() at startup.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency: Redis client."""
    if _redis_client is None:
        raise RuntimeError("Redis not initialized.")
    return _redis_client


async def get_qdrant():
    """FastAPI dependency: Qdrant client."""
    return _qdrant_client


# ─── TimescaleDB Utilities ────────────────────────────────────────────────────

class TimescaleHelper:
    """
    Helper methods for TimescaleDB time-series queries.
    Wraps common patterns for hypertable operations.
    """

    @staticmethod
    async def query_vitals_range(
        db: AsyncSession,
        patient_deident_id: str,
        parameter: str,
        hours: int = 6,
    ) -> list:
        """
        Efficient time-range query on vitals hypertable.
        TimescaleDB automatically selects relevant chunks.
        """
        from sqlalchemy import text
        result = await db.execute(
            text("""
                SELECT time, parameter, value, unit, is_anomaly, anomaly_sigma
                FROM cliniqai_ai.vitals_timeseries
                WHERE patient_deident_id = :patient_id
                  AND parameter = :parameter
                  AND time > NOW() - INTERVAL ':hours hours'
                ORDER BY time DESC
            """),
            {"patient_id": patient_deident_id, "parameter": parameter, "hours": hours},
        )
        return result.fetchall()

    @staticmethod
    async def get_latest_vitals(db: AsyncSession, patient_deident_id: str) -> dict:
        """Get most recent reading per vital parameter (DISTINCT ON query)."""
        from sqlalchemy import text
        result = await db.execute(
            text("""
                SELECT DISTINCT ON (parameter)
                    parameter, value, unit, time, is_critical_low, is_critical_high
                FROM cliniqai_ai.vitals_timeseries
                WHERE patient_deident_id = :patient_id
                  AND time > NOW() - INTERVAL '2 hours'
                  AND is_artifact = FALSE
                ORDER BY parameter, time DESC
            """),
            {"patient_id": patient_deident_id},
        )
        rows = result.fetchall()
        return {r.parameter: {"value": r.value, "unit": r.unit, "time": r.time} for r in rows}

    @staticmethod
    async def insert_vital_batch(db: AsyncSession, vitals: list) -> int:
        """
        Bulk insert vitals into TimescaleDB hypertable.
        Uses PostgreSQL COPY for maximum throughput.
        Returns number of rows inserted.
        """
        from sqlalchemy import text
        if not vitals:
            return 0

        # Build VALUES list
        values = ", ".join(
            f"('{v['time']}', '{v['patient_deident_id']}', '{v['encounter_id']}', "
            f"'{v['parameter']}', {v['value']}, '{v['unit']}', "
            f"{'TRUE' if v.get('is_artifact') else 'FALSE'}, "
            f"{v.get('quality_score', 1.0)}, "
            f"'{v.get('device_id', '')}', '{v.get('source_system', 'icu_monitor')}')"
            for v in vitals
        )

        await db.execute(
            text(f"""
                INSERT INTO cliniqai_ai.vitals_timeseries
                    (time, patient_deident_id, encounter_id, parameter, value, unit,
                     is_artifact, quality_score, device_id, source_system)
                VALUES {values}
                ON CONFLICT DO NOTHING
            """)
        )
        return len(vitals)


# ─── Redis Cache Helpers ──────────────────────────────────────────────────────

class RedisCache:
    """
    Typed Redis cache operations for CliniQAI.
    Key namespaces:
        session:{patient_id}     — Agent session state (TTL: 24h)
        vitals:{patient_id}      — Latest vitals snapshot (TTL: 5min)
        prediction:{patient_id}  — Latest AI prediction (TTL: 15min)
        consent:{patient_id}     — Consent state (TTL: 1h)
        blocklist:{jti}          — Token blocklist for logout (TTL: token_expiry)
    """

    def __init__(self, redis: aioredis.Redis):
        self._r = redis

    async def get_agent_session(self, patient_id: str) -> Optional[dict]:
        import json
        data = await self._r.get(f"session:{patient_id}")
        return json.loads(data) if data else None

    async def set_agent_session(self, patient_id: str, session: dict, ttl: int = 86400):
        import json
        await self._r.setex(f"session:{patient_id}", ttl, json.dumps(session, default=str))

    async def get_latest_vitals(self, patient_id: str) -> Optional[dict]:
        import json
        data = await self._r.get(f"vitals:{patient_id}")
        return json.loads(data) if data else None

    async def set_latest_vitals(self, patient_id: str, vitals: dict, ttl: int = 300):
        import json
        await self._r.setex(f"vitals:{patient_id}", ttl, json.dumps(vitals, default=str))

    async def get_prediction(self, patient_id: str) -> Optional[dict]:
        import json
        data = await self._r.get(f"prediction:{patient_id}")
        return json.loads(data) if data else None

    async def set_prediction(self, patient_id: str, prediction: dict, ttl: int = 900):
        import json
        await self._r.setex(f"prediction:{patient_id}", ttl, json.dumps(prediction, default=str))

    async def is_token_blocked(self, jti: str) -> bool:
        return await self._r.exists(f"blocklist:{jti}") > 0

    async def block_token(self, jti: str, ttl_seconds: int):
        await self._r.setex(f"blocklist:{jti}", ttl_seconds, "1")

    async def get_consent(self, patient_id: str) -> Optional[dict]:
        import json
        data = await self._r.get(f"consent:{patient_id}")
        return json.loads(data) if data else None

    async def set_consent(self, patient_id: str, consent: dict, ttl: int = 3600):
        import json
        await self._r.setex(f"consent:{patient_id}", ttl, json.dumps(consent))

    async def publish_alert(self, ward_id: str, alert: dict):
        """Publish clinical alert to ward WebSocket subscribers."""
        import json
        await self._r.publish(f"alerts:{ward_id}", json.dumps(alert, default=str))
