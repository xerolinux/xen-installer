# Btrfs Support Design

**Status:** Implemented 2026-09-12. 1072 tests passing (up from 1058),
ruff/mypy clean. `btrfs-progs` added to `xen-app`'s PKGBUILD depends
(already present in both `~/XeroBuild` live ISO profiles' package lists).

## Goal

Add Btrfs as a third selectable root filesystem (alongside the existing
ext4/xfs) in AUTO partitioning mode, with a correct Snapper-compatible
subvolume layout, correct mount/fstab behavior, a working swapfile, and
no changes whatsoever to the ext4/xfs code paths.

## Non-goals (explicitly out of scope for this pass)

- Snapper installation/configuration. Not wired up. A future pass can add
  it without restructuring this one - the subvolume layout below is
  Snapper's own suggested minimum layout, chosen for exactly this reason.
- grub-btrfs / snapshot boot entries.
- Btrfs multi-device/RAID (Xen is a single-disk installer).
- Manual partitioning mode subvolume auto-layout (see "Manual mode" below).
- Any change to real Calamares config - XeroLinux's actual Calamares
  (`availableFileSystemTypes: ["xfs","ext4"]`) does not support Btrfs at
  all. This is new capability, not a port.

## Research basis

Full ArchWiki Btrfs page and the GRUB wiki's Btrfs-specific sections were
read in full. Key facts driving this design:

- Btrfs has no built-in encryption. The existing LUKS-wrap-the-raw-partition
  flow (`_setup_luks`, `PartitionLayout.root_target` resolving to the LUKS
  mapper device) needs zero changes - `mkfs.btrfs` runs on the mapper
  device exactly like `mkfs.xfs`/`mkfs.ext4` already do.
- The wiki explicitly warns against using the top-level subvolume (ID 5)
  as `/`. Recommended practice: create a dedicated subvolume (conventionally
  named `@`) and mount that as root instead.
- `grub-mkconfig` auto-detects the root subvolume and writes the correct
  `rootflags=subvol=...` itself, *as long as* `/etc/fstab`'s root entry
  already has the right `subvol=` mount option. No manual GRUB changes
  needed - confirmed `bootloader.py` has zero filesystem-type branching
  today and needs none added.
- `genfstab -U` (already how `systemconfig.py._generate_fstab` works)
  introspects live mount state via `findmnt`, so it will capture whatever
  `subvol=`/`compress=` options were active at mount time for every one of
  the five Btrfs mountpoints, as separate fstab lines, automatically. No
  changes needed to `_generate_fstab`.
- `_generate_crypttab` keys only off `encryption`/`root_partition`,
  identical regardless of root filesystem. No changes needed.
- Btrfs is a kernel module, not built-in - needs `btrfs` added to
  mkinitcpio's `MODULES=()` array. No `HOOKS=` entry needed (that's only
  for multi-device/RAID pools, which Xen doesn't do).
- A Btrfs swapfile needs its own subvolume (Btrfs forbids snapshotting a
  subvolume with an active swapfile) and must be created via
  `btrfs filesystem mkswapfile` (handles NOCOW + preallocation correctly),
  not plain `dd`+`mkswap`. Confirmed the current `_setup_swapfile()` would
  silently produce a broken, unusable swapfile on Btrfs as written today.
- fstab's fsck-pass field should be `0` for Btrfs (no fsck-on-boot
  support) - genfstab already does this correctly for every filesystem
  type without any special-casing needed in Xen's own code.

## Subvolume layout (AUTO mode only)

Five subvolumes off the top-level (ID 5), created once immediately after
`mkfs.btrfs`, matching Snapper's suggested minimum layout plus splitting
out log/cache churn:

| Subvolume            | Mounted at               | Why                                                          |
|-----------------------|---------------------------|---------------------------------------------------------------|
| `@`                   | `<target>`                | Root. Never the top-level subvolume itself (wiki warning).    |
| `@home`               | `<target>/home`           | Standard snapshot boundary - root rollback shouldn't touch user files. |
| `@var_log`            | `<target>/var/log`        | Log churn shouldn't bloat/pollute future root snapshots.      |
| `@var_cache_pacman`   | `<target>/var/cache/pacman` | Same reasoning - package cache churn is snapshot noise.      |
| `@swap`               | `<target>/swap`           | Required separately - Btrfs forbids snapshotting a subvolume containing an active swapfile. |

