import base64
import csv
import io
import json
import re
import socket
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
SCREEN_W, SCREEN_H = 1920, 1080
LIVE_MEMORY = 6144
TESSDATA_DIRS = [Path("/usr/share/tessdata"), HERE / ".cache" / "tessdata"]
AGENT_UNIT = """[Unit]
Description=vmtest command agent
ConditionPathExists=!/etc/initrd-release

[Service]
ImportCredential=xen.agent
ExecStart=/usr/bin/python3 %d/xen.agent
"""
AGENT_WANTS = "[Unit]\nWants=xen-agent.service\n"
SHIFTED = {
    "_": "minus", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "+": "equal", ":": "semicolon", "?": "slash",
}  # fmt: skip
PLAIN = {
    "-": "minus", "=": "equal", ".": "dot", ",": "comma", "/": "slash", ";": "semicolon",
    " ": "spc", "\n": "ret",
}  # fmt: skip


def tessdata():
    for path in TESSDATA_DIRS:
        if (path / "eng.traineddata").exists():
            return path
    return None


def _credential(name, text):
    data = base64.b64encode(text.encode()).decode()
    return ["-smbios", f"type=11,value=io.systemd.credential.binary:{name}={data}"]


def agent_credentials():
    agent = (HERE / "guest_agent.py").read_text()
    return [
        *_credential("xen.agent", agent),
        *_credential("systemd.extra-unit.xen-agent.service", AGENT_UNIT),
        *_credential("systemd.unit-dropin.multi-user.target~xen-agent", AGENT_WANTS),
    ]


