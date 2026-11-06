"""Record files and the VSS name file (names.dat)."""

from pathlib import Path
from typing import Optional, Union

from vss2git.physical.reader import BufferReader, EndOfBuffer
from vss2git.physical.record import RecordHeader

HEADER_LENGTH = 8  # RecordHeader.LENGTH in C#
COMMENT_SIGNATURE = "MC"


class RecordNotFoundError(Exception):
    """Raised when a record has an unexpected signature."""


class RecordTruncatedError(Exception):
    """Raised when a record extends beyond the end of its buffer."""


class RecordFile:
    """Port of VssRecordFile: a file consisting of a stream of records."""

    def __init__(self, path: Union[str, Path], encoding: str):
        self.filename = str(path)
        with open(path, "rb") as stream:
            data = stream.read()
        self.reader = BufferReader(data, encoding)

    def _read_header_and_body(self):
        header = RecordHeader.read(self.reader)
        body = self.reader.Extract(header.length)
        # comment records always seem to have a zero CRC
        header.check(check_crc=header.signature != COMMENT_SIGNATURE)
        return header, body

    def read_record(self, record, offset: Optional[int] = None) -> None:
        """Read the record at the current (or given) offset into `record`."""
        if offset is not None:
            self.reader.offset = offset
        try:
            header, body = self._read_header_and_body()
            if header.signature != record.SIGNATURE:
                raise RecordNotFoundError(
                    f"Unexpected record signature: "
                    f"expected={record.SIGNATURE}, actual={header.signature}"
                )
            record.read(body, header)
        except EndOfBuffer as e:
            raise RecordTruncatedError(str(e)) from e

    def read_next_record(self, record) -> bool:
        """Read the next record with record's signature, skipping others."""
        while self.reader.Remaining > HEADER_LENGTH:
            try:
                header, body = self._read_header_and_body()
                if header.signature == record.SIGNATURE:
                    record.read(body, header)
                    return True
            except EndOfBuffer as e:
                raise RecordTruncatedError(str(e)) from e
        return False


class NameHeaderRecord:
    """Header record (HN) for the name file."""

    SIGNATURE = "HN"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.eof_offset = 0

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        reader.Skip(16)  # reserved; always 0
        self.eof_offset = reader.ReadInt32()
        # remaining reserved; always 0


class NameKind:
    DOS = 1  # DOS 8.3 filename
    LONG = 2  # Win95/NT long filename
    MACOS = 3  # Mac OS 9 and earlier 31-character filename
    PROJECT = 10  # VSS project name


class NameRecord:
    """Record (SN) containing the logical names of an object."""

    SIGNATURE = "SN"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.kinds: list = []
        self.names: list = []

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    @property
    def kind_count(self) -> int:
        return len(self.kinds)

    def index_of(self, kind: int) -> int:
        for i, k in enumerate(self.kinds):
            if k == kind:
                return i
        return -1

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        kind_count = reader.ReadInt16()
        reader.Skip(2)  # unknown
        self.kinds = []
        self.names = []
        base_offset = reader.offset + kind_count * 4
        for _ in range(kind_count):
            self.kinds.append(reader.ReadInt16())
            name_offset = reader.ReadInt16()
            save_offset = reader.offset
            try:
                reader.offset = base_offset + name_offset
                self.names.append(reader.ReadString(reader.Remaining))
            finally:
                reader.offset = save_offset


class NameFile(RecordFile):
    """Port of NameFile: a file containing VSS name records."""

    def __init__(self, path: Union[str, Path], encoding: str):
        super().__init__(path, encoding)
        self.header = NameHeaderRecord()
        self.read_record(self.header)

    def get_name(self, offset: int) -> NameRecord:
        record = NameRecord()
        self.read_record(record, offset)
        return record

    def next_name(self) -> Optional[NameRecord]:
        record = NameRecord()
        return record if self.read_next_record(record) else None
