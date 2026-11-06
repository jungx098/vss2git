from vss2git.logical.database import Database


def test_root_project_has_children(vss_db):
    db = Database(vss_db, "cp1252")
    root = db.item("$")
    assert root.is_project
    names = [child.name for child in root.children()]
    assert names
