from vss2git.physical.items import ItemFile
from vss2git.physical.names import NameFile

def test_names_dat_has_sn_records(vss_db):
    names = NameFile(vss_db / "data" / "names.dat", "cp1252")
    first = names.next_name()
    assert first is not None
    assert first.signature == "SN"
    assert any(name for name in first.names)

def test_root_project_signature_and_version(vss_db):
    item = ItemFile(vss_db / "data" / "a" / "aaaaaaaa", "cp1252")
    assert item.file_type == 1  # project
    assert item.version == 6
    assert item.header.signature == "DH"
