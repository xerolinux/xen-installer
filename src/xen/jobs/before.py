"""
Before Job - run XeroLinux's pre-configuration scripts on the target.

Its script (/usr/local/bin/xerolinux-before) re-initializes the pacman
keyring, installs the GRUB theme, and overwrites the archiso-only
/etc/mkinitcpio.d/linux.preset with the target's real preset. Must run
after install and before systemconfig rebuilds the initramfs.
"""

from __future__ import annotations

from xen.jobs.finalize import FinalizeJob


class BeforeJob(FinalizeJob):
    """Runs a config-driven, ordered list of pre-configuration scripts via
    arch-chroot - same mechanics as FinalizeJob, run at the start of the
    target-configuration phase instead of the end."""

    name = "before"
    description = "Run XeroLinux pre-configuration scripts"
    _label = "pre-configuration"
