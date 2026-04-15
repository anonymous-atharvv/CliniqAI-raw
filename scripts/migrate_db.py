#!/usr/bin/env python3
"""
Database Migration Script
Runs SQL schema migrations against the target database.
Uses Alembic for versioned migrations + raw SQL for TimescaleDB-specific setup.

Usage:
  python scripts/migrate_db.py                    # Run all pending migrations
  python scripts/migrate_db.py --check            # Show pending migrations
  python scripts/migrate_db.py --rollback 1       # Roll back 1 migration
  python scripts/migrate_db.py --seed-dev         # Load dev synthetic data (non-prod only)

HIPAA: Never run seed-dev against a production database.
Production data: real patient data — never seed.
"""

import os, sys, argparse, logging, asyncio
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Add backend to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


# ─────────────────────────────────────────────
# Migration Steps
# ─────────────────────────────────────────────

MIGRATIONS = [
    {
        "version": "001",
        "name": "initial_schema",
        "description": "Create all schemas, tables, hypertables, indexes",
        "file": "scripts/init_db.sql",
        "reversible": False,  # Cannot drop schemas with data
    },
    {
        "version": "002",
        "name": "add_patient_baseline_index",
        "description": "Add composite index on patient_baselines for faster lookup",
        "sql_up": "CREATE INDEX IF NOT EXISTS idx_baselines_patient_encounter ON cliniqai_ai.patient_baselines(patient_deident_id, encounter_id);",
        "sql_down": "DROP INDEX IF EXISTS cliniqai_ai.idx_baselines_patient_encounter;",
        "reversible": True,
    },
    {
        "version": "003",
        "name": "add_feedback_ml_signal_trigger",
        "description": "Auto-compute ml_signal from signal field on insert",
        "sql_up": """
            CREATE OR REPLACE FUNCTION compute_ml_signal()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.ml_signal := CASE NEW.signal
                    WHEN 'accepted'    THEN 1.0
                    WHEN 'thumbs_up'   THEN 1.0
                    WHEN 'modified'    THEN 0.5
                    WHEN 'rejected'    THEN -0.5
                    WHEN 'thumbs_down' THEN -1.0
                    ELSE 0.0
                END;
                NEW.is_valid_for_training := (
                    NEW.is_treating_physician AND
                    NEW.is_in_distribution AND
                    NOT (NEW.signal = 'thumbs_down' AND NEW.free_text_reason IS NULL)
                );
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS trg_feedback_ml_signal ON cliniqai_ai.feedback;
            CREATE TRIGGER trg_feedback_ml_signal
                BEFORE INSERT ON cliniqai_ai.feedback
                FOR EACH ROW EXECUTE FUNCTION compute_ml_signal();
        """,
        "sql_down": "DROP TRIGGER IF EXISTS trg_feedback_ml_signal ON cliniqai_ai.feedback;",
        "reversible": True,
    },
    {
        "version": "004",
        "name": "add_drift_snapshot_table",
        "description": "Add model drift snapshot tracking table",
        "sql_up": """
            CREATE TABLE IF NOT EXISTS cliniqai_ops.drift_snapshots (
                snapshot_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                model_id            UUID,
                hospital_id         VARCHAR(64),
                snapshot_week       DATE NOT NULL,
                auroc               NUMERIC(5,4),
                acceptance_rate     NUMERIC(5,4),
                false_positive_rate NUMERIC(5,4),
                rejection_rate      NUMERIC(5,4),
                auroc_vs_baseline   NUMERIC(6,4),
                drift_detected      BOOLEAN NOT NULL DEFAULT FALSE,
                drift_alerts        TEXT[],
                auto_updates_frozen BOOLEAN NOT NULL DEFAULT FALSE,
                action_taken        VARCHAR(128),
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_drift_model_week
                ON cliniqai_ops.drift_snapshots(model_id, snapshot_week DESC);
        """,
        "sql_down": "DROP TABLE IF EXISTS cliniqai_ops.drift_snapshots;",
        "reversible": True,
    },
]


