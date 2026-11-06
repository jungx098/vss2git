"""Port of RevisionAnalyzer (without the work queue, logging or exclusions).

Walks a project tree and emits one Revision per revision record, ordered by
time. Order of the C# analyzer is preserved: items are visited as VssUtil.
RecurseItems does (a project, then its subprojects recursively, then its
files), revisions of an item are read in version order, and revisions are
bucketed by timestamp in a SortedDictionary, so revisions with the same
timestamp keep visiting order. A stable sort on the timestamp alone is the
same total order.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterator, List, Optional, Set

from vss2git.logical.database import Database, VssItem, VssProject
from vss2git.physical import revisions as rv
from vss2git.physical.items import BadHeaderError
from vss2git.physical.names import RecordNotFoundError, RecordTruncatedError
from vss2git.physical.record import CrcError

# Python equivalents of the C# RecordException hierarchy.
RECORD_ERRORS = (CrcError, RecordNotFoundError, RecordTruncatedError,
                 rv.UnrecognizedRecordError, BadHeaderError)


class VssActionType(Enum):
    LABEL = 0
    CREATE = 1
    DESTROY = 2
    ADD = 3
    DELETE = 4
    RECOVER = 5
    RENAME = 6
    MOVE_FROM = 7
    MOVE_TO = 8
    SHARE = 9
    PIN = 10
    BRANCH = 11
    EDIT = 12
    ARCHIVE = 13
    RESTORE = 14


@dataclass(frozen=True)
class ItemName:
    """Port of VssItemName."""

    logical_name: str
    physical_name: str
    is_project: bool


@dataclass(frozen=True)
class Action:
    """Port of the VssAction hierarchy, flattened.

    `target` is the VssNamedAction.Name (None for label and edit actions).
    `source` is VssBranchAction.Source. `version` is VssPinAction.Revision;
    the C# reader never creates pin actions, so it stays 0 here as well.
    """

    type: VssActionType
    target: Optional[ItemName] = None
    original_name: Optional[str] = None  # rename
    target_project: Optional[str] = None  # move from/to, share
    label_name: Optional[str] = None  # label
    version: int = 0  # pin
    source: Optional[ItemName] = None  # branch


@dataclass(frozen=True)
class Revision:
    when: datetime
    user: str
    physical: str  # physical name of the item the revision belongs to
    path: str  # logical path of that item, in the project it was found in
    action: Action
    comment: Optional[str]
    version: int


_SIMPLE_TYPES = {
    rv.Action.CREATE_PROJECT: VssActionType.CREATE,
    rv.Action.CREATE_FILE: VssActionType.CREATE,
    rv.Action.ADD_PROJECT: VssActionType.ADD,
    rv.Action.ADD_FILE: VssActionType.ADD,
    rv.Action.DELETE_PROJECT: VssActionType.DELETE,
    rv.Action.DELETE_FILE: VssActionType.DELETE,
    rv.Action.RECOVER_PROJECT: VssActionType.RECOVER,
    rv.Action.RECOVER_FILE: VssActionType.RECOVER,
    rv.Action.DESTROY_PROJECT: VssActionType.DESTROY,
    rv.Action.DESTROY_FILE: VssActionType.DESTROY,
    rv.Action.ARCHIVE_PROJECT: VssActionType.ARCHIVE,
    rv.Action.ARCHIVE_FILE: VssActionType.ARCHIVE,
    rv.Action.ARCHIVE_VERSIONS: VssActionType.ARCHIVE,
    rv.Action.ARCHIVE_ALL: VssActionType.ARCHIVE,
    rv.Action.RESTORE_VERSIONS: VssActionType.RESTORE,
    rv.Action.RESTORE_PROJECT: VssActionType.RESTORE,
    rv.Action.RESTORE_FILE: VssActionType.RESTORE,
}


def _item_name(db: Database, name, physical: str) -> ItemName:
    return ItemName(db.get_full_name(name), physical, name.is_project)


def _create_action(item: VssItem, record: rv.RevisionRecord) -> Action:
    """Port of VssRevision.CreateAction."""
    db = item.database
    kind = record.action
    if kind == rv.Action.LABEL:
        return Action(VssActionType.LABEL, label_name=record.label)
    if kind in (rv.Action.RENAME_PROJECT, rv.Action.RENAME_FILE):
        return Action(VssActionType.RENAME,
                      target=_item_name(db, record.name, record.physical),
                      original_name=db.get_full_name(record.old_name))
    if kind == rv.Action.MOVE_FROM:
        return Action(VssActionType.MOVE_FROM,
                      target=_item_name(db, record.name, record.physical),
                      target_project=record.project_path)
    if kind == rv.Action.MOVE_TO:
        return Action(VssActionType.MOVE_TO,
                      target=_item_name(db, record.name, record.physical),
                      target_project=record.project_path)
    if kind == rv.Action.SHARE_FILE:
        return Action(VssActionType.SHARE,
                      target=_item_name(db, record.name, record.physical),
                      target_project=record.project_path)
    if kind in (rv.Action.BRANCH_FILE, rv.Action.CREATE_BRANCH):
        name = db.get_full_name(record.name)
        is_project = record.name.is_project
        return Action(VssActionType.BRANCH,
                      target=ItemName(name, record.physical, is_project),
                      source=ItemName(name, record.branch_file, is_project))
    if kind == rv.Action.EDIT_FILE:
        return Action(VssActionType.EDIT)
    action_type = _SIMPLE_TYPES.get(kind)
    if action_type is None:
        raise ValueError(f"Unknown revision action: {kind}")
    return Action(action_type,
                  target=_item_name(db, record.name, record.physical))


def _read_comment(item: VssItem, record: rv.RevisionRecord) -> Optional[str]:
    """Port of VssItem.CreateRevision's comment lookup."""
    offset = 0
    if record.comment_length > 0 and record.comment_offset > 0:
        offset = record.comment_offset
    elif (record.action == rv.Action.LABEL
          and record.label_comment_length > 0
          and record.label_comment_offset > 0):
        offset = record.label_comment_offset
    if offset == 0:
        return None
    comment = rv.CommentRecord()
    item.item_file.read_record(comment, offset)
    return comment.comment


