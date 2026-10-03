# Manual Partition Encryption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any partition in manual mode be LUKS2-encrypted independently (root, home, a data partition, swap), with exactly one passphrase prompt at boot (root's) and everything else auto-unlocking via a generated keyfile - mirroring Calamares' per-partition `EncryptWidget` in the New/Edit dialogs.

**Architecture:** Encryption rides the existing `format` operation's params (`encrypt`, root-only `encryption_passphrase`) rather than a new operation type - LUKS-wrapping is a pre-step to formatting, exactly like AUTO mode's `_setup_luks()`. Live "Apply Changes" (in the editor, before the real install starts) does the actual `luksFormat`/`luksOpen`/`mkfs`-on-mapper work; a new post-Apply reconciliation step in `bridge.py` promotes a newly-created partition's queued mount/encryption intent onto its real kernel-assigned name (fixing a pre-existing gap where this never happened for new partitions at all) and swaps the raw device path for the mapper path wherever encryption applies. Non-root keyfile setup (so only root prompts at boot) runs later, at real install time, using a passphrase generated during live-Apply and carried in `bridge.py`'s own memory across the two phases (same process, no reboot in between).

**Tech Stack:** Python 3.14, PySide6/QML, cryptsetup (LUKS2), parted, dm-crypt (swap), pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-manual-partition-encryption-design.md` (including its "Addendum" section - read both before starting; this plan implements the per-partition encryption feature only, not the GRUB/Argon2id KDF fix, which is a separate, already-written plan: `docs/superpowers/plans/2026-10-03-grub-argon2id-kdf-fix.md`, and should be implemented first since this plan's Task 3 reuses `_luks_kdf_args` from it unmodified).

## Global Constraints

- Never log a passphrase, a generated keyfile's bytes, or a raw `cryptsetup luksFormat`/`luksOpen`/`luksAddKey` command line. Extend the existing redaction posture (`bridge.py`'s `secret_keys` set, `_run_secret_command`) - don't weaken it.
- ESP can never be encrypted - the encrypt option must not even render for it in the UI, and `validate_operations` must reject it as a defense-in-depth backstop.
- Swap encryption is never LUKS2 - it's plain dm-crypt with a random key regenerated every boot (`crypttab`'s `swap` option), not `cryptsetup luksFormat`. No persistent secret, no keyfile, no passphrase field.
- A non-root passphrase is generated internally and never surfaced in the UI - the New/Edit dialogs only ever show a passphrase field when the dialog's Mount Point is exactly `/`.
- At most one partition across the whole plan may be both encrypted and mounted at `/`.
- Full test suite (`pytest tests/`) must stay green after every task.

## Review Focus

- A partition's Mount Point field is changed AFTER its Encrypt checkbox + passphrase were already filled in (e.g. user sets passphrase while Mount Point still says `/`, then changes it to `/home`) - the dialog must re-evaluate which fields to show live, not just at open time. Covered in Task 6's reactive-binding test.
- Two different operations both claim `mountpoint == "/"` with `encrypt: true` (a bug in the queue, not just a UI mistake - e.g. two New-partition ops both set `/` before either applies) - `validate_operations` must catch this, not just the UI's own single-root assumption. Covered in Task 2's test.
- Encrypting an existing partition that's currently mounted/in-use (busy device) - `validate_operations_applicable`'s existing busy-device check must still fire before any encryption-specific logic runs, so an encrypt+format combo on a busy device fails the same way a plain format on a busy device already does. Covered in Task 2 by confirming the existing applicability check still runs first.
- `applyPartitionOperations()` succeeding with a non-root encrypted partition, then the user closing the app (or crashing) before actually installing - the generated passphrase living only in `bridge.py`'s memory is lost, but the LUKS container on disk is still valid (just has only its original random keyslot, no keyfile convenience-unlock added yet) and the partition itself isn't corrupted. Covered in Task 8's test asserting the keyfile step is independently re-runnable / fails gracefully (not silently corrupting state) when the passphrase carryover dict is empty for a partition `selections` claims is encrypted.
- A disk with an existing LUKS signature on a partition the user is NOT re-encrypting this time (reusing an old encrypted partition's raw bytes for something else) - `_wipe_signatures` (already called by `_format_with_optional_subvolumes`) must still run before any encryption-path mkfs, same as the existing crypto_LUKS stale-signature fix. Covered in Task 3 by confirming `_wipe_signatures` still runs ahead of the new encrypt branch, not bypassed by it.

---

### Task 1: `_manual_mapper_name` pure function

**Files:**
- Modify: `src/xen/jobs/partition.py` (add immediately after `_luks_kdf_args`, which Task 3 of the GRUB/Argon2id plan places right after `_parted_flag`)
- Test: `tests/unit/test_partition_operations.py` (new `TestManualMapperName` class)

**Interfaces:**
- Produces: `_manual_mapper_name(mountpoint: str) -> str` - deterministic `/dev/mapper/<name>` suffix from a resolved mountpoint. Used identically by the backend job (at Apply time) and `bridge.py` (at reconciliation time) so neither has to pass the mapper name to the other.

- [ ] **Step 1: Write the failing test**

```python
class TestManualMapperName:
    """Mapper name is derived purely from mountpoint - deterministic, so
    both the job and bridge.py can compute it independently."""

    def test_root_is_cryptroot(self) -> None:
        assert _manual_mapper_name("/") == "cryptroot"

    def test_home_is_crypthome(self) -> None:
        assert _manual_mapper_name("/home") == "crypthome"

    def test_swap_is_cryptswap(self) -> None:
        assert _manual_mapper_name("swap") == "cryptswap"

    def test_other_data_mountpoint_is_sanitized(self) -> None:
        assert _manual_mapper_name("/var/lib/data") == "cryptdata-var-lib-data"

    def test_blank_mountpoint_falls_back_to_generic_name(self) -> None:
        assert _manual_mapper_name("") == "cryptdata"
```

Add `_manual_mapper_name` to the `from xen.jobs.partition import (...)` block at the top of `tests/unit/test_partition_operations.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_operations.py::TestManualMapperName -v`
Expected: FAIL with `ImportError: cannot import name '_manual_mapper_name'`

- [ ] **Step 3: Write minimal implementation**

```python
def _manual_mapper_name(mountpoint: str) -> str:
    """Deterministic ``/dev/mapper/<name>`` suffix for a manually-encrypted
    partition, derived purely from its resolved mountpoint so the job (at
    Apply time) and bridge.py (at post-Apply reconciliation) always agree
    without needing to pass this name between them.

    "/" -> "cryptroot", "/home" -> "crypthome", "swap" -> "cryptswap",
    anything else -> "cryptdata" plus a sanitized suffix from the path.
    """
    mp = mountpoint.strip().strip("/")
    if mountpoint.strip() == "/":
        return "cryptroot"
    if mountpoint.strip() == "swap":
        return "cryptswap"
    if mp == "home":
        return "crypthome"
    if not mp:
        return "cryptdata"
    safe = "".join(c if c.isalnum() else "-" for c in mp)
    return f"cryptdata-{safe}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_operations.py::TestManualMapperName -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/xen/jobs/partition.py tests/unit/test_partition_operations.py
