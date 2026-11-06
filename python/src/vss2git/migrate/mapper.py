"""Port of VssPathMapper (Vss2Git.Core/Vss/VssPathMapper.cs).

Tracks the names and locations of VSS projects and files as revisions are
replayed. Items are keyed by physical name; an item name is the `ItemName`
of the analyzer (logical name, physical name, is_project).

Project paths are built with os.path.join on the logical names, starting
from the working path of the root project, so they are absolute paths into
the repository being written.
"""

import os
from typing import Dict, Iterator, List, Optional

from vss2git.migrate.analyze import ItemName

PROJECT_SEPARATOR = "/"


class ItemInfo:
    """Base class for representing VSS items (VssItemInfo)."""

    def __init__(self, physical_name: str, logical_name: str):
        self.physical_name = physical_name
        self.logical_name = logical_name
        self.destroyed = False


class ProjectInfo(ItemInfo):
    """Current state of a VSS project (VssProjectInfo)."""

    def __init__(self, physical_name: str, logical_name: str):
        super().__init__(physical_name, logical_name)
        self._parent: Optional["ProjectInfo"] = None
        self.is_root = False
        # valid only for root paths; used to resolve project specifiers
        self.original_vss_path: Optional[str] = None
        self.items: List[ItemInfo] = []

    @property
    def parent(self) -> Optional["ProjectInfo"]:
        return self._parent

    @parent.setter
    def parent(self, value: Optional["ProjectInfo"]) -> None:
        if self._parent is not value:
            if self._parent is not None:
                self._parent.remove_item(self)
            self._parent = value
            if self._parent is not None:
                self._parent.add_item(self)

    @property
    def is_rooted(self) -> bool:
        project = self
        while project._parent is not None:
            project = project._parent
        return project.is_root

    def get_path(self) -> Optional[str]:
        if self.is_rooted:
            if self._parent is not None:
                return os.path.join(self._parent.get_path(), self.logical_name)
            return self.logical_name
        return None

    def is_same_or_subproject(self, parent_info: "ProjectInfo") -> bool:
        project: Optional[ProjectInfo] = self
        while project is not None:
            if project is parent_info:
                return True
            project = project._parent
        return False

    def add_item(self, item: ItemInfo) -> None:
        self.items.append(item)

    def remove_item(self, item: ItemInfo) -> bool:
        for index, existing in enumerate(self.items):
            if existing is item:
                del self.items[index]
                return True
        return False

    def contains_logical_name(self, logical_name: str) -> bool:
        return any(item.logical_name.casefold() == logical_name.casefold()
                   for item in self.items)

    def _walk(self) -> Iterator[ItemInfo]:
        """Breadth-first over items, as the C# queue of subprojects does.

        A subproject is yielded where it is found, before the items of the
        subprojects queued ahead of it.
        """
        queue: List[ProjectInfo] = []
        project: Optional[ProjectInfo] = self
        while project is not None:
            for item in list(project.items):
                if isinstance(item, ProjectInfo):
                    queue.append(item)
                yield item
            project = queue.pop(0) if queue else None

    def contains_files(self) -> bool:
        return any(isinstance(item, FileInfo) for item in self._walk())

    def get_all_files(self) -> Iterator["FileInfo"]:
        return (item for item in self._walk() if isinstance(item, FileInfo))

    def get_all_projects(self) -> Iterator["ProjectInfo"]:
        return (item for item in self._walk() if isinstance(item, ProjectInfo))


class FileInfo(ItemInfo):
    """Current state of a VSS file (VssFileInfo)."""

    def __init__(self, physical_name: str, logical_name: str):
        super().__init__(physical_name, logical_name)
        self.projects: List[ProjectInfo] = []
        # per-project logical name for shared files that may have different
        # names in different projects
        self._project_names: Dict[int, str] = {}
        self.version = 1

    def add_project(self, project: ProjectInfo, logical_name: str) -> None:
        if not any(p is project for p in self.projects):
            self.projects.append(project)
        self._project_names[id(project)] = logical_name

    def remove_project(self, project: ProjectInfo) -> bool:
        self._project_names.pop(id(project), None)
        for index, existing in enumerate(self.projects):
            if existing is project:
                del self.projects[index]
                return True
        return False

    def get_logical_name_for_project(self, project: ProjectInfo) -> str:
        return self._project_names.get(id(project), self.logical_name)

    def rename_in_all_projects(self, new_logical_name: str) -> None:
        for project in self.projects:
            self._project_names[id(project)] = new_logical_name


