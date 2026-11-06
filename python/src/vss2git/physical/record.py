"""VSS physical record headers."""

from dataclasses import dataclass

from vss2git.physical.crc import crc16
from vss2git.physical.reader import BufferReader


class CrcError(Exception):
    """Raised when a record body does not match its stored CRC."""


@dataclass
class RecordHeader:
    offset: int
    length: int
    signature: str
    stored_crc: int
    actual_crc: int

    @classmethod
    def read(cls, reader: BufferReader) -> "RecordHeader":
        offset = reader.offset
        length = reader.ReadInt32()
        signature = reader.ReadSignature(2)
        stored_crc = reader.ReadInt16() & 0xFFFF

        reader.CheckRead(length)
        body_start = reader.offset
        actual_crc = crc16(reader._data[body_start:body_start + length])

        return cls(offset, length, signature, stored_crc, actual_crc)

    def check(self, check_crc: bool = True) -> None:
        if check_crc and self.stored_crc != self.actual_crc:
            raise CrcError(
                f"CRC error in {self.signature} record: "
                f"expected={self.stored_crc}, actual={self.actual_crc}"
            )