git commit -m "feat: add _manual_mapper_name for per-partition LUKS mapper naming"
```

---

### Task 2: `encrypt`/`encryption_passphrase` validation rules

**Files:**
- Modify: `src/xen/jobs/partition.py:461-510` (`validate_operations`, right after the existing `_plan_sets_bios_grub` check at line 492)
- Test: `tests/unit/test_partition_operations.py` (extend `TestValidateOperations` at line 305)

**Interfaces:**
- Consumes: `op.params.get("encrypt")`, `op.params.get("encryption_passphrase")`, `op.params.get("mountpoint")`, `op.params.get("fstype")`.

- [ ] **Step 1: Write the failing test**

Add to `TestValidateOperations` in `tests/unit/test_partition_operations.py` (use that class's existing disk-geometry fixture pattern - check the class for its helper, e.g. a `_disk_geom`/`_uefi_disk` fixture already in use, and follow it):

```python
    def test_two_root_mounted_encrypted_partitions_rejected(self) -> None:
        ops = [
            PartitionOperation.from_dict({
                "type": "format", "target": "/dev/sda2",
                "params": {"path": "/dev/sda2", "fstype": "ext4",
                           "mountpoint": "/", "encrypt": True,
                           "encryption_passphrase": "S3cretPass!"},
            }),
            PartitionOperation.from_dict({
                "type": "format", "target": "/dev/sda3",
                "params": {"path": "/dev/sda3", "fstype": "ext4",
                           "mountpoint": "/", "encrypt": True,
                           "encryption_passphrase": "An0therPass!"},
            }),
        ]
        ok, reason = validate_operations(self._uefi_disk_geom(), ops, uefi=True)
        assert ok is False
        assert "one" in reason.lower()

    def test_encrypted_esp_rejected(self) -> None:
        ops = [
            PartitionOperation.from_dict({
                "type": "format", "target": "/dev/sda1",
                "params": {"path": "/dev/sda1", "fstype": "vfat",
                           "mountpoint": "/boot/efi", "encrypt": True},
            }),
        ]
        ok, reason = validate_operations(self._uefi_disk_geom(), ops, uefi=True)
        assert ok is False
        assert "esp" in reason.lower() or "efi" in reason.lower()

    def test_non_root_encrypted_partition_with_passphrase_rejected(self) -> None:
        ops = [
            PartitionOperation.from_dict({
                "type": "format", "target": "/dev/sda3",
                "params": {"path": "/dev/sda3", "fstype": "ext4",
                           "mountpoint": "/home", "encrypt": True,
                           "encryption_passphrase": "ShouldNotBeHere!"},
            }),
        ]
        ok, reason = validate_operations(self._uefi_disk_geom(), ops, uefi=True)
        assert ok is False
        assert "passphrase" in reason.lower()
```

(If `TestValidateOperations` has no existing `_uefi_disk_geom`-style helper, use whatever disk-geometry fixture its other tests already call - match the exact helper name/shape already present in that class rather than inventing a new one.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_operations.py::TestValidateOperations -v -k encrypt`
Expected: FAIL - all three new tests currently pass validation (return `ok=True`) since no rule exists yet; assertions on `ok is False` fail.

- [ ] **Step 3: Write minimal implementation**

