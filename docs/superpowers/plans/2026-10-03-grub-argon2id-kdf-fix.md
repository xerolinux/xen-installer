# GRUB/Argon2id LUKS2 KDF Compatibility Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When GRUB will itself have to decrypt an encrypted root partition directly (no separate unencrypted `/boot` ahead of it), LUKS-format it with `--pbkdf pbkdf2` instead of cryptsetup's Argon2id default, so the install actually boots - and show a short note in the UI explaining why, when this applies.

**Architecture:** One pure function (`_luks_kdf_args`) decides PBKDF2-vs-default from `(bootloader, has_separate_boot)`. AUTO mode's `_setup_luks` call site is the only caller for now (AUTO mode structurally never has a separate `/boot`, so `has_separate_boot` is always `False` there); the function is written general enough that the manual-partition-encryption feature (separate plan) can call it unmodified once it exists. A new read-only `bootloaderType` bridge property (backed by a new public `BaseJob.config` accessor) lets `PartitionView.qml` show the compatibility note conditionally.

**Tech Stack:** Python 3.14, PySide6/QML, cryptsetup (LUKS2), pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-manual-partition-encryption-design.md` (see "GRUB/Argon2id compatibility" section - this plan implements that section only; the rest of that spec is a separate, later plan).

## Global Constraints

- Never log a passphrase or the raw `cryptsetup luksFormat`/`luksOpen` command line - `_run_secret_command` already enforces this; this plan must not weaken it.
- `bootloader` selection values are exact-match strings (`"grub"` / `"systemd-boot"`), matching `BootloaderJob.GRUB`/`BootloaderJob.SYSTEMD_BOOT` - no case-normalization, consistent with how `bootloader.py` itself already compares these.
- AUTO mode only in this plan. Manual mode has no encryption support yet (separate plan); `has_separate_boot` is always passed as `False` from AUTO mode's call site, never inferred.
- Full test suite (`pytest tests/`) must stay green after every task.

## Review Focus

- Missing `"bootloader"` key entirely in `selections` (no bootloader page/profile override at all) → must default to `"systemd-boot"` behavior (no PBKDF2 forced, no crash). Covered in Task 1's test (`_luks_kdf_args("systemd-boot", False)` is the realistic default-path case) and Task 2's existing-test-still-passes check.
- `encryption=False` (no encryption at all) → `_luks_kdf_args` must never be called / its result must never affect anything, since `_setup_luks` only runs when `encryption` is true. Covered by Task 2's dry-run and already-passing non-encryption tests continuing to pass unchanged.
- Dry-run with `encryption=True` and `bootloader="grub"` → `_setup_luks`'s dry-run branch returns early before any `_run_secret_command` call; must not raise computing `kdf_args` it never uses. Covered in Task 2 Step 1's dry-run test.
- `bootloaderType` bridge property when no job named `"bootloader"` exists in `engine.jobs` at all (a minimal/custom profile that omits it) → must return the `"systemd-boot"` default, not raise `StopIteration`/`IndexError`. Covered in Task 3's test.
- QML note visibility must react to `encryptionEnabled` AND `bootloaderType`, not just one - must stay hidden when encryption is off even if `bootloaderType === "grub"`, and hidden when `bootloaderType !== "grub"` even if encryption is on. Covered in Task 4's two negative-case assertions.

---

### Task 1: `_luks_kdf_args` pure function

**Files:**
- Modify: `src/xen/jobs/partition.py` (add module-level function near `_parted_flag`, around line 767, right after `_PARTED_FLAGS`/`_parted_flag`)
- Test: `tests/unit/test_partition_job.py` (new `TestLuksKdfArgs` class, placed directly above the existing `TestEncryption` class at line 1692)

**Interfaces:**
- Produces: `_luks_kdf_args(bootloader: str, has_separate_boot: bool) -> list[str]` - extra argv tokens to splice into a `cryptsetup luksFormat` command. Returns `["--pbkdf", "pbkdf2"]` when GRUB must decrypt the volume directly; `[]` (cryptsetup's own Argon2id default) otherwise.

- [ ] **Step 1: Write the failing test**

```python
class TestLuksKdfArgs:
    """GRUB's cryptodisk module can only unlock LUKS2 via PBKDF2, never
    Argon2id - these pin exactly when the fix kicks in."""

    def test_grub_with_no_separate_boot_forces_pbkdf2(self) -> None:
        assert _luks_kdf_args("grub", has_separate_boot=False) == ["--pbkdf", "pbkdf2"]

    def test_grub_with_separate_boot_uses_default(self) -> None:
        # GRUB reads /boot/grub + the kernel from the separate, unencrypted
        # partition - it never touches this LUKS header directly.
        assert _luks_kdf_args("grub", has_separate_boot=True) == []

    def test_systemd_boot_always_uses_default(self) -> None:
        # systemd-boot never decrypts anything itself; mkinitcpio's
        # "encrypt" hook does, which has no PBKDF2-only limitation.
        assert _luks_kdf_args("systemd-boot", has_separate_boot=False) == []
        assert _luks_kdf_args("systemd-boot", has_separate_boot=True) == []

    def test_unknown_bootloader_value_uses_default(self) -> None:
        # Anything other than exactly "grub" is treated as non-GRUB - safe
        # (Argon2id) default rather than guessing.
        assert _luks_kdf_args("", False) == []
        assert _luks_kdf_args("unknown", False) == []
