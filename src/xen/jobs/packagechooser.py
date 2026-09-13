"""
Package Chooser Job - optional package group/package selection.

Resolves a config-driven list of optional package groups into a flat
package list, honoring per-group hidden/selected/critical flags and
$LOCALE substitution, then hands the result to PackagesJob via
context.selections["optional_packages"].

Two kinds of group:

- Bundle groups: ``packages`` is a flat list of raw package name strings
  (e.g. "firefox"). All-or-nothing - one checkbox for the whole group.
- Multi-choice groups: ``packages`` is a list of {package, description}
  dicts (e.g. "Graphics & Design" containing gimp/krita/blender/...).
  Each package gets its own checkbox.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "group"


class PackageChooserJob(BaseJob):
    """
    Optional package selection job.

    Configuration (from xen.yaml):
        groups_file: str  # path to a YAML file of package groups; Engine
                           # resolves this to an absolute path at load time.

    Reads selections written by PackageChooserView.qml (via EngineBridge):

    - context.selections["packagechooser_selection"] (dict[group_id, bool]):
      bundle-group toggles. Groups with no entry fall back to their own
      "selected" default.
    - context.selections["packagechooser_packages"]
      (dict["{group_id}:{package}", bool]): individual package toggles
      within multi-choice groups. Packages with no entry default to
      unselected.

    "critical" groups are always fully selected regardless of input.
    """

    name = "packagechooser"
    description = "Optional package selection"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._groups: list[dict[str, Any]] = []

    def _load_groups(self) -> JobResult:
        groups_file = self._config.get("groups_file")
        if not groups_file:
            return JobResult.fail("packagechooser job requires a 'groups_file' config key")

        path = Path(groups_file)
        if not path.exists():
            return JobResult.fail(f"Package groups file not found: {path}")

        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return JobResult.fail(f"Invalid YAML in {path}: {e}")

        if not isinstance(data, list):
            return JobResult.fail(f"Package groups file must contain a list of groups: {path}")

        self._groups = data
        return JobResult.ok(f"Loaded {len(data)} package groups")

    def _locale_lang(self, context: JobContext) -> str:
        locale = str(context.selections.get("locale", "en_US.UTF-8"))
        return locale.split(".")[0].split("_")[0].lower()

    def _resolve_package_name(self, entry: Any, lang: str) -> str | None:
        name: str | Any
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = entry.get("package")
        else:
            return None

        if not name:
            return None

        name = str(name)
        if lang == "en" and "$LOCALE" in name:
            return None

        return name.replace("$LOCALE", lang)

    def _group_id(self, group: dict[str, Any]) -> str:
        return str(group.get("id") or _slugify(str(group.get("name", ""))))

    def _is_group_selected(self, group: dict[str, Any], overrides: dict[str, bool]) -> bool:
        if group.get("critical", False):
            return True

        group_id = self._group_id(group)
        if group_id in overrides:
            return bool(overrides[group_id])

        return bool(group.get("selected", False))

    @staticmethod
    def _is_multi_choice(packages: list[Any]) -> bool:
        """Multi-choice groups list {package, description} dicts (e.g.
        "Graphics & Design"); bundle groups list raw name strings
        (e.g. "firefox") and are all-or-nothing."""
        return any(isinstance(entry, dict) for entry in packages)

    def _package_key(self, group_id: str, entry: Any) -> str | None:
        raw_name = entry.get("package") if isinstance(entry, dict) else entry
        if not raw_name:
            return None
        return f"{group_id}:{raw_name}"

    def _selected_entries(
        self,
        group: dict[str, Any],
        packages: list[Any],
        package_overrides: dict[str, bool],
    ) -> list[Any]:
        """Entries to install from a multi-choice group's package list."""
        if group.get("critical", False):
            return packages

        group_id = self._group_id(group)
        entries = []
        for entry in packages:
            key = self._package_key(group_id, entry)
            if key is None:
                continue
            default = entry.get("selected", False) if isinstance(entry, dict) else False
            if package_overrides.get(key, default):
                entries.append(entry)
        return entries

    def run(self, context: JobContext) -> JobResult:
        # Offline: no repos to fetch optional packages from at all - skip
        # rather than resolve a list nothing can actually install.
        if context.selections.get("has_internet") is False:
            context.selections["optional_packages"] = []
            return JobResult.ok("Skipped (offline)")

        context.report_progress(0, "Loading optional package list...")

        load_result = self._load_groups()
        if not load_result.success:
            return load_result

        overrides = context.selections.get("packagechooser_selection", {})
        if not isinstance(overrides, dict):
            overrides = {}

        package_overrides = context.selections.get("packagechooser_packages", {})
        if not isinstance(package_overrides, dict):
            package_overrides = {}

        lang = self._locale_lang(context)
        selected_packages: list[str] = []

        for group in self._groups:
            if not isinstance(group, dict) or group.get("hidden", False):
                continue

            packages = group.get("packages")
            if not isinstance(packages, list):
                logger.warning("Group %r has no 'packages' list, skipping", group.get("name"))
                continue

            if self._is_multi_choice(packages):
                entries = self._selected_entries(group, packages, package_overrides)
            elif self._is_group_selected(group, overrides):
                entries = packages
            else:
                entries = []

            for entry in entries:
                package_name = self._resolve_package_name(entry, lang)
                if package_name and package_name not in selected_packages:
                    selected_packages.append(package_name)

        context.selections["optional_packages"] = selected_packages
        context.report_progress(100, f"{len(selected_packages)} optional packages selected")

        return JobResult.ok(
            f"Resolved {len(selected_packages)} optional packages",
            data={"packages_selected": selected_packages},
        )

    def estimate_duration(self) -> int:
        return 1