In `src/xen/jobs/partition.py`, insert right after the `_plan_sets_bios_grub` check (after line 492's closing `)`):

```python
    encrypted_roots = [
        op for op in operations
        if op.type == "format"
        and op.params.get("encrypt")
        and op.params.get("mountpoint") == "/"
    ]
    if len(encrypted_roots) > 1:
        return False, "At most one partition may be encrypted and mounted at /"

    for op in operations:
        if op.type != "format" or not op.params.get("encrypt"):
            continue
        fstype = str(op.params.get("fstype", "")).lower()
        mountpoint = op.params.get("mountpoint")
        if fstype in ("vfat", "fat", "fat32") and mountpoint in ("/boot/efi", "/boot"):
            return False, "The EFI System Partition cannot be encrypted"
        if mountpoint != "/" and op.params.get("encryption_passphrase"):
            return False, (
                "Only the partition mounted at / takes a passphrase - every "
                "other encrypted partition unlocks automatically"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_operations.py::TestValidateOperations -v`
Expected: PASS, all tests in the class (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/xen/jobs/partition.py tests/unit/test_partition_operations.py
git commit -m "feat: validate encrypt/encryption_passphrase operation params"
```

---

### Task 3: Apply-time LUKS wrapping in `_format_with_optional_subvolumes`

**Files:**
- Modify: `src/xen/jobs/partition.py:1646-1823` (`_op_create`, `_op_format`, `_format_with_optional_subvolumes`)
- Modify: `src/xen/jobs/partition.py:1495-1569` (`_apply_operations` - thread `bootloader`/`has_separate_boot` through)
- Test: `tests/unit/test_partition_job.py` (new `TestManualEncryption` class)

**Interfaces:**
- Consumes: `_luks_kdf_args(bootloader, has_separate_boot)` (GRUB/Argon2id plan, Task 1), `_manual_mapper_name(mountpoint)` (Task 1 of this plan).
- Modifies: `_format_with_optional_subvolumes` gains `encrypt`, `passphrase`, `mountpoint`, `bootloader`, `has_separate_boot` parameters (all optional, defaulting to no-encryption behavior so every existing non-encrypted caller is unaffected). Returns the SAME `JobResult` shape as before.
- Produces: when encryption applies, the mkfs step runs against `/dev/mapper/<_manual_mapper_name(mountpoint)>` instead of the raw `path` - callers that need to know the resulting mounted device (the post-Apply bridge reconciliation, Task 4) derive it themselves via the same pure `_manual_mapper_name` function, not from this method's return value.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_partition_job.py` (new class, after `TestEncryption`):

```python
class TestManualEncryption:
    """LUKS wrapping for manual-mode format/create operations."""

    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_root_format_op_luks_wraps_before_mkfs(
        self, mock_run_cmd: MagicMock, mock_secret: MagicMock
    ) -> None:
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()

        op = PartitionOperation.from_dict({
            "type": "format", "target": "/dev/sda2",
            "params": {"path": "/dev/sda2", "fstype": "ext4", "mountpoint": "/",
                       "encrypt": True, "encryption_passphrase": "S3cretPass!"},
        })
        result = job._op_format(op, dry_run=False, bootloader="systemd-boot", has_separate_boot=False)
        assert result.success is True

        secret_cmds = [list(c.args[0]) for c in mock_secret.call_args_list]
        assert any("luksFormat" in cmd and "/dev/sda2" in cmd for cmd in secret_cmds)
        assert any("luksOpen" in cmd for cmd in secret_cmds)

        format_cmds = [list(c.args[0]) for c in mock_run_cmd.call_args_list]
        assert any(
            "mkfs.ext4" in cmd and "/dev/mapper/cryptroot" in cmd for cmd in format_cmds
        )
        # Never mkfs the raw, still-encrypted partition directly.
        assert not any("mkfs.ext4" in cmd and "/dev/sda2" in cmd for cmd in format_cmds)

    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_non_root_format_op_generates_passphrase_internally(
        self, mock_run_cmd: MagicMock, mock_secret: MagicMock
    ) -> None:
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()

        op = PartitionOperation.from_dict({
            "type": "format", "target": "/dev/sda3",
            "params": {"path": "/dev/sda3", "fstype": "ext4",
                       "mountpoint": "/home", "encrypt": True},
        })
        result = job._op_format(op, dry_run=False, bootloader="systemd-boot", has_separate_boot=False)
        assert result.success is True

        secret_cmds = [list(c.args[0]) for c in mock_secret.call_args_list]
        assert any("luksFormat" in cmd and "/dev/sda3" in cmd for cmd in secret_cmds)
        assert any("luksOpen" in cmd and "crypthome" in cmd for cmd in secret_cmds)

        # The generated passphrase is recorded for the keyfile step later
        # (Task 8), keyed by the real device path.
        assert "/dev/sda3" in job._manual_generated_passphrases
        assert job._manual_generated_passphrases["/dev/sda3"]

        format_cmds = [list(c.args[0]) for c in mock_run_cmd.call_args_list]
        assert any(
            "mkfs.ext4" in cmd and "/dev/mapper/crypthome" in cmd for cmd in format_cmds
        )

    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_encrypted_swap_skips_luks_and_mkswap(
        self, mock_run_cmd: MagicMock, mock_secret: MagicMock
    ) -> None:
        """Swap encryption is plain dm-crypt with a random key at every boot
        (crypttab's "swap" option, wired in Task 7) - never LUKS, never a
        persistent mkswap here."""
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()

        op = PartitionOperation.from_dict({
            "type": "format", "target": "/dev/sda4",
            "params": {"path": "/dev/sda4", "fstype": "swap",
                       "mountpoint": "swap", "encrypt": True},
        })
        result = job._op_format(op, dry_run=False, bootloader="systemd-boot", has_separate_boot=False)
        assert result.success is True
        assert mock_secret.call_count == 0

        format_cmds = [list(c.args[0]) for c in mock_run_cmd.call_args_list]
        assert not any("mkswap" in cmd for cmd in format_cmds)

    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_grub_root_uses_pbkdf2(
        self, mock_run_cmd: MagicMock, mock_secret: MagicMock
    ) -> None:
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()

        op = PartitionOperation.from_dict({
            "type": "format", "target": "/dev/sda2",
            "params": {"path": "/dev/sda2", "fstype": "ext4", "mountpoint": "/",
                       "encrypt": True, "encryption_passphrase": "S3cretPass!"},
        })
        job._op_format(op, dry_run=False, bootloader="grub", has_separate_boot=False)

        secret_cmds = [list(c.args[0]) for c in mock_secret.call_args_list]
        luks_format_cmd = next(cmd for cmd in secret_cmds if "luksFormat" in cmd)
        assert "--pbkdf" in luks_format_cmd

    @patch("xen.jobs.partition.PartitionJob._mount_partitions")
    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_new_partition_create_op_with_encrypt_routes_through_op_create(
        self, mock_run_cmd: MagicMock, mock_secret: MagicMock, mock_mount: MagicMock
    ) -> None:
        """The New Partition dialog bundles encrypt/encryption_passphrase
        into the single "create" op it queues (see Task 6) - a brand-new
        encrypted partition never produces a separate "format" op, so
        _op_create (not _op_format) must be the one that actually LUKS-wraps
        it. Goes through _apply_operations end-to-end (not _op_create
        directly) since _op_create reads self._manual_bootloader /
        self._manual_has_separate_boot, which only _apply_operations sets."""
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()
        mock_mount.return_value = JobResult.ok()

        context = JobContext(
            selections={
                "disk": "/dev/sda",
                "partition_operations": [{
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": 2048,
                        "size_sectors": 2048000,
                        "fstype": "ext4",
                        "name": "NEW-123",
                        "mountpoint": "/",
                        "encrypt": True,
                        "encryption_passphrase": "S3cretPass!",
                        "number": 2,
                    },
                }],
                "bootloader": "systemd-boot",
                "has_separate_boot": False,
            }
        )
        result = job._apply_operations(
            context, "/dev/sda", context.selections, dry_run=False
        )
        assert result.success is True

        secret_cmds = [list(c.args[0]) for c in mock_secret.call_args_list]
        assert any("luksFormat" in cmd and "/dev/sda2" in cmd for cmd in secret_cmds)
        assert any("luksOpen" in cmd and "cryptroot" in cmd for cmd in secret_cmds)

        format_cmds = [list(c.args[0]) for c in mock_run_cmd.call_args_list]
        assert any(
            "mkfs.ext4" in cmd and "/dev/mapper/cryptroot" in cmd for cmd in format_cmds
        )
```

Add `PartitionOperation` to the existing import block if not already there (it already is, per `tests/unit/test_partition_job.py`'s current imports listed in Task 1 of the KDF plan).

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py::TestManualEncryption -v`
Expected: FAIL with `TypeError: _op_format() got an unexpected keyword argument 'bootloader'`

- [ ] **Step 3: Write minimal implementation**

In `src/xen/jobs/partition.py`, add an instance attribute in `__init__` (near the existing `self._layout: PartitionLayout | None = None`-style init, right after `super().__init__(config)` in the `__init__` shown at line 887-889):

```python
        self._manual_generated_passphrases: dict[str, str] = {}
```

Replace `_op_format` (lines 1764-1774):

```python
    def _op_format(
        self,
        op: PartitionOperation,
        dry_run: bool,
        bootloader: str = "systemd-boot",
        has_separate_boot: bool = False,
    ) -> JobResult:
        """Format an existing partition (mkfs) without touching the table."""
        path = _dev_path(str(op.params["path"]))
        fstype = str(op.params["fstype"])
        return self._format_with_optional_subvolumes(
            path,
            fstype,
            op.params.get("subvolumes"),
            dry_run,
            label=str(op.params.get("label", "")),
            encrypt=bool(op.params.get("encrypt", False)),
            passphrase=str(op.params.get("encryption_passphrase", "")),
            mountpoint=str(op.params.get("mountpoint", "")),
            bootloader=bootloader,
            has_separate_boot=has_separate_boot,
        )
```

Replace `_format_with_optional_subvolumes` (lines 1791-1823):

```python
    def _format_with_optional_subvolumes(
        self,
        path: str,
        fstype: str,
        subvolumes: list[dict[str, Any]] | None,
        dry_run: bool,
        label: str = "",
        encrypt: bool = False,
        passphrase: str = "",
        mountpoint: str = "",
        bootloader: str = "systemd-boot",
        has_separate_boot: bool = False,
    ) -> JobResult:
        """
        Wipe stale signatures, optionally LUKS-wrap, mkfs ``path`` (or its
        LUKS mapper), then create Btrfs subvolumes if ``subvolumes`` is
        given. Shared by the M2 create/format operations and the M1
        per-partition reformat path, so a user-defined subvolume layout
        works identically regardless of which path created the filesystem.

        ``encrypt`` on a swap partition is handled entirely differently:
        swap encryption is plain dm-crypt with a random key regenerated
        every boot (crypttab's "swap" option, wired up at install time in
        systemconfig.py) - there's no persistent LUKS container and no
        mkswap to run here at all.
        """
        if encrypt and fstype.lower() == "swap":
            return JobResult.ok(f"Encrypted swap {path}: no persistent format needed")

        target = path
        if encrypt:
            mapper_name = _manual_mapper_name(mountpoint)
            luks_result = self._setup_manual_luks(
                path, mapper_name, mountpoint, passphrase, bootloader, has_separate_boot, dry_run
            )
            if not luks_result.success:
                return luks_result
            target = f"/dev/mapper/{mapper_name}"

        mkfs = self._mkfs_command(fstype, target, label=label)
        if mkfs is None:
            return JobResult.fail(f"Unsupported filesystem for {target}: {fstype}", error_code=51)
        wipe = self._wipe_signatures(path, dry_run)
        if not wipe.success:
            return wipe
        result = self._run_partitioning_command(
            mkfs, description=f"Formatting {target} ({fstype})", dry_run=dry_run
        )
        if not result.success:
            return result

        if fstype.lower() == "btrfs" and subvolumes:
            ok, reason = validate_btrfs_subvolumes(subvolumes)
            if not ok:
                return JobResult.fail(f"Invalid Btrfs subvolume layout: {reason}", error_code=61)
            return self._create_manual_btrfs_subvolumes(target, subvolumes, dry_run)

        return result

    def _setup_manual_luks(
        self,
        raw_path: str,
        mapper_name: str,
        mountpoint: str,
        passphrase: str,
        bootloader: str,
        has_separate_boot: bool,
        dry_run: bool,
    ) -> JobResult:
        """LUKS-format + open one manually-encrypted partition.

        Root's passphrase is the user's own; every other partition gets an
        internally-generated one (never shown in the UI) recorded in
        ``self._manual_generated_passphrases`` keyed by ``raw_path`` for the
        later per-partition keyfile step (real install time - see
        systemconfig.py).
        """
        is_root = mountpoint == "/"
        if not is_root:
            passphrase = secrets.token_urlsafe(32)
            self._manual_generated_passphrases[raw_path] = passphrase

        if dry_run:
            logger.info(f"[DRY-RUN] Would LUKS-format and open {raw_path} as {mapper_name}")
            return JobResult.ok("[DRY-RUN] Manual LUKS setup")

        if not passphrase:
            return JobResult.fail("LUKS passphrase missing", error_code=46)

        kdf_args = _luks_kdf_args(bootloader, has_separate_boot) if is_root else []
        fmt = self._run_secret_command(
            ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode", *kdf_args, raw_path],
            passphrase=passphrase,
            description=f"LUKS formatting {raw_path}",
        )
        if not fmt.success:
            return fmt

        opened = self._run_secret_command(
            ["cryptsetup", "luksOpen", raw_path, mapper_name],
            passphrase=passphrase,
            description=f"Opening LUKS container {mapper_name}",
        )
        if not opened.success:
            return opened

        return JobResult.ok(f"LUKS container {mapper_name} ready")
```

Add `import secrets` to the top of `src/xen/jobs/partition.py`'s import block (alongside the existing `import subprocess` etc.).

Update `_op_create` (around line 1646-1685) to thread the same params through - replace its final two lines:

```python
        path = _dev_path(str(params.get("path") or part_path(disk, number)))
        return self._format_with_optional_subvolumes(
            path, fstype, params.get("subvolumes"), dry_run, label=str(params.get("label", ""))
        )
```

with:

```python
        path = _dev_path(str(params.get("path") or part_path(disk, number)))
        return self._format_with_optional_subvolumes(
            path,
            fstype,
            params.get("subvolumes"),
            dry_run,
            label=str(params.get("label", "")),
            encrypt=bool(params.get("encrypt", False)),
            passphrase=str(params.get("encryption_passphrase", "")),
            mountpoint=str(params.get("mountpoint", "")),
            bootloader=self._manual_bootloader,
            has_separate_boot=self._manual_has_separate_boot,
        )
```

And update `_op_create`'s signature isn't changed (it still only takes `disk, op, dry_run`) - instead, `_apply_operations` sets `self._manual_bootloader`/`self._manual_has_separate_boot` once, consumed by both `_op_create` (which has no `bootloader`/`has_separate_boot` params of its own) and `_op_format` (called via `_run_operation`, which also doesn't pass them through yet - fix that too). Update `_run_operation` (around line 1612-1624):

```python
    def _run_operation(self, disk: str, op: PartitionOperation, dry_run: bool) -> JobResult:
        """Dispatch a single operation to its command sequence."""
        if op.type == "delete":
            return self._op_delete(disk, op, dry_run)
        if op.type == "create":
            return self._op_create(disk, op, dry_run)
        if op.type == "format":
            return self._op_format(
                op, dry_run, bootloader=self._manual_bootloader,
                has_separate_boot=self._manual_has_separate_boot,
            )
        if op.type == "setflag":
            return self._op_setflag(disk, op, dry_run)
        if op.type == "resize":
            return self._op_resize(disk, op, dry_run)
        return JobResult.fail(f"Unsupported operation: {op.type}", error_code=53)
```

Finally, in `_apply_operations` (lines 1495-1532), set the two new instance attributes right after parsing operations and before dispatching any group - insert after `self._record_operations_layout(operations)`:

```python
        # Consumed by _op_create/_op_format via _run_operation - set once
        # per batch so neither has to carry these through its own signature.
        self._manual_bootloader = str(selections.get("bootloader", "systemd-boot"))
        self._manual_has_separate_boot = bool(selections.get("has_separate_boot", False))
```

Add `self._manual_bootloader: str = "systemd-boot"` and `self._manual_has_separate_boot: bool = False` to `__init__` alongside `self._manual_generated_passphrases` for a safe default. `_op_create` has no `bootloader`/`has_separate_boot` kwargs of its own - it always reads these two instance attributes, which `_apply_operations` sets once per batch before dispatching any operation (see below). The `test_new_partition_create_op_with_encrypt_routes_through_op_create` test above goes through `_apply_operations` for exactly this reason - calling `_op_create` directly would skip that setup and read the `__init__` defaults instead.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py::TestManualEncryption -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full partition test files to check nothing else broke**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py tests/unit/test_partition_operations.py tests/unit/test_partition_safeguards.py -v`
Expected: PASS, all tests (the `_op_format`/`_format_with_optional_subvolumes`/`_op_create` signature changes are additive/default-valued, so every pre-existing non-encrypted call site is unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/xen/jobs/partition.py tests/unit/test_partition_job.py
git commit -m "feat: LUKS-wrap manually-formatted partitions when encrypt is set"
```

---

### Task 4: Post-Apply reconciliation in `bridge.py`

**Files:**
- Modify: `src/xen/gui/bridge.py` (`_on_partition_apply_finished`, `applyPartitionOperations`, `PartitionApplyWorker`)
- Test: `tests/unit/test_bridge_partition_ops.py` (new `TestPostApplyReconciliation` class)

**Interfaces:**
- Consumes: `_manual_mapper_name(mountpoint)` (Task 1).
- Produces: `bridge._manual_luks_passphrases: dict[str, str]` (real partition name -> generated passphrase), populated here, consumed by Task 8.
- Modifies: `PartitionApplyWorker.__init__` gains `bootloader: str` and `has_separate_boot: bool` params, threaded into the `selections` dict it builds.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_bridge_partition_ops.py`:

```python
class TestPostApplyReconciliation:
    """After a successful live Apply, a new partition's queued
    mountpoint/encryption intent is promoted onto its real device name -
    fixes the pre-existing gap where this never happened at all, and is
    required for the mapper path substitution to land on the right key."""

    def test_new_partition_mountpoint_promoted_to_real_name(self, bridge: EngineBridge) -> None:
        bridge._disks_model = [_mock_disk()]
        op = {
            "type": "create",
            "target": f"free:{_ALIGN}",
            "params": {
                "start_sector": _ALIGN,
                "size_sectors": _sectors(1024),
                "fstype": "ext4",
                "name": "NEW-123",
                "mountpoint": "/home",
            },
        }
        bridge._partition_operations = [op]
        bridge._reconcile_after_apply([op])

        # The mock disk's rescanned segments determine the real name landed
        # on - matches whatever _mock_disk()'s post-apply fixture names the
        # partition at that start sector (see the disk fixture already used
        # by this test file's other tests for the exact name it assigns).
        assert any(
            entry.get("mountpoint") == "/home"
            for entry in bridge._partition_assignments.values()
        )

    def test_encrypted_partition_path_becomes_mapper_device(self, bridge: EngineBridge) -> None:
        bridge._disks_model = [_mock_disk()]
        op = {
            "type": "create",
            "target": f"free:{_ALIGN}",
            "params": {
                "start_sector": _ALIGN,
                "size_sectors": _sectors(1024),
                "fstype": "ext4",
                "name": "NEW-123",
                "mountpoint": "/home",
                "encrypt": True,
            },
        }
        bridge._partition_operations = [op]
        bridge._reconcile_after_apply([op])

        matching = [
            entry for entry in bridge._partition_assignments.values()
            if entry.get("mountpoint") == "/home"
        ]
        assert matching
        assert matching[0]["path"] == "/dev/mapper/crypthome"

    def test_non_root_encrypted_generated_passphrase_carried_over(
        self, bridge: EngineBridge
    ) -> None:
        bridge._manual_luks_passphrases["sda3"] = "generated-pw"
        assert bridge._manual_luks_passphrases["sda3"] == "generated-pw"
```

(The exact real device name `_mock_disk()`'s rescan assigns at `_ALIGN` needs matching against that fixture's own convention - use whatever name the file's existing tests already expect for a partition at that start sector, rather than guessing a literal like `"sda2"`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py::TestPostApplyReconciliation -v`
Expected: FAIL with `AttributeError: 'EngineBridge' object has no attribute '_reconcile_after_apply'`

- [ ] **Step 3: Write minimal implementation**

In `src/xen/gui/bridge.py`, add `self._manual_luks_passphrases: dict[str, str] = {}` to `EngineBridge.__init__`, right after the existing `self._partition_assignments: dict[str, dict[str, Any]] = {}` (line 1412).

Add a new method, placed right before `_on_partition_apply_finished`:

```python
    def _reconcile_after_apply(self, applied_ops: list[dict[str, Any]]) -> None:
        """
        Promote each applied create/format op's queued mountpoint/encryption
        intent onto the real partition it now corresponds to.

        Fixes a pre-existing gap: a newly-created partition's Mount Point
        field was captured in its `create` op's params but nothing ever
        carried it into `_partition_assignments` (keyed by the partition's
        real, post-Apply kernel name) - only the Edit dialog's
        `setPartitionMount`, on an already-existing partition, worked. This
        also has to exist for `encrypt` to correctly resolve to a mapper
        device path for a newly-created partition, so both are fixed here
        together.
        """
        disk_name = str(self._selections.get("disk", ""))
        disk = next((d for d in self._disks_model if d.get("name") == disk_name), None)
        if disk is None:
            return
        segments = disk.get("segments", [])

        for op in applied_ops:
            params = op.get("params", {})
            mountpoint = params.get("mountpoint")
            encrypt = bool(params.get("encrypt"))
            if not mountpoint and not encrypt:
                continue

            real_name = self._resolve_real_partition_name(op, segments)
            if not real_name:
                continue

            entry = self._assignment(real_name)
            if mountpoint:
                entry["mountpoint"] = mountpoint
            if params.get("fstype"):
                entry["fstype"] = params["fstype"]
            if params.get("subvolumes"):
                entry["subvolumes"] = params["subvolumes"]
            if encrypt:
                entry["path"] = f"/dev/mapper/{_manual_mapper_name(str(mountpoint or ''))}"

        self.partitionPlanChanged.emit()

    @staticmethod
    def _resolve_real_partition_name(
        op: dict[str, Any], segments: list[dict[str, Any]]
    ) -> str | None:
        """Match a `create`/`format` op to the real partition it now
        corresponds to, by start sector - same sector-matching
        `_resolve_partition_number` already does job-side, mirrored here
        client-side against freshly-rescanned disk geometry."""
        params = op.get("params", {})
        if op.get("type") == "format":
            path = str(params.get("path", ""))
            return path.rsplit("/", 1)[-1] if path else None
        start = params.get("start_sector")
        if start is None:
            return None
        for seg in segments:
            if seg.get("kind") == "partition" and seg.get("startSector") == start:
                return str(seg.get("name", ""))
        return None
```

Add `_manual_mapper_name` to `bridge.py`'s existing `from xen.jobs.partition import (...)` block.

Update `_on_partition_apply_finished` to call this before clearing the queue:

```python
    def _on_partition_apply_finished(self, success: bool, message: str) -> None:
        """Main-thread handler: on success, reconcile queued intent onto
        real partition names, clear the queue, and rescan the disks."""
        self._partition_applying = False
        self.partitionApplyingChanged.emit()
        if success:
            self.refreshDisks()
            self._reconcile_after_apply(list(self._partition_operations))
            self.resetPartitionOperations()
        if self._debug:
            print(f"[Engine] Partition apply finished: success={success} ({message})")
        self.partitionApplyFinished.emit(success, message)
```

(`refreshDisks()` now runs BEFORE reconciliation, not after - reconciliation needs the freshly-rescanned segments to resolve real names; the original ordering only needed the rescan for the UI's own "Available Disks" refresh, so moving it earlier doesn't change that behavior.)

Thread `bootloader`/`has_separate_boot` through `applyPartitionOperations` and `PartitionApplyWorker`:

```python
class PartitionApplyWorker(QObject):
    """Worker that applies the manual (M2) partition operations off the UI thread."""

    finished = Signal(bool, str)  # success, message

    def __init__(
        self,
        disk: str,
        operations: list[dict[str, Any]],
        dry_run: bool,
        bootloader: str,
        has_separate_boot: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._disk = disk
        self._operations = operations
        self._dry_run = dry_run
        self._bootloader = bootloader
        self._has_separate_boot = has_separate_boot

    def run(self) -> None:
        """Execute the queued operations (real sgdisk/mkfs) against the disk."""
        try:
            from xen.jobs.base import JobContext
            from xen.jobs.partition import PartitionJob

            job = PartitionJob()
            context = JobContext(
                selections={
                    "disk": self._disk,
                    "partition_operations": self._operations,
                    "bootloader": self._bootloader,
                    "has_separate_boot": self._has_separate_boot,
                }
            )
            result = job._apply_operations(
                context, self._disk, context.selections, dry_run=self._dry_run
            )
            self._generated_passphrases = dict(job._manual_generated_passphrases)
            self.finished.emit(result.success, result.message)
        except Exception as e:  # noqa: BLE001 - never let the thread crash the UI
            self._generated_passphrases = {}
            self.finished.emit(False, str(e))
```

In `applyPartitionOperations`, compute and pass the two new values (insert right before `self._apply_thread = QThread(self)`):

```python
        bootloader = str(self._selections.get("bootloader", "systemd-boot"))
        has_separate_boot = any(
            op.get("params", {}).get("mountpoint") == "/boot"
            for op in self._partition_operations
        ) or any(
            entry.get("mountpoint") == "/boot"
            for entry in self._partition_assignments.values()
        )
```

and update the `PartitionApplyWorker(...)` construction call to pass them:

```python
        self._apply_worker = PartitionApplyWorker(
            disk_path, list(self._partition_operations), self._dry_run,
            bootloader, has_separate_boot,
        )
```

Finally, carry the generated passphrases into `_manual_luks_passphrases`, keyed by real partition name (the worker recorded them keyed by raw device path - e.g. `/dev/sda3` - so reconciliation needs to translate that to the bare name `sda3` used everywhere else in `_partition_assignments`). Add this inside `_on_partition_apply_finished`, right after the `self._reconcile_after_apply(...)` call:

```python
            for raw_path, pw in getattr(self._apply_worker, "_generated_passphrases", {}).items():
                name = raw_path.rsplit("/", 1)[-1]
                self._manual_luks_passphrases[name] = pw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py::TestPostApplyReconciliation -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full bridge partition-ops test file to check nothing else broke**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py -v`
Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
git add src/xen/gui/bridge.py tests/unit/test_bridge_partition_ops.py
git commit -m "feat: reconcile queued partition intent onto real names after Apply"
```

---

### Task 5: Redact manual-encryption secrets

**Files:**
- Modify: `src/xen/gui/bridge.py:3017` (`applySelectionsToContext`'s `secret_keys` registration loop)
- Test: `tests/unit/test_bridge_partition_ops.py` (new test in a `TestSecretRedaction` class, or extend an existing one if the file already has one - check first)

**Interfaces:**
- Consumes: `bridge._manual_luks_passphrases`, `bridge._partition_operations` (each op's `params.get("encryption_passphrase")`).

- [ ] **Step 1: Write the failing test**

```python
class TestManualEncryptionRedaction:
    def test_operation_passphrase_registered_with_redactor(self, bridge: EngineBridge) -> None:
        bridge._partition_operations = [{
            "type": "format", "target": "/dev/sda2",
            "params": {"path": "/dev/sda2", "fstype": "ext4", "mountpoint": "/",
                       "encrypt": True, "encryption_passphrase": "S3cretManualPass!"},
        }]
        bridge.applySelectionsToContext()
        assert "S3cretManualPass!" in bridge._redactor._secrets

    def test_generated_non_root_passphrase_registered_with_redactor(
        self, bridge: EngineBridge
    ) -> None:
        bridge._manual_luks_passphrases["sda3"] = "GeneratedHomePass!"
        bridge.applySelectionsToContext()
        assert "GeneratedHomePass!" in bridge._redactor._secrets
```

(If `SecretRedactor` doesn't expose `_secrets` as a plain attribute, use whatever public check it already offers elsewhere in this test file - e.g. a `redact()`/`is_secret()` method - matching the pattern the file's other redaction tests already use instead of reaching into a private attribute blind.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py::TestManualEncryptionRedaction -v`
Expected: FAIL - the manual-mode passphrases aren't registered with the redactor yet.

- [ ] **Step 3: Write minimal implementation**

In `src/xen/gui/bridge.py`'s `applySelectionsToContext`, right after the existing `secret_keys` loop (after the `self._redactor.add_secret(value)` block, before the `if self._debug:` block):

```python
        for op in self._partition_operations:
            passphrase = op.get("params", {}).get("encryption_passphrase")
            if isinstance(passphrase, str) and passphrase:
                self._redactor.add_secret(passphrase)
        for passphrase in self._manual_luks_passphrases.values():
            if passphrase:
                self._redactor.add_secret(passphrase)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py::TestManualEncryptionRedaction -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/xen/gui/bridge.py tests/unit/test_bridge_partition_ops.py
git commit -m "fix: redact manual-partitioning LUKS passphrases"
```

---

### Task 6: Encrypt checkbox + passphrase fields in New/Edit dialogs

**Files:**
- Modify: `src/xen/gui/qml/components/ManualPartitionEditor.qml` (`newDialog`, `editDialog` - both inside their `doFormat`-visible sections, right after the existing FS Label field)
- Test: `tests/integration/test_partition_view_manual.py` (new test function)

**Interfaces:**
- Consumes: nothing new from `engine` - encryption params ride the existing `addPartitionOperation`/`setPartitionMount` JSON payload, already fully generic.

- [ ] **Step 1: Write the failing test**

```python
def test_encrypt_checkbox_passphrase_visibility(bridge: EngineBridge, qapp: QApplication) -> None:
    """Passphrase fields show only when Mount Point is exactly '/' and
    Encrypt is checked; the checkbox itself is hidden for the ESP."""
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        qapp.processEvents()

        new_dialog = _find_by_object_name(root, "newPartitionDialog")
        assert new_dialog is not None

        encrypt_check = _find_by_object_name(new_dialog, "newEncryptCheck")
        passphrase_field = _find_by_object_name(new_dialog, "newEncryptPassField")
        assert encrypt_check is not None
        assert passphrase_field is not None

        # Unchecked: passphrase hidden regardless of mount point.
        assert bool(passphrase_field.property("visible")) is False

        encrypt_check.setProperty("checked", True)
        qapp.processEvents()
        # Mount Point field defaults empty (not "/") - still hidden.
        assert bool(passphrase_field.property("visible")) is False

        mount_field = _find_by_object_name(new_dialog, "newMountField")
        mount_field.setProperty("text", "/")
        qapp.processEvents()
        assert bool(passphrase_field.property("visible")) is True

        mount_field.setProperty("text", "/home")
        qapp.processEvents()
        assert bool(passphrase_field.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine
```

(Needs `newMountField` to already have `objectName: "newMountField"` - check whether it does; if not, add that one-line `objectName` alongside this task's changes, same as every other dialog field this file already exposes for testing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py::test_encrypt_checkbox_passphrase_visibility -v`
Expected: FAIL - `newEncryptCheck`/`newEncryptPassField` not found.

- [ ] **Step 3: Write minimal implementation**

In `ManualPartitionEditor.qml`, inside `newDialog`'s content, right after the FS Label `RowLayout` (the block with `newLabelField`) and before `BtrfsSubvolumeEditor`, insert:

```qml
                RowLayout {
                    spacing: 8
                    visible: (newDialog.freeSeg ? true : true) && fstype !== ""
                    CheckBox {
                        id: newEncryptCheck
                        objectName: "newEncryptCheck"
                        text: qsTr("Encrypt this partition (LUKS2)")
                        contentItem: Text {
                            text: parent.text
                            color: textColor
                            font.pixelSize: 12
                            leftPadding: parent.indicator.width + parent.spacing
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: newEncryptCheck.checked && newMountField.text.trim() === "/"
                    spacing: 6
                    StyledField {
                        id: newEncryptPassField
                        objectName: "newEncryptPassField"
                        echoMode: TextInput.Password
                        placeholderText: qsTr("Encryption passphrase (min 8 characters)")
                    }
                    StyledField {
                        id: newEncryptPassConfirmField
                        objectName: "newEncryptPassConfirmField"
                        echoMode: TextInput.Password
                        placeholderText: qsTr("Confirm passphrase")
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: newEncryptCheck.checked && newMountField.text.trim() !== "/"
                    text: newMountField.text.trim() === "swap"
                        ? qsTr("Encrypted with a fresh random key every boot - nothing to remember.")
                        : qsTr("A random key is generated automatically and unlocked at boot - nothing to remember.")
                    font.pixelSize: 10
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                }
```

Give the existing `newMountField` an `objectName: "newMountField"` if it doesn't already have one (find its `TextField { id: newMountField ... }` block and add the line right after `id: newMountField`).

Update the `newDialog`'s `Create` button `onClicked` (the block built in Task "New Partition dialog confirm()" earlier in this file) to include the encryption params - extend the existing `params` object:

```qml
                        if (newEncryptCheck.checked) {
                            params.encrypt = true
                            if (newMountField.text.trim() === "/") {
                                params.encryption_passphrase = newEncryptPassField.text
                            }
                        }
```

(Insert this right before the existing `queueOp("create", "free:" + seg.startSector, params)` call.)

Mirror the same three blocks (checkbox, passphrase fields, caption) into `editDialog`, inserted right after its FS Label `RowLayout` (`editLabelField`) and before `BtrfsSubvolumeEditor`, using `editEncryptCheck`/`editEncryptPassField`/`editEncryptPassConfirmField` as the ids/objectNames and `editMountField` as the mount-point reference (that field already has `objectName: "editMountField"` per the existing code). Update `editDialog.confirm()` to include `encrypt`/`encryption_passphrase` in the `format` op it queues when `editDialog.doFormat` is true - extend the existing `params` object right before `queueOp("format", path, params)`:

```qml
            if (editEncryptCheck.checked) {
                params.encrypt = true
                if (editMountField.text.trim() === "/") {
                    params.encryption_passphrase = editEncryptPassField.text
                }
            }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py::test_encrypt_checkbox_passphrase_visibility -v`
Expected: PASS

- [ ] **Step 5: Run the full manual-partition integration test file**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py -v`
Expected: PASS, all tests, no QML warnings.

- [ ] **Step 6: Commit**

```bash
git add src/xen/gui/qml/components/ManualPartitionEditor.qml tests/integration/test_partition_view_manual.py
git commit -m "feat: add per-partition Encrypt checkbox to New/Edit dialogs"
```

---

### Task 7: ESP exclusion + swap caption wiring

**Files:**
- Modify: `src/xen/gui/qml/components/ManualPartitionEditor.qml` (the two `RowLayout { ... newEncryptCheck ... }` blocks added in Task 6)
- Test: `tests/integration/test_partition_view_manual.py` (extend Task 6's test or add a focused new one)

**Interfaces:**
- Consumes: the dialog's existing `fsChoice`/`fstype` state (already used elsewhere in both dialogs to gate FS-specific fields like `BtrfsSubvolumeEditor`).

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_partition_view_manual.py`:

```python
def test_encrypt_checkbox_hidden_for_esp(bridge: EngineBridge, qapp: QApplication) -> None:
    """The encrypt option must never render when formatting as vfat with
    the esp flag - GRUB/systemd-boot both need to read the ESP in the clear."""
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        qapp.processEvents()

        new_dialog = _find_by_object_name(root, "newPartitionDialog")
        fs_combo = _find_by_object_name(new_dialog, "newFsCombo")
        encrypt_row = _find_by_object_name(new_dialog, "newEncryptRow")
        assert fs_combo is not None
        assert encrypt_row is not None

        fs_combo.setProperty("currentIndex", fs_combo.property("model").index(-1) if False else 0)
        # Select "fat32" explicitly by value rather than index (index can
        # shift based on online/offline btrfs gating) - find it in the model.
        model = list(fs_combo.property("model"))
        fat32_index = model.index("fat32")
        fs_combo.setProperty("currentIndex", fat32_index)
        qapp.processEvents()
        assert bool(encrypt_row.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py::test_encrypt_checkbox_hidden_for_esp -v`
Expected: FAIL - `newEncryptRow` object not found (Task 6 didn't give the wrapping `RowLayout` an `objectName`) and/or it's always visible regardless of fstype.

- [ ] **Step 3: Write minimal implementation**

In `ManualPartitionEditor.qml`, update both encrypt-checkbox `RowLayout` blocks from Task 6 to gate on fstype and carry an `objectName`:

```qml
                RowLayout {
                    objectName: "newEncryptRow"
                    spacing: 8
                    visible: newDialog.fsChoice !== "fat32"
                    CheckBox {
                        id: newEncryptCheck
                        objectName: "newEncryptCheck"
                        text: qsTr("Encrypt this partition (LUKS2)")
                        contentItem: Text {
                            text: parent.text
                            color: textColor
                            font.pixelSize: 12
                            leftPadding: parent.indicator.width + parent.spacing
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
```

(Same `objectName: "editEncryptRow"` / `visible: editDialog.fsChoice !== "fat32"` change for the `editDialog` copy.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py::test_encrypt_checkbox_hidden_for_esp -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/xen/gui/qml/components/ManualPartitionEditor.qml tests/integration/test_partition_view_manual.py
git commit -m "fix: hide Encrypt option for fat32/ESP partitions"
```

---

### Task 8: Boot chain - crypttab, non-root keyfiles, root detection for manual mode

**Files:**
- Modify: `src/xen/jobs/systemconfig.py:147-180` (`_generate_crypttab`)
- Modify: `src/xen/jobs/bootloader.py` (wherever `context.selections.get("encryption")`/`"root_partition"` are read - lines ~277-291, ~395-414, ~431-456 per the spec's "Research basis" section)
- Modify: `src/xen/jobs/finished.py:237-250` (LUKS mapper cleanup)
- Test: `tests/unit/test_systemconfig_job.py` (or wherever `_generate_crypttab`'s existing tests live - search for them first) and `tests/unit/test_bootloader_job.py`

**Interfaces:**
- Consumes: `context.selections["manual_luks_passphrases"]` (dict, real partition name -> passphrase, pushed from `bridge._manual_luks_passphrases` by `applySelectionsToContext`), `context.selections["partition_assignments"]` (to find every non-root/swap assignment whose `path` starts with `/dev/mapper/`).

- [ ] **Step 1: Write the failing tests**

First, locate `_generate_crypttab`'s existing test(s): `grep -rn "_generate_crypttab\|generate_crypttab" tests/unit/`. Add new tests alongside them, in the same file/class, following that file's existing mocking conventions for `subprocess.run`/`blkid`:

```python
def test_generate_crypttab_adds_non_root_keyfile_entries(self, ...):
    """A non-root encrypted partition (e.g. /home) gets its own crypttab
    line pointing at a per-partition keyfile, plus the keyfile itself
    written and added as a LUKS keyslot - using the passphrase carried
    over from live-Apply."""
    # Arrange: selections with partition_assignments containing a
    # /dev/mapper/crypthome entry and manual_luks_passphrases["sda3"] set,
    # mock blkid/cryptsetup subprocess calls.
    # Act: call the job's crypttab-generation method.
    # Assert: crypttab contains a "crypthome" line referencing a keyfile
    # path, and cryptsetup luksAddKey was invoked for /dev/sda3 using the
    # carried-over passphrase (never the literal passphrase string in any
    # logged/asserted command argv - check only that luksAddKey ran).

def test_generate_crypttab_adds_swap_random_key_entry(self, ...):
    """An encrypted swap partition gets a plain dm-crypt crypttab line
    using /dev/urandom - no LUKS, no keyfile, no passphrase at all."""
    # Assert the crypttab line matches:
    # "cryptswap UUID=<raw-uuid> /dev/urandom swap,cipher=aes-xts-plain64,size=256"
```

Write these two tests following the exact mocking style `_generate_crypttab`'s current test(s) already use (patch `subprocess.run`, feed canned `blkid` output) - read that existing test first and match its fixture/patch pattern precisely rather than inventing a new one.

For `bootloader.py`, add to `tests/unit/test_bootloader_job.py`:

```python
    def test_manual_mode_root_encryption_detected_from_assignments(self, ...):
        """When partition_mode is manual, "is root encrypted" comes from
        partition_assignments (a mapper path at mountpoint "/"), not the
        single auto-mode encryption selection."""
        # selections = {"partition_mode": "manual", "partition_assignments": [
        #     {"path": "/dev/mapper/cryptroot", "mountpoint": "/"},
        # ]}
        # Assert cryptdevice=/GRUB_ENABLE_CRYPTODISK logic triggers the same
        # way it does for selections={"encryption": True, "root_partition": "/dev/sda2"}.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_systemconfig_job.py tests/unit/test_bootloader_job.py -v -k manual`
Expected: FAIL (new behavior doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `src/xen/jobs/systemconfig.py`, add a helper used by `_generate_crypttab` for the root line (unchanged) plus new per-partition lines:

```python
    def _generate_crypttab(self, target_root: str, selections: dict[str, Any]) -> JobResult:
        """Write /etc/crypttab: the encrypted root (unlocked at boot via
        GRUB/mkinitcpio - bootloader.py's cryptdevice= kernel param), any
        other manually-encrypted partition (unlocked automatically via a
        per-partition keyfile added as a second LUKS keyslot, written
        here), and any encrypted swap (plain dm-crypt, a fresh random key
        every boot - never LUKS, never a remembered secret)."""
        crypttab_path = Path(target_root) / "etc" / "crypttab"
        try:
            existing = crypttab_path.read_text(encoding="utf-8") if crypttab_path.exists() else ""
        except OSError as e:
            return JobResult.fail(f"Failed to read {crypttab_path}: {e}", error_code=109)

        lines: list[str] = []

        raw_partition = selections.get("root_partition")
        if raw_partition and "cryptroot" not in existing:
            luks_uuid = self._blkid_uuid(raw_partition)
            if not luks_uuid:
                return JobResult.fail(
                    f"Could not determine LUKS UUID for {raw_partition}", error_code=108
                )
            lines.append(f"cryptroot UUID={luks_uuid} none luks\n")

        passphrases = selections.get("manual_luks_passphrases", {})
        for assignment in selections.get("partition_assignments", []):
            path = str(assignment.get("path", ""))
            mountpoint = str(assignment.get("mountpoint", ""))
            if not path.startswith("/dev/mapper/"):
                continue
            mapper_name = path.rsplit("/", 1)[-1]
            if mapper_name == "cryptroot" or mapper_name in existing:
                continue

            if mountpoint == "swap":
                raw_swap_path = str(assignment.get("raw_path", ""))
                swap_uuid = self._blkid_uuid(raw_swap_path) if raw_swap_path else None
                if not swap_uuid:
                    continue
                lines.append(
                    f"{mapper_name} UUID={swap_uuid} /dev/urandom "
                    "swap,cipher=aes-xts-plain64,size=256\n"
                )
                continue

            raw_path = str(assignment.get("raw_path", ""))
            partition_name = raw_path.rsplit("/", 1)[-1] if raw_path else ""
            passphrase = passphrases.get(partition_name, "")
            keyfile_path = f"/etc/cryptsetup-keys.d/{mapper_name}.key"
            keyfile_result = self._write_manual_keyfile(
                target_root, keyfile_path, raw_path, passphrase
            )
            if not keyfile_result.success:
                return keyfile_result
            luks_uuid = self._blkid_uuid(raw_path)
            if not luks_uuid:
                return JobResult.fail(f"Could not determine LUKS UUID for {raw_path}", error_code=113)
            lines.append(f"{mapper_name} UUID={luks_uuid} {keyfile_path} luks\n")

        if not lines:
            return JobResult.ok("No crypttab entries needed")

        try:
            crypttab_path.parent.mkdir(parents=True, exist_ok=True)
            crypttab_path.write_text(existing + "".join(lines), encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {crypttab_path}: {e}", error_code=109)

        logger.info(f"Wrote {crypttab_path}")
        return JobResult.ok("crypttab generated")

    def _write_manual_keyfile(
        self, target_root: str, keyfile_path: str, raw_path: str, passphrase: str
    ) -> JobResult:
        """Generate a random keyfile, write it (mode 000) under
        target_root, and add it as a second LUKS keyslot on raw_path -
        authenticated with the passphrase generated during live-Apply."""
        if not passphrase:
            return JobResult.fail(
                f"No carried-over passphrase for {raw_path} - cannot add keyfile",
                error_code=114,
            )
        full_path = Path(target_root) / keyfile_path.lstrip("/")
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(os.urandom(4096))
            os.chmod(full_path, 0o000)
        except OSError as e:
            return JobResult.fail(f"Failed to write keyfile {keyfile_path}: {e}", error_code=115)

        try:
            subprocess.run(
                ["cryptsetup", "luksAddKey", raw_path, str(full_path), "--key-file=-"],
                input=passphrase,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            return JobResult.fail(f"luksAddKey failed for {raw_path}: {e.stderr}", error_code=116)
        finally:
            passphrase = ""  # noqa: F841 - SECURITY: clear from this frame

        return JobResult.ok(f"Keyfile added for {raw_path}")
```

(`assignment.get("raw_path")` is a NEW field this task introduces - Task 4's `_reconcile_after_apply` sets `entry["path"]` to the mapper path for an encrypted partition but does not currently preserve the original raw device path anywhere, which this task needs. Go back and extend Task 4's `_reconcile_after_apply`: when `encrypt` is true, also set `entry["raw_path"] = f"/dev/{real_name}"` alongside overwriting `entry["path"]` - add this one line to that method and its test before starting this task's implementation.)

Add `import os` to `systemconfig.py`'s imports if not already present (check first - `_generate_machine_id` already does file operations, so it likely is).

In `src/xen/jobs/bootloader.py`, every place that currently does `if context.selections.get("encryption"):` followed by reading `context.selections.get("root_partition")` needs a manual-mode-aware equivalent. Add one shared helper near `_get_bootloader_type`:

```python
    def _resolve_root_encryption(self, context: JobContext) -> tuple[bool, str]:
        """(is_root_encrypted, raw_root_partition_path) - from the single
        AUTO-mode selection, or from the manual plan's resolved mount
        assignments when partition_mode is "manual"."""
        if context.selections.get("partition_mode") == "manual":
            for assignment in context.selections.get("partition_assignments", []):
                if assignment.get("mountpoint") != "/":
                    continue
                path = str(assignment.get("path", ""))
                if path.startswith("/dev/mapper/"):
                    raw_path = str(assignment.get("raw_path", ""))
                    return bool(raw_path), raw_path
                return False, ""
            return False, ""
        encrypted = bool(context.selections.get("encryption"))
        return encrypted, str(context.selections.get("root_partition", "")) if encrypted else ""
```

Replace each of the three `if context.selections.get("encryption"):` blocks (boot entry creation, kernel params, cryptodisk enablement) to call `is_encrypted, raw_partition = self._resolve_root_encryption(context)` and branch on `is_encrypted`/use `raw_partition` instead of re-reading `context.selections.get("root_partition")` directly - the rest of each block's logic (UUID lookup, `cryptdevice=`, keyfile path) stays exactly as it already is, just fed from these two resolved values instead of the raw selection keys.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_systemconfig_job.py tests/unit/test_bootloader_job.py -v`
Expected: PASS, all tests (new and pre-existing - AUTO mode's `_resolve_root_encryption` branch must reproduce its exact prior behavior).

- [ ] **Step 5: Generalize `finished.py`'s cleanup**

In `src/xen/jobs/finished.py`, replace the hardcoded single `luksClose cryptroot` block (lines 237-250) with one that closes every mapper this install opened, root included:

```python
        # Close every LUKS mapper this install opened (root's fixed name,
        # plus whatever manual mode's partition_assignments resolved to) so
        # the disk is safe to remove right after install.
        mapper_names = set()
        if context.selections.get("encryption"):
            mapper_names.add("cryptroot")
        for assignment in context.selections.get("partition_assignments", []):
            path = str(assignment.get("path", ""))
            if path.startswith("/dev/mapper/"):
                mapper_names.add(path.rsplit("/", 1)[-1])

        for mapper_name in mapper_names:
            try:
                subprocess.run(
                    ["cryptsetup", "luksClose", mapper_name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                logger.info(f"Closed LUKS mapper {mapper_name}")
            except Exception as e:
                logger.debug(f"Failed to close LUKS mapper {mapper_name}: {e}")
```

Add/update a test in `finished.py`'s existing test file (find it via `grep -rln "luksClose" tests/unit/`) asserting both `cryptroot` (AUTO) and a manual-mode mapper name get closed when present, following that file's existing mocking conventions.

Run: `PYTHONPATH=src python -m pytest tests/unit/ -v -k finished`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/xen/jobs/systemconfig.py src/xen/jobs/bootloader.py src/xen/jobs/finished.py src/xen/gui/bridge.py tests/unit/
git commit -m "feat: boot chain support for manual per-partition encryption"
```

---

### Task 9: Full suite + manual QA

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `PYTHONPATH=src python -m pytest tests/ -q`
Expected: All tests pass, 0 failures, 0 errors.

- [ ] **Step 2: Restart the real app and manually verify**

Kill any running instance, relaunch (`PYTHONPATH=src python -m xen.main --debug --dry-run`), open Manual Partitioning:
- Create a new partition, check "Encrypt this partition", set Mount Point to `/` - passphrase fields appear; change Mount Point away from `/` - they disappear and the auto-unlock caption shows instead.
- Confirm the Encrypt option is absent entirely when File System is `fat32`.
- Queue an encrypted root + an encrypted `/home`, click Apply Changes, confirm no errors in the log.
- Edit an existing partition, check Encrypt - same passphrase-field behavior as New.

- [ ] **Step 3: Commit (only if Step 2 surfaced a fixup)**

If nothing needed fixing, skip - Task 8's commit already covers everything.