class PathMapper:
    """Port of VssPathMapper."""

    def __init__(self) -> None:
        self._projects: Dict[str, ProjectInfo] = {}
        self._roots: Dict[str, ProjectInfo] = {}
        self._files: Dict[str, FileInfo] = {}

    def is_project_rooted(self, project: str) -> bool:
        info = self._projects.get(project)
        return info.is_rooted if info is not None else False

    def get_project_path(self, project: str) -> Optional[str]:
        info = self._projects.get(project)
        return info.get_path() if info is not None else None

    def set_project_path(self, project: str, path: str,
                         original_vss_path: str) -> None:
        info = ProjectInfo(project, path)
        info.is_root = True
        info.original_vss_path = original_vss_path
        self._projects[project] = info
        self._roots[project] = info

    def get_all_files(self, project: str) -> Iterator[FileInfo]:
        info = self._projects.get(project)
        return info.get_all_files() if info is not None else iter(())

    def get_all_projects(self, project: str) -> Iterator[ProjectInfo]:
        info = self._projects.get(project)
        return info.get_all_projects() if info is not None else iter(())

    def get_file_paths(self, file: str, under_project: Optional[str]) -> List[str]:
        result: List[str] = []
        file_info = self._files.get(file)
        if file_info is None:
            return result
        under_info = None
        if under_project is not None:
            under_info = self._projects.get(under_project)
            if under_info is None:
                return result
        for project in file_info.projects:
            if under_info is None or project.is_same_or_subproject(under_info):
                # ignore projects that are not rooted
                project_path = project.get_path()
                if project_path is not None:
                    result.append(os.path.join(
                        project_path,
                        file_info.get_logical_name_for_project(project)))
        return result

    def get_file_version(self, file: str) -> int:
        info = self._files.get(file)
        return info.version if info is not None else 1

    def set_file_version(self, name: ItemName, version: int) -> None:
        self._get_or_create_file(name).version = version

    def add_item(self, project: ItemName, name: ItemName) -> ItemInfo:
        parent_info = self._get_or_create_project(project)
        item_info: ItemInfo
        if name.is_project:
            project_info = self._get_or_create_project(name)
            project_info.parent = parent_info
            item_info = project_info
        else:
            file_info = self._get_or_create_file(name)
            file_info.add_project(parent_info, name.logical_name)
            parent_info.add_item(file_info)
            item_info = file_info

        # update name of item in case it was created on demand by an earlier
        # unmapped item that was subsequently renamed
        item_info.logical_name = name.logical_name
        return item_info

    def rename_item(self, name: ItemName) -> ItemInfo:
        item_info: ItemInfo
        if name.is_project:
            item_info = self._get_or_create_project(name)
        else:
            file_info = self._get_or_create_file(name)
            # VSS renames propagate to all sharing projects
            file_info.rename_in_all_projects(name.logical_name)
            item_info = file_info
        item_info.logical_name = name.logical_name
        return item_info

    def delete_item(self, project: ItemName, name: ItemName) -> ItemInfo:
        parent_info = self._get_or_create_project(project)
        if name.is_project:
            project_info = self._get_or_create_project(name)
            project_info.parent = None
            return project_info
        file_info = self._get_or_create_file(name)
        file_info.remove_project(parent_info)
        parent_info.remove_item(file_info)
        return file_info

    def recover_item(self, project: ItemName, name: ItemName) -> ItemInfo:
        parent_info = self._get_or_create_project(project)
        if name.is_project:
            project_info = self._get_or_create_project(name)
            project_info.parent = parent_info
            return project_info
        file_info = self._get_or_create_file(name)
        file_info.add_project(parent_info, name.logical_name)
        parent_info.add_item(file_info)
        return file_info

    def pin_item(self, project: ItemName, name: ItemName) -> ItemInfo:
        # pinning removes the project from the list of sharing projects, so
        # it no longer receives edits
        return self.delete_item(project, name)

    def unpin_item(self, project: ItemName, name: ItemName) -> ItemInfo:
        # unpinning restores the project to the list of sharing projects
        return self.recover_item(project, name)

    def branch_file(self, project: ItemName, new_name: ItemName,
                    old_name: ItemName) -> FileInfo:
        assert not new_name.is_project and not old_name.is_project

        # "branching a file" (in VSS parlance) essentially moves it from one
        # project to another (and could potentially change its name)
        parent_info = self._get_or_create_project(project)

        # remove filename from old project
        old_file = self._get_or_create_file(old_name)
        old_file.remove_project(parent_info)
        parent_info.remove_item(old_file)

        # add filename to new project
        new_file = self._get_or_create_file(new_name)
        new_file.add_project(parent_info, new_name.logical_name)
        parent_info.add_item(new_file)

        # preserve branch point version if already set by file-level Branch
        if new_file.version <= 1:
            new_file.version = old_file.version
        return new_file

    def move_project_from(self, project: ItemName, subproject: ItemName,
                          old_project_spec: str) -> ProjectInfo:
        assert subproject.is_project
        parent_info = self._get_or_create_project(project)
        sub_info = self._get_or_create_project(subproject)
        sub_info.logical_name = subproject.logical_name
        sub_info.parent = parent_info
        return sub_info

    def move_project_to(self, project: ItemName, subproject: ItemName,
                        new_project_spec: str) -> ProjectInfo:
        sub_info = self._get_or_create_project(subproject)
        last_slash = new_project_spec.rfind("/")
        if last_slash > 0:
            parent_info = self._resolve_project_spec(new_project_spec[:last_slash])
            if parent_info is not None:
                # only propagate destroyed flag; the parent is updated by
                # move_project_from
                sub_info.destroyed = sub_info.destroyed or parent_info.destroyed
            elif sub_info.parent is None:
                # don't mark destroyed if MoveFrom already re-parented it
                sub_info.destroyed = True
        return sub_info

    def project_contains_logical_name(self, project: ItemName,
                                      name: ItemName) -> bool:
        return self._get_or_create_project(project).contains_logical_name(
            name.logical_name)

    def _get_or_create_project(self, name: ItemName) -> ProjectInfo:
        info = self._projects.get(name.physical_name)
        if info is None:
            info = ProjectInfo(name.physical_name, name.logical_name)
            self._projects[name.physical_name] = info
        return info

    def _get_or_create_file(self, name: ItemName) -> FileInfo:
        info = self._files.get(name.physical_name)
        if info is None:
            info = FileInfo(name.physical_name, name.logical_name)
            self._files[name.physical_name] = info
        return info

    def _resolve_project_spec(self, project_spec: str) -> Optional[ProjectInfo]:
        # add missing slash if project spec starts with $ but not $/
        if project_spec.startswith("$") and not project_spec.startswith("$/"):
            project_spec = "$/" + project_spec[1:]

        if not project_spec.startswith("$/"):
            raise ValueError(
                f'Project spec must start with $/ but was "{project_spec}"')

        # longest root path first so $/ProjectX matches before $/Project
        for root in sorted(self._roots.values(),
                           key=lambda r: len(r.original_vss_path),
                           reverse=True):
            root_path = root.original_vss_path
            if not project_spec.startswith(root_path):
                continue
            # exact match - the spec is the root itself
            if project_spec == root_path:
                return root

            root_length = len(root_path)
            if not root_path.endswith("/"):
                root_length += 1
            info = root
            for sub_name in project_spec[root_length:].split("/"):
                for item in info.items:
                    if (isinstance(item, ProjectInfo)
                            and item.logical_name.casefold() == sub_name.casefold()):
                        info = item
                        break
                else:
                    return None
            return info
        return None


def get_working_path(working_root: str, vss_path: str) -> str:
    """Port of GetWorkingPath (ExportProjectToGitRoot = false)."""
    if vss_path == "$":
        return working_root
    if vss_path.startswith("$/"):
        vss_path = vss_path[2:]
    return os.path.join(working_root,
                        vss_path.replace(PROJECT_SEPARATOR, os.sep))
