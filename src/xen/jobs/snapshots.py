"""
Snapshots Job - Snapper, grub-btrfs, and btrfs-assistant configuration.

No-ops entirely when the root filesystem isn't Btrfs. Values and unit
files are copied from CachyOS's real, shipped configuration
(cachyos-snapper-support, grub-btrfs-support in
github.com/CachyOS/CachyOS-PKGBUILDS) - XeroLinux has no custom repo for
those two glue packages, so their effect is replicated here directly
instead. Must run after `packages` (needs snapper/grub-btrfs already
installed) and before `bootloader` (so its grub-mkconfig call already
picks up the grub-btrfs integration).
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)

_HOOKS_LINE = re.compile(r"^HOOKS=\((?P<hooks>.*)\)\s*$", re.MULTILINE)

# grub-btrfs's 41_snapshots-btrfs mounts root via /dev/disk/by-uuid to
# enumerate snapshots for the boot menu - unreliable mid-install, and
# there are none to find yet anyway (finalize.py takes the first one
# after bootloader.py's grub-mkconfig runs). Disabled here, restored by
# finalize.py. Renaming alone doesn't stop grub-mkconfig from running it
# (its file filter doesn't exclude arbitrary suffixes) - the executable
# bit must also be stripped, since `test -x` is the real gate it checks.
_SNAPSHOT_DETECTION_SCRIPT = "41_snapshots-btrfs"
_SNAPSHOT_DETECTION_SCRIPT_DISABLED = "41_snapshots-btrfs.xen-disabled"

_SNAPPER_TEMPLATE_NAME = "xen-root"

_SNAPPER_TEMPLATE_CONTENT = """# subvolume to snapshot
SUBVOLUME="/"

# filesystem type
FSTYPE="btrfs"

# btrfs qgroup for space aware cleanup algorithms
QGROUP=""

# fraction or absolute size of the filesystems space the snapshots may use
SPACE_LIMIT="0.5"

# fraction or absolute size of the filesystems space that should be free
FREE_LIMIT="0.2"

# users and groups allowed to work with config
ALLOW_USERS=""
ALLOW_GROUPS=""

# sync users and groups from ALLOW_USERS and ALLOW_GROUPS to .snapshots
# directory
SYNC_ACL="no"

# start comparing pre- and post-snapshot in background after creating
# post-snapshot
BACKGROUND_COMPARISON="yes"

# run daily number cleanup
NUMBER_CLEANUP="yes"

# limit for number cleanup
NUMBER_MIN_AGE="1800"
NUMBER_LIMIT="50"
NUMBER_LIMIT_IMPORTANT="15"

# create hourly snapshots
TIMELINE_CREATE="no"

# cleanup hourly snapshots after some time
TIMELINE_CLEANUP="yes"

# limits for timeline cleanup
TIMELINE_MIN_AGE="1800"
TIMELINE_LIMIT_HOURLY="5"
TIMELINE_LIMIT_DAILY="7"
TIMELINE_LIMIT_WEEKLY="0"
TIMELINE_LIMIT_MONTHLY="0"
TIMELINE_LIMIT_YEARLY="0"

# cleanup empty pre-post-pairs
EMPTY_PRE_POST_CLEANUP="yes"

# limits for empty pre-post-pair cleanup
EMPTY_PRE_POST_MIN_AGE="1800"
"""

_GRUB_BTRFS_PATH_UNIT = """[Unit]
Description=Monitors for new snapshots

[Path]
PathModified=/.snapshots

[Install]
WantedBy=multi-user.target
"""

_GRUB_BTRFS_SERVICE_UNIT = """[Unit]
Description=Regenerate grub-btrfs.cfg

