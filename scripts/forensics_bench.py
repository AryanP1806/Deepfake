#!/usr/bin/env python3
"""Measure the forensic modules against ground truth and write backend/dfir/forensics/validation_results.json.

  cd backend && uv run python ../scripts/forensics_bench.py --real-dir ../models/calibration_data/test
  cd backend && uv run python ../scripts/forensics_bench.py --quick --out /tmp/quick.json     # seconds, for a smoke test
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dfir.forensics.bench.run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
