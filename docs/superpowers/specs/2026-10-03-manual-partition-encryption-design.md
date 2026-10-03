# Manual Partition Encryption Design

**Status:** Design, not yet implemented.

## Goal

Add LUKS2 encryption to manual partitioning, mirroring Calamares' own
per-partition `EncryptWidget` (a checkbox + passphrase pair embedded
directly in its Create/Edit partition dialogs) rather than inventing a new
UI pattern. Any partition can be encrypted independently - root, a
separate `/home`, a data partition, or swap - not just a single
auto-computed root like today's AUTO mode.

Bundled in the same pass: a real, currently-live bug affecting AUTO mode's
existing encryption today (see "GRUB/Argon2id compatibility" below), found
via user feedback during design review. Same root cause, same code path
family (`cryptsetup luksFormat`), fixed once for both modes.

## Decisions (confirmed with user before writing this spec)

1. **Scope:** any partition can be encrypted independently, not just root.
2. **Boot UX:** exactly one passphrase prompt at boot, regardless of how
   many partitions are encrypted. Only root's passphrase is ever typed by
   the user and remembered; every other encrypted partition auto-unlocks.
3. **Non-root passphrase UI:** a partition that isn't mounted at `/` shows
   only an "Encrypt this partition" checkbox - no passphrase field. The
   installer generates and manages that partition's passphrase internally;
   the user never sees or types it.

## Non-goals (explicitly out of scope for this pass)

- TPM-bound unlock (clevis/tpm2, Secure Boot measured-boot auto-unlock).
  Keyfile-in-initramfs is this project's existing pattern (`luksbootkeyfile.py`)
  and this design only generalizes it; TPM is a separate, larger feature.
- Re-keying / changing an already-set passphrase after Apply.
- Encrypting the ESP (`/boot/efi`) - structurally impossible, GRUB and
  systemd-boot both need to read it in the clear to find anything to boot.
- LVM-on-LUKS or any volume-manager layer. This installer has no LVM
  support today in either mode; out of scope here too.

## Research basis

