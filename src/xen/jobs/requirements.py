"""
System Requirements Checker for Xen Installer.

Provides hardware and system requirement validation before installation.
Each check can be enabled/disabled via configuration.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from xen.i18n.messages import msg
from xen.jobs.gpu import GPUDetector

logger = logging.getLogger(__name__)


class RequirementStatus(Enum):
    """Status of a requirement check."""

    PASS = auto()  # Requirement met
    WARN = auto()  # Recommendation not met (can continue)
    FAIL = auto()  # Requirement not met (cannot continue)
    SKIP = auto()  # Check skipped (not applicable)


@dataclass
class RequirementCheck:
    """Result of a single requirement check."""

    name: str
    description: str
    status: RequirementStatus
    current_value: str = ""
    required_value: str = ""
    recommended_value: str = ""
    details: str = ""  # Used for tooltip on warn/fail icons

    @property
    def passed(self) -> bool:
        """Check if requirement is met (PASS or WARN)."""
        return self.status in (
            RequirementStatus.PASS,
            RequirementStatus.WARN,
            RequirementStatus.SKIP,
        )

    @property
    def is_critical(self) -> bool:
        """Check if this is a critical failure."""
        return self.status == RequirementStatus.FAIL


@dataclass
class RequirementsResult:
    """Result of all requirement checks."""

    checks: list[RequirementCheck] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        """Check if all requirements are met."""
        return all(check.passed for check in self.checks)

    @property
    def can_continue(self) -> bool:
        """Check if installation can continue (no critical failures)."""
        return not any(check.is_critical for check in self.checks)

    @property
    def failures(self) -> list[RequirementCheck]:
        """Get all failed checks."""
        return [c for c in self.checks if c.status == RequirementStatus.FAIL]

    @property
    def warnings(self) -> list[RequirementCheck]:
        """Get all warning checks."""
        return [c for c in self.checks if c.status == RequirementStatus.WARN]

    @property
    def passed_checks(self) -> list[RequirementCheck]:
        """Get all passed checks."""
        return [c for c in self.checks if c.status == RequirementStatus.PASS]


class SystemRequirementsChecker:
    """
    Checks system requirements for installation.

    Validates hardware, boot mode, connectivity, and other prerequisites.
    Only runs checks that are enabled in configuration.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        Initialize the requirements checker.

        Args:
            config: Requirements configuration from xen.yaml
        """
        self.config = config or {}

    def _is_enabled(self, check_name: str) -> bool:
        """Check if a requirement check is enabled in config."""
        check_config = self.config.get(check_name, {})
        # If it's a dict with 'enabled' key, check that
        if isinstance(check_config, dict):
            return check_config.get("enabled", False)
        # Legacy flat config - not enabled in new structure
        return False

    def _get_check_config(self, check_name: str) -> dict[str, Any]:
        """Get configuration for a specific check."""
        check_config = self.config.get(check_name, {})
        if isinstance(check_config, dict):
            return check_config
        return {}

    def check_all(self) -> RequirementsResult:
        """
        Run all enabled requirement checks.

        Returns:
            RequirementsResult with all check results
        """
        result = RequirementsResult()

        # =================================================================
        # Order: CPU → RAM → Storage → GPU → Boot → Network → Power
        # =================================================================

        # 1. CPU checks (cores first, then architecture)
        cpu_config = self.config.get("cpu", {})
        if isinstance(cpu_config, dict):
            # CPU Cores (most important for gaming performance)
            cpu_cores_cfg = cpu_config.get("cpu_cores", {})
            if isinstance(cpu_cores_cfg, dict) and cpu_cores_cfg.get("enabled", True):
                result.checks.append(self._check_cpu_cores(cpu_cores_cfg))

            # CPU Architecture
            cpu_arch_cfg = cpu_config.get("cpu_arch", {})
            if isinstance(cpu_arch_cfg, dict) and cpu_arch_cfg.get("enabled", True):
                result.checks.append(self._check_cpu_architecture_v2(cpu_arch_cfg))

        # Legacy support for old cpu_arch format
        elif self._is_enabled("cpu_arch"):
            result.checks.append(self._check_cpu_architecture())

        # 2. RAM
        if self._is_enabled("ram"):
            result.checks.append(self._check_ram())

        # 3. Storage (Disk)
        if self._is_enabled("disk"):
            result.checks.append(self._check_disk_space())

        # 4. GPU
        if self._is_enabled("gpu"):
            result.checks.append(self._check_gpu())

        # 5. Boot checks (EFI, Secure Boot)
        if self._is_enabled("efi"):
            result.checks.append(self._check_efi_mode())

        if self._is_enabled("secure_boot"):
            result.checks.append(self._check_secure_boot())

        # 6. Network connectivity
        if self._is_enabled("internet"):
            result.checks.append(self._check_internet())

        # 7. Power check (laptops only)
        if self._is_enabled("power"):
            power_check = self._check_power()
            # Only add if not skipped (i.e., it's a laptop)
            if power_check.status != RequirementStatus.SKIP:
                result.checks.append(power_check)

        return result

    def _check_ram(self) -> RequirementCheck:
        """
        Check available RAM.

        Thresholds from config:
        - Below min_gb: FAIL
        - Between min_gb and warn_gb: WARN
        - Above warn_gb: PASS
        """
        cfg = self._get_check_config("ram")
        min_gb = cfg.get("min_gb", 8)
        warn_gb = cfg.get("warn_gb", 16)
        recommended_gb = cfg.get("recommended_gb", 16)

        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # Parse KB value and convert to GB
                        kb = int(line.split()[1])
                        current_gb = kb / (1024 * 1024)
                        break
                else:
                    current_gb = 0

            # Determine status based on thresholds
            if current_gb < min_gb:
                status = RequirementStatus.FAIL
                details = msg(
                    "Insufficient RAM: {current} GB detected, minimum {min} GB required",
                    current=f"{current_gb:.1f}",
                    min=min_gb,
                )
            elif current_gb < warn_gb:
                status = RequirementStatus.WARN
                details = msg(
                    "RAM below recommended: {current} GB detected, {recommended} GB recommended",
                    current=f"{current_gb:.1f}",
                    recommended=warn_gb,
                )
            else:
                status = RequirementStatus.PASS
                details = msg("Sufficient RAM for optimal performance")

            return RequirementCheck(
                name="ram",
                description=msg("Memory (RAM)"),
                status=status,
                current_value=f"{current_gb:.1f} GB",
                required_value=f"{min_gb} GB",
                recommended_value=f"{recommended_gb} GB",
                details=details,
            )

        except Exception as e:
            logger.warning(f"Could not check RAM: {e}")
            return RequirementCheck(
                name="ram",
                description=msg("Memory (RAM)"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check RAM: {error}", error=e),
            )

    def _check_disk_space(self) -> RequirementCheck:
        """
        Check available disk space.

        Finds the largest block device and checks against requirements.
        """
        cfg = self._get_check_config("disk")
        min_gb = cfg.get("min_gb", 60)
        recommended_gb = cfg.get("recommended_gb", 120)
        recommend_ssd = cfg.get("recommend_ssd", True)

        try:
            # Check available space on largest block device
            max_space_gb = 0.0

            # Check /sys/block for disk sizes
            block_path = Path("/sys/block")
            if block_path.exists():
                for device in block_path.iterdir():
                    name = device.name
                    # Skip loop, ram, and virtual devices
                    if name.startswith(("loop", "ram", "dm-", "sr", "zram")):
                        continue

                    size_file = device / "size"
                    if size_file.exists():
                        # Size is in 512-byte sectors
                        sectors = int(size_file.read_text().strip())
                        size_gb = (sectors * 512) / (1024**3)
                        max_space_gb = max(max_space_gb, size_gb)

            # Determine status
            if max_space_gb < min_gb:
                status = RequirementStatus.FAIL
                details = msg(
                    "Insufficient disk space: {available} GB available, minimum {min} GB required",
                    available=f"{max_space_gb:.0f}",
                    min=min_gb,
                )
            elif max_space_gb < recommended_gb:
                status = RequirementStatus.WARN
                details = msg(
                    "Disk space below recommended: {available} GB available, {recommended} GB recommended",
                    available=f"{max_space_gb:.0f}",
                    recommended=recommended_gb,
                )
            else:
                status = RequirementStatus.PASS
                details = msg("Sufficient storage space available")

            if recommend_ssd:
                rec_value = msg("{size} GB (SSD recommended)", size=recommended_gb)
            else:
                rec_value = f"{recommended_gb} GB"

            return RequirementCheck(
                name="disk",
                description=msg("Storage Space"),
                status=status,
                current_value=f"{max_space_gb:.0f} GB",
                required_value=f"{min_gb} GB",
                recommended_value=rec_value,
                details=details,
            )

        except Exception as e:
            logger.warning(f"Could not check disk space: {e}")
            return RequirementCheck(
                name="disk",
                description=msg("Storage Space"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check disk space: {error}", error=e),
            )

    def _check_cpu_architecture(self) -> RequirementCheck:
        """Check CPU architecture (legacy format)."""
        cfg = self._get_check_config("cpu_arch")
        require_x86_64 = cfg.get("require_x86_64", True)

        try:
            arch = os.uname().machine

            if arch == "x86_64":
                status = RequirementStatus.PASS
                details = msg("64-bit processor detected")
            elif require_x86_64:
                status = RequirementStatus.FAIL
                details = msg("x86_64 architecture required, detected: {arch}", arch=arch)
            else:
                status = RequirementStatus.WARN
                details = msg("Detected {arch}, x86_64 recommended", arch=arch)

            return RequirementCheck(
                name="cpu_arch",
                description=msg("CPU Architecture"),
                status=status,
                current_value=arch,
                required_value="x86_64" if require_x86_64 else msg("Any"),
                details=details,
            )

        except Exception as e:
            return RequirementCheck(
                name="cpu_arch",
                description=msg("CPU Architecture"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check architecture: {error}", error=e),
            )

    def _check_cpu_architecture_v2(self, cfg: dict[str, Any]) -> RequirementCheck:
        """
        Check CPU architecture (new format with sub-section config).

        Args:
            cfg: Configuration dict with 'required' key (default: x86_64)
        """
        required_arch = cfg.get("required", "x86_64")

        try:
            arch = os.uname().machine

            if arch == required_arch:
                status = RequirementStatus.PASS
                details = msg("{arch} architecture detected", arch=arch)
            else:
                status = RequirementStatus.FAIL
                details = msg(
                    "{required} architecture required, detected: {arch}",
                    required=required_arch,
                    arch=arch,
                )

            return RequirementCheck(
                name="cpu_arch",
                description=msg("CPU Architecture"),
                status=status,
                current_value=arch,
                required_value=required_arch,
                details=details,
            )

        except Exception as e:
            return RequirementCheck(
                name="cpu_arch",
                description=msg("CPU Architecture"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check architecture: {error}", error=e),
            )

    def _check_cpu_cores(self, cfg: dict[str, Any]) -> RequirementCheck:
        """
        Check CPU core count.

        Args:
            cfg: Configuration dict with 'min_cores' and 'warn_cores' keys

        Thresholds:
        - Below min_cores: FAIL
        - Between min_cores and warn_cores: WARN
        - Above warn_cores: PASS
        """
        min_cores = cfg.get("min_cores", 4)
        warn_cores = cfg.get("warn_cores", 8)
        recommended_cores = cfg.get("recommended_cores", 8)

        try:
            # Get CPU core count
            cpu_count = os.cpu_count()
            if cpu_count is None:
                # Fallback: read from /proc/cpuinfo
                with open("/proc/cpuinfo") as f:
                    cpu_count = sum(1 for line in f if line.startswith("processor"))

            current_cores = cpu_count or 0

            # Determine status based on thresholds
            if current_cores < min_cores:
                status = RequirementStatus.FAIL
                details = msg(
                    "Insufficient CPU cores: {current} detected, minimum {min} required",
                    current=current_cores,
                    min=min_cores,
                )
            elif current_cores < warn_cores:
                status = RequirementStatus.WARN
                details = msg(
                    "CPU cores below recommended: {current} detected, {recommended} recommended",
                    current=current_cores,
                    recommended=warn_cores,
                )
            else:
                status = RequirementStatus.PASS
                details = msg("Sufficient CPU cores for optimal performance")

            return RequirementCheck(
                name="cpu_cores",
                description=msg("CPU Cores"),
                status=status,
                current_value=msg("{count} cores", count=current_cores),
                required_value=msg("{count} cores", count=min_cores),
                recommended_value=msg("{count} cores", count=recommended_cores),
                details=details,
            )

        except Exception as e:
            logger.warning(f"Could not check CPU cores: {e}")
            return RequirementCheck(
                name="cpu_cores",
                description=msg("CPU Cores"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check CPU cores: {error}", error=e),
            )

    def _check_efi_mode(self) -> RequirementCheck:
        """Check if booted in EFI mode."""
        cfg = self._get_check_config("efi")
        require_efi = cfg.get("required", False)

        efi_path = Path("/sys/firmware/efi")
        is_efi = efi_path.exists()

        if is_efi:
            status = RequirementStatus.PASS
            current = "UEFI"
            details = msg("System booted in UEFI mode")
        elif require_efi:
            status = RequirementStatus.FAIL
            current = msg("Legacy BIOS")
            details = msg("UEFI boot mode required, currently in Legacy BIOS mode")
        else:
            status = RequirementStatus.WARN
            current = msg("Legacy BIOS")
            details = msg("Legacy BIOS detected, UEFI recommended for modern installations")

        return RequirementCheck(
            name="efi",
            description=msg("Boot Mode"),
            status=status,
            current_value=current,
            required_value="UEFI" if require_efi else msg("UEFI (recommended)"),
            details=details,
        )

    def _check_secure_boot(self) -> RequirementCheck:
        """Check Secure Boot status."""
        cfg = self._get_check_config("secure_boot")
        require_disabled = cfg.get("require_disabled", False)

        try:
            # Check mokutil if available
            if shutil.which("mokutil"):
                result = subprocess.run(
                    ["mokutil", "--sb-state"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                is_enabled = "SecureBoot enabled" in result.stdout
            else:
                # Check via efivar
                sb_path = Path(
                    "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
                )
                if sb_path.exists():
                    data = sb_path.read_bytes()
                    # Last byte indicates state (1 = enabled)
                    is_enabled = len(data) > 4 and data[-1] == 1
                else:
                    # Can't determine, assume disabled
                    is_enabled = False

            if is_enabled:
                if require_disabled:
                    status = RequirementStatus.FAIL
                    details = msg(
                        "Secure Boot is enabled but must be disabled for driver compatibility"
                    )
                else:
                    status = RequirementStatus.WARN
                    details = msg("Secure Boot is enabled, may interfere with some drivers")
                current = msg("Enabled")
            else:
                status = RequirementStatus.PASS
                current = msg("Disabled")
                details = msg("Secure Boot is disabled")

            return RequirementCheck(
                name="secure_boot",
                description=msg("Secure Boot"),
                status=status,
                current_value=current,
                required_value=msg("Disabled") if require_disabled else msg("Any"),
                details=details,
            )

        except Exception as e:
            return RequirementCheck(
                name="secure_boot",
                description=msg("Secure Boot"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check Secure Boot: {error}", error=e),
            )

    def _check_internet(self) -> RequirementCheck:
        """Check internet connectivity."""
        cfg = self._get_check_config("internet")
        require_internet = cfg.get("required", False)
        recommend_internet = cfg.get("recommended", True)

        try:
            from xen.utils.network_helper import NetworkHelper

            if NetworkHelper.check_internet_connectivity():
                status = RequirementStatus.PASS
                current = msg("Connected")
                details = msg("Internet connection available")
            elif require_internet:
                status = RequirementStatus.FAIL
                current = msg("Not connected")
                details = msg("Internet connection required for installation")
            elif recommend_internet:
                status = RequirementStatus.WARN
                current = msg("Not connected")
                details = msg("No internet connection, recommended for package updates")
            else:
                status = RequirementStatus.PASS
                current = msg("Not connected")
                details = msg("Offline installation mode")

            return RequirementCheck(
                name="internet",
                description=msg("Internet Connection"),
                status=status,
                current_value=current,
                required_value=msg("Required")
                if require_internet
                else msg("Recommended")
                if recommend_internet
                else msg("Optional"),
                details=details,
            )

        except Exception as e:
            return RequirementCheck(
                name="internet",
                description=msg("Internet Connection"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check internet: {error}", error=e),
            )

    def _check_power(self) -> RequirementCheck:
        """
        Check power source and battery level (LAPTOP ONLY).

        This check is SKIPPED on desktop systems (no battery detected).
        On laptops:
        - PASS: On AC power
        - WARN: On battery AND battery >= min_battery_percent
        - FAIL: On battery AND battery < min_battery_percent
        """
        cfg = self._get_check_config("power")
        min_battery_percent = cfg.get("min_battery_percent", 80)

        try:
            power_supply = Path("/sys/class/power_supply")

            if not power_supply.exists():
                # Desktop without battery subsystem - skip
                return RequirementCheck(
                    name="power",
                    description=msg("Power Source"),
                    status=RequirementStatus.SKIP,
                    details=msg("Desktop system - no battery check needed"),
                )

            # Look for AC adapter and laptop battery
            # Filter out wireless device batteries (hidpp_battery_*, hid-*, etc.)
            on_ac = False
            has_laptop_battery = False
            battery_level = 0

            for supply in power_supply.iterdir():
                type_file = supply / "type"
                if not type_file.exists():
                    continue

                supply_type = type_file.read_text().strip()
                supply_name = supply.name.lower()

                if supply_type == "Mains":
                    online_file = supply / "online"
                    if online_file.exists():
                        on_ac = online_file.read_text().strip() == "1"
                elif supply_type == "Battery":
                    # Filter out wireless device batteries (not laptop batteries)
                    # Laptop batteries typically named: BAT0, BAT1, CMB0, etc.
                    # Wireless devices: hidpp_battery_*, hid-*, wacom_*, sony_controller_*
                    wireless_prefixes = ("hidpp_", "hid-", "wacom_", "sony_", "ps_controller")
                    if any(supply_name.startswith(prefix) for prefix in wireless_prefixes):
                        continue  # Skip wireless device batteries

                    has_laptop_battery = True
                    capacity_file = supply / "capacity"
                    if capacity_file.exists():
                        battery_level = int(capacity_file.read_text().strip())

            # Desktop system (no laptop battery detected)
            if not has_laptop_battery:
                return RequirementCheck(
                    name="power",
                    description=msg("Power Source"),
                    status=RequirementStatus.SKIP,
                    details=msg("Desktop system - no battery check needed"),
                )

            # Laptop with battery
            if on_ac:
                status = RequirementStatus.PASS
                current = msg("AC Power ({level}%)", level=battery_level)
                details = msg("System plugged into AC power")
            elif battery_level >= min_battery_percent:
                status = RequirementStatus.WARN
                current = msg("Battery ({level}%)", level=battery_level)
                details = msg(
                    "Running on battery at {level}%, AC power recommended", level=battery_level
                )
            else:
                status = RequirementStatus.FAIL
                current = msg("Battery ({level}%)", level=battery_level)
                details = msg(
                    "Battery too low: {level}% < {min}% minimum. Please plug in AC power.",
                    level=battery_level,
                    min=min_battery_percent,
                )

            return RequirementCheck(
                name="power",
                description=msg("Power Source"),
                status=status,
                current_value=current,
                required_value=msg("AC Power or ≥{min}% battery", min=min_battery_percent),
                details=details,
            )

        except Exception as e:
            return RequirementCheck(
                name="power",
                description=msg("Power Source"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check power: {error}", error=e),
            )

    def _check_gpu(self) -> RequirementCheck:
        """
        Check GPU compatibility with vendor and model overrides.

        Uses the GPUDetector to:
        - Detect all GPUs in the system
        - Check against configured availability list (AMD, INTEL, NVIDIA)
        - Validate against minimum model overrides if configured
        - Distinguish between dedicated and integrated GPUs
        - Display dGPU first, then iGPU
        """
        cfg = self._get_check_config("gpu")

        # Get configuration values
        availability = cfg.get("availability", ["AMD", "INTEL", "NVIDIA"])
        require_dedicated = cfg.get("require_dedicated", False)
        overrides = cfg.get("overrides", {})
        # GPU is advisory by default (WARN) so GPU-less / virtual machines can
        # install; set gpu.required=true to make a compatible GPU mandatory.
        require_gpu = cfg.get("required", False)

        try:
            detector = GPUDetector()
            # check_compatibility returns tuple: (status, message, gpu_list)
            status_str, message, _ = detector.check_compatibility(
                availability=availability,
                require_dedicated=require_dedicated,
                overrides=overrides,
            )

            # Sort GPUs: dGPU first, then iGPU
            sorted_gpu_names = self._sort_gpus_dgpu_first(detector)

            # Show only primary GPU in current_value (dGPU if available)
            primary_gpu = sorted_gpu_names[0] if sorted_gpu_names else msg("None detected")

            # Build details with full GPU list for tooltip
            if len(sorted_gpu_names) > 1:
                full_list = ", ".join(sorted_gpu_names)
                extra_info = msg("All GPUs: {list}", list=full_list)
            else:
                extra_info = ""

            # Determine status based on result
            if status_str == "pass":
                status = RequirementStatus.PASS
                details = extra_info or msg("Compatible GPU detected")
            elif status_str == "warn":
                status = RequirementStatus.WARN
                details = message or extra_info or msg("GPU compatibility warning")
            else:  # fail
                status = RequirementStatus.FAIL if require_gpu else RequirementStatus.WARN
                details = message or msg("No compatible GPU detected")

            # Build recommended value string
            if require_dedicated:
                rec_value = msg("Dedicated GPU required")
            else:
                rec_parts = []
                if "NVIDIA" in availability:
                    nvidia_min = overrides.get("nvidia", "")
                    rec_parts.append(f"NVIDIA{' >=' + nvidia_min if nvidia_min else ''}")
                if "AMD" in availability:
                    amd_min = overrides.get("amd", "")
                    rec_parts.append(f"AMD{' >=' + amd_min if amd_min else ''}")
                if "INTEL" in availability:
                    intel_min = overrides.get("intel", "")
                    rec_parts.append(f"Intel{' >=' + intel_min if intel_min else ''}")
                rec_value = " / ".join(rec_parts) if rec_parts else msg("Any GPU")

            return RequirementCheck(
                name="gpu",
                description=msg("Graphics (GPU)"),
                status=status,
                current_value=primary_gpu,
                recommended_value=rec_value,
                details=details,
            )

        except Exception as e:
            logger.warning(f"Could not check GPU: {e}")
            return RequirementCheck(
                name="gpu",
                description=msg("Graphics (GPU)"),
                status=RequirementStatus.SKIP,
                details=msg("Could not check GPU: {error}", error=e),
            )

    def _get_short_gpu_name(self, gpu: Any) -> str:
        """
        Extract a short, readable GPU name from verbose lspci output.

        Examples:
        - "AMD Advanced Micro Devices, Inc. [AMD/ATI] Navi 31 [Radeon RX 7900 XT/...]"
          → "AMD Radeon RX 7900 XT"
        - "INTEL Intel Corporation Raptor Lake-S GT1 [UHD Graphics 770] (rev 04)"
          → "Intel UHD Graphics 770"
        """
        import re

        full_name = str(gpu)
        vendor = gpu.vendor.value  # AMD, INTEL, NVIDIA

        # Extract marketing name from brackets [Name]
        # Look for the bracket containing the actual product name
        brackets = re.findall(r"\[([^\]]+)\]", full_name)

        if brackets:
            # Filter out vendor tags like "AMD/ATI"
            product_names = [
                b for b in brackets if "/" not in b or "RX" in b or "GTX" in b or "RTX" in b
            ]

            if product_names:
                # Get the most relevant name (usually the last one with product info)
                for name in reversed(product_names):
                    # Skip generic tags
                    if name in ("VGA controller", "3D controller", "Display controller"):
                        continue
                    # For multi-model strings like "Radeon RX 7900 XT/7900 XTX/...", take first
                    if "/" in name:
                        name = name.split("/")[0].strip()
                    # Format vendor prefix properly
                    vendor_prefix = "Intel" if vendor == "INTEL" else vendor
                    # Avoid duplicate vendor name
                    if name.upper().startswith(vendor.upper()):
                        return name
                    return f"{vendor_prefix} {name}"

        # Fallback: return vendor + model field
        if hasattr(gpu, "model") and gpu.model:
            vendor_prefix = "Intel" if vendor == "INTEL" else vendor
            return f"{vendor_prefix} {gpu.model}"

        return full_name[:40]  # Truncate if nothing else works

    def _sort_gpus_dgpu_first(self, detector: GPUDetector) -> list[str]:
        """
        Sort GPUs with dedicated (dGPU) first, then integrated (iGPU).

        Args:
            detector: GPUDetector instance with detected GPUs

        Returns:
            List of short GPU names sorted by type (dGPU first)
        """
        dgpus = []
        igpus = []
        others = []

        for gpu in detector.gpus:
            short_name = self._get_short_gpu_name(gpu)
            if gpu.is_dedicated:
                dgpus.append(short_name)
            elif gpu.is_integrated:
                igpus.append(short_name)
            else:
                others.append(short_name)

        return dgpus + igpus + others
