.pragma library

var efi = "#5597e6"
var linux = "#10B981"
var btrfs = "#F59E0B"
var windows = "#38BDF8"
var swap = "#A78BFA"
var other = "#9CA3AF"
var free = "#9932373c"
var created = "#c0392b"

function forPartition(partType, fstype) {
    var t = (partType || "").toLowerCase()
    var f = (fstype || "").toLowerCase()
    if (t === "efi" || f === "vfat") return efi
    if (t === "swap" || f === "swap") return swap
    if (t === "windows" || f === "ntfs") return windows
    if (f === "btrfs") return btrfs
    if (t === "linux" || f === "ext4" || f === "ext3" || f === "ext2" || f === "xfs") return linux
    return other
}

function forSegment(seg) {
    if (seg.kind === "free") return free
    if (seg.newRef) return created
    return forPartition(seg.partType, seg.fstype)
}
