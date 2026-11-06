"""Revision records (EL), branch records (BF) and the ItemFile revision walks.

Every revision variant of the C# ItemFile.CreateRevisionRecord is parsed
(common, destroy, rename, move, share, branch, edit, archive), plus comment
records (MC), which revision analysis reads.
"""

from typing import Optional

from vss2git.physical.delta import DeltaRecord
from vss2git.physical.items import ItemFile, VssName, _read_datetime, _read_name
from vss2git.physical.names import RecordFile
from vss2git.physical.reader import BufferReader
from vss2git.physical.record import RecordHeader

HEADER_LENGTH = 8  # RecordHeader.LENGTH in C#


class Action:
    LABEL = 0
    CREATE_PROJECT = 1
    ADD_PROJECT = 2
    ADD_FILE = 3
    DESTROY_PROJECT = 4
    DESTROY_FILE = 5
    DELETE_PROJECT = 6
    DELETE_FILE = 7
    RECOVER_PROJECT = 8
    RECOVER_FILE = 9
    RENAME_PROJECT = 10
    RENAME_FILE = 11
    MOVE_FROM = 12
    MOVE_TO = 13
    SHARE_FILE = 14
    BRANCH_FILE = 15
    CREATE_FILE = 16
    EDIT_FILE = 17
    ARCHIVE_FILE = 18
    CREATE_BRANCH = 19
    ARCHIVE_VERSIONS = 20
    ARCHIVE_ALL = 21
    RESTORE_VERSIONS = 22
    ARCHIVE_PROJECT = 23
    RESTORE_FILE = 24
    RESTORE_PROJECT = 25


class UnrecognizedRecordError(Exception):
    """Raised when an item file holds a record that cannot be handled."""


class RevisionRecord:
    """Record (EL) representing a project/file revision (common prefix)."""

    SIGNATURE = "EL"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.prev_rev_offset = 0
        self.action = 0
        self.revision = 0
        self.datetime = None
        self.user = ""
        self.label = ""
        self.comment_offset = 0  # or next revision if no comment
        self.label_comment_offset = 0  # or label comment
        self.comment_length = 0
        self.label_comment_length = 0

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    @staticmethod
    def peek_action(reader: BufferReader) -> int:
        save_offset = reader.offset
        try:
            reader.Skip(4)
            return reader.ReadInt16()
        finally:
            reader.offset = save_offset

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        self.prev_rev_offset = reader.ReadInt32()
        self.action = reader.ReadInt16()
        self.revision = reader.ReadInt16()
        self.datetime = _read_datetime(reader)
        self.user = reader.ReadString(32)
        self.label = reader.ReadString(32)
        self.comment_offset = reader.ReadInt32()
        self.label_comment_offset = reader.ReadInt32()
        self.comment_length = reader.ReadInt16()
        self.label_comment_length = reader.ReadInt16()


class EditRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.prev_delta_offset = 0
        self.project_path = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.prev_delta_offset = reader.ReadInt32()
        reader.Skip(4)  # reserved; always 0
        self.project_path = reader.ReadString(260)


class BranchRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.name: Optional[VssName] = None
        self.physical = ""
        self.branch_file = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.name = _read_name(reader)
        self.physical = reader.ReadString(10)
        self.branch_file = reader.ReadString(10)


class CommonRevisionRecord(RevisionRecord):
    """Create/add/delete/recover actions: item name plus physical name."""

    def __init__(self):
        super().__init__()
        self.name: Optional[VssName] = None
        self.physical = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.name = _read_name(reader)
        self.physical = reader.ReadString(10)


class DestroyRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.name: Optional[VssName] = None
        self.physical = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.name = _read_name(reader)
        reader.Skip(2)  # 0 or 1
        self.physical = reader.ReadString(10)


class RenameRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.name: Optional[VssName] = None
        self.old_name: Optional[VssName] = None
        self.physical = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.name = _read_name(reader)
        self.old_name = _read_name(reader)
        self.physical = reader.ReadString(10)


class MoveRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.project_path = ""
        self.name: Optional[VssName] = None
        self.physical = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.project_path = reader.ReadString(260)
        self.name = _read_name(reader)
        self.physical = reader.ReadString(10)


class ShareRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.project_path = ""
        self.name: Optional[VssName] = None
        self.unpinned_revision = 0  # -1: shared, 0: pinned; >0 unpinned version
        self.pinned_revision = 0  # >0: pinned version, ==0 unpinned
        self.physical = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.project_path = reader.ReadString(260)
        self.name = _read_name(reader)
        self.unpinned_revision = reader.ReadInt16()
        self.pinned_revision = reader.ReadInt16()
        reader.Skip(2)  # often seems to increment
        self.physical = reader.ReadString(10)


