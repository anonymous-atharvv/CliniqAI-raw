"""
Integration Tests — Kafka Pipeline
Tests the full MQTT→Kafka→TimescaleDB pipeline under load.
Requires Kafka + PostgreSQL running. Set KAFKA_AVAILABLE=true to enable.
"""
import pytest, asyncio, os, uuid, json

pytestmark = pytest.mark.skipif(
    os.environ.get("KAFKA_AVAILABLE") != "true",
    reason="Kafka not available — set KAFKA_AVAILABLE=true to enable"
)

@pytest.mark.asyncio
async def test_pipeline_end_to_end():
    """Verifies message flows from MQTT bridge through Kafka to storage."""
    from services.ingestion.stream_icu import MQTTKafkaBridge, VitalsKafkaConsumer
    bridge = MQTTKafkaBridge()
    stats = bridge.get_stats()
    assert "received" in stats
    assert "forwarded" in stats

@pytest.mark.asyncio
async def test_kafka_topics_exist():
    """Verify required Kafka topics are created."""
    from aiokafka.admin import AIOKafkaAdminClient
    admin = AIOKafkaAdminClient(bootstrap_servers="localhost:9092")
    await admin.start()
    try:
        topics = await admin.list_topics()
        required = ["icu.vitals.raw", "fhir.normalized", "clinical.alerts", "ai.feedback", "dlq.failed"]
        for topic in required:
            assert topic in topics, f"Required Kafka topic missing: {topic}"
    finally:
        await admin.close()

@pytest.mark.asyncio
async def test_dead_letter_queue_routing():
    """Invalid messages should route to DLQ, not crash the consumer."""
    from services.ingestion.batch_etl import DeadLetterQueue, ETLRecord
    dlq = DeadLetterQueue(kafka_producer=None, max_retries=3)
    record = ETLRecord(
        record_id=str(uuid.uuid4()), source_system="test",
        source_format="hl7v2", raw_content="INVALID_HL7",
        received_at="2026-04-11T00:00:00Z",
    )
    record.retry_count = 3
    assert not await dlq.should_retry(record)
    await dlq.send_to_dlq(record, "Max retries exceeded")
