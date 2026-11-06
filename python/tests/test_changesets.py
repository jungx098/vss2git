from vss2git.logical.database import Database
from vss2git.migrate.analyze import analyze
from vss2git.migrate.changesets import build_changesets, target_file

def test_each_revision_is_in_one_changeset(vss_db):
    db = Database(vss_db, "cp1252")
    revisions = analyze(db, "$")
    changesets = build_changesets(revisions, any_comment_seconds=0, same_comment_seconds=60)
    seen = [rev for cs in changesets for rev in cs.revisions]
    assert len(seen) == len(revisions)
    assert [cs.when for cs in changesets] == sorted(cs.when for cs in changesets)
    for cs in changesets:
        # the C# builder's notion of "same file" is item physical name plus,
        # for project-level actions, the physical name of the action target
        # (revision.Item.PhysicalName + "/" + Name.PhysicalName), so one project
        # revising several children is not a conflict
        targets = [target_file(rev) for rev in cs.revisions]
        assert len(targets) == len(set(targets))
