"""Port of VssDatabase, VssItem, VssProject and VssFile.

Children enumeration plus file contents reconstructed from reverse deltas
(VssFileRevision.GetContents). Revision metadata/actions are not ported here.
"""

import os
from typing import Dict, Iterator, List, Optional

from vss2git.logical.paths import PathResolver
from vss2git.physical import delta as deltas
from vss2git.physical import revisions
from vss2git.physical.entries import ProjectEntryFile
from vss2git.physical.items import ItemFile, ItemType, ProjectHeaderRecord, VssName
from vss2git.physical.names import NameFile, NameKind

ROOT_PROJECT_NAME = "$"
ROOT_PROJECT_FILE = "AAAAAAAA"
PROJECT_SEPARATOR = "/"


class VssPathError(Exception):
    """Raised when a logical path cannot be resolved."""


def read_ini(path: str) -> Dict[str, str]:
    """Port of SimpleIniReader.Parse (file is read as cp1252)."""
    entries: Dict[str, str] = {}
    with open(path, "r", encoding="cp1252", newline="") as stream:
        text = stream.read()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(";"):
            sep = line.find("=")
            if sep > 0:
                entries[line[:sep].strip()] = line[sep + 1:].strip()
    return entries


def _has_drive_letter(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":"


class VssItem:
    def __init__(self, database: "Database", logical_name: str,
                 physical_name: str, is_project: bool):
        self.database = database
        self.name = logical_name
        self.physical_name = physical_name
        self.is_project = is_project
        self.physical_path = database.get_data_path(physical_name)
        self._item_file: Optional[ItemFile] = None

    @property
    def item_file(self) -> ItemFile:
        if self._item_file is None:
            self._item_file = ItemFile(
                self.database.resolve(self.physical_path),
                self.database.encoding)
        return self._item_file

    @property
    def data_path(self) -> str:
        return self.physical_path + self.item_file.header.data_ext

    @property
    def revision_count(self) -> int:
        return self.item_file.header.revisions

    def _find_revision(self, version: int):
        """Port of VssItem.GetRevision: returns (owning item, revision record).

        A version before the first revision of a branched file belongs to the
        file it was branched from.
        """
        item_file = self.item_file
        if version < 1 or version > item_file.header.revisions:
            raise ValueError(f"Invalid version number: {version}")

        if version < item_file.header.first_revision:
            if not self.is_project:
                origin = self.database.item_physical(
                    item_file.header.branch_file)
                return origin._find_revision(version)
            raise ValueError(f"Undefined version: {version}")

        record = revisions.get_first_revision(item_file)
        while record is not None and record.revision < version:
            record = revisions.get_next_revision(item_file, record)
        if record is None:
            raise ValueError(f"Version not found: {version}")
        return self, record

    def __repr__(self) -> str:
        return (f"{'$' if self.is_project else ''}{self.name}"
                f"({self.physical_name})")


class VssFile(VssItem):
    def __init__(self, database: "Database", logical_name: str,
                 physical_name: str):
        super().__init__(database, logical_name, physical_name, False)

    def contents(self, version: int) -> bytes:
        """Bytes of the given version (VssFileRevision.GetContents)."""
        owner, record = self._find_revision(version)
        return owner._reconstruct(record.revision)

    def _reconstruct(self, version: int) -> bytes:
        db = self.database
        with open(db.resolve(self.data_path), "rb") as stream:
            data = stream.read()

        item_file = self.item_file
        last = revisions.get_last_revision(item_file)
        if last is None:
            return data

        ops = None
        while last is not None and last.revision > version:
            if isinstance(last, revisions.BranchRevisionRecord):
                branch_rev = last.revision
                item_file = ItemFile(
                    db.resolve(db.get_data_path(last.branch_file)),
                    db.encoding)
                last = revisions.get_last_revision(item_file)
                while last is not None and last.revision >= branch_rev:
                    last = revisions.get_previous_revision(item_file, last)
            else:
                if isinstance(last, revisions.EditRevisionRecord):
                    record = revisions.get_previous_delta(item_file, last)
                    if record is not None:
                        ops = (record.operations if ops is None
                               else deltas.merge(ops, record.operations))
                last = revisions.get_previous_revision(item_file, last)

        if ops is not None:
            data = deltas.apply(ops, data)
        return data


class VssProject(VssItem):
    def __init__(self, database: "Database", logical_name: str,
                 physical_name: str, logical_path: str):
        super().__init__(database, logical_name, physical_name, True)
        self.path = logical_path

    def entries(self):
        """Yield (item, is_deleted) for each project entry, in VSS order."""
        db = self.database
        entry_file = ProjectEntryFile(
            db.resolve(self.data_path), db.encoding)
        for rec in entry_file.entries():
            physical = rec.physical.upper()
            logical = db.get_full_name(rec.name)
            if rec.item_type == ItemType.PROJECT:
                item = db.open_project(self, physical, logical)
            elif rec.item_type == ItemType.FILE:
                item = db.open_file(physical, logical)
            else:
                continue
            yield item, rec.is_deleted

    def children(self) -> Iterator[VssItem]:
        """All entries (projects and files) in VSS order, deleted included."""
        for item, _deleted in self.entries():
            yield item

    def projects(self) -> Iterator["VssProject"]:
        return (c for c in self.children() if c.is_project)

    def files(self) -> Iterator[VssFile]:
        return (c for c in self.children() if not c.is_project)

    def walk(self) -> Iterator[VssItem]:
        """Yield this project, then every descendant (depth first)."""
        yield self
        for child in self.children():
            if child.is_project:
                yield from child.walk()
            else:
                yield child

    def find_project(self, name: str) -> Optional["VssProject"]:
        return next((p for p in self.projects() if p.name == name), None)

    def find_file(self, name: str) -> Optional[VssFile]:
        return next((f for f in self.files() if f.name == name), None)


class Database:
    def __init__(self, path, encoding: str):
        self.base_path = os.fspath(path)
        self.encoding = encoding
        self._paths = PathResolver()

        self.ini_path = os.path.join(self.base_path, "srcsafe.ini")
        ini = read_ini(self._paths.resolve(self.ini_path))
        self.data_path = self._compute_data_path(
            ini.get("Data_Path", "data"))

        self._name_file = NameFile(
            self.resolve(os.path.join(self.data_path, "names.dat")), encoding)
        self.root_project = self.open_project(
            None, ROOT_PROJECT_FILE, ROOT_PROJECT_NAME)

    def _compute_data_path(self, value: str) -> str:
        if os.sep != "\\" and (value.startswith("\\\\")
                               or _has_drive_letter(value)):
            return os.path.join(self.base_path, "data")
        return os.path.join(self.base_path, value.replace("\\", os.sep))

    def resolve(self, path) -> str:
        return self._paths.resolve(path)

    def get_data_path(self, physical_name: str) -> str:
        return os.path.join(self.data_path, physical_name[0], physical_name)

    def get_full_name(self, name: VssName) -> str:
        if name.name_file_offset != 0:
            record = self._name_file.get_name(name.name_file_offset)
            index = record.index_of(
                NameKind.PROJECT if name.is_project else NameKind.LONG)
            if index >= 0:
                return record.names[index]
        return name.short_name

    def open_project(self, parent: Optional[VssProject], physical_name: str,
                     logical_name: str) -> VssProject:
        path = (parent.path + PROJECT_SEPARATOR + logical_name
                if parent is not None else logical_name)
        return VssProject(self, logical_name, physical_name, path)

    def open_file(self, physical_name: str, logical_name: str) -> VssFile:
        return VssFile(self, logical_name, physical_name)

    def item_physical(self, physical_name: str) -> VssItem:
        """Port of GetItemPhysical (without the item cache)."""
        physical_name = physical_name.upper()
        if physical_name == ROOT_PROJECT_FILE:
            return self.root_project
        item_file = ItemFile(
            self.resolve(self.get_data_path(physical_name)), self.encoding)
        logical = self.get_full_name(item_file.header.name)
        if item_file.header.item_type == ItemType.PROJECT:
            header = item_file.header
            assert isinstance(header, ProjectHeaderRecord)
            parent = self.item_physical(header.parent_file)
            item = self.open_project(parent, physical_name, logical)
        else:
            item = self.open_file(physical_name, logical)
        item._item_file = item_file
        return item

    def item(self, logical_path: str) -> VssItem:
        """Port of GetItem: resolve a logical path like "$/a/b"."""
        segments: List[str] = [s for s in logical_path.split(PROJECT_SEPARATOR) if s]
        index = 1 if segments and segments[0] == ROOT_PROJECT_NAME else 0
        project = self.root_project
        while index < len(segments):
            name = segments[index]
            index += 1
            sub = project.find_project(name)
            if sub is not None:
                project = sub
                continue
            file = project.find_file(name)
            if file is not None:
                if index == len(segments):
                    return file
                raise VssPathError(
                    f"{PROJECT_SEPARATOR.join(segments[:index])} is not a project")
            raise VssPathError(f"{name} not found in {project.path}")
        return project