- Calamares' `EncryptWidget` (`calamaresx-app/src/modules/partition/gui/
  EncryptWidget.{ui,cpp}`) is embedded in both `CreatePartitionDialog` and
  `EditExistingPartitionDialog` - a checkbox that reveals a password +
  confirm `QLineEdit` pair when checked. Confirms the per-partition
  checkbox+passphrase shape is the right one to mirror, though Calamares'
  widget doesn't do this project's root/non-root passphrase-UI split -
  that refinement is this design's own addition (decision 3 above), made
  possible by already capturing the Mount Point field in the same dialog.
- This codebase already has a complete LUKS2 pipeline for AUTO mode's
  single root partition: `partition.py`'s `_setup_luks()` (`cryptsetup
  luksFormat --type luks2 --batch-mode` + `luksOpen`), `PartitionLayout
  .root_target` resolving to `/dev/mapper/cryptroot` for every subsequent
  format/mount step, `bootloader.py` (`cryptdevice=`, `GRUB_ENABLE_CRYPTODISK`,
  `cryptkey=rootfs:<path>`), `systemconfig.py` (`/etc/crypttab`
  generation, mkinitcpio `encrypt` hook, embedding the keyfile into the
  initramfs via `FILES=`), and `luksbootkeyfile.py` (adds a random keyfile
  as a second LUKS keyslot so GRUB prompts once, not twice - once for
  itself, once again for mkinitcpio). This design generalizes all of it to
  work per-partition instead of off one global `selections["encryption"]`
  flag.
- `bootloader.py` supports both `systemd-boot` (default) and `grub`. The
  bootloader job's own yaml `type:` is authoritative (XeroLinux profiles
  set `type: grub`); nothing in the UI sets `selections["bootloader"]`.
  `Engine.bootloader_type()` resolves it (job `type:`, then
  `selections["bootloader"]`, then `systemd-boot`, matching
  `BootloaderJob._get_bootloader_type`), and `Engine.run_all` seeds the
  result into `context.selections["bootloader"]` once per run so jobs that
  run before the bootloader (partition) can read it from selections.
  AUTO mode's fixed 2-partition layout (EFI + Root, no separate `/boot`)
  means `/boot` always lives inside the encrypted root when encryption is
  on. Manual mode additionally allows a separate, unencrypted `/boot`
  partition ahead of an encrypted root (already a supported, validated
  layout per the existing boot/efi mountpoint-consistency rules) - this
  matters directly for the KDF fix below.
- Swap encryption is conventionally NOT LUKS2 with a remembered passphrase
  - the standard approach (Arch Wiki: dm-crypt/Swap encryption) is a plain
  dm-crypt mapping keyed by `/dev/urandom` via a `crypttab` line like
  `swap /dev/<part> /dev/urandom swap,cipher=aes-xts-plain64,size=256`, a
  fresh random key every boot. No `cryptsetup luksFormat` at all, no
  secret to manage or lose.

## GRUB/Argon2id compatibility (bug, affects AUTO mode too)

Reported by a user's own testing: `cryptsetup luksFormat` with no explicit
`--pbkdf` uses LUKS2's current default, Argon2id. GRUB's `cryptodisk`
module only understands PBKDF2 for LUKS2 - it cannot derive the key from
an Argon2id header at all. When GRUB itself must decrypt a volume directly
(its `core.img`, before any kernel is even loaded) to read `/boot/grub`
and the kernel/initramfs, an Argon2id-protected header makes that
impossible and the install fails to boot, dropping to GRUB's rescue
shell. This is **not** specific to manual mode or this new feature - it
already affects every AUTO-mode encrypted install that selects GRUB
today, because AUTO mode's fixed EFI+Root layout always puts `/boot`
inside the encrypted root.

This only matters when GRUB itself performs the decryption. It does not
apply when:
- The bootloader is `systemd-boot` instead - it reads the kernel/initramfs
  directly from the ESP (Arch convention: `/boot` mounted on the ESP
  itself) and never touches the LUKS header. Unlocking happens entirely
  in the initramfs via mkinitcpio's `encrypt` hook, which uses `cryptsetup`
  directly and has no such limitation - Argon2id works fine.
- GRUB is selected, but there's a separate, unencrypted `/boot` partition
  ahead of the encrypted root (manual mode only). GRUB reads `/boot/grub`
  and the kernel/initramfs from that plain partition and never needs to
  decrypt anything itself; mkinitcpio's `encrypt` hook does the unlocking
  later, same as the systemd-boot case. Argon2id is safe here too.

**Fix:** at the point a partition destined to be mounted at `/` is
LUKS-formatted, decide the KDF from whether GRUB will need to decrypt it
directly:

```
needs_pbkdf2 = (
    selections["bootloader"] == "grub"  # seeded by Engine.run_all from the
                                        # bootloader job's yaml `type:`
    and not <a separate partition is mounted at /boot ahead of this one>
)
```

When true, add `--pbkdf pbkdf2` to the `luksFormat` invocation (still
LUKS2, still fully supported by cryptsetup and the kernel - PBKDF2 is a
weaker KDF against offline brute-force than Argon2id, not an insecure or
deprecated one). When false, use cryptsetup's own default (Argon2id) -
the stronger choice, used whenever it's actually compatible with the
chosen boot path. This is a conditional, narrowly-scoped choice grounded
in a real technical incompatibility, not a blanket downgrade.

In AUTO mode there is structurally never a separate `/boot` partition, so
the condition simplifies to `bootloader == "grub"`.

Also add a short, non-blocking explanatory note under the root passphrase
field (both the manual Edit/New dialog and AUTO mode's existing encryption
section in `PartitionView.qml`) whenever this applies: *"GRUB can't unlock
Argon2id-protected volumes directly - using PBKDF2 for compatibility."*
Purely informational (the installer already chose correctly); prevents
the exact confusion the reporting user hit when they had to diagnose it
themselves after a failed first boot.

## UI (`ManualPartitionEditor.qml`)

In both the New Partition dialog (`newDialog`) and Edit Partition dialog
(`editDialog`), inside the `doFormat`-visible section (you cannot wrap
existing data in LUKS without destroying it - same constraint Calamares
enforces by only showing `EncryptWidget` when formatting):

- `CheckBox { text: "Encrypt this partition (LUKS2)" }`, bound to a new
  `encryptChoice` dialog property.
- When checked AND the dialog's Mount Point field is exactly `/`: reveal a
  passphrase `StyledField` (echoMode: Password) + a confirm field, with
  the same red/green border validity pattern the Users page's password
  field already uses, and the same minimum-length rule
  (`passwordField.text.length >= 8`-equivalent) as the user account
  password for consistency.
  - Also show the GRUB/Argon2id note here, conditionally, per above.
- When checked and Mount Point is anything else (or blank), show neither
  passphrase field - just a one-line caption: *"A random key is generated
  automatically and unlocked at boot - nothing to remember."* (swap gets
  its own caption instead: *"Encrypted with a fresh random key every
  boot."*)
- ESP: the checkbox doesn't render at all (same `visible` gating already
  used to hide other ESP-inappropriate controls in this dialog).

`confirm()` includes `encrypt` (bool) and, for root only,
`encryption_passphrase` in the `format` operation's `params` when queuing
the op (see below). Mirrors the existing pattern where `confirm()` already
assembles `format` op params for label/subvolumes.

## Operation model (`partition.py`)

No new operation type. `encrypt: bool` and (root only)
`encryption_passphrase: str` become optional keys in an existing `format`
operation's `params`, alongside the existing `label`/`subvolumes` keys.
LUKS-wrapping is inherently a pre-step to formatting the same partition,
exactly mirroring how AUTO mode's `_setup_luks()` already runs
immediately before `_format_with_optional_subvolumes()` today.

`validate_operations` gains:
- At most one `format` op across the whole plan may have both `encrypt`
  and a mountpoint resolving to `/`.
- An `encrypt` op targeting the ESP (fstype `vfat` with the `esp` flag) is
  rejected - same shape as the existing `bios_grub`-not-supported check.
- A non-root `encrypt` op must not also carry `encryption_passphrase` -
  that field is root-only, both in the UI and as a defense-in-depth
  validation (prevents a stray/stale passphrase lingering in an operation
  object for a partition it was never meant for).

`PartitionJob` apply-time handling for a `format` op with `encrypt: true`:

1. If mounted at `/`: `cryptsetup luksFormat` with the user's passphrase,
   picking PBKDF2 or Argon2id per the KDF-selection logic above.
2. If swap: skip LUKS entirely - append the dm-crypt/random-key
   `crypttab` line instead (`systemconfig.py`, see below) and do not run
   `mkswap`/format on the raw partition at all - it runs on the resulting
   mapper device instead, exactly like a LUKS-wrapped filesystem would.
3. Otherwise (non-root, non-swap - e.g. `/home`, a data partition):
   `cryptsetup luksFormat` with an internally-generated random passphrase
   (`secrets.token_urlsafe` or equivalent, never surfaced to the UI, never
   logged - held only in-process for the duration of Apply).
4. `luksOpen` the result, `mkfs.<fstype>` runs on the `/dev/mapper/<name>`
   device - identical to how `PartitionLayout.root_target` already
   resolves for AUTO mode's root.
5. For case 3 only (non-root, non-swap): immediately add a random keyfile
   as a second LUKS keyslot and register it for a `crypttab` entry +
   keyfile placement under the installed root (not necessarily embedded
   in the initramfs - see below). This generalizes `luksbootkeyfile.py`'s
   existing root-only logic to run once per such partition.

Mapper naming: AUTO mode's single hardcoded `"cryptroot"` doesn't
generalize to multiple encrypted partitions. Manual mode derives a mapper
name per partition from its mountpoint (`cryptroot` for `/`, `crypthome`
for `/home`, `cryptdata-<n>` for anything else, `cryptswap` for swap),
sanitized to a safe mapper-name charset. `PartitionLayout` (or its manual
equivalent) tracks a `{partition_number: mapper_name}` map instead of the
single `luks_mapper_name` string.

## Boot chain (`bootloader.py`, `systemconfig.py`)

Both currently read a single `context.selections["encryption"]` /
`"root_partition"` pair. Replace that read with: "is the partition
currently resolved to be mounted at `/` encrypted?", sourced from the
manual plan's resolved M1 mount assignments when in manual mode, or the
existing single `encryption` selection when in auto mode (auto mode's own
behavior is unchanged - this is purely widening what feeds the same
decision point, not altering AUTO mode's existing logic). Same
`cryptdevice=`, `GRUB_ENABLE_CRYPTODISK`, mkinitcpio `encrypt` hook, and
keyfile-in-initramfs machinery applies either way.

Non-root encrypted partitions (home, a data partition) are NOT on the
early-boot critical path - they mount after root is already up, the
normal way systemd's `systemd-cryptsetup-generator` processes
`/etc/crypttab` post-pivot. Their keyfiles live directly in the installed
root filesystem (e.g. `/etc/cryptsetup-keys.d/home.key`, mode `000`, root
-owned) - no initramfs embedding needed, since they're not required to
unlock anything before root itself is mounted. `systemconfig.py`'s
crypttab generation gains one line per such partition:
`<mapper-name> UUID=<luks-uuid> <keyfile-path> luks`.

Swap's crypttab line is structurally different (no LUKS, no keyfile - see
"Operation model" case 2 above):
`<mapper-name> UUID=<raw-partition-uuid> /dev/urandom swap,cipher=aes-xts-plain64,size=256`,
and the existing swap/fstab wiring points at `/dev/mapper/<mapper-name>`
instead of the raw partition when this applies.

## Security

- Non-root passphrases (generated internally) and all keyfiles follow the
  same "never log" posture `bridge.py` already enforces for
  `encryptionPassphrase` via its `secret_keys` set - extended to redact
  `encryption_passphrase` wherever operation params get printed/logged
  (debug output, the removed-but-still-present-in-bridge
  `commandPreview`, any future operation-dump path).
- Generated passphrases and keyfiles exist only in-process during Apply
  and as files under the target root (never the live ISO's own
  filesystem) with restrictive permissions, matching
  `luksbootkeyfile.py`'s existing `KEYFILE_PATH`/mode-000 pattern.
- `--pbkdf pbkdf2` is a compatibility-driven algorithm choice grounded in
  a real GRUB limitation, applied only when that limitation actually
  applies (see "GRUB/Argon2id compatibility") - not a blanket security
  downgrade.

## Testing

- `tests/unit/test_partition_operations.py`: `encrypt`/`encryption_passphrase`
  parsing and validation (at-most-one-root-encrypted, ESP rejection,
  non-root-passphrase rejection).
- `tests/unit/test_partition_job.py`: apply-time LUKS setup for root,
  non-root (keyfile path), and swap (random-key crypttab, no luksFormat
  call) - mocked `cryptsetup` calls, same pattern the existing
  `_setup_luks` tests already use.
- `tests/unit/test_partition_safeguards.py`: KDF selection logic
  (systemd-boot vs GRUB, with/without a separate `/boot`), for both auto
  and manual mode shapes.
- `tests/unit/test_bootloader_job.py`: crypttab/kernel-param generation
  reading the manual plan's resolved root mount instead of the single
  auto-mode selection, confirming AUTO mode's existing tests still pass
  unchanged.
- `tests/integration/test_partition_view_manual.py`: encrypt checkbox
  visibility rules (Format-only, ESP hidden, passphrase fields root-only),
  offscreen, no QML warnings.

## Addendum: new-partition reconciliation and passphrase carryover

Found while writing the implementation plan, confirmed with the user
before encoding it here.

**The gap:** a brand-new partition's mount point (set in the New dialog)
was already non-functional before this feature - `_op_create` never reads
`params["mountpoint"]`, and `bridge._partition_assignments` only gets
populated by the Edit dialog's `setPartitionMount`, keyed by a partition's
*real* kernel-assigned name. A new partition is queued under a throwaway
`"NEW-<timestamp>"` placeholder, which nothing ever reconciles to the real
name afterward. Encryption's LUKS mapper device needs to replace the raw
path at exactly that same mount-resolution point, so it inherits this gap
unless fixed. Decision: fix it, not route around it.

**Reconciliation mechanism:** after `applyPartitionOperations()` succeeds
and before the queue is cleared, `bridge.py` walks the just-applied
operations. For each `create`/`format` op carrying `mountpoint` and/or
`encrypt`, it resolves the operation's real resulting partition name by
matching `start_sector` against the freshly-rescanned disk geometry (the
same sector-matching `_resolve_partition_number` already does
job-side, mirrored client-side against `refreshDisks()`'s new segment
list), then promotes that op's `mountpoint`/`fstype`/`subvolumes` into
`_partition_assignments[<real_name>]` - fixing the pre-existing gap as a
natural side effect of needing the same resolution for encryption. When
`encrypt` was set, that assignment's `path` is additionally overwritten to
`/dev/mapper/<name>` (via the same `_manual_mapper_name(mountpoint)` pure
function the backend job used at Apply time, recomputed independently so
no new plumbing has to carry the mapper name across the job/bridge
boundary).

**Passphrase carryover for non-root keyfiles:** the per-partition keyfile
step (generalizing `luksbootkeyfile.py`'s pattern to every non-root
encrypted partition) needs to run with `target_root` mounted - i.e. at
real install time, not during the live "Apply Changes" in the editor.
`cryptsetup luksAddKey` requires authenticating with an *existing*
credential, which for a non-root partition is the randomly-generated
passphrase used at live-Apply time, in a different job instance. Since
both live-Apply and the real install run in the same long-lived GUI
process (no reboot between them), `bridge.py` holds
`self._manual_luks_passphrases: dict[str, str]` (real partition name ->
generated passphrase) in memory for the session, populated when a
non-root partition's live-Apply encryption succeeds, and threads it into
`context.selections["manual_luks_passphrases"]` at install time (same
"never log" posture as every other passphrase in this codebase). Root's
passphrase needs no such carryover - it's typed by the user and already
flows through `context.selections["encryption_passphrase"]` like AUTO
mode's does today.

**Amended after the final whole-branch review:** root's typed passphrase
IS carried forward too (same `manual_luks_passphrases` map, keyed by the
real partition name), so `luksbootkeyfile.py` can add the single-prompt
keyfile for a manual encrypted root. It only does so when GRUB decrypts
root directly (no separate plain `/boot`); with systemd-boot or a plain
`/boot` the initramfs is unencrypted and must never carry root's key.
"Which assignment is root" is decided in one place
(`partition.resolve_manual_root`, Btrfs-subvolume-aware), and the
encryption rules gate Apply against the already-applied assignments plus
the queued batch, with a final consistency check at install time.

## Self-review notes

- No placeholders/TBDs left - every mechanism above has a concrete
  implementation surface named.
- Checked for contradictions: the "one prompt total" decision and the
  "non-root keyfile, not initramfs-embedded" mechanism are consistent -
  only root's keyfile needs initramfs embedding (early-boot critical
  path); non-root keyfiles living in the installed root filesystem is
  sufficient since they're read post-pivot.
- Scope check: single implementation plan is reasonable - this is one
  coherent feature (plus one tightly-related bugfix sharing the same code
  path), not several unrelated ones.
- Ambiguity check: "any partition independently" (decision 1) could have
  been read as "any number of simultaneous encrypted partitions" or "any
  one partition, user's choice of which" - made explicit as the former
  throughout (root + home + a data partition all encryptable at once).
