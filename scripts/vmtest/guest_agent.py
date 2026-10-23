import json
import os
import subprocess
import time

PORT = "/dev/virtio-ports/xen.agent"


def handle(request):
    try:
        proc = subprocess.run(
            ["bash", "-c", request["cmd"]],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=request.get("timeout", 120),
        )
        return {"id": request["id"], "rc": proc.returncode, "out": proc.stdout + proc.stderr}
    except subprocess.TimeoutExpired:
        return {"id": request["id"], "rc": -1, "out": "agent: timeout"}


def main():
    while not os.path.exists(PORT):
        time.sleep(0.5)
    fd = os.open(PORT, os.O_RDWR)
    buffer = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            time.sleep(0.2)
            continue
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                data = memoryview(json.dumps(handle(json.loads(line))).encode() + b"\n")
                while data:
                    data = data[os.write(fd, data):]


if __name__ == "__main__":
    main()
