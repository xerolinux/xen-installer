# GPU Compatibility Configuration

This document describes the GPU compatibility checking system in Xen Installer, including the configurable override system for minimum GPU requirements.

## Overview

The Xen Installer includes a GPU compatibility checker that:

1. Detects all GPUs in the system via the DRM subsystem
2. Identifies GPU vendor (NVIDIA, AMD, Intel) and model
3. Classifies GPUs as dedicated (dGPU) or integrated (iGPU)
4. Validates against configurable minimum requirements

## Configuration

GPU requirements are configured in the distribution's config file (e.g., `config/examples/xerolinux.yaml`) under the `jobs` section for the `welcome` job:

```yaml
jobs:
  - name: welcome
    config:
      requirements:
        gpu:
          # Supported GPU vendors
          availability:
            - AMD
            - INTEL
            - NVIDIA

          # Require dedicated GPU (dGPU)
          require_dedicated: false

          # Minimum GPU model overrides by vendor
          overrides:
            nvidia: "GTX 1650"
            amd: ""
            intel: ""
```

### Configuration Options

| Option | Type | Description |
|--------|------|-------------|
| `availability` | List[str] | Allowed GPU vendors. Valid values: `AMD`, `INTEL`, `NVIDIA` |
| `require_dedicated` | bool | If `true`, only dedicated GPUs pass; iGPUs show warning |
| `overrides.nvidia` | str | Minimum NVIDIA model (empty = no minimum) |
| `overrides.amd` | str | Minimum AMD model (empty = no minimum) |
| `overrides.intel` | str | Minimum Intel model (empty = no minimum) |

## GPU Model Index

Below is the complete index of supported GPU models, organized by vendor and type. Models are listed in ascending order of performance - higher positions indicate better/newer GPUs.

### NVIDIA

#### Dedicated GPUs (dGPU)

| Index | Model | Architecture | Notes |
|-------|-------|--------------|-------|
| 0 | GTX 950 | Maxwell | Entry-level gaming |
| 1 | GTX 960 | Maxwell | |
| 2 | GTX 970 | Maxwell | |
| 3 | GTX 980 | Maxwell | |
| 4 | GTX 980 Ti | Maxwell | |
| 5 | GTX 1050 | Pascal | |
| 6 | GTX 1050 Ti | Pascal | |
| 7 | GTX 1060 | Pascal | Popular VR-capable |
| 8 | GTX 1070 | Pascal | |
| 9 | GTX 1070 Ti | Pascal | |
| 10 | GTX 1080 | Pascal | |
| 11 | GTX 1080 Ti | Pascal | |
| 12 | GTX 1650 | Turing | |
| 13 | GTX 1650 Super | Turing | |
| 14 | GTX 1660 | Turing | |
| 15 | GTX 1660 Super | Turing | |
| 16 | GTX 1660 Ti | Turing | |
| 17 | RTX 2060 | Turing | First RTX with ray tracing |
| 18 | RTX 2060 Super | Turing | |
| 19 | RTX 2070 | Turing | |
| 20 | RTX 2070 Super | Turing | |
| 21 | RTX 2080 | Turing | |
| 22 | RTX 2080 Super | Turing | |
| 23 | RTX 2080 Ti | Turing | |
| 24 | RTX 3050 | Ampere | |
| 25 | RTX 3060 | Ampere | |
| 26 | RTX 3060 Ti | Ampere | |
| 27 | RTX 3070 | Ampere | |
| 28 | RTX 3070 Ti | Ampere | |
| 29 | RTX 3080 | Ampere | |
| 30 | RTX 3080 Ti | Ampere | |
| 31 | RTX 3090 | Ampere | |
| 32 | RTX 3090 Ti | Ampere | |
| 33 | RTX 4060 | Ada Lovelace | |
| 34 | RTX 4060 Ti | Ada Lovelace | |
| 35 | RTX 4070 | Ada Lovelace | |
| 36 | RTX 4070 Super | Ada Lovelace | |
| 37 | RTX 4070 Ti | Ada Lovelace | |
| 38 | RTX 4070 Ti Super | Ada Lovelace | |
| 39 | RTX 4080 | Ada Lovelace | |
| 40 | RTX 4080 Super | Ada Lovelace | |
| 41 | RTX 4090 | Ada Lovelace | Flagship |
| 42 | RTX 5070 | Blackwell | Next-gen |
| 43 | RTX 5070 Ti | Blackwell | |
| 44 | RTX 5080 | Blackwell | |
| 45 | RTX 5090 | Blackwell | Latest flagship |

#### Integrated GPUs (iGPU)

| Index | Model | Notes |
|-------|-------|-------|
| 0 | Tegra K1 | Mobile/embedded |
| 1 | Tegra X1 | Shield, Switch |
| 2 | Tegra X2 | Automotive |

### AMD

#### Dedicated GPUs (dGPU)

