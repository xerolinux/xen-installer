"""Validate the XeroLinux example configs (regular + nVidia).

Each profile now runs online or offline from the same file - see
xerolinux.yaml's header comment. packagechooser/systemupdate are always
present in the job list; they skip themselves at runtime based on the
"has_internet" selection (see PackageChooserJob.run/SystemUpdateJob.run).
"""

from pathlib import Path

import yaml

from xen.core.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
NVIDIA_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux-nvidia.yaml"
PACKAGES_OPTIONAL = PROJECT_ROOT / "config" / "examples" / "packages-optional.yaml"

ALL_CONFIGS = [XEROLINUX_CONFIG, NVIDIA_CONFIG]


class TestXerolinuxConfig:
    def test_loads_without_error(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        assert len(engine.jobs) > 0

    def test_includes_packagechooser_and_systemupdate(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        job_names = engine.get_job_names()
        assert "packagechooser" in job_names
        assert "systemupdate" in job_names

    def test_systemupdate_runs_before_packages(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        job_names = engine.get_job_names()
        assert job_names.index("systemupdate") < job_names.index("packages")

    def test_packagechooser_groups_file_resolved_and_exists(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        job = next(j for j in engine.jobs if j.name == "packagechooser")
        groups_file = Path(job._config["groups_file"])
        assert groups_file.is_absolute()
        assert groups_file.exists()

    def test_includes_systemconfig_after_install(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        job_names = engine.get_job_names()
        assert job_names.index("install") < job_names.index("systemconfig")
        assert job_names.index("systemconfig") < job_names.index("users")


class TestAllXerolinuxConfigsMatchCalamaresJobOrdering:
    """Regression: locale.py used to run before partition/install, writing
    locale.conf/timezone/keyboard/hwclock to a target that wasn't mounted
    yet - the files landed on the live session's own filesystem and got
    buried once partition later mounted the real disk over that path.

    Also locks in the "before" (shellprocess@before) and "removeuser"
    steps ported from Calamares's real exec sequence, at the positions
    their actions actually require.
    """

    def test_locale_runs_after_target_is_mounted_and_populated(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert job_names.index("partition") < job_names.index("locale")
            assert job_names.index("install") < job_names.index("locale")

    def test_before_runs_after_install_and_before_systemconfig(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert job_names.index("install") < job_names.index("before")
            assert job_names.index("before") < job_names.index("systemconfig")

    def test_luksbootkeyfile_runs_after_install_and_before_systemconfig(self) -> None:
        """Must run before systemconfig's mkinitcpio rebuild, so the keyfile
        it writes exists on disk in time to be embedded via FILES=."""
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert job_names.index("install") < job_names.index("luksbootkeyfile")
            assert job_names.index("luksbootkeyfile") < job_names.index("systemconfig")

    def test_snapshots_runs_after_packages_and_before_bootloader(self) -> None:
        """Must run after packages (needs snapper/grub-btrfs already
        installed) and before bootloader (so its grub-mkconfig call picks
        up the grub-btrfs integration)."""
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert job_names.index("packages") < job_names.index("snapshots")
            assert job_names.index("snapshots") < job_names.index("bootloader")

    def test_removeuser_runs_before_users(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert job_names.index("removeuser") < job_names.index("users")

    def test_services_include_display_manager(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job = next(j for j in engine.jobs if j.name == "services")
            unit_names = [u["name"] for u in job._config["units"]]
            assert "plasmalogin" in unit_names


class TestAllXerolinuxConfigsUseGrub:
    """XeroLinux's real Calamares bootloader.conf uses GRUB, not systemd-boot."""

    def test_bootloader_type_is_grub(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job = next(j for j in engine.jobs if j.name == "bootloader")
            assert job._config["type"] == "grub", f"{config_path.name} should use grub"

    def test_bootloader_job_actually_resolves_to_grub(self) -> None:
        """Regression: the yaml's type: grub sat in job._config but
        run()/validate() only ever read context.selections["bootloader"]
        (a key nothing sets), so every real install silently ran
        systemd-boot regardless of this config value. Assert the value the
        job actually uses, not just the raw yaml content."""
        from xen.jobs.base import JobContext

        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job = next(j for j in engine.jobs if j.name == "bootloader")
            resolved = job._get_bootloader_type(JobContext())
            assert resolved == "grub", f"{config_path.name}'s bootloader job resolves to grub"


class TestDualBootAndPartitionConfig:
    def test_target_gets_os_prober_and_ntfs_3g(self) -> None:
        from xen.jobs.base import JobContext

        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job = next(j for j in engine.jobs if j.name == "packages")
            packages = job._get_package_list(JobContext(selections={"filesystem": "ext4"}))
            assert {"os-prober", "ntfs-3g"} <= set(packages), config_path.name
            assert not {"os-prober", "ntfs-3g"} & set(job._config.get("remove", []))

    def test_partition_job_carries_subvolumes_and_min_install_size(self) -> None:
        expected = ["@", "@home", "@root", "@srv", "@cache", "@tmp", "@log", "@swap", "@snapshots"]
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job = next(j for j in engine.jobs if j.name == "partition")
            assert job._config["min_install_gib"] == 25
            assert [sv["name"] for sv in job._config["btrfs_subvolumes"]] == expected


class TestAllXerolinuxConfigsHaveServicesAndFinalize:
    def test_services_and_finalize_jobs_present(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert "services" in job_names, f"{config_path.name} missing services job"
            assert "finalize" in job_names, f"{config_path.name} missing finalize job"

    def test_services_runs_before_finalize_before_finished(self) -> None:
        for config_path in ALL_CONFIGS:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()
            assert job_names.index("services") < job_names.index("finalize")
            assert job_names.index("finalize") < job_names.index("finished")


class TestRegularProfileConfig:
    """Regular (Intel/AMD) profile: nouveau blacklist, no nvidia services."""

    def test_finalize_ends_with_nouveau(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        job = next(j for j in engine.jobs if j.name == "finalize")
        assert job._config["commands"][-1] == "/usr/local/bin/nouveau"

    def test_no_nvidia_services(self) -> None:
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        job = next(j for j in engine.jobs if j.name == "services")
        unit_names = [u["name"] for u in job._config["units"]]
        assert "nvidia-suspend" not in unit_names
        assert "nvidia-hibernate" not in unit_names
        assert "nvidia-resume" not in unit_names


class TestNvidiaProfileConfig:
    """nVidia profile: nvramfs (mkinitcpio + GRUB modeset), nvidia power services."""

    def test_loads_without_error(self) -> None:
        engine = Engine.from_config_file(NVIDIA_CONFIG)
        assert len(engine.jobs) > 0

    def test_finalize_ends_with_nvramfs(self) -> None:
        engine = Engine.from_config_file(NVIDIA_CONFIG)
        job = next(j for j in engine.jobs if j.name == "finalize")
        assert job._config["commands"][-1] == "/usr/local/bin/nvramfs"

    def test_includes_nvidia_power_services_as_mandatory(self) -> None:
        engine = Engine.from_config_file(NVIDIA_CONFIG)
        job = next(j for j in engine.jobs if j.name == "services")
        units = {u["name"]: u for u in job._config["units"]}
        for name in ("nvidia-suspend", "nvidia-hibernate", "nvidia-resume"):
            assert name in units
            assert units[name]["action"] == "enable"
            assert units[name]["mandatory"] is True

    def test_includes_packagechooser_and_systemupdate(self) -> None:
        engine = Engine.from_config_file(NVIDIA_CONFIG)
        job_names = engine.get_job_names()
        assert "packagechooser" in job_names
        assert "systemupdate" in job_names


class TestPackagesOptionalYaml:
    def test_is_a_list_of_groups(self) -> None:
        with PACKAGES_OPTIONAL.open("r", encoding="utf-8") as f:
            groups = yaml.safe_load(f)

        assert isinstance(groups, list)
        assert len(groups) > 0

    def test_every_group_has_unique_id_and_packages(self) -> None:
        with PACKAGES_OPTIONAL.open("r", encoding="utf-8") as f:
            groups = yaml.safe_load(f)

        ids = [g["id"] for g in groups]
        assert len(ids) == len(set(ids))
        for group in groups:
            assert isinstance(group["packages"], list)
            assert len(group["packages"]) > 0
