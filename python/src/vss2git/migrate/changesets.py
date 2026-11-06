"""Port of ChangesetBuilder (without the work queue or logging).

Groups the time-ordered revisions from analyze() into changesets, one open
changeset per user, using the flush conditions of the C# builder.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from vss2git.migrate.analyze import Revision, VssActionType


@dataclass
class Changeset:
    user: str
    when: datetime  # time of the last revision added (C# Changeset.DateTime)
    revisions: List[Revision] = field(default_factory=list)
    comment: Optional[str] = None
    target_files: Set[str] = field(default_factory=set)


def target_file(rev: Revision) -> str:
    """The C# conflict key: item physical name, plus the physical name of the
    named action target ("project/file") for project-level actions."""
    target = rev.action.target
    if target is not None:
        return rev.physical + "/" + target.physical_name
    return rev.physical


def _is_creating(rev: Revision) -> bool:
    kind = rev.action.type
    if kind == VssActionType.CREATE:
        return True
    # revision.Item.IsProject: a branch record names the item itself
    return (kind == VssActionType.BRANCH
            and not (rev.action.target and rev.action.target.is_project))


def _has_same_comment(rev1: Revision, rev2: Revision) -> bool:
    return bool(rev1.comment) and rev1.comment == rev2.comment


def _comment_contains_line(accumulated: str, new_comment: str) -> bool:
    return any(line.strip() == new_comment.strip()
               for line in accumulated.split("\n"))


def build_changesets(revisions: List[Revision], any_comment_seconds: float = 0,
                     same_comment_seconds: float = 60) -> List[Changeset]:
    """`revisions` must be in time order (as analyze() returns them)."""
    any_threshold = timedelta(seconds=any_comment_seconds)
    same_threshold = timedelta(seconds=same_comment_seconds)
    changesets: List[Changeset] = []
    pending: Dict[str, Changeset] = {}

    for rev in revisions:
        target = target_file(rev)
        creating = _is_creating(rev)
        # Share is never a conflict (Share always precedes Branch)
        nonconflicting = creating or rev.action.type == VssActionType.SHARE

        pending_change: Optional[Changeset] = None
        flushed: List[str] = []
        for user, change in pending.items():
            flush = False
            time_diff = rev.when - change.when
            if time_diff > any_threshold:
                if _has_same_comment(rev, change.revisions[-1]):
                    if time_diff >= same_threshold:
                        flush = True
                else:
                    flush = True
            elif not nonconflicting and target in change.target_files:
                flush = True

            if flush:
                changesets.append(change)
                flushed.append(user)
            elif user == rev.user:
                pending_change = change
        for user in flushed:
            del pending[user]

        if pending_change is None:
            pending_change = Changeset(rev.user, rev.when)
            pending[rev.user] = pending_change

        pending_change.when = rev.when
        pending_change.revisions.append(rev)
        if not nonconflicting:
            pending_change.target_files.add(target)

        comment = rev.comment.strip() if rev.comment is not None else ""
        if comment:
            if not pending_change.comment:
                pending_change.comment = comment
            elif not _comment_contains_line(pending_change.comment, comment):
                pending_change.comment += "\n" + comment

    changesets.extend(pending.values())
    return changesets
