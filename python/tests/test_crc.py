import struct

from vss2git.physical.crc import crc16


def test_known_vector():
    assert crc16(b"123456789") == 0x0075


def test_names_dat_header_crc(vss_db):
    data = (vss_db / "data" / "names.dat").read_bytes()
    length, sig, stored = struct.unpack_from("<I2sH", data, 0)
    assert sig == b"HN"
    assert crc16(data[8:8 + length]) == stored
