"""Project entry files (data files of projects) and their JP records."""

from pathlib import Path
from typing import Optional, Union

from vss2git.physical.items import ItemType, VssName, _read_name
from vss2git.physical.names import RecordFile
from vss2git.physical.reader import BufferReader
from vss2git.physical.record import RecordHeader


class ProjectEntryFlags:
    NONE = 0
    DELETED = 0x01
    BINARY = 0x02
    LATEST_ONLY = 0x04
    SHARED = 0x08


class ProjectEntryRecord:
    """Record (JP) describing one item contained in a project."""

    SIGNATURE = "JP"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.item_type = 0
        self.flags = 0
        self.name: Optional[VssName] = None
        self.pinned_version = 0
        self.physical = ""

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    @property
    def is_deleted(self) -> bool:
        return (self.flags & ProjectEntryFlags.DELETED) != 0

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        self.item_type = reader.ReadInt16()
        self.flags = reader.ReadInt16()
        self.name = _read_name(reader)
        self.pinned_version = reader.ReadInt16()
        self.physical = reader.ReadString(10)


class ProjectEntryFile(RecordFile):
    """Port of ProjectEntryFile."""

    def __init__(self, path: Union[str, Path], encoding: str):
        super().__init__(path, encoding)

    def get_first_entry(self) -> Optional[ProjectEntryRecord]:
        self.reader.offset = 0
        return self.get_next_entry()

    def get_next_entry(self) -> Optional[ProjectEntryRecord]:
        record = ProjectEntryRecord()
        return record if self.read_next_record(record) else None

    def entries(self):
        """Yield every JP record in file order."""
        record = self.get_first_entry()
        while record is not None:
            yield record
            record = self.get_next_entry()


__all__ = [
    "ItemType",
    "ProjectEntryFile",
    "ProjectEntryFlags",
    "ProjectEntryRecord",
]