Shared mount options for every subvolume: `noatime,compress=zstd,space_cache=v2`
plus `subvol=<name>` per mount. `noatime` because Btrfs CoW metadata
updates on atime writes are real overhead (the wiki's own tip).
`space_cache=v2` avoids the v1-deprecation warning. `compress=zstd` per
explicit user choice. `discard=async` is deliberately omitted - already
the kernel default since 6.2.

## Implementation surface

All changes are gated behind `filesystem == "btrfs"` (or the equivalent
`FilesystemType.BTRFS` enum check). Every other filesystem's code path is
untouched - confirmed via the codebase survey that today's `_format_partitions`,
`_mount_partitions`, and `_setup_swapfile` are simple if/elif chains keyed
on the `filesystem` string, so adding a new branch cannot alter existing
branches' behavior.

### `src/xen/jobs/partition.py`

1. `FilesystemType` enum: add `BTRFS = "btrfs"`.
2. `_format_partitions`: add a `btrfs` branch running
   `["mkfs.btrfs", "-f", "-L", "arch_root", root_target]`, then (AUTO mode
   only) create the five subvolumes: mount the top-level subvolume
   (`subvolid=5`) at a temporary mountpoint, `btrfs subvolume create` each
   of `@`, `@home`, `@var_log`, `@var_cache_pacman`, `@swap`, then unmount.
3. `_mount_partitions`: add a `btrfs` branch that, instead of the single
   `mount -t {filesystem} root_target target_root` call used by
   ext4/xfs, mounts `@` at `target_root` first, then `mkdir -p` +
   mounts `@home`/`@var_log`/`@var_cache_pacman`/`@swap` at their
   respective paths under `target_root`, all with the shared mount
   options above plus each one's own `subvol=`.
4. `_setup_swapfile`: add a filesystem-aware branch. When
   `filesystem == "btrfs"`, use
   `btrfs filesystem mkswapfile --size {size}m --uuid clear {target_root}/swap/swapfile`
   followed by `swapon`, instead of the `dd`+`chmod`+`mkswap` sequence.
   All other filesystems keep the existing sequence unchanged.
5. `_mkfs_command` (manual-mode helper): add `"btrfs"` returning
   `["mkfs.btrfs", "-f", path]` - manual mode gets plain Btrfs formatting
   with no subvolume auto-layout (see "Manual mode" below).

### `src/xen/jobs/systemconfig.py`

Add `_ensure_btrfs_module()`, called from `run()` when
`selections.get("filesystem") == "btrfs"`, before the existing mkinitcpio
hooks step: parses `MODULES=(...)` the same way `_configure_mkinitcpio_hooks`
already parses `HOOKS=(...)`, appends `btrfs` if not already present.

No other changes to this file - `_generate_fstab` and `_generate_crypttab`
are correct as-is (see Research basis).

### `src/xen/jobs/bootloader.py`

No changes.

### QML (`src/xen/gui/qml/components/PartitionView.qml`)

- AUTO-mode picker: add a third radio button for `"btrfs"`, matching the
  existing `ext4`/`xfs` pattern exactly (`filesystemSelected("btrfs")`,
  `checked: filesystem === "btrfs"`).
- Manual-mode per-partition format dropdown (three occurrences): add
  `"btrfs"` to each hardcoded `model: [...]` list.
- Update the two doc comments listing allowed filesystem values.

### `src/xen/gui/bridge.py`

No changes - `filesystem`/`setFilesystem` are already an unvalidated
passthrough; the allowlist lives solely in `partition.py`'s
`FilesystemType` check, which is being extended anyway.

### yaml config

No changes required - no profile declares a filesystem allowlist today
(enforcement is solely the Python enum). `default_filesystem` stays `xfs`/
`ext4` per profile; `btrfs` becomes an additional selectable value, not a
new default.

## Manual mode

Manual partitioning mode lets the user assign arbitrary mount points to
arbitrary partitions - the automatic five-subvolume layout above doesn't
fit that model (there's no single "the root partition" moment to hang
subvolume creation off). Manual mode gets plain `mkfs.btrfs` formatting
(via the `_mkfs_command` addition above) with no subvolumes - functionally
identical to how ext4/xfs already behave in manual mode today. Subvolume
support for manual mode is a stated future gap, not a regression (manual
mode has no automatic filesystem-specific behavior for any filesystem
today).

## Testing

Every new branch gets unit tests following the existing per-filesystem
test patterns in `tests/unit/test_partition_job.py` and
`tests/unit/test_systemconfig_job.py`:

- `_format_partitions` with `filesystem="btrfs"`: correct `mkfs.btrfs`
  invocation, correct subvolume-creation command sequence.
- `_mount_partitions` with `filesystem="btrfs"`: correct multi-mount
  sequence with correct `subvol=`/shared options per mountpoint.
- `_setup_swapfile` with `filesystem="btrfs"`: `mkswapfile` command
  instead of `dd`/`mkswap`.
- Regression tests confirming `filesystem="ext4"`/`"xfs"` behavior in all
  three methods is byte-for-byte unchanged from before this feature.
- `_ensure_btrfs_module`: appends `btrfs` to `MODULES=()`, no-ops if
  already present, no-ops entirely when filesystem isn't btrfs.
- Existing full suite must stay green (1058 tests as of this spec).

## Self-review notes

- No placeholders/TBDs.
- Scope check: single cohesive feature, one implementation plan.
- Ambiguity check: subvolume names, mount options, and job ordering are
  all explicit above; no open questions remain for the implementer.
