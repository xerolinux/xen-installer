#!/usr/bin/env python3
import argparse
import fcntl
import http.server
import importlib
import json
import os
import pkgutil
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = HERE / ".cache"
ISO_DIR = Path.home() / "ISOs"
OVMF_CODE = Path("/usr/share/edk2/x64/OVMF_CODE.4m.fd")
OVMF_VARS = Path("/usr/share/edk2/x64/OVMF_VARS.4m.fd")
XERO_ISO_DIR = Path.home() / "XeroBuild" / "XeroISO"
BOOT_MEMORY = 6144
KERNEL = "arch/boot/x86_64/vmlinuz-linux"
INITRD = "arch/boot/x86_64/initramfs-linux.img"
BOOT_ENTRY = "loader/entries/01-archiso-linux.conf"
RESULT_RE = re.compile(r"XEN-RESULT (\{.*\})\s*$", re.M)
PASSPHRASE_PROMPT = re.compile(r"Enter passphrase for ")

sys.path.insert(0, str(HERE))

from fixtures import BOOT_HOSTNAME  # noqa: E402


def fail_setup(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def prepare_boot_files():
    isos = sorted(ISO_DIR.glob("archlinux-*.iso"))
    if not isos:
        fail_setup(f"no archlinux-*.iso in {ISO_DIR}")
    iso = isos[-1]
    out = CACHE / "boot" / iso.stem
    if not (out / KERNEL).exists() or not (out / INITRD).exists():
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(["bsdtar", "-xf", str(iso), "-C", str(out), KERNEL, INITRD], check=True)
    entry = subprocess.run(
        ["bsdtar", "-xOf", str(iso), BOOT_ENTRY], check=True, capture_output=True, text=True
    ).stdout
    uuid = re.search(r"archisosearchuuid=(\S+)", entry)
    if not uuid:
        fail_setup(f"no archisosearchuuid in {BOOT_ENTRY} of {iso}")
    date = re.search(r"archlinux-(\d{4})\.(\d{2})\.(\d{2})", iso.name)
    if not date:
        fail_setup(f"no build date in {iso.name}")
    return iso, out / KERNEL, out / INITRD, uuid.group(1), "/".join(date.groups())


def xero_iso():
    isos = sorted(XERO_ISO_DIR.glob("xerolinux-*.iso"))
    if not isos:
        fail_setup(f"no xerolinux-*.iso in {XERO_ISO_DIR}")
    return isos[-1]


def build_www():
    www = CACHE / "www"
    shutil.rmtree(www, ignore_errors=True)
    www.mkdir(parents=True)
    shutil.copy(HERE / "guest.sh", www / "guest.sh")

    def keep(info):
        skip = "__pycache__" in info.name or "/.cache" in info.name
        return None if skip else info

    with tarfile.open(www / "payload.tar.gz", "w:gz") as tar:
        tar.add(ROOT / "src", arcname="src", filter=keep)
        tar.add(HERE, arcname="scripts/vmtest", filter=keep)
        tar.add(ROOT / "config", arcname="config", filter=keep)
    return www


def serve(www):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(www), **kwargs)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def all_scenarios():
    pkg = importlib.import_module("scenarios")
    return sorted(m.name for m in pkgutil.iter_modules(pkg.__path__))


def parse_result(text, name):
    for raw in reversed(RESULT_RE.findall(text)):
        try:
            result = json.loads(raw)
        except ValueError:
            continue
        if result.get("scenario") == name:
            return result
    return None


def tail(path, lines=40):
    text = path.read_text(errors="replace") if path.exists() else ""
    return "\n".join(text.splitlines()[-lines:])


def create_disks(disks, run_dir):
    drives = []
    for i, disk in enumerate(disks):
        img = run_dir / f"disk{i}.qcow2"
        subprocess.run(
            ["qemu-img", "create", "-q", "-f", "qcow2", str(img), f"{disk['size_gib']}G"], check=True
        )
        lbs = disk["logical_block_size"]
        drives += [
            "-drive", f"if=none,id=d{i},file={img},format=qcow2",
            "-device", f"virtio-blk-pci,drive=d{i},logical_block_size={lbs},physical_block_size={lbs}",
        ]
    return drives


def cdrom(index, iso):
    return [
        "-drive", f"if=none,id=cd{index},file={iso},media=cdrom,readonly=on",
        "-device", f"ide-cd,drive=cd{index},bus=ide.{index}",
    ]


def _serial_client(sock_path, serial, proc):
    deadline = time.monotonic() + 30
    while not sock_path.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            return None
        time.sleep(0.1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(sock_path))

    def copy():
        with open(serial, "ab") as log:
            while chunk := client.recv(4096):
                log.write(chunk)
                log.flush()

    threading.Thread(target=copy, daemon=True).start()
    return client


