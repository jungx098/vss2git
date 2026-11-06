"""Command line interface: migrate, list and verify.

Exit codes: 0 success, 1 bad arguments or startup failure, 2 migration
finished with errors.  Setting TZ is the caller's job.
"""

import argparse
import codecs
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Sequence

from vss2git.logical.database import Database, VssProject
from vss2git.migrate import export

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERRORS = 2

# FileFlags.Shared in the physical file header
_FILE_SHARED = 0x20


class CliError(Exception):
    """A startup or argument failure: printed as ERROR, exit code 1."""


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def _codec(code_page: int) -> str:
    name = f"cp{code_page}"
    try:
        codecs.lookup(name)
    except LookupError:
        raise CliError(f"Invalid encoding code page: {code_page}") from None
    return name


# -- argument parsing ----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vss2git", description="Migrate a Visual SourceSafe database to Git")
    sub = parser.add_subparsers(dest="command", metavar="{migrate,list,verify}")

    m = sub.add_parser("migrate", help="Migrate VSS repository to Git")
    m.add_argument("-v", "--vss-dir", required=True,
                   help="Path to VSS database directory (contains srcsafe.ini)")
    m.add_argument("-g", "--git-dir", required=True,
                   help="Output directory for Git repository")
    m.add_argument("-p", "--vss-project", default="$",
                   help="VSS project path to export (default: $)")
    m.add_argument("-e", "--exclude",
                   help="Exclude file patterns (not supported by this build)")
    m.add_argument("-d", "--email-domain", default="localhost",
                   help="Email domain for generated email addresses "
                        "(default: localhost)")
    m.add_argument("--default-comment", default="",
                   help="Default comment for changesets with no comment")
    m.add_argument("-c", "--encoding", type=int, default=1252,
                   help="VSS encoding code page (default: 1252)")
    m.add_argument("-l", "--log", default=None,
                   help="Path to log file (default: no log file)")
    m.add_argument("-i", "--ignore-errors", action="store_true",
                   help="Ignore errors and continue migration")
    m.add_argument("-f", "--force", action="store_true",
                   help="Continue even if output directory is not empty "
                        "(not supported by this build)")
    m.add_argument("--interactive", action="store_true",
                   help="Prompt for user input on errors "
                        "(not supported by this build)")
    m.add_argument("--any-comment-threshold", type=int, default=0,
                   help="Seconds threshold for grouping revisions regardless "
                        "of comment (0 = same-second only)")
    m.add_argument("--same-comment-threshold", type=int, default=60,
                   help="Seconds threshold for grouping revisions with "
                        "identical comment (default: 60)")
    m.add_argument("-t", "--transcode", type=_bool_arg, nargs="?", const=True,
                   default=True,
                   help="Transcode comments to UTF-8; always on in this build")
    m.add_argument("--force-annotated-tags", type=_bool_arg, nargs="?",
                   const=True, default=True,
                   help="Force annotated tags; always on in this build")
    m.add_argument("--export-to-root", action="store_true",
                   help="Export project directly to Git root "
                        "(not supported by this build)")
    m.add_argument("--from-date",
                   help="Export changesets from this date "
                        "(not supported by this build)")
    m.add_argument("--to-date",
                   help="Export changesets up to this date "
                        "(not supported by this build)")
    m.add_argument("--perf", action="store_true",
                   help="Print elapsed time at end")
    m.add_argument("--git-backend", default=None,
                   help="Not supported: this build only supports the git "
                        "executable")
    m.set_defaults(func=cmd_migrate)

    ls = sub.add_parser(
        "list", help="List projects and files in a VSS database")
    ls.add_argument("-v", "--vss-dir", required=True,
                    help="Path to VSS database directory (contains srcsafe.ini)")
    ls.add_argument("-p", "--vss-project", default="$",
                    help="Root project path to scan (default: $)")
    ls.add_argument("-c", "--encoding", type=int, default=1252,
                    help="VSS encoding code page (default: 1252)")
    ls.add_argument("-t", "--type", default="all",
                    help="Item types to list: projects, files, all "
                         "(default: all)")
    ls.add_argument("-s", "--shared", action="store_true",
                    help="List only shared files, grouped by physical file")
    ls.add_argument("--stats", action="store_true",
                    help="Database statistics (not supported by this build)")
    ls.add_argument("--checkouts", action="store_true",
                    help="List checked-out files (not supported by this build)")
    ls.add_argument("--include-deleted", action="store_true",
                    help="Include soft-deleted entries")
    ls.add_argument("-f", "--format", default="tree",
                    help="Output format: tree, flat (default: tree)")
    ls.set_defaults(func=cmd_list)

    v = sub.add_parser("verify", help="Compare source and target directories")
    v.add_argument("-s", "--source", required=True,
                   help="Source directory (VSS or expected)")
    v.add_argument("-t", "--target", required=True,
                   help="Target directory (Git)")
    v.add_argument("-x", "--exclude",
                   help="Exclude patterns (semicolon-separated)")
    v.set_defaults(func=cmd_verify)
    return parser


