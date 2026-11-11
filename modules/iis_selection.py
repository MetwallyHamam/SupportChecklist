from __future__ import annotations

import ntpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class IISChildSelection:
    """Derived child-app behavior for the currently selected IIS sites."""

    visible_keys: frozenset[str]
    backup_keys: frozenset[str]
    cleanup_keys: frozenset[str]


def is_windows_path_within(path: str, parent: str) -> bool:
    """Return whether *path* is equal to or below *parent* on Windows."""
    child = ntpath.normcase(ntpath.abspath(str(path).strip()))
    root = ntpath.normcase(ntpath.abspath(str(parent).strip()))
    if not child or not root:
        return False
    try:
        return ntpath.commonpath((child, root)) == root
    except ValueError:
        # Different drives cannot share a physical root.
        return False


def site_child_selection(
    selected_sites: Mapping[str, str],
    applications: Iterable[Mapping[str, object]],
) -> IISChildSelection:
    """Select every child for cleanup, but back up only children outside the site root."""
    visible: set[str] = set()
    external: set[str] = set()
    for app in applications:
        site_name = str(app.get("site", ""))
        site_root = str(selected_sites.get(site_name, ""))
        app_path = str(app.get("physical_path", ""))
        if not site_root:
            continue
        key = f"{site_name}|{app.get('path', '')}"
        visible.add(key)
        if app_path and not is_windows_path_within(app_path, site_root):
            external.add(key)
    return IISChildSelection(
        visible_keys=frozenset(visible),
        backup_keys=frozenset(external),
        cleanup_keys=frozenset(visible),
    )


def external_child_app_keys(
    selected_sites: Mapping[str, str],
    applications: Iterable[Mapping[str, object]],
) -> set[str]:
    """Compatibility helper returning children stored outside selected site roots."""
    return set(site_child_selection(selected_sites, applications).backup_keys)