| Index | Model | Architecture | Notes |
|-------|-------|--------------|-------|
| 0 | RX 460 | Polaris | Entry-level |
| 1 | RX 470 | Polaris | |
| 2 | RX 480 | Polaris | |
| 3 | RX 550 | Polaris | |
| 4 | RX 560 | Polaris | |
| 5 | RX 570 | Polaris | |
| 6 | RX 580 | Polaris | Popular for gaming |
| 7 | RX 590 | Polaris | |
| 8 | RX 5500 XT | RDNA | |
| 9 | RX 5600 XT | RDNA | |
| 10 | RX 5700 | RDNA | |
| 11 | RX 5700 XT | RDNA | |
| 12 | RX 6400 | RDNA 2 | |
| 13 | RX 6500 XT | RDNA 2 | |
| 14 | RX 6600 | RDNA 2 | |
| 15 | RX 6600 XT | RDNA 2 | |
| 16 | RX 6650 XT | RDNA 2 | |
| 17 | RX 6700 XT | RDNA 2 | |
| 18 | RX 6750 XT | RDNA 2 | |
| 19 | RX 6800 | RDNA 2 | |
| 20 | RX 6800 XT | RDNA 2 | |
| 21 | RX 6900 XT | RDNA 2 | |
| 22 | RX 6950 XT | RDNA 2 | |
| 23 | RX 7600 | RDNA 3 | |
| 24 | RX 7600 XT | RDNA 3 | |
| 25 | RX 7700 XT | RDNA 3 | |
| 26 | RX 7800 XT | RDNA 3 | |
| 27 | RX 7900 GRE | RDNA 3 | |
| 28 | RX 7900 XT | RDNA 3 | |
| 29 | RX 7900 XTX | RDNA 3 | |
| 30 | RX 9070 | RDNA 4 | Next-gen |
| 31 | RX 9070 XT | RDNA 4 | Latest |

#### Integrated GPUs (iGPU)

| Index | Model | Notes |
|-------|-------|-------|
| 0 | Vega 3 | APU entry-level |
| 1 | Vega 6 | APU |
| 2 | Vega 7 | APU |
| 3 | Vega 8 | APU |
| 4 | Vega 10 | APU |
| 5 | Vega 11 | APU |
| 6 | Radeon 660M | RDNA 2 APU |
| 7 | Radeon 680M | RDNA 2 APU |
| 8 | Radeon 740M | RDNA 3 APU |
| 9 | Radeon 760M | RDNA 3 APU |
| 10 | Radeon 780M | RDNA 3 APU |
| 11 | Radeon 880M | RDNA 3.5 APU |
| 12 | Radeon 890M | RDNA 3.5 APU, latest |

### Intel

#### Dedicated GPUs (dGPU)

| Index | Model | Architecture | Notes |
|-------|-------|--------------|-------|
| 0 | Arc A310 | Alchemist | Entry-level Arc |
| 1 | Arc A380 | Alchemist | |
| 2 | Arc A580 | Alchemist | |
| 3 | Arc A750 | Alchemist | Mid-range |
| 4 | Arc A770 | Alchemist | Flagship Alchemist |
| 5 | Arc B570 | Battlemage | Next-gen |
| 6 | Arc B580 | Battlemage | Latest |

#### Integrated GPUs (iGPU)

| Index | Model | Notes |
|-------|-------|-------|
| 0 | HD 4000 | Ivy Bridge |
| 1 | HD 4600 | Haswell |
| 2 | HD 5500 | Broadwell |
| 3 | HD 520 | Skylake |
| 4 | HD 530 | Skylake |
| 5 | HD 620 | Kaby Lake |
| 6 | HD 630 | Kaby Lake |
| 7 | UHD 620 | Coffee Lake |
| 8 | UHD 630 | Coffee Lake |
| 9 | UHD 730 | Alder Lake |
| 10 | UHD 770 | Alder Lake |
| 11 | Iris Xe | Tiger Lake+ |
| 12 | Iris Xe Max | DG1 |
| 13 | Arc Graphics | Meteor Lake, latest |

## GPU Detection

The GPU detector uses the Linux DRM (Direct Rendering Manager) subsystem at `/sys/class/drm` to identify GPUs:

1. **Vendor Detection**: Via PCI vendor ID
   - `0x10de` = NVIDIA
   - `0x1002` = AMD
   - `0x8086` = Intel

2. **Model Detection**: Via `lspci` output parsing

3. **Type Classification**:
   - NVIDIA: All consumer GeForce are dGPU, Tegra are iGPU
   - AMD: RX series are dGPU, Vega/Radeon M series are iGPU
   - Intel: Arc A/B series are dGPU, HD/UHD/Iris are iGPU

## Status Values

The GPU check returns one of three statuses:

| Status | Condition | User Impact |
|--------|-----------|-------------|
| **pass** | Compatible dGPU detected meeting minimum | Installation proceeds normally |
| **warn** | iGPU only (when `require_dedicated: false`), or dGPU below minimum | Warning shown, installation allowed |
| **fail** | No compatible GPU, or dGPU required but only iGPU | Blocking popup, cannot install |

## Example Configurations

### Gaming Distribution (Strict)

```yaml
gpu:
  availability:
    - AMD
    - NVIDIA
  require_dedicated: true
  overrides:
    nvidia: "RTX 2060"
    amd: "RX 5600 XT"
```

### General Purpose (Lenient)

```yaml
gpu:
  availability:
    - AMD
    - INTEL
    - NVIDIA
  require_dedicated: false
  overrides:
    nvidia: ""
    amd: ""
    intel: ""
```

### NVIDIA-Only Workstation

```yaml
gpu:
  availability:
    - NVIDIA
  require_dedicated: true
  overrides:
    nvidia: "RTX 3080"
```

## Troubleshooting

### GPU Not Detected

1. Check if GPU appears in `lspci | grep -i vga`
2. Verify DRM entries exist in `/sys/class/drm/`
3. Ensure kernel modules are loaded (`nvidia`, `amdgpu`, `i915`)

### Wrong Model Detected

The model detection relies on `lspci` output. If detection fails:

1. Run `lspci -v | grep -A 10 VGA` to see raw output
2. Check if the model string matches patterns in `gpu.py`
3. Submit an issue with `lspci` output for model database updates

### Override Not Working

1. Verify the model name exactly matches the index (case-sensitive)
2. Check YAML syntax in config file
3. Models not in the index are treated as "unknown" and may not compare correctly