def _item_revisions(item: VssItem, path: str) -> Iterator[Revision]:
    """Revisions of one item in version order (VssItem.Revisions)."""
    item_file = item.item_file
    record = rv.get_first_revision(item_file)
    while record is not None:
        yield Revision(record.datetime, record.user, item.physical_name, path,
                       _create_action(item, record),
                       _read_comment(item, record), record.revision)
        record = rv.get_next_revision(item_file, record)


def _visit(project: VssProject, seen: Set[str], out: List[Revision],
           errors: Optional[List[str]]) -> None:
    """Port of VssUtil.RecurseItems over RevisionAnalyzer.ProcessItem."""
    children = list(project.children())
    _process(project, project.path, out, errors)
    for sub in children:
        if sub.is_project:
            _visit(sub, seen, out, errors)
    for file in children:
        # only process shared files once (projects are never shared)
        if not file.is_project and file.physical_name not in seen:
            seen.add(file.physical_name)
            _process(file, project.path + "/" + file.name, out, errors)


def _process(item: VssItem, path: str, out: List[Revision],
             errors: Optional[List[str]]) -> None:
    try:
        for revision in _item_revisions(item, path):
            out.append(revision)
    except RECORD_ERRORS as e:
        if errors is None:
            raise
        errors.append(f"Failed to read revisions for {path} "
                      f"({item.physical_name}): {e}")


def analyze(db: Database, project: str,
            errors: Optional[List[str]] = None) -> List[Revision]:
    """Revisions of `project` and everything below it, ordered by time.

    Like the C# analyzer, a record error in one item is reported (appended to
    `errors`) and the revisions read before it are kept; without `errors`
    the exception propagates instead.
    """
    root = db.item(project)
    if not isinstance(root, VssProject):
        raise ValueError(f"{project} is not a project")
    collected: List[Revision] = []
    _visit(root, set(), collected, errors)
    # stable: same-timestamp revisions keep visiting order, as the per-time
    # lists of the C# SortedDictionary do
    return sorted(collected, key=lambda rev: rev.when)
