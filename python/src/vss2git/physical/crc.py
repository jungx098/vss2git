"""Record CRC from HashLib: CRC-32/IEEE folded with XOR to 16 bits."""

_POLY = 0xEDB88320
_TABLE = []
for _i in range(256):
    _value = _i
    for _ in range(8):
        _xor = _value & 1
        _value >>= 1
        if _xor:
            _value ^= _POLY
    _TABLE.append(_value)


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = (_crc_shift(crc, byte)) & 0xFFFFFFFF
    return (crc ^ (crc >> 16)) & 0xFFFF


def _crc_shift(crc: int, byte: int) -> int:
    return (crc >> 8) ^ _TABLE[(crc ^ byte) & 0xFF]