```

Add this class to `tests/unit/test_partition_job.py`, and add `_luks_kdf_args` to the `from xen.jobs.partition import (...)` block at the top of that file (alongside `DiskInfo`, `FilesystemType`, etc.).

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py::TestLuksKdfArgs -v`
Expected: FAIL with `ImportError: cannot import name '_luks_kdf_args'`

- [ ] **Step 3: Write minimal implementation**

In `src/xen/jobs/partition.py`, immediately after the existing `_parted_flag` function (after line 767's `return token if token in _PARTED_FLAGS else None`):

```python
def _luks_kdf_args(bootloader: str, has_separate_boot: bool) -> list[str]:
    """Extra ``cryptsetup luksFormat`` arguments for GRUB/Argon2id compatibility.

    GRUB's ``cryptodisk`` module can only unlock a LUKS2 header using
    PBKDF2 - it has no Argon2id support at all. When GRUB itself must
    decrypt a volume directly (no separate, unencrypted ``/boot``
    partition ahead of it) to read ``/boot/grub`` and the kernel/initramfs,
    formatting with LUKS2's Argon2id default makes the system unable to
    boot - GRUB drops to its rescue shell, unable to read the header.

    Does not apply when GRUB never needs to decrypt anything directly:
    systemd-boot reads the kernel/initramfs from the ESP itself and
    unlocking happens later, in the initramfs, via mkinitcpio's "encrypt"
    hook (plain cryptsetup, no PBKDF2-only limitation) - same as GRUB with
    a separate unencrypted ``/boot`` partition, which reads its config and
    kernel from that plain partition and never touches this LUKS header.

    Args:
        bootloader: exact selection value, e.g. "grub" or "systemd-boot".
        has_separate_boot: True if there's a separate, unencrypted
            partition mounted at /boot ahead of the partition being
            formatted (always False in AUTO mode, which has no such
            partition structurally).

    Returns:
        ``["--pbkdf", "pbkdf2"]`` when GRUB must decrypt this volume
        directly; ``[]`` (cryptsetup's own default, Argon2id) otherwise.
    """
    if bootloader == "grub" and not has_separate_boot:
        return ["--pbkdf", "pbkdf2"]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py::TestLuksKdfArgs -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/xen/jobs/partition.py tests/unit/test_partition_job.py
git commit -m "feat: add _luks_kdf_args for GRUB/Argon2id LUKS2 compatibility"
```

---

### Task 2: Wire `_luks_kdf_args` into `_setup_luks`

**Files:**
- Modify: `src/xen/jobs/partition.py:2122-2167` (`_setup_luks` method and its call site at line 1373-1377 inside `_partition_auto`)
- Test: `tests/unit/test_partition_job.py` (extend the existing `TestEncryption` class at line 1692)

**Interfaces:**
- Consumes: `_luks_kdf_args(bootloader: str, has_separate_boot: bool) -> list[str]` from Task 1.
- Modifies: `_setup_luks(self, passphrase: str, dry_run: bool)` gains a new parameter. Its only caller (`_partition_auto`) is updated in the same step - no other callers exist (confirmed: `_setup_luks` is called exactly once, at line 1375).

- [ ] **Step 1: Write the failing test**

Add to `TestEncryption` in `tests/unit/test_partition_job.py` (after `test_encryption_uses_luks_and_mapper`):

```python
    @patch("xen.jobs.partition.PartitionJob._mount_partitions")
    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_encryption_with_grub_uses_pbkdf2(
        self,
        mock_run_cmd: MagicMock,
        mock_secret: MagicMock,
        mock_mount: MagicMock,
    ) -> None:
        """bootloader=grub (AUTO mode: never a separate /boot) must force
        --pbkdf pbkdf2, or GRUB can't unlock the volume it just created."""
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()
        mock_mount.return_value = JobResult.ok()

        result = job._partition_auto(
            context=JobContext(target_root="/mnt"),
            disk="/dev/sda",
            filesystem="ext4",
            selections={"swap_strategy": "none", "encryption": True, "bootloader": "grub"},
            passphrase="S3cretPass!",
            dry_run=False,
        )
        assert result.success is True

        secret_cmds = [list(c.args[0]) for c in mock_secret.call_args_list]
        luks_format_cmd = next(cmd for cmd in secret_cmds if "luksFormat" in cmd)
        assert "--pbkdf" in luks_format_cmd
        assert luks_format_cmd[luks_format_cmd.index("--pbkdf") + 1] == "pbkdf2"

    @patch("xen.jobs.partition.PartitionJob._mount_partitions")
    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_encryption_with_systemd_boot_uses_default_kdf(
        self,
        mock_run_cmd: MagicMock,
        mock_secret: MagicMock,
        mock_mount: MagicMock,
    ) -> None:
        """Default bootloader (systemd-boot): no --pbkdf override, Argon2id
        (cryptsetup's own default) is fully compatible."""
        job = PartitionJob()
        mock_run_cmd.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()
        mock_mount.return_value = JobResult.ok()

        result = job._partition_auto(
            context=JobContext(target_root="/mnt"),
            disk="/dev/sda",
            filesystem="ext4",
            selections={"swap_strategy": "none", "encryption": True},
            passphrase="S3cretPass!",
            dry_run=False,
        )
        assert result.success is True

        secret_cmds = [list(c.args[0]) for c in mock_secret.call_args_list]
        luks_format_cmd = next(cmd for cmd in secret_cmds if "luksFormat" in cmd)
        assert "--pbkdf" not in luks_format_cmd

    def test_setup_luks_dry_run_with_grub_does_not_raise(self) -> None:
        """Dry-run returns before any command runs - computing kdf_args it
        never uses must not raise."""
        job = PartitionJob()
        job._layout = PartitionLayout(root_partition="/dev/sda2")
        result = job._setup_luks("pw", dry_run=True, bootloader="grub", has_separate_boot=False)
        assert result.success is True
        assert job._layout.encrypted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py::TestEncryption -v`
Expected: FAIL - `test_encryption_with_grub_uses_pbkdf2` fails on the `"--pbkdf" in luks_format_cmd` assertion (not present yet); `test_setup_luks_dry_run_with_grub_does_not_raise` fails with `TypeError: _setup_luks() got an unexpected keyword argument 'bootloader'`.

- [ ] **Step 3: Write minimal implementation**

In `src/xen/jobs/partition.py`, change `_setup_luks`'s signature and body (lines 2122-2167):

```python
    def _setup_luks(
        self, passphrase: str, dry_run: bool, bootloader: str, has_separate_boot: bool
    ) -> JobResult:
        """
        Wrap the root partition in a LUKS2 container.

        SECURITY: the passphrase is fed through stdin and is never logged, nor
        included in any logged command line.

        Args:
            passphrase: LUKS passphrase (must be non-empty in real runs)
            dry_run: If True, simulate only
            bootloader: exact bootloader selection value ("grub" or
                "systemd-boot") - decides the LUKS2 key derivation function
                (see ``_luks_kdf_args``).
            has_separate_boot: True if a separate, unencrypted partition is
                mounted at /boot ahead of this one (always False in AUTO
                mode's fixed EFI+Root layout).

        Returns:
            JobResult indicating success or failure
        """
        if not self._layout:
            return JobResult.fail("Partition layout not initialized", error_code=40)

        root_part = self._layout.root_partition
        mapper_name = self._layout.luks_mapper_name
        kdf_args = _luks_kdf_args(bootloader, has_separate_boot)

        if dry_run:
            logger.info(f"[DRY-RUN] Would LUKS-format and open {root_part} as {mapper_name}")
            self._layout.encrypted = True
            return JobResult.ok("[DRY-RUN] LUKS setup")

        if not passphrase:
            return JobResult.fail("LUKS passphrase missing", error_code=46)

        fmt = self._run_secret_command(
            ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode", *kdf_args, root_part],
            passphrase=passphrase,
            description="LUKS formatting root partition",
        )
        if not fmt.success:
            return fmt

        opened = self._run_secret_command(
            ["cryptsetup", "luksOpen", root_part, mapper_name],
            passphrase=passphrase,
            description="Opening LUKS container",
        )
        if not opened.success:
            return opened

        self._layout.encrypted = True
        return JobResult.ok("LUKS container ready")
```

Then update the call site at line 1373-1377 (inside `_partition_auto`):

```python
        # Step 6: Optionally wrap root in LUKS2 before formatting.
        if encryption:
            context.report_progress(55, "Encrypting root partition...")
            bootloader = str(selections.get("bootloader", "systemd-boot"))
            result = self._setup_luks(
                passphrase, dry_run, bootloader=bootloader, has_separate_boot=False
            )
            if not result.success:
                return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py::TestEncryption -v`
Expected: PASS (all tests in the class, including the two pre-existing ones - `root_target == "/dev/mapper/cryptroot"` behavior is unchanged).

- [ ] **Step 5: Run the full partition test files to check nothing else broke**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_partition_job.py tests/unit/test_partition_operations.py -v`
Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
git add src/xen/jobs/partition.py tests/unit/test_partition_job.py
git commit -m "fix: use PBKDF2 for LUKS2 when GRUB must decrypt the volume directly"
```

---

### Task 3: Expose `bootloaderType` to QML

**Files:**
- Modify: `src/xen/jobs/base.py` (add a public `config` property to `BaseJob`, right after the existing `status` property/setter pair)
- Modify: `src/xen/gui/bridge.py` (add `bootloaderType` property to `EngineBridge`, grouped with the other `@Property(str, constant=True)` branding/info properties - e.g. near `edition` at line 397)
- Test: `tests/unit/test_bridge_partition_ops.py` (new `TestBootloaderType` class)

**Interfaces:**
- Produces: `BaseJob.config -> dict[str, Any]` (read-only property, returns the job's own `_config`).
- Produces: `EngineBridge.bootloaderType -> str` (Qt `constant=True` property) - `"grub"` or `"systemd-boot"`, read from whichever job in `engine.jobs` has `.name == "bootloader"`; `"systemd-boot"` if no such job exists.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_bridge_partition_ops.py` (needs `BootloaderJob` imported - add `from xen.jobs.bootloader import BootloaderJob` to its import block):

```python
class TestBootloaderType:
    """engine.bootloaderType - read-only, used by PartitionView.qml to show
    the GRUB/Argon2id compatibility note. Set by the distro's config YAML,
    never by the user."""

    def test_defaults_to_systemd_boot(self, bridge: EngineBridge) -> None:
        # MINIMAL_CONFIG's bootloader job entry has no "type:" override.
        assert bridge.bootloaderType == "systemd-boot"

    def test_reads_grub_from_job_config(self, bridge: EngineBridge) -> None:
        for job in bridge._engine.jobs:
            if job.name == "bootloader":
                job._config["type"] = "grub"
        assert bridge.bootloaderType == "grub"

    def test_defaults_when_no_bootloader_job_present(self, bridge: EngineBridge) -> None:
        bridge._engine.jobs = [j for j in bridge._engine.jobs if j.name != "bootloader"]
        assert bridge.bootloaderType == "systemd-boot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py::TestBootloaderType -v`
Expected: FAIL with `AttributeError: 'EngineBridge' object has no attribute 'bootloaderType'`

- [ ] **Step 3: Write minimal implementation**

In `src/xen/jobs/base.py`, immediately after the `status` property/setter pair (after the `self._status = value` line):

```python
    @property
    def config(self) -> dict[str, Any]:
        """This job's own configuration dict, as given at construction."""
        return self._config
```

In `src/xen/gui/bridge.py`, inside the `EngineBridge` class, near the other simple read-only info properties:

```python
    @Property(str, constant=True)
    def bootloaderType(self) -> str:
        """Which bootloader this install profile configures ("grub" or
        "systemd-boot") - set by the distro's config YAML, never changed by
        the user. Used only to decide whether to show the GRUB/Argon2id
        LUKS2 compatibility note near the encryption passphrase field."""
        for job in self._engine.jobs:
            if job.name == "bootloader":
                return str(job.config.get("type", "systemd-boot"))
        return "systemd-boot"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/unit/test_bridge_partition_ops.py::TestBootloaderType -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/xen/jobs/base.py src/xen/gui/bridge.py tests/unit/test_bridge_partition_ops.py
git commit -m "feat: expose bootloaderType on EngineBridge"
```

---

### Task 4: UI compatibility note

**Files:**
- Modify: `src/xen/gui/qml/components/PartitionView.qml:1019-1030` (inside the passphrase `ColumnLayout`, right after the `showEncPassCheck` `CheckBox`)
- Test: `tests/integration/test_partition_view_manual.py` (new test function - reuses the existing `_load_main`/`_find_by_object_name` helpers already in that file)

**Interfaces:**
- Consumes: `engine.bootloaderType` (Task 3) via QML's existing `engine` context property.

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_partition_view_manual.py` (as a new top-level test function, after the existing test classes - needs `bridge._engine.jobs` to include a `BootloaderJob` with `type: grub` for the positive case, so add `from xen.jobs.bootloader import BootloaderJob` to the imports):

```python
def test_grub_encryption_note_visibility(bridge: EngineBridge, qapp: QApplication) -> None:
    """The GRUB/Argon2id note shows only when both encryption is on AND
    bootloader is grub - hidden in every other combination."""
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        qapp.processEvents()

        note = _find_by_object_name(root, "grubKdfNote")
        assert note is not None, "grubKdfNote Text not found in the QML tree"

        # Neither encryption nor grub: hidden.
        assert bool(note.property("visible")) is False

        # Encryption on, but default bootloader (systemd-boot): still hidden.
        bridge.setEncryption(True)
        qapp.processEvents()
        assert bool(note.property("visible")) is False

        # Encryption on AND grub: visible.
        for job in bridge._engine.jobs:
            if job.name == "bootloader":
                job._config["type"] = "grub"
        qapp.processEvents()
        assert bool(note.property("visible")) is True

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py::test_grub_encryption_note_visibility -v`
Expected: FAIL - `assert note is not None` fails (`grubKdfNote` doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `src/xen/gui/qml/components/PartitionView.qml`, insert right after the `showEncPassCheck` `CheckBox` block (after its closing `}` at line 1029, still inside the passphrase `ColumnLayout` that closes at line 1030):

```qml
                                Text {
                                    id: grubKdfNote
                                    objectName: "grubKdfNote"
                                    Layout.fillWidth: true
                                    visible: encryptionEnabled && engine.bootloaderType === "grub"
                                    text: qsTr("GRUB can't unlock Argon2id-protected volumes directly - using PBKDF2 for compatibility.")
                                    font.pixelSize: 10
                                    color: textMutedColor
                                    wrapMode: Text.WordWrap
                                }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_partition_view_manual.py::test_grub_encryption_note_visibility -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/xen/gui/qml/components/PartitionView.qml tests/integration/test_partition_view_manual.py
git commit -m "feat: show GRUB/Argon2id compatibility note when relevant"
```

---

### Task 5: Full suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `PYTHONPATH=src python -m pytest tests/ -q`
Expected: All tests pass, 0 failures, 0 errors (same skip count as before this plan started).

- [ ] **Step 2: Restart the real app and manually verify**

Kill any running instance, relaunch (`PYTHONPATH=src python -m xen.main --debug --dry-run`), confirm no new QML warnings in the log, open the Partition step in AUTO mode, check "Enable encryption (LUKS)" - the compatibility note must stay hidden (default profile is `systemd-boot`). This plan has no way to flip the live app to `grub` without editing the loaded config YAML's `bootloader:` job entry to `type: grub` - if you want to see the note live, temporarily add that to `config/examples/xerolinux.yaml`'s bootloader job entry, relaunch, and revert it after.

- [ ] **Step 3: Commit (only if Step 2 required a temporary config edit that needs reverting, or if any fixup was needed)**

If nothing needed fixing, skip this step - Task 4's commit already covers everything.
