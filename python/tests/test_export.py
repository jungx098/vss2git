import subprocess
from vss2git.migrate.export import migrate

def test_migrate_writes_commits(vss_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    git_dir = tmp_path / "out"
    commits = migrate(vss_db, git_dir, project="$", encoding="cp1252", email_domain="test.local")
    assert commits > 0
    log = subprocess.check_output(
        ["git", "log", "--reverse", "--format=%ae"], cwd=git_dir, text=True)
    assert log.strip()
    assert all(line.endswith("@test.local") for line in log.splitlines())
    files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=git_dir, text=True)
    assert files.strip()
