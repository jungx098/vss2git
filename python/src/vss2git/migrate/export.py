"""Port of GitExporter: replay changesets into a new git repository.

Revisions of each changeset are replayed against a working tree, in the
order of the C# exporter, and the tree is committed once per changeset.
Not ported: --from-date/--to-date, exclusions, GC scheduling and the other
git backends.
"""

import logging
import os
import re
import shutil
import time
from typing import Callable, List, Optional, Set

from vss2git.logical.database import Database
from vss2git.migrate.analyze import (
    ItemName, Revision, VssActionType, analyze)
from vss2git.migrate.changesets import Changeset, build_changesets
from vss2git.migrate.git import GitRepo
from vss2git.migrate.mapper import (
    FileInfo, PathMapper, ProjectInfo, get_working_path)

log = logging.getLogger(__name__)

T = VssActionType

# Causal ordering of action types within a changeset (GetActionPriority).
_PRIORITY = {
    T.CREATE: 0, T.LABEL: 1,
    T.ADD: 2, T.SHARE: 2, T.RECOVER: 2, T.RESTORE: 2,
    T.MOVE_FROM: 3, T.BRANCH: 4, T.PIN: 5, T.EDIT: 6, T.RENAME: 7,
    T.ARCHIVE: 8, T.MOVE_TO: 9, T.DELETE: 10, T.DESTROY: 11,
}


class MigrateError(Exception):
    pass


def _is_inside(path: str, directory: str) -> bool:
    return os.path.commonpath([path, directory]) == directory


def get_email(user: str, domain: str) -> str:
    # GitExporter.GetEmail: ToLower().Replace(' ', '.')
    return user.lower().replace(" ", ".") + "@" + domain


