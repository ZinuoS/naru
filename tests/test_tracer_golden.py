"""Pin tracer.py's transform output against a frozen golden.

This must stay green through the Phase 1 refactor that extracts these
transforms into src/naru/ops.py (see tracer.py Task 5) -- if it goes red,
the refactor changed behavior, not just location.
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tracer import FIXTURE_PATH, SHEET_NAME, read_raw_grid, transform  # noqa: E402

GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "tracer_expected.parquet"


def test_transform_matches_frozen_golden() -> None:
    """The tracer's transform chain, run against ust_lite.xlsx, must
    reproduce the frozen golden exactly -- same values, same dtypes.
    """
    actual = transform(read_raw_grid(FIXTURE_PATH, SHEET_NAME))
    expected = pd.read_parquet(GOLDEN_PATH)
    pd.testing.assert_frame_equal(actual, expected)