class LineSocket:
    def __init__(self, path, timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(str(path))
                break
            except OSError:
                self.sock.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        self.buffer = b""

    def send(self, obj):
        self.sock.sendall(json.dumps(obj).encode() + b"\n")

    def recv(self, timeout):
        self.sock.settimeout(timeout)
        while b"\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def close(self):
        self.sock.close()


class Qmp:
    def __init__(self, path):
        self.conn = LineSocket(path, 30)
        self.conn.recv(30)
        self.cmd("qmp_capabilities")

    def cmd(self, name, **arguments):
        self.conn.send({"execute": name, "arguments": arguments})
        while True:
            reply = self.conn.recv(60)
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                raise RuntimeError(f"qmp {name}: {reply['error']}")


class Agent:
    def __init__(self, path):
        self.conn = LineSocket(path, 30)
        self.next_id = 0

    def try_run(self, cmd, timeout=120):
        self.next_id += 1
        self.conn.send({"id": self.next_id, "cmd": cmd, "timeout": timeout})
        while True:
            reply = self.conn.recv(timeout + 30)
            if reply.get("id") == self.next_id:
                return reply["rc"], reply["out"]

    def run(self, cmd, timeout=120):
        rc, out = self.try_run(cmd, timeout)
        if rc:
            raise AssertionError(f"guest command failed (rc={rc}): {cmd}\n{out}")
        return out

    def wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        self.conn.sock.settimeout(5)
        while time.monotonic() < deadline:
            self.next_id += 1
            self.conn.send({"id": self.next_id, "cmd": "true", "timeout": 5})
            try:
                while self.conn.recv(5).get("id") != self.next_id:
                    pass
                return
            except (TimeoutError, socket.timeout):
                continue
        raise AssertionError(f"guest agent did not answer within {timeout}s")


def _tesseract(gray):
    big = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
    buf = io.BytesIO()
    big.save(buf, "PNG")
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "--tessdata-dir", str(tessdata()), "-l", "eng",
         "--psm", "11", "-c", "tessedit_create_tsv=1"],
        input=buf.getvalue(), capture_output=True, check=True,
    )  # fmt: skip
    words = []
    rows = csv.DictReader(io.StringIO(proc.stdout.decode()), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in rows:
        text = (row.get("text") or "").strip()
        if text and float(row["conf"]) > 30:
            box = [int(row[k]) // 2 for k in ("left", "top", "width", "height")]
            words.append({"text": text, "box": box, "line": (row["block_num"], row["par_num"], row["line_num"])})
    return words


def _ocr_image(img):
    gray = ImageOps.grayscale(img)
    # Dark text on the light-inverted page, then white text on dark and on bright (button) fills.
    variants = [ImageOps.invert(gray)] + [gray.point(lambda v, t=t: 0 if v > t else 255) for t in (170, 215)]
    words = []
    for i, variant in enumerate(variants):
        for w in _tesseract(variant):
            w["line"] = (i, *w["line"])
            words.append(w)
    return words


def _norm(word):
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _close(word, target):
    """Equal, or one OCR slip (substitution, insertion or deletion) in a word of 5+ letters."""
    if word == target:
        return True
    if len(target) < 5 or abs(len(word) - len(target)) > 1:
        return False
    prev = list(range(len(target) + 1))
    for i, a in enumerate(word, 1):
        cur = [i]
        for j, b in enumerate(target, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1] <= 1


class Match:
    def __init__(self, box):
        self.x, self.y, self.w, self.h = box

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    def __repr__(self):
        return f"Match({self.x},{self.y},{self.w},{self.h})"


def find_phrase(words, phrase):
    target = [t for t in (_norm(p) for p in phrase.split()) if t]
    words = [w for w in words if _norm(w["text"])]
    hits = []
    for i in range(len(words)):
        seq = words[i : i + len(target)]
        if len(seq) < len(target) or any(w["line"] != seq[0]["line"] for w in seq):
            continue
        if all(_close(_norm(w["text"]), t) for w, t in zip(seq, target)):
            x0 = min(w["box"][0] for w in seq)
            y0 = min(w["box"][1] for w in seq)
            x1 = max(w["box"][0] + w["box"][2] for w in seq)
            y1 = max(w["box"][1] + w["box"][3] for w in seq)
            hits.append(Match((x0, y0, x1 - x0, y1 - y0)))
    return hits


class Gui:
    def __init__(self, qmp, tmp_dir, shots_dir):
        self.qmp = qmp
        self.shots = Path(shots_dir)
        self.counter = 0
        self.tmp = Path(tmp_dir) / "screen.png"

    def grab(self):
        self.qmp.cmd("screendump", filename=str(self.tmp), format="png")
        img = Image.open(self.tmp)
        img.load()
        return img.convert("RGB")

    def save(self, page):
        self.counter += 1
        img = self.grab()
        img.save(self.shots / f"{self.counter:02d}-{page}.png")
        return img

    def words(self, region=None):
        words = _ocr_image(self.grab())
        if region:
            x0, y0, x1, y1 = region
            words = [w for w in words if x0 <= w["box"][0] and w["box"][0] + w["box"][2] <= x1
                     and y0 <= w["box"][1] and w["box"][1] + w["box"][3] <= y1]  # fmt: skip
        return words

    def find(self, phrase, region=None):
        return find_phrase(self.words(region), phrase)

    def text(self):
        return " ".join(w["text"] for w in self.words())

    def wait(self, phrase, timeout=60, region=None, gone=False):
        deadline = time.monotonic() + timeout
        misses = 0
        while True:
            hits = self.find(phrase, region)
            misses = 0 if hits else misses + 1
            # One OCR miss is common, so "gone" needs two in a row
            if (misses >= 2) if gone else hits:
                return hits
            if time.monotonic() > deadline:
                state = "still shown" if gone else "not shown"
                raise AssertionError(f"'{phrase}' {state} after {timeout}s; screen: {self.text()[:600]}")
            time.sleep(1)

    def move(self, x, y):
        ax, ay = round(x * 32767 / (SCREEN_W - 1)), round(y * 32767 / (SCREEN_H - 1))
        self.qmp.cmd("input-send-event", events=[
            {"type": "abs", "data": {"axis": "x", "value": ax}},
            {"type": "abs", "data": {"axis": "y", "value": ay}},
        ])  # fmt: skip

    def click(self, x, y, settle=1.0):
        self.move(x, y)
        time.sleep(0.15)
        for down in (True, False):
            self.qmp.cmd("input-send-event", events=[{"type": "btn", "data": {"down": down, "button": "left"}}])
            time.sleep(0.08)
        time.sleep(settle)

    def click_text(self, phrase, region=None, timeout=30):
        m = min(self.wait(phrase, timeout, region), key=lambda m: (m.y, m.x))
        self.click(*m.center)
        return m

    def click_left(self, phrase, offset, timeout=30):
        """Clicks `offset` px left of a label: the radio or checkbox it belongs to."""
        m = min(self.wait(phrase, timeout), key=lambda m: (m.y, m.x))
        self.click(m.x - offset, m.y + m.h // 2)
        return m

    def key(self, *names):
        self.qmp.cmd("send-key", keys=[{"type": "qcode", "data": n} for n in names])
        time.sleep(0.05)

    def type(self, text):
        for ch in text:
            if ch.isalpha() and ch.isascii():
                keys = ["shift", ch.lower()] if ch.isupper() else [ch]
            elif ch.isdigit():
                keys = [ch]
            elif ch in SHIFTED:
                keys = ["shift", SHIFTED[ch]]
            elif ch in PLAIN:
                keys = [PLAIN[ch]]
            else:
                raise ValueError(f"cannot type {ch!r}")
            self.key(*keys)
        time.sleep(0.3)


PATCH_CONFIG = """
import yaml
path = "/etc/xen/xerolinux.yaml"
config = yaml.safe_load(open(path))
job = next(j for j in config["jobs"] if j["name"] == "bootloader")
job.setdefault("config", {})["extra_kernel_params"] = ["console=ttyS0"]
yaml.safe_dump(config, open(path, "w"), sort_keys=False)
"""
APP = "^/usr/bin/python /usr/bin/xen"


class LiveSession:
    def __init__(self, run_dir, iso, drives, ovmf_code, shots_dir):
        self.proc, self.qmp, self.agent = start_live(run_dir, iso, drives, ovmf_code)
        self.gui = Gui(self.qmp, run_dir, shots_dir)

    def setup(self, base_url, packages, timeout=600):
        self.agent.wait_ready(timeout)
        sh = self.agent.run
        sh("until pgrep -u liveuser -x plasmashell && pgrep -u liveuser -x live-launcher; do sleep 1; done",
           timeout)  # fmt: skip
        sh("nm-online -q -t 120", 150)
        fetch = " && ".join(f"curl -fsSO {base_url}/{p}" for p in packages)
        sh(f"mkdir -p /root/xenpkg && cd /root/xenpkg && {fetch}")
        sh("pacman -U --noconfirm /root/xenpkg/*.pkg.tar.zst", 300)
        sh(f"python3 -c '{PATCH_CONFIG}'")
        sh("pkill -x live-launcher; true")

    def launch(self):
        # kioclient runs the menu entry's Exec (xen-polkit -> pkexec -> xen-root-launch) the
        # way Plasma does; ExitType=cgroup keeps the unit alive after kioclient returns.
        self.agent.run(
            "systemd-run --user -M liveuser@ --collect -p ExitType=cgroup --unit=xen-gui"
            " kioclient exec /usr/share/applications/xen.desktop"
        )
        self.agent.run(f"for i in $(seq 60); do pgrep -f '{APP}' && exit 0; sleep 1; done; exit 1", 90)

    def journal(self, lines=60):
        return self.agent.try_run(f"journalctl -b --no-pager -o cat | grep -v audit | tail -n {lines}")[1]

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    def wait_exit(self, timeout):
        try:
            self.proc.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            return False


def click_next(g):
    # The wizard's Next is the lowest "Next" on screen (guide text can mention it too).
    g.click(*max(g.wait("Next"), key=lambda m: m.y).center)


def next_page(g, title, timeout=60):
    click_next(g)
    return g.wait(title, timeout)


def choose(g, current, option, region=None):
    combo = g.click_text(current, region)
    time.sleep(0.5)
    hits = [m for m in g.wait(option, 10) if m.y > combo.y]
    assert hits, f"option {option!r} not shown below the {current!r} combo"
    g.click(*min(hits, key=lambda m: m.y).center)


def front_pages(g, hostname, password):
    g.wait("Welcome to XeroLinux", 120)
    g.save("welcome")
    g.click_text("Install XeroLinux")
    g.wait("Location", 60)
    g.save("locale")
    next_page(g, "Keyboard Configuration")
    g.save("keyboard")
    next_page(g, "Optional Software")
    g.save("packages")
    next_page(g, "Create User Account")
    label = g.wait("Username")[0]
    g.click(label.x + 60, label.y + 50, settle=0.3)
    for text in ["xen", "Xen Boot", hostname, password, password]:
        g.key("ctrl", "a")
        g.type(text)
        g.key("tab")
    g.wait("Passwords match", 10)
    g.save("users")
    next_page(g, "Select Installation Disk")
    g.wait("Select a disk", 30)
    g.save("partition-disk")


def fill_passphrase(g, passphrase):
    g.key("tab")
    for _ in range(2):
        g.key("ctrl", "a")
        g.type(passphrase)
        g.key("tab")


MANUAL_TABLE = (0, 0, 1100, SCREEN_H)


def dialog_ok(g, region):
    # OK/confirm buttons draw dark text on the accent fill, which OCR misses: they sit right of Cancel.
    cancel = max(g.wait("Cancel", 10, region), key=lambda m: (m.y, m.x))
    g.click(cancel.x + cancel.w + 52, cancel.y + cancel.h // 2)


def create_partition(g, size_mib, fs, mount):
    g.click_text("Free Space", MANUAL_TABLE)
    g.click_text("Create", MANUAL_TABLE)
    title = g.wait("Create a Partition", 10)[0]
    dialog = (title.x - 30, title.y - 40, title.x + 580, title.y + 420)
    if size_mib:
        mib = g.wait("MiB", 10, dialog)[0]
        g.click(mib.x - 20, mib.y + mib.h // 2, settle=0.3)
        g.key("ctrl", "a")
        g.type(str(size_mib))
        g.key("tab")
        g.wait(f"{size_mib} MiB", 10, dialog)
    if fs != "btrfs":
        choose(g, "btrfs", fs, dialog)
    g.wait(fs, 10, dialog)
    field = g.wait("no mount point", 10, dialog)[0]
    g.click(*field.center, settle=0.3)
    g.type(mount)
    g.save(f"manual-create-{fs}")
    title = g.wait("Create a Partition", 10)[0]
    dialog_ok(g, (title.x - 30, title.y - 40, title.x + 580, title.y + 420))
    g.wait("Create a Partition", 10, gone=True)


def pick_disk(g, name):
    choose(g, "Select a disk", name)
    g.wait("Options", 30)


def install(g, session, timeout):
    g.wait("Review Installation", 60)
    g.save("summary")
    g.click_left("understand that the", 34)
    button = max(g.wait("Install"), key=lambda m: m.y)
    g.click(*button.center)
    g.wait("Review Installation", 60, gone=True)
    g.wait("Please wait while the system", 60)
    g.save("installing")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        words = g.words()
        if find_phrase(words, "Installation Complete"):
            g.save("finished")
            return
        if find_phrase(words, "Installation Failed"):
            g.save("failed")
            raise AssertionError(f"installation failed: {' '.join(w['text'] for w in words)[:800]}\n{session.journal()}")
        time.sleep(20)
    g.save("timeout")
    raise AssertionError(f"install not finished after {timeout}s\n{session.journal()}")


def reboot(g, session):
    g.click_text("Reboot Now")
    assert session.wait_exit(180), "the live system did not reboot after Reboot Now"


def start_live(run_dir, iso, drives, ovmf_code):
    qmp_path, agent_path = run_dir / "qmp.sock", run_dir / "agent.sock"
    for path in (qmp_path, agent_path):
        path.unlink(missing_ok=True)
    cmd = [
        "qemu-system-x86_64", "-machine", "q35", "-enable-kvm", "-m", str(LIVE_MEMORY), "-smp", "4",
        "-display", "none", "-no-reboot",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={ovmf_code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={run_dir / 'OVMF_VARS.4m.fd'}",
        "-device", f"virtio-vga,xres={SCREEN_W},yres={SCREEN_H}",
        "-device", "qemu-xhci", "-device", "usb-tablet",
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-chardev", f"socket,id=agent,path={agent_path},server=on,wait=off",
        "-device", "virtio-serial-pci", "-device", "virtserialport,chardev=agent,name=xen.agent",
        *agent_credentials(),
        "-drive", f"if=none,id=cd0,file={iso},media=cdrom,readonly=on",
        "-device", "ide-cd,drive=cd0,bus=ide.0,bootindex=0",
        *drives,
        "-nic", "user,model=virtio-net-pci",
        "-serial", f"file:{run_dir / 'serial-live.log'}",
    ]  # fmt: skip
    with open(run_dir / "qemu-live.err", "wb") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
    return proc, Qmp(qmp_path), Agent(agent_path)