class Exporter:
    def __init__(self, db: Database, repo_path: str, git: GitRepo,
                 revisions: List[Revision], project_physical_names: Set[str],
                 email_domain: str, default_comment: str,
                 ignore_errors: bool):
        self.db = db
        self.root = repo_path
        self.git = git
        self.email_domain = email_domain
        self.default_comment = default_comment
        self.ignore_errors = ignore_errors
        self.mapper = PathMapper()
        self.projects = project_physical_names
        self.tags_used: Set[str] = set()
        self.errors: List[str] = []
        # physical names of items destroyed in a project (DESTROY revisions)
        self.destroyed = {
            r.action.target.physical_name for r in revisions
            if r.action.type == T.DESTROY and r.action.target is not None}

    # -- file system helpers ------------------------------------------------

    def _on_disk(self, path: str) -> str:
        """`path` with each existing component spelled as it is on disk."""
        if path != self.root and not path.startswith(self.root + os.sep):
            return path
        current = self.root
        parts = path[len(self.root):].split(os.sep)
        for index, part in enumerate(p for p in parts if p):
            try:
                names = os.listdir(current)
            except OSError:
                names = []
            if part in names:
                actual = part
            else:
                actual = next((n for n in sorted(names)
                               if n.casefold() == part.casefold()), None)
            if actual is None:
                rest = [p for p in parts if p][index:]
                return os.path.join(current, *rest)
            current = os.path.join(current, actual)
        return current

    def _dest(self, path: str) -> str:
        """Like _on_disk, but keeps the spelling of the last component."""
        directory, name = os.path.split(path)
        return os.path.join(self._on_disk(directory), name)

    def _makedirs(self, path: str) -> str:
        path = self._on_disk(path)
        os.makedirs(path, exist_ok=True)
        return path

    def _case_rename(self, source: str, dest: str,
                     renamer: Callable[[str, str], None]) -> None:
        """Port of CaseSensitiveRename (two-step rename for case-only changes)."""
        if source.casefold() == dest.casefold():
            source_dir, source_file = os.path.split(source)
            dest_dir, dest_file = os.path.split(dest)
            if source_dir != dest_dir:
                # recursively rename containing directories that differ in case
                self._case_rename(source_dir, dest_dir, renamer)
                source = os.path.join(dest_dir, source_file)
            if source_file != dest_file:
                temp = source + ".mvtmp"
                self._case_rename(source, temp, renamer)
                self._case_rename(temp, dest, renamer)
        else:
            renamer(source, dest)

    @staticmethod
    def _fs_move(source: str, dest: str) -> None:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(source, dest)

    # -- replay ---------------------------------------------------------------

    def replay_changeset(self, changeset: Changeset,
                         labels: List[Revision]) -> bool:
        need_commit = False
        # time, then causal priority of the action (both stable)
        for rev in sorted(changeset.revisions,
                          key=lambda r: (r.when, _PRIORITY.get(r.action.type, 6))):
            try:
                need_commit |= self.replay_revision(rev, labels)
            except Exception as e:
                message = (f"{rev.when} {rev.action.type.name} "
                           f"{rev.path}: {type(e).__name__}: {e}")
                if not self.ignore_errors:
                    raise MigrateError(message) from e
                log.error(message)
                self.errors.append(message)
        return need_commit

    def _item_name(self, rev: Revision) -> ItemName:
        return ItemName(rev.path.rsplit("/", 1)[-1], rev.physical,
                        rev.physical in self.projects)

    def replay_revision(self, rev: Revision, labels: List[Revision]) -> bool:
        kind = rev.action.type
        item = self._item_name(rev)
        if item.is_project:
            return self._replay_project_revision(rev, item, labels)
        # item is a file, not a project
        if kind in (T.EDIT, T.BRANCH):
            # if the action is Branch, this is necessary only if the item was
            # branched from a file outside the migration subset; it makes sure
            # we start with the correct revision instead of the first one
            self.mapper.set_file_version(item, rev.version)
            # write current rev to all sharing projects
            return self._write_revision(kind, item.physical_name,
                                        rev.version, None)
        return False

    def _replay_project_revision(self, rev: Revision, project: ItemName,
                                 labels: List[Revision]) -> bool:
        mapper = self.mapper
        action = rev.action
        kind = action.type
        # project path (and therefore target path) can be None if a project
        # was moved and its original location was subsequently destroyed
        project_path = mapper.get_project_path(project.physical_name)
        desc = project_path
        if project_path is None:
            desc = f"{rev.path} ({project.physical_name})"
            log.info("NOTE: %s is currently unmapped", desc)

        target = action.target
        target_path: Optional[str] = None
        if target is not None and project_path is not None:
            target_path = os.path.join(project_path, target.logical_name)

        need_commit = False
        is_add = False
        write_project = False
        write_file = False
        item_info = None

        if kind == T.LABEL:
            # defer tagging until after commit
            labels.append(rev)

        elif kind == T.CREATE:
            pass  # items are actually created when added to a project

        elif kind in (T.ADD, T.SHARE):
            log.info("%s: %s %s", desc, kind.name, target.logical_name)
            item_info = mapper.add_item(project, target)
            is_add = True

        elif kind == T.RECOVER:
            log.info("%s: %s %s", desc, kind.name, target.logical_name)
            item_info = mapper.recover_item(project, target)
            is_add = True

        elif kind in (T.DELETE, T.DESTROY):
            log.info("%s: %s %s", desc, kind.name, target.logical_name)
            item_info = mapper.delete_item(project, target)
            if target_path is not None and not item_info.destroyed:
                need_commit |= self._delete_target(
                    project, target, target_path, item_info, desc)

        elif kind == T.RENAME:
            log.info("%s: %s %s to %s", desc, kind.name, action.original_name,
                     target.logical_name)
            item_info = mapper.rename_item(target)
            if target_path is not None and not item_info.destroyed:
                need_commit |= self._rename_target(
                    project_path, target, target_path, item_info,
                    action.original_name)

        elif kind == T.MOVE_FROM:
            # if both MoveFrom & MoveTo are present (e.g. one of them has not
            # been destroyed), only one can succeed, so check the source exists
            log.info("%s: Move from %s to %s", desc, action.target_project,
                     target_path or target.logical_name)
            source_path = mapper.get_project_path(target.physical_name)
            project_info = mapper.move_project_from(
                project, target, action.target_project)
            if target_path is not None and not project_info.destroyed:
                moved, write_project = self._move_target(
                    source_path, target_path, project_info)
                need_commit |= moved

        elif kind == T.MOVE_TO:
            # actual moves are handled in MoveFrom; this only cleans up
            # destroyed projects
            log.info("%s: Move to %s from %s", desc, action.target_project,
                     target_path or target.logical_name)
            project_info = mapper.move_project_to(
                project, target, action.target_project)
            if project_info.destroyed and target_path is not None:
                existing = self._on_disk(target_path)
                if os.path.isdir(existing):
                    # moved to a now-destroyed project; remove empty directory
                    shutil.rmtree(existing)

        elif kind == T.PIN:
            if action.version != 0:
                log.info("%s: Pin %s", desc, target.logical_name)
                item_info = mapper.pin_item(project, target)
            else:
                log.info("%s: Unpin %s", desc, target.logical_name)
                item_info = mapper.unpin_item(project, target)
                write_file = not item_info.destroyed

        elif kind == T.BRANCH:
            log.info("%s: %s %s", desc, kind.name, target.logical_name)
            item_info = mapper.branch_file(project, target, action.source)
            # branching within the project might happen after the file branch
            write_file = True

        elif kind == T.ARCHIVE:
            log.info("%s: Archive %s (ignored)", desc,
                     target.logical_name if target else "")

        elif kind == T.RESTORE:
            log.info("%s: Restore %s from archive", desc, target.logical_name)
            item_info = mapper.add_item(project, target)
            is_add = True

        if target_path is not None:
            if is_add:
                if (target.physical_name in self.destroyed
                        and not self._item_exists(target.physical_name)):
                    log.info("NOTE: Skipping destroyed file: %s", target_path)
                    item_info.destroyed = True
                elif target.is_project:
                    self._makedirs(target_path)
                    write_project = True
                else:
                    write_file = True

            if write_project and mapper.is_project_rooted(target.physical_name):
                # create all contained subdirectories
                for info in mapper.get_all_projects(target.physical_name):
                    sub_path = info.get_path()
                    log.info("%s: Creating subdirectory %s", desc,
                             info.logical_name)
                    self._makedirs(sub_path)

                # write current rev of all contained files
                for file_info in list(mapper.get_all_files(target.physical_name)):
                    if self._write_revision(kind, file_info.physical_name,
                                            file_info.version,
                                            target.physical_name):
                        need_commit = True
            elif write_file:
                # write current rev to working path
                version = mapper.get_file_version(target.physical_name)
                if self._write_revision_to(target.physical_name, version,
                                           target_path):
                    need_commit = True
        return need_commit

    # -- individual actions ---------------------------------------------------

    def _item_exists(self, physical_name: str) -> bool:
        return os.path.isfile(
            self.db.resolve(self.db.get_data_path(physical_name)))

    def _delete_target(self, project: ItemName, target: ItemName,
                       target_path: str, item_info, desc: str) -> bool:
        path = self._on_disk(target_path)
        if target.is_project:
            if os.path.isdir(path):
                assert isinstance(item_info, ProjectInfo)
                if item_info.contains_files():
                    self.git.remove(path, True)
                    return True
                # git doesn't care about directories with no files
                shutil.rmtree(path)
            return False
        if os.path.isfile(path):
            # a project can evidently contain another file with the same
            # logical name, so check that this is not the case before
            # deleting the file
            if self.mapper.project_contains_logical_name(project, target):
                log.info("NOTE: %s contains another file named %s; "
                         "not deleting file", desc, target.logical_name)
                return False
            os.remove(path)
            return True
        return False

    def _rename_target(self, project_path: str, target: ItemName,
                       target_path: str, item_info, original_name: str) -> bool:
        need_commit = False
        source_path = self._on_disk(os.path.join(project_path, original_name))
        dest_path = self._dest(target_path)
        exists = os.path.isdir if target.is_project else os.path.isfile
        # clean up destination if it already exists (e.g. files were
        # committed under the new name via Share before the rename)
        if (exists(source_path) and exists(dest_path)
                and source_path.casefold() != dest_path.casefold()):
            log.info("NOTE: Rename destination already exists, removing: %s",
                     dest_path)
            self.git.remove(dest_path, target.is_project)
            need_commit = True
        if exists(source_path):
            # renaming a file or a project that contains files?
            if (not isinstance(item_info, ProjectInfo)
                    or item_info.contains_files()):
                self._case_rename(source_path, dest_path, self.git.move)
                need_commit = True
            else:
                # git doesn't care about directories with no files
                self._case_rename(source_path, dest_path, self._fs_move)
        else:
            log.info("NOTE: Skipping rename because %s does not exist",
                     source_path)
        return need_commit

    def _move_target(self, source_path: Optional[str], target_path: str,
                     project_info: ProjectInfo):
        """MoveFrom; returns (need_commit, write_project)."""
        need_commit = False
        source = self._on_disk(source_path) if source_path is not None else None
        dest = self._dest(target_path)
        # clean up destination if it already exists to prevent stale files
        # (when the mapper resolves a different name than the one on disk the
        # source check below fails and the project is re-created from scratch)
        if (os.path.isdir(dest)
                and (source is None or source.casefold() != dest.casefold())):
            log.info("NOTE: MoveFrom destination already exists, removing: %s",
                     dest)
            self.git.remove(dest, True)
            need_commit = True

        if source is not None and os.path.isdir(source):
            if project_info.contains_files():
                self._case_rename(source, dest, self.git.move)
                need_commit = True
            else:
                # git doesn't care about directories with no files
                self._case_rename(source, dest, self._fs_move)
            return need_commit, False
        # project was moved from a now-destroyed project
        return need_commit, True

    # -- writing file contents -------------------------------------------------

    def _write_revision(self, kind: VssActionType, physical: str, version: int,
                        under_project: Optional[str]) -> bool:
        need_commit = False
        for path in self.mapper.get_file_paths(physical, under_project):
            log.info("%s: %s revision %s", path, kind.name, version)
            if self._write_revision_to(physical, version, path):
                need_commit = True
        return need_commit

    def _write_revision_to(self, physical: str, version: int,
                           dest_path: str) -> bool:
        try:
            data = self.db.item_physical(physical).contents(version)
        except Exception as e:
            # log an error for missing data files or versions, but keep going
            message = (f"ERROR: cannot read {physical} version {version}: "
                       f"{type(e).__name__}: {e}")
            log.error(message)
            self.errors.append(message)
            return False
        dest_path = self._on_disk(dest_path)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as stream:
            stream.write(data)
        return True

    # -- commits and tags -----------------------------------------------------

    def commit_changeset(self, changeset: Changeset) -> bool:
        # C# stages only the changed paths (`git add -- paths`); `add -A` is
        # equivalent here and does not fail on paths that no longer exist
        self.git.add_all()
        return self.git.commit(
            changeset.user, get_email(changeset.user, self.email_domain),
            changeset.comment or self.default_comment, changeset.when)

    def tag_name(self, label: str) -> str:
        # git tag names must be valid filenames, so replace sequences of
        # invalid characters with an underscore
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", label)
        # git tags are global, whereas VSS tags are local, so ensure global
        # uniqueness by appending a number; ignore case, since the file system
        # may be case-insensitive
        tag = base
        i = 2
        while tag.upper() in self.tags_used:
            tag = f"{base}-{i}"
            i += 1
        self.tags_used.add(tag.upper())
        return tag

    def create_tags(self, labels: List[Revision], commit_count: int) -> int:
        count = 0
        for label in labels:
            label_name = label.action.label_name
            if not label_name:
                log.info("NOTE: Ignoring empty label")
            elif commit_count == 0:
                log.info("NOTE: Ignoring label '%s' before initial commit",
                         label_name)
            else:
                tag = self.tag_name(label_name)
                # annotated tags need a message; use the label if none given
                self.git.tag(tag, label.user,
                             get_email(label.user, self.email_domain),
                             label.comment or label_name, label.when)
                count += 1
        return count

    def remove_empty_directories(self) -> int:
        removed = 0
        git_dir = os.path.join(self.root, ".git")
        for directory, _dirs, _files in os.walk(self.root, topdown=False):
            if directory == self.root or directory == git_dir \
                    or directory.startswith(git_dir + os.sep):
                continue
            if not os.listdir(directory):
                os.rmdir(directory)
                removed += 1
        return removed


