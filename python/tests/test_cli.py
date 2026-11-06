import subprocess
import sys

import pytest

def test_migrate_help():
    proc = subprocess.run([sys.executable, "-m", "vss2git", "migrate", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--vss-dir" in proc.stdout
    assert "--encoding" in proc.stdout

def test_migrate_on_copy(vss_db, tmp_path):
    git_dir = tmp_path / "repo"
    proc = subprocess.run(
        [sys.executable, "-m", "vss2git", "migrate",
         "--vss-dir", str(vss_db), "--git-dir", str(git_dir),
         "--encoding", "1252", "--email-domain", "test.local"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (git_dir / ".git").is_dir()


def test_migrate_bad_log_path(vss_db, tmp_path, capsys):
    from vss2git.cli import main

    log_path = tmp_path / "missing" / "vss.log"
    with pytest.raises(SystemExit) as exc:
        main(["migrate",
              "--vss-dir", str(vss_db),
              "--git-dir", str(tmp_path / "repo"),
              "--log", str(log_path),
              "--email-domain", "test.local"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.startswith("ERROR:")
    assert str(log_path) in err