class ArchiveRevisionRecord(RevisionRecord):
    def __init__(self):
        super().__init__()
        self.name: Optional[VssName] = None
        self.physical = ""
        self.archive_path = ""

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.name = _read_name(reader)
        self.physical = reader.ReadString(10)
        reader.Skip(2)  # 0?
        self.archive_path = reader.ReadString(260)
        reader.Skip(4)  # ?


class CommentRecord:
    """Record (MC) holding a revision or label comment."""

    SIGNATURE = "MC"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.comment = ""

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        self.comment = reader.ReadString(reader.Remaining)


class BranchRecord:
    """Record (BF) listing a file this file was branched into."""

    SIGNATURE = "BF"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.prev_branch_offset = 0
        self.branch_file = ""

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        self.prev_branch_offset = reader.ReadInt32()
        self.branch_file = reader.ReadString(12)


_ACTION_RECORDS = {
    Action.CREATE_PROJECT: CommonRevisionRecord,
    Action.CREATE_FILE: CommonRevisionRecord,
    Action.ADD_PROJECT: CommonRevisionRecord,
    Action.ADD_FILE: CommonRevisionRecord,
    Action.DELETE_PROJECT: CommonRevisionRecord,
    Action.DELETE_FILE: CommonRevisionRecord,
    Action.RECOVER_PROJECT: CommonRevisionRecord,
    Action.RECOVER_FILE: CommonRevisionRecord,
    Action.DESTROY_PROJECT: DestroyRevisionRecord,
    Action.DESTROY_FILE: DestroyRevisionRecord,
    Action.RENAME_PROJECT: RenameRevisionRecord,
    Action.RENAME_FILE: RenameRevisionRecord,
    Action.MOVE_FROM: MoveRevisionRecord,
    Action.MOVE_TO: MoveRevisionRecord,
    Action.SHARE_FILE: ShareRevisionRecord,
    Action.ARCHIVE_FILE: ArchiveRevisionRecord,
    Action.ARCHIVE_VERSIONS: ArchiveRevisionRecord,
    Action.ARCHIVE_ALL: ArchiveRevisionRecord,
    Action.ARCHIVE_PROJECT: ArchiveRevisionRecord,
    Action.RESTORE_VERSIONS: ArchiveRevisionRecord,
    Action.RESTORE_PROJECT: ArchiveRevisionRecord,
    Action.RESTORE_FILE: ArchiveRevisionRecord,
}


def _create_revision_record(body: BufferReader) -> RevisionRecord:
    action = RevisionRecord.peek_action(body)
    if action == Action.EDIT_FILE:
        return EditRevisionRecord()
    if action in (Action.BRANCH_FILE, Action.CREATE_BRANCH):
        return BranchRevisionRecord()
    if action == Action.LABEL:
        return RevisionRecord()
    # like the C# switch, unknown actions fall back to the common layout
    return _ACTION_RECORDS.get(action, CommonRevisionRecord)()


def _get_revision(item_file: RecordFile, ignore_unknown: bool,
                  offset: Optional[int] = None) -> Optional[RevisionRecord]:
    """Port of GetRecord<RevisionRecord>(CreateRevisionRecord, ...)."""
    if offset is not None:
        item_file.reader.offset = offset
    # comment records skip the CRC check; every other record is verified
    header, body = item_file._read_header_and_body()
    if header.signature != RevisionRecord.SIGNATURE:
        if ignore_unknown:
            return None
        raise UnrecognizedRecordError(
            f"Unrecognized record signature {header.signature} in item file "
            f"at offset {header.offset}")
    record = _create_revision_record(body)
    record.read(body, header)
    return record


def get_first_revision(item_file: ItemFile) -> Optional[RevisionRecord]:
    if item_file.header.first_rev_offset > 0:
        return _get_revision(item_file, False, item_file.header.first_rev_offset)
    return None


def get_next_revision(item_file: ItemFile,
                      revision: RevisionRecord) -> Optional[RevisionRecord]:
    if item_file.reader.offset == item_file.header.eof_offset:
        return None
    item_file.reader.offset = (revision.header.offset
                               + revision.header.length + HEADER_LENGTH)
    while item_file.reader.Remaining > HEADER_LENGTH:
        record = _get_revision(item_file, True)
        if record is not None:
            return record
    return None


def get_last_revision(item_file: ItemFile) -> Optional[RevisionRecord]:
    if item_file.header.last_rev_offset > 0:
        return _get_revision(item_file, False, item_file.header.last_rev_offset)
    return None


def get_previous_revision(item_file: ItemFile,
                          revision: RevisionRecord) -> Optional[RevisionRecord]:
    if revision.prev_rev_offset > 0:
        return _get_revision(item_file, False, revision.prev_rev_offset)
    return None


def get_previous_delta(item_file: ItemFile,
                       revision: EditRevisionRecord) -> Optional[DeltaRecord]:
    if revision.prev_delta_offset > 0:
        record = DeltaRecord()
        item_file.read_record(record, revision.prev_delta_offset)
        return record
    return None
