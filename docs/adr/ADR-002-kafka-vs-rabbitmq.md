# ADR-002: Apache Kafka for ICU Streaming (vs RabbitMQ / AWS Kinesis)

**Date**: 2025-Q3 | **Status**: Accepted

## Decision: Apache Kafka

**Rejected:** RabbitMQ, AWS Kinesis, Redis Streams

## Rationale

| Criterion | Kafka | RabbitMQ | Kinesis |
|-----------|-------|----------|---------|
| ICU throughput (500 msg/s) | ✅ Handles with ease | ⚠ Possible but complex | ✅ |
| Message replay (for AI re-processing) | ✅ Log retention | ❌ Messages deleted after ACK | ✅ |
| Self-hosted (HIPAA on-premise option) | ✅ | ✅ | ❌ AWS only |
| Dead-letter queue | ✅ Native | ✅ Native | ⚠ Manual |
| Schema evolution | ✅ Schema Registry | ⚠ Limited | ⚠ Limited |
| Hospital IT familiarity | Medium | Low | Low |

**Why not RabbitMQ:** ICU at 1Hz × 100 devices × 5 params = 500 msg/sec. RabbitMQ can handle this but lacks Kafka's log retention — critical for replaying data when a new AI model is deployed. Re-running 30 days of vitals through a new model requires replay capability.

**Why not Kinesis:** AWS lock-in prevents on-premise hospital deployment required for some contracts.

**Kafka topic design:**
- `icu.vitals.raw` — 6 partitions (parallelism for 100+ devices)
- `fhir.normalized` — 4 partitions
- `clinical.alerts` — 4 partitions
- `ai.feedback` — 2 partitions
- `dlq.failed` — 2 partitions (dead-letter queue)
