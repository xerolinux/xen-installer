#!/bin/bash
exec > /dev/ttyS0 2>&1

SCENARIO=$(sed -n 's/.*xen_scenario=\([^ ]*\).*/\1/p' /proc/cmdline)
PHASE=$(sed -n 's/.*xen_phase=\([^ ]*\).*/\1/p' /proc/cmdline)
BASE=$(sed -n 's/.*script=\(http:\/\/[^ ]*\)\/guest\.sh.*/\1/p' /proc/cmdline)
ARCHIVE=$(sed -n 's/.*xen_archive=\([^ ]*\).*/\1/p' /proc/cmdline)

fail() {
    python -c 'import json, sys; print("XEN-RESULT " + json.dumps({"scenario": sys.argv[1], "status": "FAIL", "detail": sys.argv[2]}))' "$SCENARIO" "$1"
    poweroff
    exit 1
}

echo "XEN-VM-START $SCENARIO"
# Repos as of the ISO's build day, so -Sy installs match the live system (no partial upgrade)
echo "Server = https://archive.archlinux.org/repos/$ARCHIVE/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
for try in 1 2 3; do
    pacman -Sy --noconfirm python-yaml python-pydantic python-pydantic-core os-prober squashfs-tools arch-install-scripts && break
    [ "$try" = 3 ] && fail "pacman install failed"
    sleep 10
done
mkdir -p /root/xen
curl -fsS "$BASE/payload.tar.gz" | tar -xzC /root/xen || fail "payload download failed"
if [ "$PHASE" = verify ]; then
    curl -fsS -o /root/xen/state.json "$BASE/state-$SCENARIO.json" || fail "state download failed"
fi

export PYTHONPATH=/root/xen/src:/root/xen/scripts/vmtest
python -c 'import xen.jobs.partition' || fail "cannot import xen.jobs.partition in guest"
cd /root/xen/scripts/vmtest
python -m scenarios_runner "$SCENARIO" "$PHASE"
poweroff