def qemu(run_dir, serial, memory, extra, done, timeout, wait_exit=True, passphrase=None):
    """Runs QEMU until done(serial text) returns a value, QEMU exits or the timeout hits.

    With a passphrase, the serial port is a socket and every passphrase prompt gets it typed.
    """
    sock_path = run_dir / "serial.sock"
    if passphrase:
        sock_path.unlink(missing_ok=True)
        serial_args = ["-chardev", f"socket,id=ser0,path={sock_path},server=on,wait=on",
                       "-serial", "chardev:ser0"]  # fmt: skip
    else:
        serial_args = ["-serial", f"file:{serial}"]
    cmd = [
        "qemu-system-x86_64", "-machine", "q35", "-enable-kvm", "-m", str(memory), "-smp", "4",
        "-display", "none", "-no-reboot",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={OVMF_CODE}",
        "-drive", f"if=pflash,format=raw,unit=1,file={run_dir / 'OVMF_VARS.4m.fd'}",
        *extra,
        "-nic", "user,model=virtio-net-pci",
        *serial_args,
    ]
    err_path = serial.with_suffix(".err")
    with open(err_path, "wb") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
    client = _serial_client(sock_path, serial, proc) if passphrase else None
    answered = 0
    try:
        deadline = time.monotonic() + timeout
        while True:
            exited = proc.poll() is not None
            text = serial.read_text(errors="replace") if serial.exists() else ""
            if client is not None:
                for _ in range(len(PASSPHRASE_PROMPT.findall(text)) - answered):
                    client.sendall(f"{passphrase}\r".encode())
                    answered += 1
            found = done(text)
            if found:
                if wait_exit:
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        pass
                return found, ""
            if exited:
                return None, f"qemu exited rc={proc.returncode}\n{tail(serial)}\n{tail(err_path)}"
            if time.monotonic() > deadline:
                return None, f"timeout after {timeout}s\n{tail(serial)}"
            time.sleep(1)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if client is not None:
            client.close()


def live_phase(name, phase, boot, port, run_dir, drives, cdroms, memory, timeout):
    iso, kernel, initrd, uuid, archive = boot
    append = (
        f"archisobasedir=arch archisosearchuuid={uuid} console=ttyS0 cow_spacesize=6G xen_archive={archive} "
        f"xen_scenario={name} xen_phase={phase} script=http://10.0.2.2:{port}/guest.sh"
    )
    extra = ["-kernel", str(kernel), "-initrd", str(initrd), "-append", append,
             *cdrom(0, iso), *cdroms, *drives]  # fmt: skip
    result, error = qemu(
        run_dir, run_dir / f"serial-{phase}.log", memory, extra,
        lambda text: parse_result(text, name), timeout,
    )  # fmt: skip
    if result is None:
        return None, f"{phase}: no result: {error}"
    if result["status"] != "PASS":
        return None, f"{phase}: {result['detail']}"
    return result, ""


def boot_from_disk(run_dir, drives, timeout, passphrase):
    login = re.compile(rf"\b{BOOT_HOSTNAME} login:")
    found, error = qemu(
        run_dir, run_dir / "serial-boot.log", BOOT_MEMORY, drives,
        login.search, timeout, wait_exit=False, passphrase=passphrase,
    )  # fmt: skip
    if not found:
        return f"boot: no '{BOOT_HOSTNAME} login:' on serial: {error}"
    prompts = len(PASSPHRASE_PROMPT.findall(tail(run_dir / "serial-boot.log", 10**6)))
    if passphrase and prompts != 1:
        # GRUB asks once; the initramfs must unlock root with the embedded root.key
        return f"boot: {prompts} passphrase prompts on serial, expected 1"
    return ""


def build_packages(www):
    out = CACHE / "pkg"
    shutil.rmtree(out, ignore_errors=True)
    env = {**os.environ, "PKGDEST": str(www), "BUILDDIR": str(out / "build"), "SRCDEST": str(out / "src")}
    proc = subprocess.run(
        ["makepkg", "-f", "--noconfirm"], cwd=ROOT / "packaging" / "xen-installer", env=env,
        capture_output=True, text=True,
    )  # fmt: skip
    if proc.returncode:
        fail_setup(f"makepkg failed:\n{proc.stdout[-3000:]}{proc.stderr[-3000:]}")
    names = [p.name for p in www.glob("*.pkg.tar.zst") if not p.name.startswith("xen-cfg-xerolinux-nvidia")]
    if len(names) != 2:
        fail_setup(f"expected xen-app and xen-cfg-xerolinux packages, got {names}")
    return sorted(names)


