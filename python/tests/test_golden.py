import os

import pytest

from vss2git.migrate.export import migrate
from vss2git.migrate.git import manifest


@pytest.mark.skipif(not os.environ.get("VSS2GIT_GOLDEN"),
                    reason="set VSS2GIT_GOLDEN to the Windows Process manifest")
def test_matches_dotnet_process_backend(vss_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    git_dir = tmp_path / "out"
    migrate(vss_db, git_dir, project="$", encoding="cp1252",
            email_domain="test.local")
    with open(os.environ["VSS2GIT_GOLDEN"], encoding="utf-8") as f:
        expected = f.read().replace("\r\n", "\n")
    assert manifest(git_dir) == expected
