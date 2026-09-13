"""
Integration test: packagechooser -> packages handoff, and systemupdate
ordering, via a real Engine loading real job classes. No Qt/QML involved.
All subprocess calls are mocked - nothing here touches a real disk or
package manager.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import yaml

from xen.core.engine import Engine
from xen.jobs.base import JobContext, JobResult
from xen.jobs.packages import PackagesJob


def _write_chooser_config(tmp_path: Path) -> Path:
    groups_file = tmp_path / "packages-optional.yaml"
    groups_file.write_text(
        yaml.safe_dump(
            [
                {"id": "firefox", "name": "Firefox", "selected": True, "packages": ["firefox"]},
                {"id": "extras", "name": "Extras", "selected": False, "packages": ["gimp"]},
            ]
        )
    )

    config = {
        "version": "1.0",
        "branding": {"name": "TestOS"},
        "jobs": [
            {"name": "packagechooser", "config": {"groups_file": "packages-optional.yaml"}},
            {"name": "packages", "config": {"mode": "essential"}},
        ],
    }
    config_path = tmp_path / "xen.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path


class TestPackageChooserToPackagesHandoff:
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_selected_packages_flow_into_install(
        self, mock_update: Mock, mock_install: Mock, tmp_path: Path
    ) -> None:
        mock_update.return_value = JobResult.ok("Updated")
        mock_install.return_value = JobResult.ok("Installed")

        config_path = _write_chooser_config(tmp_path)
        engine = Engine.from_config_file(config_path)

        with tempfile.TemporaryDirectory() as target_root:
            context = JobContext(target_root=target_root)
            context.on_progress = MagicMock()

            success = engine.run_all(context)

            assert success is True
            mock_install.assert_called_once()
            installed_packages = mock_install.call_args[0][0]
            assert "firefox" in installed_packages
            assert "gimp" not in installed_packages


class TestSystemUpdateBeforePackages:
    @patch("xen.jobs.packages.NetworkHelper.check_internet_connectivity")
    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_offline_skips_systemupdate_and_package_install_cleanly(
        self,
        mock_update: Mock,
        mock_install: Mock,
        mock_systemupdate_check: Mock,
        mock_packages_check: Mock,
        tmp_path: Path,
    ) -> None:
        """A single profile now runs online or offline. Going offline must
        skip systemupdate cleanly and never abort the rest of the install -
        but it must also skip the package install itself: the live ISO ships
        no pacman sync databases or cached package archives, so there is no
        local source to install anything new from offline."""
        mock_systemupdate_check.return_value = False
        mock_packages_check.return_value = False
        mock_update.return_value = JobResult.ok("Updated")
        mock_install.return_value = JobResult.ok("Installed")

        config = {
            "version": "1.0",
            "branding": {"name": "TestOS"},
            "jobs": [
                {"name": "systemupdate", "config": {}},
                {"name": "packages", "config": {"mode": "essential"}},
            ],
        }
        config_path = tmp_path / "xen.yaml"
        config_path.write_text(yaml.safe_dump(config))

        engine = Engine.from_config_file(config_path)

        with tempfile.TemporaryDirectory() as target_root:
            context = JobContext(target_root=target_root)

            success = engine.run_all(context)

            assert success is True
            mock_update.assert_not_called()
            mock_install.assert_not_called()
