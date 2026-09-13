# Snapper + grub-btrfs + btrfs-assistant Design

**Status:** Implemented 2026-09-12. User asked to match CachyOS's real,
verified setup ("check how cachyos does it") rather than a generic Snapper
config - `https://github.com/CachyOS/CachyOS-PKGBUILDS` (`grub-btrfs-support`,
`cachyos-snapper-support`) and `https://github.com/CachyOS/cachyos-calamares`
were cloned and read directly. All values/mechanisms below are copied from
that real source, not guessed. 1098 tests passing, ruff/mypy clean.

**Two decisions made after this doc's first draft, both from direct user
correction:**
- **Offline: hidden entirely, not partially degraded.** The initial plan
  was to keep core Btrfs available offline and only skip the Snapper/
  grub-btrfs tooling. User explicitly rejected that: "either we implement
  it right or none at all not half-baked." Btrfs is now hidden from the
  UI (QML `btrfsAvailable: engine.hasInternet`) and rejected server-side
  in `partition.py`'s `validate()` when offline, unconditionally - not
  just when the snapshot tooling would be affected.
- **Subvolume layout changed to match CachyOS exactly**, not the
  ArchWiki-suggested layout the original Btrfs-support spec used. Real
  CachyOS `mount.conf` (`btrfsSubvolumes:`): `@`, `@home`, `@root`, `@srv`,
  `@cache` (all of `/var/cache`, not just pacman), `@tmp` (`/var/tmp`),
  `@log` (`/var/log`) - no dedicated `@snapshots` subvolume (snapper just
  creates its normal nested `.snapshots` inside `@`). Xen's already-shipped
  `@var_log`/`@var_cache_pacman` naming was renamed to match. `@swap` is
  kept in addition - a genuine Btrfs technical requirement (swapfiles
  can't live in a snapshotted subvolume) independent of naming convention,
  not present in CachyOS's list because they likely don't use a Btrfs
  swapfile by default.

**`archlinux.yaml` deliberately excluded** from the `snapshots` job and
`btrfs_packages` - that profile uses `systemd-boot`, and `grub-btrfs`
cannot function without GRUB. Adding the job there would be dead weight,
not "properly scoped." Core Btrfs (subvolumes/mount/swap, from the prior
feature) still works on that profile regardless of bootloader choice.

## Goal

When `filesystem == "btrfs"`: install and configure Snapper (with snap-pac
for automatic pre/post pacman-transaction snapshots), grub-btrfs (snapshots
show up as GRUB boot entries), and btrfs-assistant (GUI for managing both).
Take one permanent "fresh install" snapshot as the last step of install.
Zero effect on ext4/xfs installs.

## What CachyOS actually does (verified from source)

- Packages (for GRUB, which is Xen's only bootloader): `snapper`,
  `snap-pac`, `btrfs-assistant`, plus a `grub-btrfs-support` glue package
  that depends on the real `grub-btrfs` package.
- Snapper config template (`cachyos-root`, applied via
  `snapper -c root create-config --template cachyos-root /`):
  `TIMELINE_CREATE="no"` (no hourly noise) - instead `NUMBER_CLEANUP="yes"`,
  `NUMBER_MIN_AGE="1800"`, `NUMBER_LIMIT="50"`, `NUMBER_LIMIT_IMPORTANT="15"`,
  `SPACE_LIMIT="0.5"`, `FREE_LIMIT="0.2"`, `EMPTY_PRE_POST_CLEANUP="yes"`.
  Only `snapper-cleanup.timer` gets enabled - not `snapper-timeline.timer`.
- GRUB integration: instead of upstream `grub-btrfs`'s own `grub-btrfsd`
  inotify daemon, `grub-btrfs-support` ships a systemd `.path` unit
  (`grub-btrfs-snapper.path`, watches `/.snapshots` for modification) that
  triggers a oneshot `.service` regenerating `grub-btrfs.cfg` (or a full
  `grub-mkconfig` if that file doesn't exist yet). Lighter than the
  inotify-tools daemon, no extra dependency.
- `grub-btrfs-overlayfs` gets added to mkinitcpio `HOOKS=` (lets you boot
  directly into a read-only snapshot via an overlay, matching ArchWiki's
  own documented mechanism for this).
- No dedicated `@snapshots` subvolume - snapper just creates its normal
  nested `.snapshots` inside `@`. Xen's existing subvolume layout from the
  Btrfs feature needs no changes.
- Real exec sequence: `pacstrap` (installs the above packages) → ... →
  `initcpiocfg`/`initcpio` → ... → `bootloader` (grub-install +
  grub-mkconfig) → their finalize-equivalent shellprocess steps →
  `shellprocess@btrfs_snapshot` (the one permanent "fresh install"
  snapshot) → cleanup/umount. The snapshot is deliberately the very last
  content-changing step, capturing the fully-configured system.

XeroLinux has no custom repo for `grub-btrfs-support`/`cachyos-snapper-support`
- their effect is replicated directly in Xen's own Python job code instead
of depending on those exact packages.

## Implementation surface

All gated behind `context.selections.get("filesystem") == "btrfs"`.

### `src/xen/jobs/packages.py`

`_get_package_list`: after the existing `optional_packages` merge loop, add
a second merge for `self._config.get("btrfs_packages", [])`, applied only
when `context.selections.get("filesystem") == "btrfs"`. Same
already-installed-package dedup logic as the existing loop (skip if already
in `packages`).

### `config/examples/*.yaml`

`packages: config: btrfs_packages: [snapper, snap-pac, grub-btrfs, btrfs-assistant]`
in xerolinux.yaml, xerolinux-nvidia.yaml, and archlinux.yaml.

### `src/xen/jobs/snapshots.py` (new)

`SnapshotsJob`, `name = "snapshots"`. No-ops (`JobResult.ok`) immediately
when filesystem isn't btrfs. Otherwise, in order:

1. Write `/etc/snapper/config-templates/xen-root` on the target with the
   CachyOS-matching tuned values above (`SUBVOLUME="/"`, `FSTYPE="btrfs"`,
   the NUMBER_*/SPACE_LIMIT/FREE_LIMIT/EMPTY_PRE_POST_CLEANUP values,
   `TIMELINE_CREATE="no"`).
2. `arch-chroot target snapper --no-dbus -c root create-config --template xen-root /`
   - `--no-dbus` because snapperd isn't running inside `arch-chroot`
   (matches CachyOS's own "workaround if command is run inside chroot").
3. Write two systemd unit files to the target's `/etc/systemd/system/`
   (not `/usr/lib/systemd/system/`, which is reserved for package-managed
   files): `grub-btrfs-snapper.path` and `grub-btrfs-snapper.service`,
   content copied from CachyOS's real units (watch `/.snapshots`, run
   `/etc/grub.d/41_snapshots-btrfs` or fall back to a full `grub-mkconfig`).
4. `systemctl enable` (not `--now` - target isn't a running system)
   `snapper-cleanup.timer` and `grub-btrfs-snapper.path`.
5. Add `grub-btrfs-overlayfs` to the target's mkinitcpio `HOOKS=` and run
   its own `mkinitcpio -P` rebuild. This job runs after `packages` (so
   `grub-btrfs`'s overlayfs hook script already exists on disk) - it
   cannot be folded into `systemconfig.py`'s existing HOOKS-editing pass,
   which runs before `packages` and would reference a hook script that
   doesn't exist yet, failing `mkinitcpio -P`.

Job order: after `packages`, before `bootloader` - this also means the
`/.snapshots` subvolume and the `41_snapshots-btrfs` grub.d script already
exist by the time `bootloader.py`'s existing `grub-mkconfig` call runs, so
`bootloader.py` itself needs zero changes.

### `src/xen/jobs/finalize.py`

Add `_take_fresh_install_snapshot(target_root, context)`, called at the
end of `run()`, gated on `filesystem == "btrfs"`, non-fatal (log warning on
failure, never fails the job): `arch-chroot target snapper --no-dbus -c root create --description "Fresh installation"`
(no `--cleanup-algorithm` flag - Snapper defaults to no cleanup algorithm,
so this snapshot is permanent, matching CachyOS's own comment on why this
works: "the snapshot will never be automatically removed").

This runs after all of finalize's existing scripts and after `bootloader`
(finalize is the last content-changing job in the pipeline before
`finished`), matching CachyOS's exact "very last step" timing.

### `src/xen/jobs/systemconfig.py`, `src/xen/jobs/bootloader.py`

No changes.

## Testing

Same rigor as the Btrfs feature: unit tests for `_get_package_list`'s new
`btrfs_packages` merge (present when btrfs, absent otherwise, no
duplicates), `SnapshotsJob`'s no-op path and each step of its btrfs path,
`finalize.py`'s new snapshot step (called when btrfs, skipped otherwise,
non-fatal on failure). Full suite must stay green.

## Related fix found during real-VM testing (same session, two rounds)

Real Btrfs VM boot showed `error: commands/loadenv.c:check_blocklists:289:
sparse file not allowed` at every boot (cosmetic - system still booted).
Unrelated to Snapper/grub-btrfs directly, but found and fixed in the same
testing pass.

Round 1 (incomplete): confirmed against ArchWiki's `GRUB/Tips_and_tricks`
that `GRUB_DEFAULT=saved` "will only work if /boot is not a btrfs".
Forced `GRUB_DEFAULT="0"` for btrfs in `_write_grub_defaults`. User
re-tested: the error still appeared - this alone doesn't address the
actual trigger.

Round 2 (actual fix): reading GRUB's real, locally-installed
`/etc/grub.d/00_header` directly showed `if [ -s $prefix/grubenv ]; then
load_env; fi` is **unconditional** - gated only on the file existing and
being non-empty, independent of `GRUB_DEFAULT`/`GRUB_SAVEDEFAULT`.
`grub-install` creates `/boot/grub/grubenv` unconditionally regardless of
config. Web search confirmed this exact error is a long-standing,
widely-reported GRUB+Btrfs limitation across Fedora/Ubuntu/Arch/Manjaro/
EndeavourOS - not Xen-specific, no clean universal fix exists in the
ecosystem (CachyOS's own `no_save_default`/`GRUB_SAVEDEFAULT=false`
approach only prevents *future* corruption from repeated writes, doesn't
eliminate the message either per multiple reports). Fixed:
`bootloader.py._remove_unusable_grubenv()`, called after `grub-mkconfig`
in `_install_grub` when `filesystem == "btrfs"` - deletes
`/boot/grub/grubenv` so the existence check fails and `load_env` never
runs. Non-fatal. The round-1 `GRUB_DEFAULT="0"` change is kept and is now
better-justified: with `grubenv` deleted, `${saved_entry}` (what "saved"
resolves to) would be permanently empty anyway.

## Self-review

No placeholders. Scope is one cohesive feature (Snapper/grub-btrfs/
btrfs-assistant), single implementation plan. Every value copied from a
real, verified source (CachyOS's actual shipped packages) rather than
invented - no ambiguity left for the implementer.