# -- migrate --------------------------------------------------------------------

class _ErrorCollector(logging.Handler):
    """Counts ERROR records, e.g. the revisions skipped by --ignore-errors."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def _check_migrate_options(args: argparse.Namespace) -> None:
    if args.git_backend is not None:
        raise CliError("this build only supports the git executable")
    unsupported = []
    if args.exclude:
        unsupported.append("--exclude")
    if args.force:
        unsupported.append("--force")
    if args.interactive:
        unsupported.append("--interactive")
    if args.export_to_root:
        unsupported.append("--export-to-root")
    if args.from_date:
        unsupported.append("--from-date")
    if args.to_date:
        unsupported.append("--to-date")
    if not args.transcode:
        unsupported.append("--transcode=false")
    if not args.force_annotated_tags:
        unsupported.append("--force-annotated-tags=false")
    if unsupported:
        raise CliError("not supported by this build: " + ", ".join(unsupported))
    if args.any_comment_threshold < 0 or args.same_comment_threshold < 0:
        raise CliError("comment thresholds must not be negative")


def _open_project(vss_dir: str, encoding: str, project: str) -> VssProject:
    if not os.path.isfile(os.path.join(vss_dir, "srcsafe.ini")):
        raise CliError(f"Cannot open VSS database: no srcsafe.ini in {vss_dir}")
    try:
        db = Database(vss_dir, encoding)
    except Exception as e:
        raise CliError(f"Cannot open VSS database: {e}") from e
    try:
        item = db.item(project)
    except Exception as e:
        raise CliError(str(e)) from e
    if not isinstance(item, VssProject):
        raise CliError(f"'{project}' is not a project")
    return item


def cmd_migrate(args: argparse.Namespace) -> int:
    _check_migrate_options(args)
    encoding = _codec(args.encoding)
    vss_dir = os.path.realpath(args.vss_dir)
    git_dir = os.path.realpath(args.git_dir)
    _open_project(vss_dir, encoding, args.vss_project)
    try:
        export._check_directories(vss_dir, git_dir)
    except export.MigrateError as e:
        raise CliError(str(e)) from e

    root_log = logging.getLogger()
    collector = _ErrorCollector()
    handlers: List[logging.Handler] = [collector]
    if args.log:
        try:
            file_handler = logging.FileHandler(args.log, encoding="utf-8")
        except OSError as e:
            raise CliError(f"Cannot open log file {args.log}: {e.strerror}") from e
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        handlers.append(file_handler)
    old_level = root_log.level
    root_log.setLevel(logging.INFO)
    for handler in handlers:
        root_log.addHandler(handler)

    print("Configuration:")
    print(f"  VSS Directory:     {vss_dir}")
    print(f"  VSS Project:       {args.vss_project}")
    print(f"  Git Directory:     {git_dir}")
    print(f"  Email Domain:      {args.email_domain}")
    print(f"  Encoding:          {encoding}")
    print(f"  Ignore Errors:     {args.ignore_errors}")
    print()
    print("Starting migration...")

    started = time.monotonic()
    failure: Optional[BaseException] = None
    commits = 0
    try:
        commits = export.migrate(
            vss_dir, git_dir, project=args.vss_project, encoding=encoding,
            email_domain=args.email_domain,
            any_comment_seconds=args.any_comment_threshold,
            same_comment_seconds=args.same_comment_threshold,
            default_comment=args.default_comment,
            ignore_errors=args.ignore_errors)
    except Exception as e:  # the migration ran and failed part-way
        failure = e
        logging.getLogger(__name__).error("%s: %s", type(e).__name__, e)
    finally:
        for handler in handlers:
            root_log.removeHandler(handler)
            handler.close()
        root_log.setLevel(old_level)

    errors = collector.count
    print()
    if failure is not None:
        _eprint(f"ERROR: migration aborted: {failure}")
    print("Migration completed with errors." if errors
          else "Migration completed successfully!")
    print(f"  Commits:    {commits}")
    if errors:
        print(f"  Errors:     {errors}")
    if args.perf:
        print(f"  Duration:   {time.monotonic() - started:.1f}s")
    return EXIT_ERRORS if errors else EXIT_OK


# -- list -----------------------------------------------------------------------

class _Node:
    def __init__(self, name: str, path: str, is_project: bool,
                 physical: str = "", shared: bool = False,
                 deleted: bool = False):
        self.name = name
        self.path = path
        self.is_project = is_project
        self.physical = physical
        self.shared = shared
        self.deleted = deleted
        self.children: List["_Node"] = []

    @property
    def marker(self) -> str:
        parts = []
        if not self.is_project and self.shared:
            parts.append("shared")
        if self.deleted:
            parts.append("deleted")
        return "  [" + ", ".join(parts) + "]" if parts else ""


def _build_tree(project: VssProject, include_files: bool,
                include_deleted: bool) -> _Node:
    node = _Node(project.name, project.path, True)
    _add_children(node, project, include_files, include_deleted)
    return node


def _add_children(node: _Node, project: VssProject, include_files: bool,
                  include_deleted: bool) -> None:
    entries = []
    try:
        entries = list(project.entries())
    except Exception as e:
        _eprint(f"  WARNING: Error enumerating {project.path}: {e}")
    for item, deleted in entries:  # projects first, like the C# tree
        if not item.is_project or (deleted and not include_deleted):
            continue
        child = _Node(item.name, item.path, True, item.physical_name,
                      deleted=deleted)
        node.children.append(child)
        _add_children(child, item, include_files, include_deleted)
    if include_files:
        for item, deleted in entries:
            if item.is_project or (deleted and not include_deleted):
                continue
            try:
                flags = item.item_file.header.flags
            except Exception as e:
                _eprint(f"  WARNING: Error reading item {item.name}: {e}")
                continue
            node.children.append(_Node(
                item.name, f"{project.path}/{item.name}", False,
                item.physical_name, bool(flags & _FILE_SHARED), deleted))


def _count(node: _Node) -> List[int]:
    projects = files = 0
    for child in node.children:
        if child.is_project:
            projects += 1
        else:
            files += 1
        p, f = _count(child)
        projects += p
        files += f
    return [projects, files]


def _has_file(node: _Node) -> bool:
    return any(not c.is_project or _has_file(c) for c in node.children)


def _print_tree(root: _Node, include_projects: bool) -> None:
    print(root.path + "/")
    _print_tree_children(root, "", include_projects)
    projects, files = _count(root)
    print()
    print(f"{projects} projects, {files} files")


def _print_tree_children(node: _Node, indent: str,
                         include_projects: bool) -> None:
    visible = [c for c in node.children
               if not c.is_project or include_projects or _has_file(c)]
    for i, child in enumerate(visible):
        last = i == len(visible) - 1
        connector = "└── " if last else "├── "
        suffix = "/" if child.is_project else ""
        print(f"{indent}{connector}{child.name}{suffix}{child.marker}")
        if child.is_project:
            _print_tree_children(
                child, indent + ("    " if last else "│   "), include_projects)


def _collect_flat(node: _Node, include_projects: bool, include_files: bool,
                  lines: List[str], counts: List[int]) -> None:
    for child in node.children:
        if child.is_project:
            counts[0] += 1
            if include_projects:
                lines.append(child.path + "/")
            _collect_flat(child, include_projects, include_files, lines, counts)
        else:
            counts[1] += 1
            if include_files:
                lines.append(child.path + child.marker)


def _print_flat(root: _Node, include_projects: bool,
                include_files: bool) -> None:
    lines: List[str] = []
    counts = [0, 0]
    _collect_flat(root, include_projects, include_files, lines, counts)
    for line in sorted(lines, key=str.casefold):
        print(line)
    print()
    print(f"{counts[0]} projects, {counts[1]} files")


class _Shared:
    def __init__(self, name: str, physical: str, shared: bool):
        self.name = name
        self.physical = physical
        self.shared = shared
        self.paths: List[tuple] = []


def _collect_shared(node: _Node, by_physical: Dict[str, _Shared]) -> None:
    for child in node.children:
        if child.is_project:
            _collect_shared(child, by_physical)
            continue
        key = (child.physical or child.path).upper()
        entry = by_physical.get(key)
        if entry is None:
            entry = by_physical[key] = _Shared(
                child.name, child.physical, child.shared)
        else:
            entry.shared = entry.shared or child.shared
        entry.paths.append((child.path, child.deleted))


def _parent_path(path: str) -> str:
    slash = path.rfind("/")
    return path if slash <= 0 else path[:slash + 1]


def _print_shared(root: _Node, include_single: bool) -> None:
    by_physical: Dict[str, _Shared] = {}
    _collect_shared(root, by_physical)
    multi = [e for e in by_physical.values() if len(e.paths) > 1]
    single = [e for e in by_physical.values()
              if len(e.paths) <= 1 and include_single and e.shared]
    # highest fan-out first
    multi.sort(key=lambda e: (-len(e.paths), e.name.casefold()))
    single.sort(key=lambda e: e.name.casefold())
    total_refs = sum(len(e.paths) for e in multi) + len(single)
    shared = len(multi) + len(single)

    def plural(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    if shared == 0:
        title = f"No shared files found in {root.path}"
    else:
        title = (f"Shared files in {root.path} — {plural(shared, 'file')}, "
                 f"{plural(total_refs, 'reference')}")
    print(title)
    print("═" * min(len(title), 78))
    print()
    if shared == 0:
        return
    for entry in multi:
        print(f"{entry.name}  [{entry.physical}]")
        for path, deleted in sorted(entry.paths, key=lambda p: p[0].casefold()):
            print(f"  {_parent_path(path)}{'  [deleted]' if deleted else ''}")
        print()
    if single:
        print(f"Files flagged Shared in VSS but with only one path inside "
              f"{root.path}")
        print("(other references exist outside the scan root, or are "
              "soft-deleted — try --include-deleted):")
        print()
        for entry in single:
            path, deleted = entry.paths[0]
            print(f"{entry.name}  [{entry.physical}]")
            print(f"  {_parent_path(path)}{'  [deleted]' if deleted else ''}")
            print()
    print(f"{plural(shared, 'shared file')}, {plural(total_refs, 'reference')}")


def cmd_list(args: argparse.Namespace) -> int:
    item_type = (args.type or "").strip().lower() or "projects"
    if item_type not in ("projects", "files", "all"):
        raise CliError(
            f"Invalid --type '{args.type}'. Use projects, files, or all.")
    fmt = (args.format or "").strip().lower() or "tree"
    if fmt not in ("tree", "flat"):
        raise CliError(f"Invalid --format '{args.format}'. Use tree or flat.")
    for flag, wanted in (("--stats", args.stats),
                         ("--checkouts", args.checkouts)):
        if wanted:
            raise CliError(f"list {flag} is not supported by this build")
    include_projects = item_type in ("projects", "all")
    include_files = item_type in ("files", "all") or args.shared

    root = _open_project(os.path.realpath(args.vss_dir),
                         _codec(args.encoding), args.vss_project)
    tree = _build_tree(root, include_files, args.include_deleted)
    if args.shared:
        _print_shared(tree, args.include_deleted)
    elif fmt == "flat":
        _print_flat(tree, include_projects, include_files)
    else:
        _print_tree(tree, include_projects)
    return EXIT_OK


# -- verify ---------------------------------------------------------------------

DEFAULT_EXCLUDES = [".git", ".vs", "! Borland !", "! CodeGear - RAD Studio !",
                    "ToPAZ"]


def _excluded(relative: str, excludes: Sequence[str]) -> bool:
    segments = [s for s in relative.replace("\\", "/").split("/") if s]
    wanted = {e.casefold() for e in excludes}
    return any(s.casefold() in wanted for s in segments)


def _scan(base: str, excludes: Sequence[str]) -> Dict[str, str]:
    """Map case-insensitive relative path keys to the path as spelled."""
    items: Dict[str, str] = {}
    for current, dirs, files in os.walk(base):
        kept = []
        for name in dirs:
            rel = os.path.relpath(os.path.join(current, name), base)
            if not _excluded(rel, excludes):
                items[rel.casefold()] = rel
                kept.append(name)
        dirs[:] = kept  # do not descend into excluded directories
        for name in files:
            rel = os.path.relpath(os.path.join(current, name), base)
            if not _excluded(rel, excludes):
                items[rel.casefold()] = rel
    return items


def cmd_verify(args: argparse.Namespace) -> int:
    excludes = ([e for e in args.exclude.split(";") if e]
                if args.exclude else DEFAULT_EXCLUDES)
    print()
    print("Comparing directory structures...")
    print(f"Source: {args.source}")
    print(f"Target: {args.target}")
    print(f"Excluded: {', '.join(excludes)}")
    print()
    if not os.path.isdir(args.source):
        raise CliError(f"Source directory does not exist: {args.source}")
    if not os.path.isdir(args.target):
        raise CliError(f"Target directory does not exist: {args.target}")

    source = _scan(args.source, excludes)
    target = _scan(args.target, excludes)
    missing = sorted(source[k] for k in source if k not in target)
    extra = sorted(target[k] for k in target if k not in source)

    print(f"Total items in source: {len(source)}")
    print(f"Total items in target: {len(target)}")
    print()
    if not missing and not extra:
        print("SUCCESS: Directory structures match perfectly!")
        print("Missing in target: 0 items")
        print("Extra in target:   0 items")
        return EXIT_OK
    for item in missing:
        print(f"MISSING_IN_TARGET: {item}")
    for item in extra:
        print(f"EXTRA_IN_TARGET: {item}")
    print()
    print(f"Missing in target: {len(missing)} items")
    print(f"Extra in target:   {len(extra)} items")
    return EXIT_USAGE


# -- entry point ----------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)  # argparse exits with 2 on bad usage
    except SystemExit as e:
        if e.code not in (0, None):
            sys.exit(EXIT_USAGE)
        raise
    if getattr(args, "func", None) is None:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE)
    try:
        code = args.func(args)
    except CliError as e:
        _eprint(f"ERROR: {e}")
        code = EXIT_USAGE
    sys.stdout.flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
