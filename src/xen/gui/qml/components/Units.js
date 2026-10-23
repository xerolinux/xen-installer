function humanSize(bytes) {
    var units = [qsTr("B"), qsTr("KiB"), qsTr("MiB"), qsTr("GiB"), qsTr("TiB")]
    if (!bytes || bytes <= 0) return "0 " + units[0]
    var i = 0
    var v = bytes
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
    return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)) + " " + units[i]
}
