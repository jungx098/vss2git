"""Case-insensitive path resolution for databases copied from Windows.

Never creates files or directories; it only locates existing ones.
"""

import os
from typing import Dict, List


class PathResolver:
    def __init__(self):
        # directory -> {casefolded name: actual name}
        self._listings: Dict[str, Dict[str, str]] = {}

    def _listing(self, directory: str) -> Dict[str, str]:
        listing = self._listings.get(directory)
        if listing is None:
            listing = {}
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                names = []
            for name in names:
                listing.setdefault(name.casefold(), name)
            self._listings[directory] = listing
        return listing

    def resolve(self, path) -> str:
        """Return the existing spelling of `path`, or `path` if not found."""
        path = os.fspath(path)
        if os.path.exists(path):
            return path

        remaining: List[str] = []
        ancestor = path
        while ancestor and not os.path.exists(ancestor):
            parent, tail = os.path.split(ancestor)
            if parent == ancestor:
                return path
            if tail:
                remaining.insert(0, tail)
            ancestor = parent
        if not ancestor:
            ancestor = os.curdir

        current = ancestor
        for component in remaining:
            actual = self._listing(current).get(component.casefold())
            if actual is None:
                return path
            current = os.path.join(current, actual)
        return current
