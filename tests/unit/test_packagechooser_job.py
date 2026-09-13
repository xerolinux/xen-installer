"""Unit tests for PackageChooserJob."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import yaml

from xen.jobs.base import JobContext
from xen.jobs.packagechooser import PackageChooserJob


def _write_groups(tmp_path: Path, groups: list[dict[str, Any]]) -> str:
    groups_file = tmp_path / "groups.yaml"
    groups_file.write_text(yaml.safe_dump(groups))
    return str(groups_file)


class TestLoadGroups:
    def test_missing_groups_file_config(self) -> None:
        job = PackageChooserJob()
        result = job._load_groups()
        assert result.success is False
        assert "groups_file" in result.message

    def test_groups_file_not_found(self, tmp_path: Path) -> None:
        job = PackageChooserJob({"groups_file": str(tmp_path / "nope.yaml")})
        result = job._load_groups()
        assert result.success is False
        assert "not found" in result.message.lower()

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("- id: a\n  name: [unterminated")
        job = PackageChooserJob({"groups_file": str(bad_file)})
        result = job._load_groups()
        assert result.success is False

    def test_valid_groups(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [{"id": "browsers", "name": "Browsers", "packages": ["firefox"]}],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        result = job._load_groups()
        assert result.success is True
        assert job._groups == [{"id": "browsers", "name": "Browsers", "packages": ["firefox"]}]


class TestLocaleSubstitution:
    def test_locale_lang_strips_encoding(self) -> None:
        job = PackageChooserJob()
        context = JobContext(selections={"locale": "fr_FR.UTF-8"})
        assert job._locale_lang(context) == "fr"

    def test_locale_lang_default(self) -> None:
        job = PackageChooserJob()
        context = JobContext()
        assert job._locale_lang(context) == "en"

    def test_resolve_package_name_plain_string(self) -> None:
        job = PackageChooserJob()
        assert job._resolve_package_name("firefox-i18n-$LOCALE", "fr") == "firefox-i18n-fr"

    def test_resolve_package_name_dict_entry(self) -> None:
        job = PackageChooserJob()
        entry = {"package": "floorp", "description": "Firefox fork"}
        assert job._resolve_package_name(entry, "en") == "floorp"

    def test_resolve_package_name_missing_package_key(self) -> None:
        job = PackageChooserJob()
        assert job._resolve_package_name({"description": "no package key"}, "en") is None

    def test_resolve_package_name_unknown_type(self) -> None:
        job = PackageChooserJob()
        assert job._resolve_package_name(123, "en") is None  # type: ignore[arg-type]

    def test_resolve_package_name_locale_dropped_for_english(self) -> None:
        job = PackageChooserJob()
        assert job._resolve_package_name("firefox-i18n-$LOCALE", "en") is None

    def test_resolve_package_name_non_locale_package_not_dropped_for_english(self) -> None:
        job = PackageChooserJob()
        assert job._resolve_package_name("firefox", "en") == "firefox"


class TestGroupSelection:
    def test_critical_always_selected(self) -> None:
        job = PackageChooserJob()
        group = {"id": "base", "critical": True, "selected": False}
        assert job._is_group_selected(group, {}) is True

    def test_default_selected_true(self) -> None:
        job = PackageChooserJob()
        group = {"id": "firefox", "selected": True}
        assert job._is_group_selected(group, {}) is True

    def test_default_selected_false(self) -> None:
        job = PackageChooserJob()
        group = {"id": "extras", "selected": False}
        assert job._is_group_selected(group, {}) is False

    def test_override_selects_unselected_default(self) -> None:
        job = PackageChooserJob()
        group = {"id": "extras", "selected": False}
        assert job._is_group_selected(group, {"extras": True}) is True

    def test_override_deselects_selected_default(self) -> None:
        job = PackageChooserJob()
        group = {"id": "firefox", "selected": True}
        assert job._is_group_selected(group, {"firefox": False}) is False

    def test_group_id_falls_back_to_slugified_name(self) -> None:
        job = PackageChooserJob()
        group = {"name": "Other Misc Browsers", "selected": False}
        assert job._is_group_selected(group, {"other-misc-browsers": True}) is True


class TestPackageChooserJobRun:
    def test_run_resolves_selected_packages_only(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [
                {
                    "id": "firefox",
                    "name": "Firefox",
                    "selected": True,
                    "packages": ["firefox", "firefox-i18n-$LOCALE"],
                },
                {
                    "id": "extras",
                    "name": "Extras",
                    "selected": False,
                    "packages": ["gimp"],
                },
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext(selections={"locale": "fr_FR.UTF-8"})

        result = job.run(context)

        assert result.success is True
        assert context.selections["optional_packages"] == ["firefox", "firefox-i18n-fr"]

    def test_run_skips_when_offline(self, tmp_path: Path) -> None:
        """No repos to fetch from offline - skip rather than resolve a list
        nothing can actually install."""
        groups_file = _write_groups(
            tmp_path,
            [{"id": "firefox", "name": "Firefox", "selected": True, "packages": ["firefox"]}],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext(selections={"has_internet": False})

        result = job.run(context)

        assert result.success is True
        assert context.selections["optional_packages"] == []

    def test_run_hidden_group_excluded(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [
                {
                    "id": "hidden-grp",
                    "hidden": True,
                    "selected": True,
                    "packages": ["should-not-appear"],
                }
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext()

        result = job.run(context)

        assert result.success is True
        assert context.selections["optional_packages"] == []

    def test_run_critical_group_forced(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [
                {
                    "id": "mandatory",
                    "critical": True,
                    "selected": False,
                    "packages": ["archlinux-keyring"],
                }
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext(selections={"packagechooser_selection": {"mandatory": False}})

        job.run(context)

        assert context.selections["optional_packages"] == ["archlinux-keyring"]

    def test_run_selection_override_from_context(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [{"id": "extras", "selected": False, "packages": ["gimp"]}],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext(selections={"packagechooser_selection": {"extras": True}})

        job.run(context)

        assert context.selections["optional_packages"] == ["gimp"]

    def test_run_deduplicates_packages(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [
                {"id": "a", "selected": True, "packages": ["shared-pkg"]},
                {"id": "b", "selected": True, "packages": ["shared-pkg"]},
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext()

        job.run(context)

        assert context.selections["optional_packages"] == ["shared-pkg"]

    def test_run_multi_choice_group_selects_only_chosen_packages(self, tmp_path: Path) -> None:
        """Regression: a multi-choice group (dict-style packages, e.g.
        "Graphics & Design") must let the user pick individual packages,
        not just toggle the whole group on/off as one bundle."""
        groups_file = _write_groups(
            tmp_path,
            [
                {
                    "id": "graphics-design",
                    "name": "Graphics & Design",
                    "packages": [
                        {"package": "gimp", "description": "Image editor"},
                        {"package": "krita", "description": "Painting app"},
                        {"package": "blender", "description": "3D suite"},
                    ],
                }
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext(
            selections={
                "packagechooser_packages": {
                    "graphics-design:gimp": True,
                    "graphics-design:krita": True,
                    "graphics-design:blender": False,
                }
            }
        )

        job.run(context)

        assert context.selections["optional_packages"] == ["gimp", "krita"]

    def test_run_multi_choice_group_defaults_to_none_selected(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [
                {
                    "id": "graphics-design",
                    "packages": [{"package": "gimp", "description": "Image editor"}],
                }
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext()

        job.run(context)

        assert context.selections["optional_packages"] == []

    def test_run_multi_choice_critical_group_forces_all_packages(self, tmp_path: Path) -> None:
        groups_file = _write_groups(
            tmp_path,
            [
                {
                    "id": "essentials",
                    "critical": True,
                    "packages": [
                        {"package": "gimp", "description": "Image editor"},
                        {"package": "krita", "description": "Painting app"},
                    ],
                }
            ],
        )
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext(
            selections={"packagechooser_packages": {"essentials:gimp": False}}
        )

        job.run(context)

        assert context.selections["optional_packages"] == ["gimp", "krita"]

    def test_run_missing_groups_file_fails(self, tmp_path: Path) -> None:
        job = PackageChooserJob({"groups_file": str(tmp_path / "nope.yaml")})
        context = JobContext()

        result = job.run(context)

        assert result.success is False

    def test_run_group_without_packages_key_skipped(self, tmp_path: Path) -> None:
        groups_file = _write_groups(tmp_path, [{"id": "broken", "selected": True}])
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext()

        result = job.run(context)

        assert result.success is True
        assert context.selections["optional_packages"] == []

    def test_run_reports_progress(self, tmp_path: Path) -> None:
        groups_file = _write_groups(tmp_path, [])
        job = PackageChooserJob({"groups_file": groups_file})
        context = JobContext()
        context.on_progress = Mock()

        job.run(context)

        assert context.on_progress.called

    def test_estimate_duration(self) -> None:
        job = PackageChooserJob()
        assert job.estimate_duration() == 1
