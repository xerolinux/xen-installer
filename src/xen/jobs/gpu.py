"""
GPU Detection and Compatibility Module for Xen Installer.

Provides detailed GPU detection, vendor identification, and model comparison
for system requirements validation.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

from xen.i18n.messages import msg

logger = logging.getLogger(__name__)


class GPUVendor(Enum):
    """GPU vendor identifiers."""

    NVIDIA = "NVIDIA"
    AMD = "AMD"
    INTEL = "INTEL"
    UNKNOWN = "UNKNOWN"


class GPUType(Enum):
    """GPU type classification."""

    DEDICATED = auto()  # dGPU - Dedicated graphics card
    INTEGRATED = auto()  # iGPU - Integrated graphics
    UNKNOWN = auto()


@dataclass
class GPUInfo:
    """Information about a detected GPU."""

    vendor: GPUVendor
    name: str
    model: str
    gpu_type: GPUType
    pci_id: str = ""
    device_id: str = ""

    @property
    def is_dedicated(self) -> bool:
        """Check if this is a dedicated GPU."""
        return self.gpu_type == GPUType.DEDICATED

    @property
    def is_integrated(self) -> bool:
        """Check if this is an integrated GPU."""
        return self.gpu_type == GPUType.INTEGRATED

    def __str__(self) -> str:
        return f"{self.vendor.value} {self.name}"


# =============================================================================
# GPU Model Database
# =============================================================================
# Models are listed in ascending order of performance/capability
# Index position determines the hierarchy (higher index = better GPU)

NVIDIA_DGPU_MODELS: list[str] = [
    # GeForce 900 Series (Maxwell)
    "GTX 950",
    "GTX 960",
    "GTX 970",
    "GTX 980",
    "GTX 980 Ti",
    # GeForce 10 Series (Pascal)
    "GT 1030",
    "GTX 1050",
    "GTX 1050 Ti",
    "GTX 1060",
    "GTX 1070",
    "GTX 1070 Ti",
    "GTX 1080",
    "GTX 1080 Ti",
    # GeForce 16 Series (Turing)
    "GTX 1650",
    "GTX 1650 SUPER",
    "GTX 1660",
    "GTX 1660 SUPER",
    "GTX 1660 Ti",
    # GeForce 20 Series (Turing)
    "RTX 2060",
    "RTX 2060 SUPER",
    "RTX 2070",
    "RTX 2070 SUPER",
    "RTX 2080",
    "RTX 2080 SUPER",
    "RTX 2080 Ti",
    # GeForce 30 Series (Ampere)
    "RTX 3050",
    "RTX 3060",
    "RTX 3060 Ti",
    "RTX 3070",
    "RTX 3070 Ti",
    "RTX 3080",
    "RTX 3080 Ti",
    "RTX 3090",
    "RTX 3090 Ti",
    # GeForce 40 Series (Ada Lovelace)
    "RTX 4060",
    "RTX 4060 Ti",
    "RTX 4070",
    "RTX 4070 SUPER",
    "RTX 4070 Ti",
    "RTX 4070 Ti SUPER",
    "RTX 4080",
    "RTX 4080 SUPER",
    "RTX 4090",
    # GeForce 50 Series (Blackwell)
    "RTX 5070",
    "RTX 5070 Ti",
    "RTX 5080",
    "RTX 5090",
]

NVIDIA_IGPU_MODELS: list[str] = [
    # Tegra/Jetson (for completeness)
    "Tegra X1",
    "Tegra X2",
]

AMD_DGPU_MODELS: list[str] = [
    # RX 400 Series (Polaris)
    "RX 460",
    "RX 470",
    "RX 480",
    # RX 500 Series (Polaris)
    "RX 550",
    "RX 560",
    "RX 570",
    "RX 580",
    "RX 590",
    # RX 5000 Series (RDNA)
    "RX 5500 XT",
    "RX 5600 XT",
    "RX 5700",
    "RX 5700 XT",
    # RX 6000 Series (RDNA 2)
    "RX 6400",
    "RX 6500 XT",
    "RX 6600",
    "RX 6600 XT",
    "RX 6650 XT",
    "RX 6700 XT",
    "RX 6750 XT",
    "RX 6800",
    "RX 6800 XT",
    "RX 6900 XT",
    "RX 6950 XT",
    # RX 7000 Series (RDNA 3)
    "RX 7600",
    "RX 7600 XT",
    "RX 7700 XT",
    "RX 7800 XT",
    "RX 7900 GRE",
    "RX 7900 XT",
    "RX 7900 XTX",
    # RX 9000 Series (RDNA 4)
    "RX 9070",
    "RX 9070 XT",
]

AMD_IGPU_MODELS: list[str] = [
    # Vega iGPU (Ryzen APU)
    "Vega 3",
    "Vega 6",
    "Vega 7",
    "Vega 8",
    "Vega 10",
    "Vega 11",
    # RDNA 2 iGPU (Ryzen 6000+ APU)
    "Radeon 660M",
    "Radeon 680M",
    # RDNA 3 iGPU (Ryzen 7000+ APU)
    "Radeon 740M",
    "Radeon 760M",
    "Radeon 780M",
    # RDNA 3.5 iGPU (Ryzen AI 300+)
    "Radeon 880M",
    "Radeon 890M",
]

INTEL_DGPU_MODELS: list[str] = [
    # Arc A-Series (Alchemist)
    "Arc A310",
    "Arc A380",
    "Arc A580",
    "Arc A750",
    "Arc A770",
    # Arc B-Series (Battlemage)
    "Arc B570",
    "Arc B580",
]

INTEL_IGPU_MODELS: list[str] = [
    # HD Graphics (Gen 7-9)
    "HD 4000",
    "HD 4600",
    "HD 5500",
    "HD 520",
    "HD 530",
    "HD 620",
    "HD 630",
    # UHD Graphics Gen 9.5 (NOT Xe architecture)
    "UHD 620",
    "UHD 630",
    # Iris Plus (Gen 10-11, pre-Xe)
    "Iris Plus 640",
    "Iris Plus 650",
    "Iris Plus 655",
    # === Xe Architecture (Gen 12+) - Everything below passes "Xe" minimum ===
    # "Xe" is used as a reference point for minimum requirements
    "Xe",
    # UHD Graphics Gen 12+ (Xe-LP architecture - Alder Lake/Raptor Lake Desktop)
    "UHD 730",
    "UHD 750",
    "UHD 770",
    # Iris Xe (Gen 12 Mobile - Tiger Lake/Alder Lake)
    "Iris Xe",
    "Iris Xe MAX",
    # Arc Graphics (Gen 12.5+ - Meteor Lake iGPU and discrete)
    "Arc Graphics",
]


def _normalize_model_name(name: str) -> str:
    """Normalize GPU model name for comparison."""
    # Remove common prefixes/suffixes
    name = name.upper()
    name = re.sub(r"GEFORCE\s*", "", name)
    name = re.sub(r"RADEON\s*", "", name)
    name = re.sub(r"GRAPHICS\s*", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def get_model_index(model: str, model_list: list[str]) -> int:
    """
    Get the index of a GPU model in a model list.

    Args:
        model: GPU model name to find
        model_list: Ordered list of models

    Returns:
        Index in list, or -1 if not found
    """
    normalized = _normalize_model_name(model)

    for idx, list_model in enumerate(model_list):
        list_normalized = _normalize_model_name(list_model)
        if list_normalized in normalized or normalized in list_normalized:
            return idx

    return -1


def compare_models(model: str, min_model: str, model_list: list[str]) -> bool:
    """
    Check if a GPU model meets the minimum requirement.

    Args:
        model: Detected GPU model
        min_model: Minimum required model
        model_list: Ordered list of models for comparison

    Returns:
        True if model meets or exceeds minimum
    """
    model_idx = get_model_index(model, model_list)
    min_idx = get_model_index(min_model, model_list)

    if model_idx < 0 or min_idx < 0:
        # Unknown model - assume it's newer/better
        logger.debug(f"Unknown model comparison: {model} vs {min_model}")
        return True

    return model_idx >= min_idx


class GPUDetector:
    """
    Detects GPUs in the system and provides compatibility information.

    Reads GPU information from:
    - /sys/class/drm for DRM devices
    - lspci for PCI device info (if available)
    - /proc/driver/nvidia for NVIDIA-specific info
    """

    # PCI Vendor IDs
    VENDOR_IDS = {
        "0x10de": GPUVendor.NVIDIA,
        "0x1002": GPUVendor.AMD,
        "0x8086": GPUVendor.INTEL,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        Initialize the GPU detector.

        Args:
            config: GPU configuration from requirements
        """
        self.config = config or {}
        self._gpus: list[GPUInfo] | None = None

    @property
    def gpus(self) -> list[GPUInfo]:
        """Get list of detected GPUs (cached)."""
        if self._gpus is None:
            self._gpus = self._detect_gpus()
        return self._gpus

    def _detect_gpus(self) -> list[GPUInfo]:
        """Detect all GPUs in the system."""
        gpus: list[GPUInfo] = []

        # Try DRM subsystem first
        drm_gpus = self._detect_via_drm()
        gpus.extend(drm_gpus)

        logger.info(f"Detected {len(gpus)} GPU(s): {[str(g) for g in gpus]}")
        return gpus

    def _detect_via_drm(self) -> list[GPUInfo]:
        """Detect GPUs via DRM subsystem."""
        gpus: list[GPUInfo] = []
        drm_path = Path("/sys/class/drm")

        if not drm_path.exists():
            logger.warning("DRM subsystem not available")
            return gpus

        for card in drm_path.iterdir():
            # Only process main card entries (not card0-DP-1, etc.)
            if not card.name.startswith("card") or "-" in card.name:
                continue

            device_path = card / "device"
            if not device_path.exists():
                continue

            gpu_info = self._parse_drm_device(device_path)
            if gpu_info:
                gpus.append(gpu_info)

        return gpus

    def _parse_drm_device(self, device_path: Path) -> GPUInfo | None:
        """Parse GPU info from DRM device path."""
        try:
            # Read vendor ID
            vendor_file = device_path / "vendor"
            if not vendor_file.exists():
                return None

            vendor_id = vendor_file.read_text().strip()
            vendor = self.VENDOR_IDS.get(vendor_id, GPUVendor.UNKNOWN)

            if vendor == GPUVendor.UNKNOWN:
                return None

            # Read device ID
            device_id = ""
            device_id_file = device_path / "device"
            if device_id_file.exists():
                device_id = device_id_file.read_text().strip()

            # Get PCI slot
            pci_id = device_path.resolve().name

            # Determine GPU type and name
            gpu_type, name, model = self._identify_gpu(vendor, device_id, pci_id)

            return GPUInfo(
                vendor=vendor,
                name=name,
                model=model,
                gpu_type=gpu_type,
                pci_id=pci_id,
                device_id=device_id,
            )

        except Exception as e:
            logger.debug(f"Failed to parse DRM device {device_path}: {e}")
            return None

    def _identify_gpu(
        self, vendor: GPUVendor, device_id: str, pci_id: str
    ) -> tuple[GPUType, str, str]:
        """
        Identify GPU type and model name.

        Returns:
            Tuple of (gpu_type, display_name, model_name)
        """
        # Try to get name from lspci
        name = self._get_name_from_lspci(pci_id)
        if name:
            model = self._extract_model_name(name, vendor)
            gpu_type = self._determine_gpu_type(name, vendor)
            return gpu_type, name, model

        # Fallback to vendor name only
        name = f"{vendor.value} GPU ({device_id})"
        return GPUType.UNKNOWN, name, ""

    def _get_name_from_lspci(self, pci_id: str) -> str:
        """Get GPU name from lspci."""
        try:
            result = subprocess.run(
                ["lspci", "-v", "-s", pci_id],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse first line for device name
                first_line = result.stdout.split("\n")[0]
                # Format: "XX:XX.X VGA compatible controller: VENDOR NAME"
                if ":" in first_line:
                    parts = first_line.split(":", 2)
                    if len(parts) >= 3:
                        return parts[2].strip()
        except Exception as e:
            logger.debug(f"lspci failed: {e}")

        return ""

    def _extract_model_name(self, full_name: str, vendor: GPUVendor) -> str:
        """Extract the model name from full GPU name."""
        # Remove vendor prefix
        name = full_name
        if vendor == GPUVendor.NVIDIA:
            name = re.sub(r"NVIDIA Corporation\s*", "", name, flags=re.IGNORECASE)
            # Extract GeForce/Quadro model
            match = re.search(r"(GeForce\s+)?(GTX|RTX|GT)\s*\d+\s*(Ti|SUPER)?", name, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        elif vendor == GPUVendor.AMD:
            name = re.sub(r"Advanced Micro Devices.*?\[|\]", "", name, flags=re.IGNORECASE)
            # Extract RX/Radeon model
            match = re.search(r"(Radeon\s+)?(RX\s*\d+\s*(XT)?)", name, re.IGNORECASE)
            if match:
                return match.group(0).strip()
            # Check for Vega iGPU
            match = re.search(r"Vega\s*\d+", name, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        elif vendor == GPUVendor.INTEL:
            # Extract Intel GPU model
            match = re.search(r"(UHD|HD|Iris|Arc)\s*(Graphics|Xe|Plus)?\s*\d*", name, re.IGNORECASE)
            if match:
                return match.group(0).strip()

        return name

    def _determine_gpu_type(self, name: str, vendor: GPUVendor) -> GPUType:
        """Determine if GPU is dedicated or integrated."""
        name_lower = name.lower()

        if vendor == GPUVendor.NVIDIA:
            # NVIDIA: All GeForce/Quadro are dGPU, Tegra is mobile
            if "tegra" in name_lower:
                return GPUType.INTEGRATED
            return GPUType.DEDICATED

        elif vendor == GPUVendor.AMD:
            # AMD: RX series are dGPU, Vega/Radeon with M suffix are iGPU
            if "vega" in name_lower or any(
                x in name_lower for x in ["680m", "660m", "780m", "760m", "740m"]
            ):
                return GPUType.INTEGRATED
            if "rx" in name_lower:
                return GPUType.DEDICATED
            # Check for mobile/APU indicators
            if "ryzen" in name_lower or "apu" in name_lower:
                return GPUType.INTEGRATED
            return GPUType.DEDICATED

        elif vendor == GPUVendor.INTEL:
            # Intel: Arc series are dGPU, everything else is iGPU
            if "arc" in name_lower and "graphics" not in name_lower:
                return GPUType.DEDICATED
            return GPUType.INTEGRATED

        return GPUType.UNKNOWN

    def check_compatibility(
        self,
        availability: list[str] | None = None,
        require_dedicated: bool = False,
        overrides: dict[str, str] | None = None,
    ) -> tuple[str, str, list[str]]:
        """
        Check GPU compatibility against requirements.

        Args:
            availability: List of supported vendors (AMD, INTEL, NVIDIA)
            require_dedicated: Whether dedicated GPU is required
            overrides: Minimum model requirements by vendor

        Returns:
            Tuple of (status, message, gpu_list) where status is:
            - "pass": Compatible dedicated GPU detected
            - "warn": Only integrated GPU or below recommended
            - "fail": No compatible GPU or below minimum
        """
        availability = availability or ["AMD", "INTEL", "NVIDIA"]
        overrides = overrides or {}

        # Convert availability to enum
        allowed_vendors = {GPUVendor[v.upper()] for v in availability}

        # Filter GPUs by allowed vendors
        compatible_gpus = [gpu for gpu in self.gpus if gpu.vendor in allowed_vendors]
        gpu_names = [str(gpu) for gpu in compatible_gpus]

        if not compatible_gpus:
            return "fail", msg("No compatible GPU detected"), []

        # Check for dedicated GPU
        dedicated_gpus = [gpu for gpu in compatible_gpus if gpu.is_dedicated]
        integrated_gpus = [gpu for gpu in compatible_gpus if gpu.is_integrated]

        # Check model overrides
        # Logic: If ANY GPU passes its vendor's minimum, the check passes
        # Only fail if ALL GPUs fail their respective minimums
        passed_gpus: list[str] = []
        failed_overrides: list[str] = []

        for gpu in compatible_gpus:
            vendor_key = gpu.vendor.value.lower()
            min_model = overrides.get(vendor_key, "")

            if min_model and gpu.model:
                model_list = self._get_model_list(gpu.vendor, gpu.gpu_type)
                if compare_models(gpu.model, min_model, model_list):
                    # This GPU passes its vendor's minimum
                    passed_gpus.append(f"{gpu.vendor.value} {gpu.model}")
                else:
                    failed_overrides.append(f"{gpu.vendor.value} {gpu.model} < {min_model}")
            else:
                # No minimum specified for this vendor, GPU passes by default
                passed_gpus.append(f"{gpu.vendor.value} {gpu.model or 'Unknown'}")

        # If at least one GPU passed, don't fail (continue to other checks)
        # Only fail if ALL GPUs failed their minimums
        if failed_overrides and not passed_gpus:
            return (
                "fail",
                msg("GPU below minimum: {list}", list=", ".join(failed_overrides)),
                gpu_names,
            )

        # Check dedicated requirement
        if require_dedicated and not dedicated_gpus:
            if integrated_gpus:
                return "warn", msg("Integrated graphics only"), gpu_names
            return "fail", msg("No dedicated GPU detected"), gpu_names

        # All checks passed
        if dedicated_gpus:
            return "pass", msg("Compatible GPU: {gpu}", gpu=dedicated_gpus[0]), gpu_names
        elif integrated_gpus:
            return "warn", msg("Integrated graphics (dedicated recommended)"), gpu_names

        return "pass", msg("Compatible GPU detected"), gpu_names

    def _get_model_list(self, vendor: GPUVendor, gpu_type: GPUType) -> list[str]:
        """Get the appropriate model list for comparison."""
        if vendor == GPUVendor.NVIDIA:
            if gpu_type == GPUType.INTEGRATED:
                return NVIDIA_IGPU_MODELS
            return NVIDIA_DGPU_MODELS
        elif vendor == GPUVendor.AMD:
            if gpu_type == GPUType.INTEGRATED:
                return AMD_IGPU_MODELS
            return AMD_DGPU_MODELS
        elif vendor == GPUVendor.INTEL:
            if gpu_type == GPUType.DEDICATED:
                return INTEL_DGPU_MODELS
            return INTEL_IGPU_MODELS
        return []