[Service]
Type=oneshot
Environment="PATH=/sbin:/bin:/usr/sbin:/usr/bin"
EnvironmentFile=/etc/default/grub-btrfs/config
ExecStart=bash -c 'if [[ -z $(/usr/bin/findmnt -n / | /usr/bin/grep --fixed-strings ".snapshots") ]]; then if [ -s "${GRUB_BTRFS_GRUB_DIRNAME:-/boot/grub}/grub-btrfs.cfg" ]; then /etc/grub.d/41_snapshots-btrfs; else ${GRUB_BTRFS_MKCONFIG:-grub-mkconfig} -o ${GRUB_BTRFS_GRUB_DIRNAME:-/boot/grub}/grub.cfg; fi; fi'
"""


class SnapshotsJob(BaseJob):
    """Configures Snapper (with the "root" config), grub-btrfs's
    boot-menu integration, and the overlayfs boot-into-snapshot hook.
    btrfs-assistant needs no configuration - it's a GUI reading the same
    Snapper config, installed by packages.py's btrfs_packages list."""

    name = "snapshots"
    description = "Configure Snapper and grub-btrfs"

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: snapshot configuration (dry run)")

        if context.selections.get("filesystem") != "btrfs":
            context.report_progress(100, "Not Btrfs, nothing to configure")
            return JobResult.ok("Not Btrfs, skipping snapshot configuration")

        target_root = context.target_root

        context.report_progress(10, "Writing Snapper config template...")
        result = self._write_snapper_template(target_root)
        if not result.success:
            return result

        context.report_progress(30, "Creating Snapper root config...")
        result = self._create_snapper_config(target_root)
        if not result.success:
            return result

        context.report_progress(50, "Installing grub-btrfs boot-menu units...")
        result = self._write_grub_btrfs_units(target_root)
        if not result.success:
            return result

        context.report_progress(70, "Enabling snapshot services...")
        result = self._enable_units(target_root)
        if not result.success:
            return result

        context.report_progress(85, "Adding grub-btrfs-overlayfs hook...")
        result = self._add_overlayfs_hook(target_root)
        if not result.success:
            return result

        self._disable_snapshot_detection_script(target_root)

        context.report_progress(100, "Snapshot configuration complete")
        return JobResult.ok("Snapper and grub-btrfs configured")

    def _disable_snapshot_detection_script(self, target_root: str) -> None:
        """Renamed and stripped of its executable bit (see module comment
        above) so grub-mkconfig never runs it - finalize.py restores both
        once a real snapshot exists. Non-fatal."""
        script = Path(target_root) / "etc" / "grub.d" / _SNAPSHOT_DETECTION_SCRIPT
        if not script.exists():
            return
        disabled = script.with_name(_SNAPSHOT_DETECTION_SCRIPT_DISABLED)
        try:
            script.rename(disabled)
            disabled.chmod(0o644)
        except OSError as e:
            logger.warning(f"Failed to disable {script}: {e}")
            return
        logger.info(f"Disabled {script} until finalize.py restores it")

    def _write_snapper_template(self, target_root: str) -> JobResult:
        template_path = (
            Path(target_root) / "etc" / "snapper" / "config-templates" / _SNAPPER_TEMPLATE_NAME
        )
        try:
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(_SNAPPER_TEMPLATE_CONTENT, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {template_path}: {e}", error_code=130)
        return JobResult.ok("Snapper template written")

    def _create_snapper_config(self, target_root: str) -> JobResult:
        # --no-dbus: snapperd isn't running inside arch-chroot.
        try:
            result = subprocess.run(
                [
                    "arch-chroot",
                    target_root,
                    "snapper",
                    "--no-dbus",
                    "-c",
                    "root",
                    "create-config",
                    "--template",
                    _SNAPPER_TEMPLATE_NAME,
                    "/",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail("snapper create-config timed out", error_code=131)
        except FileNotFoundError:
            return JobResult.fail("arch-chroot not found", error_code=132)

        if result.returncode != 0:
            return JobResult.fail(
                f"snapper create-config failed: {result.stderr}", error_code=133
            )
        return JobResult.ok("Snapper root config created")

    def _write_grub_btrfs_units(self, target_root: str) -> JobResult:
        systemd_dir = Path(target_root) / "etc" / "systemd" / "system"
        try:
            systemd_dir.mkdir(parents=True, exist_ok=True)
            (systemd_dir / "grub-btrfs-snapper.path").write_text(
                _GRUB_BTRFS_PATH_UNIT, encoding="utf-8"
            )
            (systemd_dir / "grub-btrfs-snapper.service").write_text(
                _GRUB_BTRFS_SERVICE_UNIT, encoding="utf-8"
            )
        except OSError as e:
            return JobResult.fail(f"Failed to write grub-btrfs units: {e}", error_code=134)
        return JobResult.ok("grub-btrfs units written")

    def _enable_units(self, target_root: str) -> JobResult:
        for unit in ("snapper-cleanup.timer", "grub-btrfs-snapper.path"):
            try:
                result = subprocess.run(
                    ["arch-chroot", target_root, "systemctl", "enable", unit],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                return JobResult.fail(f"systemctl enable {unit} timed out", error_code=135)
            except FileNotFoundError:
                return JobResult.fail("arch-chroot not found", error_code=136)

            if result.returncode != 0:
                logger.warning(f"systemctl enable {unit} failed: {result.stderr}")
        return JobResult.ok("Snapshot services enabled")

    def _add_overlayfs_hook(self, target_root: str) -> JobResult:
        """Lets you boot directly into a read-only snapshot via an
        overlay. Runs after packages (grub-btrfs's overlayfs hook script
        must already exist on disk) - systemconfig.py's own mkinitcpio
        pass runs before packages and can't reference it yet, so this
        does its own HOOKS edit and its own rebuild."""
        conf_path = Path(target_root) / "etc" / "mkinitcpio.conf"
        try:
            content = conf_path.read_text(encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to read {conf_path}: {e}", error_code=137)

        match = _HOOKS_LINE.search(content)
        if not match:
            return JobResult.fail(f"No HOOKS=(...) line found in {conf_path}", error_code=138)

        hooks = match.group("hooks").split()
        if "grub-btrfs-overlayfs" in hooks:
            return JobResult.ok("grub-btrfs-overlayfs hook already present")

        hooks.append("grub-btrfs-overlayfs")
        new_line = f"HOOKS=({' '.join(hooks)})"
        new_content = content[: match.start()] + new_line + content[match.end() :]

        try:
            conf_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {conf_path}: {e}", error_code=139)

        try:
            result = subprocess.run(
                ["arch-chroot", target_root, "mkinitcpio", "-P"],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail("mkinitcpio -P timed out", error_code=140)
        except FileNotFoundError:
            return JobResult.fail("arch-chroot not found", error_code=141)

        if result.returncode != 0:
            return JobResult.fail(f"mkinitcpio -P failed: {result.stderr}", error_code=142)

        return JobResult.ok("grub-btrfs-overlayfs hook added")

    def estimate_duration(self) -> int:
        return 30
