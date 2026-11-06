import os
import shutil
from pathlib import Path

import pytest

_ENV = os.environ.get("VSS2GIT_TEST_DB")
ORIGINAL = Path(_ENV) if _ENV else None


@pytest.fixture
def vss_db(tmp_path: Path) -> Path:
    if ORIGINAL is None:
        pytest.skip("set VSS2GIT_TEST_DB to a VSS database to test against")
    assert (ORIGINAL / "srcsafe.ini").is_file()
    before = (ORIGINAL / "srcsafe.ini").stat().st_mtime_ns
    dest = tmp_path / "ss"
    shutil.copytree(ORIGINAL, dest)
    assert (ORIGINAL / "srcsafe.ini").stat().st_mtime_ns == before
    return dest
