"""Read VSS data types from an in-memory byte buffer."""


class EndOfBuffer(Exception):
    """Raised when a read would extend beyond the buffer."""


class BufferReader:
    def __init__(self, data: bytes, encoding: str = "cp1252"):
        self._data = data
        self._encoding = encoding
        self._offset = 0
        self._limit = len(data)

    @classmethod
    def _bounded(
        cls, data: bytes, encoding: str, offset: int, limit: int
    ) -> "BufferReader":
        reader = cls(data, encoding)
        reader._offset = offset
        reader._limit = limit
        return reader

    @property
    def offset(self) -> int:
        return self._offset

    @offset.setter
    def offset(self, value: int) -> None:
        self._offset = value

    @property
    def Remaining(self) -> int:
        return self._limit - self._offset

    def CheckRead(self, count: int) -> None:
        if count < 0 or self._offset + count > self._limit:
            raise EndOfBuffer(
                f"Attempted read of {count} bytes with only "
                f"{self.Remaining} bytes remaining in buffer"
            )

    def Skip(self, count: int) -> None:
        self.CheckRead(count)
        self._offset += count

    def ReadInt16(self) -> int:
        self.CheckRead(2)
        value = int.from_bytes(
            self._data[self._offset:self._offset + 2], "little", signed=True
        )
        self._offset += 2
        return value

    def ReadInt32(self) -> int:
        self.CheckRead(4)
        value = int.from_bytes(
            self._data[self._offset:self._offset + 4], "little", signed=True
        )
        self._offset += 4
        return value

    def ReadSignature(self, length: int) -> str:
        self.CheckRead(length)
        value = self._data[self._offset:self._offset + length].decode("latin-1")
        self._offset += length
        return value

    def ReadString(self, field_size: int) -> str:
        self.CheckRead(field_size)
        field = self._data[self._offset:self._offset + field_size]
        value = field.split(b"\0", 1)[0].decode(self._encoding)
        self._offset += field_size
        return value

    def Extract(self, count: int) -> "BufferReader":
        self.CheckRead(count)
        start = self._offset
        self._offset += count
        return self._bounded(self._data, self._encoding, start, self._offset)