class MigrationRunner:
    def __init__(self, db_url: str):
        self.db_url = db_url

    async def connect(self):
        import asyncpg
        self.conn = await asyncpg.connect(self.db_url)
        await self._ensure_migrations_table()

    async def disconnect(self):
        await self.conn.close()

    async def _ensure_migrations_table(self):
        await self.conn.execute("""
            CREATE SCHEMA IF NOT EXISTS cliniqai_migrations;
            CREATE TABLE IF NOT EXISTS cliniqai_migrations.applied (
                version     VARCHAR(10) PRIMARY KEY,
                name        VARCHAR(128) NOT NULL,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                applied_by  VARCHAR(64) NOT NULL DEFAULT current_user,
                checksum    VARCHAR(64)
            );
        """)

    async def get_applied_versions(self) -> set:
        rows = await self.conn.fetch("SELECT version FROM cliniqai_migrations.applied")
        return {r["version"] for r in rows}

    async def run_pending(self, dry_run: bool = False) -> int:
        applied = await self.get_applied_versions()
        pending = [m for m in MIGRATIONS if m["version"] not in applied]

        if not pending:
            logger.info("✅ Database is up to date. No pending migrations.")
            return 0

        logger.info(f"Found {len(pending)} pending migration(s):")
        for m in pending:
            logger.info(f"  [{m['version']}] {m['name']} — {m['description']}")

        if dry_run:
            logger.info("Dry run — no changes applied.")
            return len(pending)

        for migration in pending:
            await self._apply_migration(migration)

        logger.info(f"✅ Applied {len(pending)} migration(s).")
        return len(pending)

    async def _apply_migration(self, migration: dict):
        logger.info(f"Applying [{migration['version']}] {migration['name']}…")
        sql = migration.get("sql_up")

        if not sql and migration.get("file"):
            file_path = Path(migration["file"])
            if file_path.exists():
                sql = file_path.read_text()
            else:
                logger.error(f"Migration file not found: {migration['file']}")
                raise FileNotFoundError(migration["file"])

        async with self.conn.transaction():
            if sql:
                await self.conn.execute(sql)
            await self.conn.execute(
                """INSERT INTO cliniqai_migrations.applied (version, name)
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                migration["version"], migration["name"]
            )

        logger.info(f"  ✅ [{migration['version']}] applied successfully.")

    async def rollback(self, steps: int = 1):
        applied = await self.get_applied_versions()
        reversible = [
            m for m in reversed(MIGRATIONS)
            if m["version"] in applied and m.get("reversible")
        ][:steps]

        if not reversible:
            logger.warning("No reversible migrations to roll back.")
            return

        for migration in reversible:
            logger.info(f"Rolling back [{migration['version']}] {migration['name']}…")
            async with self.conn.transaction():
                if sql_down := migration.get("sql_down"):
                    await self.conn.execute(sql_down)
                await self.conn.execute(
                    "DELETE FROM cliniqai_migrations.applied WHERE version = $1",
                    migration["version"]
                )
            logger.info(f"  ✅ [{migration['version']}] rolled back.")

    async def status(self):
        applied = await self.get_applied_versions()
        logger.info("Migration Status:")
        for m in MIGRATIONS:
            status = "✅ APPLIED" if m["version"] in applied else "⏳ PENDING"
            logger.info(f"  [{m['version']}] {status} — {m['name']}")


async def main():
    parser = argparse.ArgumentParser(description="CliniQAI Database Migration Tool")
    parser.add_argument("--check", action="store_true", help="Show migration status without applying")
    parser.add_argument("--dry-run", action="store_true", help="Show pending migrations without applying")
    parser.add_argument("--rollback", type=int, metavar="N", help="Roll back N migrations")
    parser.add_argument("--seed-dev", action="store_true", help="Load dev synthetic data (non-prod only)")
    args = parser.parse_args()

    env = os.environ.get("ENVIRONMENT", "development")
    if args.seed_dev and env == "production":
        logger.error("❌ REFUSED: --seed-dev cannot run against a production database.")
        sys.exit(1)

    db_url = (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'cliniqai')}:"
        f"{os.environ.get('POSTGRES_PASSWORD', 'devpassword')}@"
        f"{os.environ.get('POSTGRES_HOST', 'localhost')}:5432/"
        f"{os.environ.get('POSTGRES_DB', 'cliniqai')}"
    )

    logger.info(f"Connecting to database (env={env})…")
    runner = MigrationRunner(db_url)

    try:
        await runner.connect()

        if args.check:
            await runner.status()
        elif args.rollback:
            await runner.rollback(args.rollback)
        elif args.dry_run:
            await runner.run_pending(dry_run=True)
        else:
            await runner.run_pending()

        if args.seed_dev and env != "production":
            logger.info("Loading synthetic dev data…")
            import subprocess
            result = subprocess.run(
                [sys.executable, "scripts/seed_synthea.py", "--patients", "100", "--icu", "20"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                logger.info("✅ Dev data loaded successfully.")
            else:
                logger.error(f"Seed failed: {result.stderr}")

    finally:
        await runner.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
