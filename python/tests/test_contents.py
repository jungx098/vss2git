from vss2git.logical.database import Database

def test_tip_is_stable_and_history_moves(vss_db):
    db = Database(vss_db, "cp1252")
    files = [c for c in db.item("$").walk() if not c.is_project]
    assert files
    item = next(f for f in files if f.revision_count > 1)
    tip = item.contents(item.revision_count)
    first = item.contents(1)
    assert tip == item.contents(item.revision_count)
    assert len(tip) > 0 and len(first) > 0
    assert first != tip
