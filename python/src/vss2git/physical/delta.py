"""Reverse-delta records and operations (DeltaOperation, DeltaRecord,
DeltaSimulator and DeltaUtil from VssPhysicalLib)."""

from typing import Callable, List, Optional, Sequence

from vss2git.physical.reader import BufferReader
from vss2git.physical.record import RecordHeader


class DeltaCommand:
    WRITE_LOG = 0  # write data from the log file
    WRITE_SUCCESSOR = 1  # write data from the subsequent revision
    STOP = 2  # indicates the last operation


class DeltaError(Exception):
    """Raised when a delta cannot be applied to its input."""


class DeltaOperation:
    """A single delta operation for a file revision."""

    __slots__ = ("command", "offset", "length", "data")

    def __init__(self, command: int = DeltaCommand.WRITE_LOG, offset: int = 0,
                 length: int = 0, data: Optional[bytes] = None):
        self.command = command
        self.offset = offset  # meaningful for WRITE_SUCCESSOR only
        self.length = length
        self.data = data  # WRITE_LOG only

    @classmethod
    def write_log(cls, data: bytes) -> "DeltaOperation":
        return cls(DeltaCommand.WRITE_LOG, 0, len(data), data)

    @classmethod
    def write_successor(cls, offset: int, length: int) -> "DeltaOperation":
        return cls(DeltaCommand.WRITE_SUCCESSOR, offset, length)

    def read(self, reader: BufferReader) -> None:
        self.command = reader.ReadInt16()
        reader.Skip(2)  # unknown
        self.offset = reader.ReadInt32()
        self.length = reader.ReadInt32()
        if self.command == DeltaCommand.WRITE_LOG:
            reader.CheckRead(self.length)
            start = reader.offset
            self.data = bytes(reader._data[start:start + self.length])
            reader.Skip(self.length)


class DeltaRecord:
    """Record (FD) holding a reverse-delta for a file revision."""

    SIGNATURE = "FD"

    def __init__(self):
        self.header: Optional[RecordHeader] = None
        self.operations: List[DeltaOperation] = []

    @property
    def signature(self) -> str:
        return self.SIGNATURE

    def read(self, reader: BufferReader, header: RecordHeader) -> None:
        self.header = header
        self.operations = []
        while True:
            operation = DeltaOperation()
            operation.read(reader)
            if operation.command == DeltaCommand.STOP:
                break
            self.operations.append(operation)


FromLog = Callable[[bytes, int, int], int]
FromSuccessor = Callable[[int, int], int]


class DeltaSimulator:
    """Stream-like traversal over a sequence of delta operations."""

    def __init__(self, operations: Sequence[DeltaOperation]):
        self.operations = operations
        self._index = 0
        self._operation_offset = 0
        self.offset = 0  # file offset
        self._eof = False
        self._reset()

    def _reset(self) -> None:
        self._index = 0
        self._eof = len(self.operations) == 0
        self._operation_offset = 0
        self.offset = 0

    def _move_next(self) -> bool:
        self._index += 1
        return self._index < len(self.operations)

    def seek(self, offset: int) -> None:
        if offset != self.offset:
            if offset < self.offset:
                self._reset()
            while self.offset < offset and not self._eof:
                seek_remaining = offset - self.offset
                operation_remaining = (self.operations[self._index].length
                                       - self._operation_offset)
                if seek_remaining < operation_remaining:
                    self._operation_offset += seek_remaining
                    self.offset += seek_remaining
                else:
                    self.offset += operation_remaining
                    self._eof = not self._move_next()
                    self._operation_offset = 0

    def read(self, length: int, from_log: FromLog,
             from_successor: FromSuccessor) -> None:
        while length > 0 and not self._eof:
            operation = self.operations[self._index]
            operation_remaining = operation.length - self._operation_offset
            count = min(length, operation_remaining)
            if operation.command == DeltaCommand.WRITE_LOG:
                bytes_read = from_log(
                    operation.data, self._operation_offset, count)
            else:
                bytes_read = from_successor(
                    operation.offset + self._operation_offset, count)
            if bytes_read == 0:
                break
            self._operation_offset += bytes_read
            self.offset += bytes_read
            if length >= operation_remaining:
                self._eof = not self._move_next()
                self._operation_offset = 0
            length -= bytes_read


def merge(last_revision: Sequence[DeltaOperation],
          prior_revision: Sequence[DeltaOperation]) -> List[DeltaOperation]:
    """Port of DeltaUtil.Merge: compose two reverse deltas into one."""
    result: List[DeltaOperation] = []
    merger = DeltaSimulator(last_revision)

    def from_log(data: bytes, offset: int, count: int) -> int:
        result.append(DeltaOperation.write_log(data[offset:offset + count]))
        return count

    def from_successor(offset: int, count: int) -> int:
        result.append(DeltaOperation.write_successor(offset, count))
        return count

    for operation in prior_revision:
        if operation.command == DeltaCommand.WRITE_LOG:
            result.append(operation)
        elif operation.command == DeltaCommand.WRITE_SUCCESSOR:
            merger.seek(operation.offset)
            merger.read(operation.length, from_log, from_successor)
    return result


def apply(operations: Sequence[DeltaOperation], source: bytes) -> bytes:
    """Port of DeltaUtil.Apply: build the older revision from `source`."""
    out = bytearray()
    for operation in operations:
        if operation.command == DeltaCommand.WRITE_LOG:
            out += operation.data
        elif operation.command == DeltaCommand.WRITE_SUCCESSOR:
            if operation.offset < 0:
                raise DeltaError("Negative successor offset")
            chunk = source[operation.offset:operation.offset + operation.length]
            if len(chunk) < operation.length:
                raise DeltaError("Unexpected end of current revision file")
            out += chunk
    return bytes(out)
