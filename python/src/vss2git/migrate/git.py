"""Thin wrapper around the git executable (port of GitWrapper).

Every command runs through subprocess.run with an argument list (no shell)
and the repository as the working directory. Author and committer identity
and dates are passed in the environment, so no git configuration is read or
written other than what `init` sets in the new repository.
"""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional


class GitError(Exception):
    """A git command exited with a non-zero status."""

    def __init__(self, args: List[str], returncode: int, stdout: str, stderr: str):
        super().__init__(
            f"git {' '.join(args)} returned exit code {returncode}: "
            f"{(stderr or stdout).strip()}")
        self.args_list = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def utc_time_string(local_time: datetime) -> str:
    """Local wall-clock time as ISO 8601 UTC (GitWrapper.GetUtcTimeString).

    VSS stores naive local times; astimezone() applies the local zone (so
    the TZ environment variable) as TimeZoneInfo.ConvertTimeToUtc does.
    """
    return local_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")


class GitRepo:
    def __init__(self, path):
        self.path = os.fspath(path)
        executable = shutil.which("git")
        if executable is None:
            raise FileNotFoundError("Git executable not found in PATH.")
        self.executable = executable

    def run(self, *args: str, input: Optional[str] = None,
            env: Optional[Dict[str, str]] = None, check: bool = True
            ) -> subprocess.CompletedProcess:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        result = subprocess.run(
            [self.executable, *args], cwd=self.path, env=full_env,
            input=input.encode("utf-8") if input is not None else None,
            stdin=None if input is not None else subprocess.DEVNULL,
            capture_output=True)
        stdout = result.stdout.decode("utf-8", "replace")
        stderr = result.stderr.decode("utf-8", "replace")
        if check and result.returncode != 0:
            raise GitError(list(args), result.returncode, stdout, stderr)
        result.stdout, result.stderr = stdout, stderr
        return result

    def _rel(self, path: str) -> str:
        """Repository-relative form of a path inside the repository."""
        return os.path.relpath(path, self.path)

    def init(self) -> None:
        self.run("init", "-q")
        self.run("config", "core.autocrlf", "false")

    def add_all(self) -> None:
        # -f: ignore rules (global excludes, .gitignore files stored in the
        # VSS tree) must not drop files from history
        self.run("add", "-A", "-f")

    def remove(self, path: str, recursive: bool) -> None:
        """git rm, falling back to deleting untracked paths directly."""
        result = self.run("rm", "-rf" if recursive else "-f", "--",
                          self._rel(path), check=False)
        if result.returncode != 0:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.remove(path)

    def move(self, source: str, dest: str) -> None:
        """git mv, falling back to a file system move for untracked paths.

        The next `add -A` picks up whatever the fallback moved.
        """
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        result = self.run("mv", "--", self._rel(source), self._rel(dest),
                          check=False)
        if result.returncode != 0:
            shutil.move(source, dest)

    def commit(self, name: str, email: str, comment: str,
               local_time: datetime) -> bool:
        """Commit the index; False when there was nothing to commit."""
        # ignore empty commits, since they are non-trivial to detect
        # (e.g. when renaming a directory)
        if self.run("diff", "--cached", "--quiet", "--exit-code",
                    check=False).returncode == 0:
            return False
        when = utc_time_string(local_time)
        self.run("-c", "commit.gpgSign=false", "commit", "--no-verify",
                 "--allow-empty-message", "--no-edit", "-F", "-",
                 input=comment,
                 env={"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                      "GIT_AUTHOR_DATE": when,
                      "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
                      "GIT_COMMITTER_DATE": when})
        return True

    def tag(self, name: str, tagger_name: str, tagger_email: str,
            comment: str, local_time: datetime) -> None:
        """Annotated tag on HEAD (an annotated tag needs a message)."""
        self.run("-c", "tag.gpgSign=false", "tag", "-a", "-F", "-", "--", name,
                 input=comment,
                 env={"GIT_COMMITTER_NAME": tagger_name,
                      "GIT_COMMITTER_EMAIL": tagger_email,
                      "GIT_COMMITTER_DATE": utc_time_string(local_time)})


def manifest(git_dir) -> str:
    """History manifest for comparison with the .NET Process backend.

    The caller sets TZ=UTC. Output uses \\n line endings only.
    """
    repo = GitRepo(git_dir)
    log = repo.run("log", "--reverse",
                   "--format=tree %T%nauthor %an <%ae> %at%n%B%n---").stdout
    tags = repo.run("for-each-ref", "refs/tags", "--sort=refname",
                    "--format=tag %(refname:short) %(*tree)%(tree)").stdout
    return (log + tags).replace("\r\n", "\n")
