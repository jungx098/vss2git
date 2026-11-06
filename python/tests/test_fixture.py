from pathlib import Path

def test_copy_contains_srcsafe_ini(vss_db: Path):
    text = (vss_db / "srcsafe.ini").read_text(encoding="latin-1")
    assert "Data_Path" in text
    assert (vss_db / "data" / "names.dat").is_file()
    assert (vss_db / "data" / "a" / "aaaaaaaa").is_file()
