#!/usr/bin/env python3
"""
Performance Load Test — CliniQAI
Tests system under hospital-scale load before deployment.

Targets:
  - API response p95 < 200ms (non-AI endpoints)
  - AI inference p95 < 10s
  - ICU vitals ingest: 500 msg/sec sustained
  - 50,000 patient records load in < 30 min
  - WebSocket: 100 concurrent connections

Usage:
  python scripts/perf_test.py --scenario api        # REST API load test
  python scripts/perf_test.py --scenario vitals     # ICU streaming load test
  python scripts/perf_test.py --scenario inference  # AI inference latency test
  python scripts/perf_test.py --scenario all        # All scenarios
  python scripts/perf_test.py --scenario 50k        # 50k patient load test
"""

import asyncio, time, sys, argparse, statistics, uuid, json, random, logging
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "http://localhost:8000"


@dataclass
class PerfResult:
    scenario: str
    total_requests: int
    successful: int
    failed: int
    latencies_ms: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successful / self.total_requests if self.total_requests > 0 else 0.0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms: return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[idx]

    @property
    def p99_ms(self) -> float:
        if not self.latencies_ms: return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.99)
        return sorted_l[idx]

    @property
    def rps(self) -> float:
        return self.successful / self.duration_seconds if self.duration_seconds > 0 else 0.0

    def print_report(self):
        status = "✅ PASS" if self._meets_targets() else "❌ FAIL"
        print(f"\n{'='*60}")
        print(f"  {status} — {self.scenario}")
        print(f"{'='*60}")
        print(f"  Duration:      {self.duration_seconds:.1f}s")
        print(f"  Total requests:{self.total_requests}")
        print(f"  Successful:    {self.successful} ({self.success_rate:.1%})")
        print(f"  Failed:        {self.failed}")
        print(f"  Throughput:    {self.rps:.1f} req/s")
        print(f"  Latency p50:   {self.p50_ms:.0f}ms")
        print(f"  Latency p95:   {self.p95_ms:.0f}ms")
        print(f"  Latency p99:   {self.p99_ms:.0f}ms")
        if self.errors[:3]:
            print(f"  Sample errors: {self.errors[:3]}")
        self._print_targets()

    def _meets_targets(self) -> bool:
        if "vitals" in self.scenario:
            return self.rps >= 450 and self.success_rate >= 0.999
        if "inference" in self.scenario:
            return self.p95_ms <= 10000 and self.success_rate >= 0.99
        return self.p95_ms <= 200 and self.success_rate >= 0.999

    def _print_targets(self):
        targets = {
            "API": {"p95_ms": 200, "success_rate": 0.999},
            "Vitals ingest": {"rps": 500, "success_rate": 0.999},
            "AI Inference": {"p95_ms": 10000, "success_rate": 0.99},
        }.get(self.scenario, {})
        if targets:
            print(f"\n  Targets for {self.scenario}:")
            for k, v in targets.items():
                actual = getattr(self, k, None)
                if actual is not None:
                    passed = "✅" if actual >= v else "❌"
                    print(f"    {passed} {k}: {actual:.1f} (target: {v})")


async def _get(session, url: str) -> tuple:
    """Single GET request. Returns (latency_ms, status_code, error)."""
    t0 = time.time()
    try:
        import aiohttp
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            await r.read()
            return (time.time() - t0) * 1000, r.status, None
    except Exception as e:
        return (time.time() - t0) * 1000, 0, str(e)


async def _post(session, url: str, payload: dict) -> tuple:
    t0 = time.time()
    try:
        import aiohttp
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            await r.read()
            return (time.time() - t0) * 1000, r.status, None
    except Exception as e:
        return (time.time() - t0) * 1000, 0, str(e)


async def test_api_load(concurrency: int = 50, duration_s: int = 30) -> PerfResult:
    """REST API load test — patient list, health check, vitals trend."""
    import aiohttp
    result = PerfResult(scenario="API Load", total_requests=0, successful=0, failed=0)
    endpoints = [
        f"{BASE_URL}/health",
        f"{BASE_URL}/api/v1/vitals/icu/ICU-B/snapshot",
        f"{BASE_URL}/api/v1/agents/status",
        f"{BASE_URL}/api/v1/admin/dashboard/cfo",
    ]

    async def worker():
        async with aiohttp.ClientSession() as session:
            end_time = time.time() + duration_s
            while time.time() < end_time:
                url = random.choice(endpoints)
                lat, status, err = await _get(session, url)
                result.total_requests += 1
                result.latencies_ms.append(lat)
                if 200 <= status < 300:
                    result.successful += 1
                else:
                    result.failed += 1
                    if err: result.errors.append(err)

    t0 = time.time()
    await asyncio.gather(*[worker() for _ in range(concurrency)])
    result.duration_seconds = time.time() - t0
    return result