def _check_directories(vss_db: str, git_dir: str) -> None:
    if _is_inside(git_dir, vss_db) or _is_inside(vss_db, git_dir):
        raise MigrateError(
            f"git directory {git_dir} and VSS database {vss_db} "
            "must not contain each other")
    if os.path.lexists(git_dir):
        if not os.path.isdir(git_dir):
            raise MigrateError(f"{git_dir} is not a directory")
        if os.listdir(git_dir):
            raise MigrateError(f"git directory {git_dir} is not empty")


def migrate(vss_db, git_dir, project: str = "$", encoding: str = "cp1252",
            email_domain: str = "localhost", any_comment_seconds: float = 0,
            same_comment_seconds: float = 60, default_comment: str = "",
            ignore_errors: bool = False) -> int:
    """Replay the history of `project` into a new repository at `git_dir`.

    Returns the number of commits written.
    """
    vss_path = os.path.realpath(os.fspath(vss_db))
    repo_path = os.path.realpath(os.fspath(git_dir))
    _check_directories(vss_path, repo_path)

    # VSS times are local wall-clock times; make sure the zone is current
    if hasattr(time, "tzset"):
        time.tzset()

    db = Database(vss_path, encoding)
    root = db.item(project)
    project_names = {item.physical_name for item in root.walk()
                     if item.is_project}

    revisions = analyze(db, project)
    changesets = build_changesets(revisions, any_comment_seconds,
                                  same_comment_seconds)

    os.makedirs(repo_path, exist_ok=True)
    git = GitRepo(repo_path)
    git.init()

    exporter = Exporter(db, repo_path, git, revisions, project_names,
                        email_domain, default_comment, ignore_errors)
    # create mappings for the root project
    exporter.mapper.set_project_path(
        root.physical_name, get_working_path(repo_path, root.path), root.path)

    commit_count = 0
    for changeset in changesets:
        labels: List[Revision] = []
        need_commit = exporter.replay_changeset(changeset, labels)
        if need_commit and exporter.commit_changeset(changeset):
            commit_count += 1
        exporter.create_tags(labels, commit_count)

    exporter.remove_empty_directories()
    log.info("Git commits: %d", commit_count)
    return commit_count
