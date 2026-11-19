"""The UI's status list must match the backend enum, or the status select shows the wrong state."""
import re
from pathlib import Path

import pytest

from app.models.case import CaseStatus

STATUSES_TS = Path(__file__).resolve().parents[2] / "frontend/src/features/cases/statuses.ts"


@pytest.mark.skipif(not STATUSES_TS.exists(), reason="frontend sources not present")
def test_frontend_case_statuses_match_enum():
    ui = re.findall(r"value: '([a-z_]+)'", STATUSES_TS.read_text())
    assert ui == [s.value for s in CaseStatus]
