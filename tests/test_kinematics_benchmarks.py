from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class KinematicsBenchmarkContractTests(unittest.TestCase):
    def test_benchmarks_emit_strict_json_and_report_required_evidence(self) -> None:
        cases = (
            ("benchmark_kinematics_series.py", ["--samples", "1000", "--iterations", "1"]),
            (
                "benchmark_kinematics_derivatives.py",
                ["--samples", "1000", "--iterations", "1"],
            ),
            (
                "benchmark_kinematics_fits.py",
                [
                    "--samples",
                    "1000",
                    "--iterations",
                    "1",
                    "--sinusoid-guess-samples",
                    "2000",
                ],
            ),
            ("benchmark_kinematics_100k.py", ["--samples", "1000", "--iterations", "1"]),
        )
        required = {
            "workload",
            "sample_count",
            "method",
            "timing_ms",
            "peak_rss_mb",
            "result_digest",
            "accuracy_error",
            "cancellation_latency_ms",
            "exit_code",
        }
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for script, arguments in cases:
            with self.subTest(script=script):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "benchmarks" / script),
                        *arguments,
                        "--enforce",
                    ],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
                payload = json.loads(completed.stdout)
                self.assertTrue(required.issubset(payload))
                self.assertEqual(payload["schema"], "neo-tracker.kinematics-benchmark/v1")
                self.assertEqual(payload["exit_code"], 0)
                self.assertTrue(payload["passed"])
                self.assertEqual(len(payload["result_digest"]), 64)
                self.assertIsInstance(payload["timing_ms"]["median"], float)
                self.assertIsInstance(payload["timing_ms"]["p95"], float)
                self.assertIsInstance(payload["timing_ms"]["max"], float)
                if script == "benchmark_kinematics_100k.py":
                    self.assertIn("hot", payload["timing_scope"])
                    self.assertIn("excludes", payload["timing_scope"])
                    self.assertIn("snapshot", payload["timing_scope"])
                    self.assertIn("exports", payload["timing_scope"])


if __name__ == "__main__":
    unittest.main()
