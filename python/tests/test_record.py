from vss2git.physical.reader import BufferReader
from vss2git.physical.record import RecordHeader


def test_names_dat_first_record(vss_db):
    data = (vss_db / "data" / "names.dat").read_bytes()
    reader = BufferReader(data)
    header = RecordHeader.read(reader)
    assert header.signature == "HN"
    assert header.length == 0x50
    assert header.stored_crc == header.actual_crc
    assert reader.offset == 8