async def test_vitals_ingest(target_rps: int = 500, duration_s: int = 20) -> PerfResult:
    """ICU vitals ingest load test — target 500 messages/second."""
    import aiohttp
    result = PerfResult(scenario="Vitals ingest", total_requests=0, successful=0, failed=0)
    patient_ids = [str(uuid.uuid4()) for _ in range(20)]
    encounter_ids = [str(uuid.uuid4()) for _ in range(20)]
    params = ["heart_rate", "spo2_pulse_ox", "bp_systolic", "respiratory_rate", "temperature"]
    units  = ["/min", "%", "mm[Hg]", "/min", "Cel"]

    async def burst_worker():
        async with aiohttp.ClientSession() as session:
            end_time = time.time() + duration_s
            while time.time() < end_time:
                i = random.randint(0, 19)
                pi = random.randint(0, 4)
                payload = {
                    "patient_deident_id": patient_ids[i],
                    "encounter_id": encounter_ids[i],
                    "parameter": params[pi],
                    "value": round(random.gauss(80 + pi * 10, 5), 2),
                    "unit": units[pi],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "device_id": f"sim-device-{i:02d}",
                    "source_system": "perf_test",
                }
                lat, status, err = await _post(session, f"{BASE_URL}/api/v1/vitals/ingest", payload)
                result.total_requests += 1
                result.latencies_ms.append(lat)
                if 200 <= status < 300:
                    result.successful += 1
                else:
                    result.failed += 1
                    if err: result.errors.append(err)

    n_workers = max(1, target_rps // 50)
    t0 = time.time()
    await asyncio.gather(*[burst_worker() for _ in range(n_workers)])
    result.duration_seconds = time.time() - t0
    return result


async def test_inference_latency(n_requests: int = 20, concurrency: int = 5) -> PerfResult:
    """AI inference latency test — target p95 < 10s."""
    import aiohttp
    result = PerfResult(scenario="AI Inference", total_requests=0, successful=0, failed=0)
    sem = asyncio.Semaphore(concurrency)

    async def run_one():
        async with sem:
            payload = {
                "patient_deident_id": str(uuid.uuid4()),
                "encounter_id": str(uuid.uuid4()),
                "include_imaging": False,
                "include_nlp": True,
                "include_vitals": True,
                "chief_complaint": "Progressive dyspnea and fever",
                "urgency": "routine",
            }
            async with aiohttp.ClientSession() as session:
                lat, status, err = await _post(session, f"{BASE_URL}/api/v1/inference/patient", payload)
            result.total_requests += 1
            result.latencies_ms.append(lat)
            if 200 <= status < 300:
                result.successful += 1
                logger.info(f"  Inference #{result.total_requests}: {lat:.0f}ms ✅")
            else:
                result.failed += 1
                logger.warning(f"  Inference #{result.total_requests}: {lat:.0f}ms ❌ status={status}")

    t0 = time.time()
    await asyncio.gather(*[run_one() for _ in range(n_requests)])
    result.duration_seconds = time.time() - t0
    return result


async def test_50k_patients() -> PerfResult:
    """Load 50,000 synthetic patient records and measure throughput."""
    import aiohttp
    result = PerfResult(scenario="50k Patient Load", total_requests=0, successful=0, failed=0)
    logger.info("Starting 50k patient load test (batches of 100)…")

    async def load_batch(session, batch_num: int):
        patients = [
            {
                "mrn": f"PERF-{batch_num:04d}-{i:03d}",
                "source_system": "perf_test",
                "last_name": f"LoadTest{batch_num}",
                "first_name": f"Patient{i}",
                "date_of_birth": f"{random.randint(1935, 1990)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
                "gender": random.choice(["M", "F"]),
            }
            for i in range(100)
        ]
        tasks = [
            _post(session, f"{BASE_URL}/api/v1/patients", p)
            for p in patients
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for lat, status, err in (r for r in results if isinstance(r, tuple)):
            result.total_requests += 1
            result.latencies_ms.append(lat)
            if 200 <= status < 300:
                result.successful += 1
            else:
                result.failed += 1

    t0 = time.time()
    async with aiohttp.ClientSession() as session:
        # 500 batches × 100 patients = 50,000
        batches = [load_batch(session, b) for b in range(500)]
        # Run 10 concurrent batches
        for i in range(0, len(batches), 10):
            chunk = batches[i:i + 10]
            await asyncio.gather(*chunk)
            if i % 50 == 0:
                elapsed = time.time() - t0
                rate = result.successful / elapsed if elapsed > 0 else 0
                logger.info(f"  Progress: {result.successful:,} patients loaded ({rate:.0f}/s)")

    result.duration_seconds = time.time() - t0
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["api", "vitals", "inference", "50k", "all"], default="api")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    print(f"\n🔬 CliniQAI Performance Test Suite")
    print(f"   Target: {BASE_URL}")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    results = []

    try:
        if args.scenario in ("api", "all"):
            logger.info("Running API load test…")
            r = await test_api_load(concurrency=args.concurrency, duration_s=args.duration)
            results.append(r)

        if args.scenario in ("vitals", "all"):
            logger.info("Running vitals ingest test…")
            r = await test_vitals_ingest()
            results.append(r)

        if args.scenario in ("inference", "all"):
            logger.info("Running AI inference latency test…")
            r = await test_inference_latency()
            results.append(r)

        if args.scenario == "50k":
            logger.info("Running 50k patient load test…")
            r = await test_50k_patients()
            results.append(r)

    except Exception as e:
        logger.error(f"Test failed: {e}")
        sys.exit(1)

    for r in results:
        r.print_report()

    all_pass = all(r._meets_targets() for r in results)
    print(f"\n{'='*60}")
    print(f"  {'✅ ALL TARGETS MET' if all_pass else '❌ SOME TARGETS MISSED'}")
    print(f"{'='*60}\n")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    try:
        import aiohttp
    except ImportError:
        print("Install aiohttp: pip install aiohttp")
        sys.exit(1)
    asyncio.run(main())
