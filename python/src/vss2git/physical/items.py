"""VSS item files (projects and files): file preamble and header record.

Only the header record is parsed; revision replay is not ported here.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

from vss2git.physical.names import RecordFile
from vss2git.physical.reader import BufferReader, EndOfBuffer
from vss2git.physical.record import RecordHeader

FILE_SIGNATURE = "SourceSafe@Microsoft"
_EPOCH = datetime(1970, 1, 1)


class ItemType:
    PROJECT = 1
    FILE = 2


class BadHeaderError(Exception):
    """Raised when an item file's preamble or header record is invalid."""


@dataclass
class VssName:
    flags: int
    short_name: str
    name_file_offset: int

    @property
    def is_project(self) -> bool:
        return (self.flags & 1) != 0


def _read_name(reader: BufferReader) -> VssName:
    reader.CheckRead(2 + 34 + 4)
    return VssName(reader.ReadInt16(), reader.ReadString(34), reader.ReadInt32())


def _read_datetime(reader: BufferReader) -> datetime:
    # C# adds the seconds to a local-kind epoch, i.e. no zone conversion.
    return _EPOCH + timedelta(seconds=reader.ReadInt32())


class ItemHeaderRecord:
    """Base item header record (DH)."""

    SIGNATURE = "DH"

    def __init__(self, item_type: int):
        self.header: Optional[RecordHeader] = None
        self.item_type = item_type
        self.revisions = 0
        self.name: Optional[VssName] = None
        self.first_revision = 0
        self.data_ext = ""
        self.first_rev_offset = 0
        self.last_rev_offset = 0
        self.eof_offset = 0
        self.rights_offset = 0

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        self.item_type = reader.ReadInt16()
        self.revisions = reader.ReadInt16()
        self.name = _read_name(reader)
        self.first_revision = reader.ReadInt16()
        self.data_ext = reader.ReadString(2)
        self.first_rev_offset = reader.ReadInt32()
        self.last_rev_offset = reader.ReadInt32()
        self.eof_offset = reader.ReadInt32()
        self.rights_offset = reader.ReadInt32()
        reader.Skip(16)  # reserved; always 0


class ProjectHeaderRecord(ItemHeaderRecord):
    def __init__(self):
        super().__init__(ItemType.PROJECT)
        self.parent_project = ""
        self.parent_file = ""
        self.total_items = 0
        self.subprojects = 0

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.parent_project = reader.ReadString(260)
        self.parent_file = reader.ReadString(8)
        reader.Skip(4)  # reserved; always 0
        self.total_items = reader.ReadInt16()
        self.subprojects = reader.ReadInt16()


class FileHeaderRecord(ItemHeaderRecord):
    def __init__(self):
        super().__init__(ItemType.FILE)
        self.flags = 0
        self.branch_file = ""
        self.branch_offset = 0
        self.project_offset = 0
        self.branch_count = 0
        self.project_count = 0
        self.first_checkout_offset = 0
        self.last_checkout_offset = 0
        self.data_crc = 0
        self.last_rev_datetime: Optional[datetime] = None
        self.modification_datetime: Optional[datetime] = None
        self.creation_datetime: Optional[datetime] = None

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        super().read(reader, header)
        self.flags = reader.ReadInt16()
        self.branch_file = reader.ReadString(8)
        reader.Skip(2)  # reserved; always 0
        self.branch_offset = reader.ReadInt32()
        self.project_offset = reader.ReadInt32()
        self.branch_count = reader.ReadInt16()
        self.project_count = reader.ReadInt16()
        self.first_checkout_offset = reader.ReadInt32()
        self.last_checkout_offset = reader.ReadInt32()
        self.data_crc = reader.ReadInt32() & 0xFFFFFFFF
        reader.Skip(8)  # reserved; always 0
        self.last_rev_datetime = _read_datetime(reader)
        self.modification_datetime = _read_datetime(reader)
        self.creation_datetime = _read_datetime(reader)
        # remaining appears to be trash


class ItemFile(RecordFile):
    """Port of the ItemFile constructor: preamble plus header record."""

    def __init__(self, path: Union[str, Path], encoding: str):
        super().__init__(path, encoding)
        try:
            file_sig = self.reader.ReadString(0x20)
            if file_sig != FILE_SIGNATURE:
                raise BadHeaderError("Incorrect file signature")

            self.file_type = self.reader.ReadInt16()
            self.version = self.reader.ReadInt16()
            if self.version != 6:
                raise BadHeaderError("Incorrect file version")

            self.reader.Skip(16)  # reserved; always 0

            if self.file_type == ItemType.PROJECT:
                self.header = ProjectHeaderRecord()
            else:
                self.header = FileHeaderRecord()

            self.read_record(self.header)
            if self.header.item_type != self.file_type:
                raise BadHeaderError("Header record type mismatch")
        except EndOfBuffer as e:
            raise BadHeaderError("Truncated header") from e
