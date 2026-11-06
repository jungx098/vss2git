from vss2git.logical.database import Database
from vss2git.migrate.analyze import analyze

def test_revisions_are_ordered(vss_db):
    db = Database(vss_db, "cp1252")
    revisions = analyze(db, "$")
    assert len(revisions) > 1
    times = [rev.when for rev in revisions]
    assert times == sorted(times)
    last_version = {}
    for rev in revisions:
        previous = last_version.get(rev.physical)
        if previous is not None:
            assert rev.version >= previous
        last_version[rev.physical] = rev.version