def run_gui(name, module, boot, port, www, run_dir, drives, args):
    import gui

    start = time.monotonic()
    timings = []
    state = None
    if hasattr(module, "prepare"):
        result, error = live_phase(name, "prepare", boot, port, run_dir, drives, [], 4096, args.timeout)
        if result is None:
            return False, error
        state = result.get("state")
        timings.append(f"prepare {time.monotonic() - start:.0f}s")
    shots = CACHE / f"gui-{name}"
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir(parents=True)
    session = gui.LiveSession(run_dir, xero_iso(), drives, OVMF_CODE, shots)
    g = session.gui
    try:
        phase = time.monotonic()
        session.setup(f"http://10.0.2.2:{port}", args.packages)
        session.launch()
        timings.append(f"live+launch {time.monotonic() - phase:.0f}s")
        phase = time.monotonic()
        gui.front_pages(g, BOOT_HOSTNAME, "vmtest-password")
        module.drive(g)
        timings.append(f"pages {time.monotonic() - phase:.0f}s")
        phase = time.monotonic()
        gui.install(g, session, args.install_timeout)
        timings.append(f"install {time.monotonic() - phase:.0f}s")
        gui.reboot(g, session)
    except Exception as exc:
        if session.proc.poll() is not None:
            return False, f"gui: qemu exited rc={session.proc.returncode}: {exc}\n{tail(run_dir / 'qemu-live.err')}"
        try:
            g.save("error")
        except Exception:
            pass
        return False, f"gui: {exc}"
    finally:
        session.close()
    phase = time.monotonic()
    error = boot_from_disk(run_dir, drives, args.login_timeout, getattr(module, "BOOT_PASSPHRASE", None))
    if error:
        return False, error
    timings.append(f"boot {time.monotonic() - phase:.0f}s")
    if hasattr(module, "verify"):
        phase = time.monotonic()
        (www / f"state-{name}.json").write_text(json.dumps(state))
        result, error = live_phase(name, "verify", boot, port, run_dir, drives, [], 4096, args.timeout)
        if result is None:
            return False, error
        timings.append(f"verify {time.monotonic() - phase:.0f}s")
    return True, ", ".join(timings)


def run_scenario(name, boot, port, www, run_dir, args):
    module = importlib.import_module(f"scenarios.{name}")
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    shutil.copy(OVMF_VARS, run_dir / "OVMF_VARS.4m.fd")
    drives = create_disks(module.DISKS, run_dir)

    if getattr(module, "GUI", False):
        return run_gui(name, module, boot, port, www, run_dir, drives, args)

    if not module.BOOT:
        result, error = live_phase(name, "run", boot, port, run_dir, drives, [], 4096, args.timeout)
        return result is not None, error

    start = time.monotonic()
    result, error = live_phase(
        name, "run", boot, port, run_dir, drives, cdrom(1, xero_iso()), BOOT_MEMORY,
        args.install_timeout,
    )  # fmt: skip
    if result is None:
        return False, error
    installed = time.monotonic()
    passphrase = getattr(module, "BOOT_PASSPHRASE", None)
    error = boot_from_disk(run_dir, drives, args.login_timeout, passphrase)
    if error:
        return False, error
    booted = time.monotonic()
    timings = f"install {installed - start:.0f}s, boot {booted - installed:.0f}s"
    if hasattr(module, "verify"):
        (www / f"state-{name}.json").write_text(json.dumps(result.get("state")))
        result, error = live_phase(
            name, "verify", boot, port, run_dir, drives, [], 4096, args.timeout
        )
        if result is None:
            return False, error
        timings += f", verify {time.monotonic() - booted:.0f}s"
    return True, timings


def main():
    ap = argparse.ArgumentParser(description="Run real-disk scenarios inside an Arch ISO VM")
    ap.add_argument("scenarios", nargs="+", metavar="SCENARIO",
                    help="scenario names, 'all' (every non-GUI scenario) or 'gui' (every GUI scenario)")
    ap.add_argument("--keep", action="store_true", help="keep disks and serial logs")
    ap.add_argument("--timeout", type=int, default=900, help="per-scenario timeout in seconds")
    ap.add_argument("--install-timeout", type=int, default=3600,
                    help="BOOT scenarios: timeout of the full install, in seconds")
    ap.add_argument("--login-timeout", type=int, default=300,
                    help="BOOT scenarios: seconds to reach the installed system's login prompt")
    args = ap.parse_args()
    CACHE.mkdir(exist_ok=True)
    lock = open(CACHE / "run.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail_setup(f"another run.py is already using {CACHE}")

    available = all_scenarios()
    gui_names = [n for n in available if getattr(importlib.import_module(f"scenarios.{n}"), "GUI", False)]
    names = []
    for name in args.scenarios:
        if name == "all":
            names += [n for n in available if n not in gui_names]
        elif name == "gui":
            names += gui_names
        else:
            names.append(name)
    unknown = [n for n in names if n not in available]
    if unknown:
        fail_setup(f"unknown scenario(s): {', '.join(unknown)}; available: {', '.join(available)}")

    boot = prepare_boot_files()
    www = build_www()
    if any(n in gui_names for n in names):
        import gui

        if gui.tessdata() is None:
            fail_setup("GUI scenarios need tesseract's English model: install tesseract-data-eng"
                       f" or put eng.traineddata in {CACHE / 'tessdata'}")
        args.packages = build_packages(www)
    server = serve(www)
    port = server.server_address[1]
    ok = True
    for name in names:
        run_dir = CACHE / f"run-{name}"
        passed, detail = run_scenario(name, boot, port, www, run_dir, args)
        ok &= passed
        if passed:
            print(f"PASS {name}" + (f" ({detail})" if detail else ""), flush=True)
        else:
            print(f"FAIL {name}: {detail}", flush=True)
        if args.keep:
            print(f"kept {run_dir}")
        else:
            shutil.rmtree(run_dir, ignore_errors=True)
    server.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
