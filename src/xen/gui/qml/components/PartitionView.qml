/*
 * PartitionView - Disk Selection and Partitioning
 *
 * Displays:
 * - List of available disks with details
 * - Data loss warning
 * - Automatic partitioning options (filesystem, swap, EFI, encryption)
 * - Manual partitioning (ManualPartitionEditor): create/delete/resize/format
 *   with mount-point assignment, filesystem choice hardcoded to ext4/xfs/btrfs
 *
 * Auto-only controls (swap strategy, encryption, fixed EFI size) are not
 * wired for manual mode's backend path and stay hidden there - manual users
 * build their own EFI/swap partitions in the editor instead.
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

Item {
    id: root

    // Signals
    signal diskSelected(string disk)
    // Auto-mode option signals (engine slots added in parallel by bridge agent)
    signal filesystemSelected(string fs)          // "ext4" | "xfs" | "btrfs"
    signal swapStrategySelected(string strategy)  // "file" | "none" | "hibernate"
    signal encryptionToggled(bool enabled)
    signal encryptionPassphraseSet(string passphrase)
    signal efiSizeChanged(int sizeMb)

    // External properties
    property var disksModel: []  // Array of disk objects: {name, size, type, removable, partitions}
    // Sélections : miroirs descendants (lecture seule) de la source de vérité
    // `engine`. Jamais réassignés impérativement (les clics émettent des signaux
    // relayés par Main.qml vers engine.setX), donc ces bindings restent vivants
    // et reflètent toujours l'état courant après un retour arrière / le résumé.
    readonly property string selectedDisk: engine.selectedDisk
    readonly property string partitionMode: engine.partitionMode  // "auto" | "manual"
    readonly property string filesystem: engine.filesystem        // "ext4" | "xfs" | "btrfs"
    readonly property string swapStrategy: engine.swapStrategy     // "file" | "none" | "hibernate"
    readonly property bool encryptionEnabled: engine.encryption
    // Btrfs needs Snapper/grub-btrfs/btrfs-assistant, none of which exist
    // offline (not part of the live squashfs) - hidden entirely rather
    // than offering a half-configured Btrfs install.
    readonly property bool btrfsAvailable: engine.hasInternet

    readonly property int efiSizeMb: 2048      // fixed for MVP

    // Encryption passphrase validity (passphrase is NOT bound downward, security)
    readonly property bool encryptionPassValid:
        !encryptionEnabled ||
        (encPassField.text.length >= 8 && encPassField.text === encPassConfirmField.text)

    // Theme colors
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color secondaryColor: "#3a7bc8"
    property color accentColor: "#6b9ce8"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"

    // Histobar partition palette (by partType, with fstype fallback)
    property color colorEfi: "#5597e6"        // efi / vfat
    property color colorLinux: "#10B981"      // ext4 / linux
    property color colorBtrfs: "#F59E0B"      // btrfs
    property color colorWindows: "#38BDF8"    // ntfs / windows
    property color colorSwap: "#A78BFA"       // swap
    property color colorOther: "#9CA3AF"      // other
    property color colorFree: Qt.rgba(0.196, 0.216, 0.235, 0.6)  // free space (surface, semi-transparent)

    // Map a partition (partType/fstype) to its histobar color
    function partitionColor(partType, fstype) {
        var t = (partType || "").toLowerCase()
        var f = (fstype || "").toLowerCase()
        if (t === "efi" || f === "vfat") return colorEfi
        if (t === "swap" || f === "swap") return colorSwap
        if (t === "windows" || f === "ntfs") return colorWindows
        if (f === "btrfs") return colorBtrfs
        if (t === "linux" || f === "ext4" || f === "ext3" || f === "ext2" || f === "xfs") return colorLinux
        return colorOther
    }

    // Human-readable byte size for tooltips/legend
    function humanSize(bytes) {
        if (!bytes || bytes <= 0) return "0 o"
        var units = ["o", "Ko", "Mo", "Go", "To"]
        var i = 0
        var v = bytes
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
        return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + " " + units[i]
    }

    readonly property bool canProceed: selectedDisk !== "" && encryptionPassValid
        && (partitionMode !== "manual" || engine.manualPlanValid)

    // Fixed, non-scrolling two-card flow (no ScrollView): a disk card,
    // then - once a disk is picked - a mode+options card directly below
    // it. Sized to fit the viewport in one screen for a normal disk count;
    // the only unbounded list (manual mode's existing-partitions table)
    // gets its own small internal scroll instead of growing the page.
    Item {
        anchors.fill: parent

        // No ambient glow here (unlike Welcome/Locale's single centered
        // card): with two cards stacked 14px apart, even a subtle glow
        // ring is big enough to bridge the gap and read as a phantom
        // third border wrapping both cards. Their own 2px solid borders
        // already read clearly against the page background.

            ColumnLayout {
                id: pageColumn
                anchors.centerIn: parent
                width: Math.min(parent.width - 40, 1100 * uiScale)
                spacing: 14

                // Page icon (same floppy-disk glyph SummaryView uses for
                // its Storage section - full-color emoji, unlike the
                // hard-drive dingbat, which most fonts render as a flat
                // monochrome outline).
                Text {
                    text: "\u{1F4BE}"
                    font.pixelSize: 48 * uiScale
                    Layout.alignment: Qt.AlignHCenter
                }

                // Title
                Text {
                    text: qsTr("Select Installation Disk")
                    font.pixelSize: 22 * uiScale
                    font.bold: true
                    color: textColor
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: qsTr("Choose where to install the system")
                    font.pixelSize: 13
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                }

                // ===== Card 1: disk selection =====
                Rectangle {
                    id: diskCard
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredHeight: diskCardColumn.implicitHeight + 28
                    radius: 20
                    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                    border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                    border.width: 2

                ColumnLayout {
                    id: diskCardColumn
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 14
                    spacing: 8

                // Warning banner
                Rectangle {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 1100
                    Layout.preferredHeight: warningColumn.implicitHeight + 12
                    radius: 12
                    color: Qt.rgba(errorColor.r, errorColor.g, errorColor.b, 0.15)
                    border.color: errorColor
                    border.width: 2
                    visible: selectedDisk !== ""

                    Column {
                        id: warningColumn
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 2

                        Row {
                            spacing: 8

                            Text {
                                text: "\u{26A0}\u{FE0F}"  // Warning sign
                                font.pixelSize: 15
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("WARNING: All data on the selected disk will be erased!")
                                font.pixelSize: 13
                                font.bold: true
                                color: errorColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        Text {
                            text: qsTr("Make sure you have backed up all important data before proceeding. This operation cannot be undone.")
                            font.pixelSize: 11
                            color: textColor
                            wrapMode: Text.WordWrap
                            width: parent.width
                        }
                    }
                }

                // Disks list
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 1100
                    spacing: 6

                    Text {
                        text: qsTr("Available Disks")
                        font.pixelSize: 16
                        font.bold: true
                        color: textColor
                    }

                    Repeater {
                        model: disksModel

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: diskColumn.implicitHeight + 12
                            radius: 16
                            color: selectedDisk === modelData.name ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.2) : surfaceColor
                            border.color: selectedDisk === modelData.name ? primaryColor : "transparent"
                            border.width: 2

                            Behavior on color {
                                ColorAnimation { duration: 150 }
                            }

                            Behavior on border.color {
                                ColorAnimation { duration: 150 }
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: diskSelected(modelData.name)
                            }

                            Column {
                                id: diskColumn
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 5

                                // Disk header
                                Row {
                                    width: parent.width
                                    spacing: 10

                                    Rectangle {
                                        width: 36
                                        height: 36
                                        radius: 8
                                        color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.2)

                                        // Device kind detection: nvme > ssd > hdd
                                        property string diskKind: {
                                            var n = (modelData.name || "").toLowerCase()
                                            if (n.indexOf("nvme") !== -1) return "nvme"
                                            if ((modelData.type || "").toUpperCase() === "SSD") return "ssd"
                                            return "hdd"
                                        }

                                        // QML-drawn vector icons (always render, no missing-glyph squares)
                                        Item {
                                            anchors.centerIn: parent
                                            width: 28
                                            height: 28

                                            // --- HDD: classic drive body + platter circle ---
                                            Item {
                                                anchors.fill: parent
                                                visible: parent.parent.diskKind === "hdd"

                                                Rectangle {
                                                    anchors.centerIn: parent
                                                    width: 26
                                                    height: 20
                                                    radius: 3
                                                    color: "transparent"
                                                    border.color: textColor
                                                    border.width: 2

                                                    Rectangle {  // platter
                                                        anchors.centerIn: parent
                                                        anchors.horizontalCenterOffset: -3
                                                        width: 11
                                                        height: 11
                                                        radius: 6
                                                        color: "transparent"
                                                        border.color: textColor
                                                        border.width: 2

                                                        Rectangle {  // spindle
                                                            anchors.centerIn: parent
                                                            width: 3
                                                            height: 3
                                                            radius: 2
                                                            color: textColor
                                                        }
                                                    }

                                                    Rectangle {  // head arm
                                                        anchors.right: parent.right
                                                        anchors.rightMargin: 4
                                                        anchors.bottom: parent.bottom
                                                        anchors.bottomMargin: 4
                                                        width: 8
                                                        height: 2
                                                        rotation: -35
                                                        color: textColor
                                                    }
                                                }
                                            }

                                            // --- SSD: 2.5" enclosure with connector pins ---
                                            Item {
                                                anchors.fill: parent
                                                visible: parent.parent.diskKind === "ssd"

                                                Rectangle {
                                                    anchors.centerIn: parent
                                                    width: 26
                                                    height: 18
                                                    radius: 3
                                                    color: "transparent"
                                                    border.color: textColor
                                                    border.width: 2

                                                    Row {  // chip dots
                                                        anchors.centerIn: parent
                                                        spacing: 4
                                                        Repeater {
                                                            model: 3
                                                            Rectangle {
                                                                width: 4; height: 4; radius: 1
                                                                color: primaryColor
                                                            }
                                                        }
                                                    }

                                                    Row {  // connector pins (bottom edge)
                                                        anchors.bottom: parent.bottom
                                                        anchors.bottomMargin: -3
                                                        anchors.left: parent.left
                                                        anchors.leftMargin: 4
                                                        spacing: 2
                                                        Repeater {
                                                            model: 4
                                                            Rectangle {
                                                                width: 2; height: 3
                                                                color: textColor
                                                            }
                                                        }
                                                    }
                                                }
                                            }

                                            // --- NVMe: M.2 stick (long thin) with notch + chips ---
                                            Item {
                                                anchors.fill: parent
                                                visible: parent.parent.diskKind === "nvme"

                                                Rectangle {
                                                    anchors.centerIn: parent
                                                    width: 28
                                                    height: 11
                                                    radius: 2
                                                    color: "transparent"
                                                    border.color: textColor
                                                    border.width: 2

                                                    Row {  // chips
                                                        anchors.centerIn: parent
                                                        anchors.horizontalCenterOffset: 2
                                                        spacing: 3
                                                        Repeater {
                                                            model: 2
                                                            Rectangle {
                                                                width: 6; height: 5; radius: 1
                                                                color: primaryColor
                                                            }
                                                        }
                                                    }

                                                    Rectangle {  // edge connector pins
                                                        anchors.left: parent.left
                                                        anchors.leftMargin: 2
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        width: 4
                                                        height: 7
                                                        color: textColor
                                                    }
                                                }

                                                Rectangle {  // mounting notch (semicircle hint)
                                                    anchors.right: parent.right
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    width: 4
                                                    height: 4
                                                    radius: 2
                                                    color: "transparent"
                                                    border.color: textColor
                                                    border.width: 1
                                                }
                                            }
                                        }
                                    }

                                    Column {
                                        width: parent.width - 60
                                        spacing: 4

                                        Row {
                                            spacing: 8

                                            Text {
                                                text: modelData.name
                                                font.pixelSize: 18
                                                font.bold: true
                                                color: textColor
                                            }

                                            Rectangle {
                                                height: 20
                                                width: typeLabel.width + 16
                                                radius: 4
                                                color: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0.3)
                                                anchors.verticalCenter: parent.verticalCenter

                                                Text {
                                                    id: typeLabel
                                                    anchors.centerIn: parent
                                                    text: modelData.type || "HDD"
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                    color: textColor
                                                }
                                            }

                                            Rectangle {
                                                height: 20
                                                width: removableLabel.width + 16
                                                radius: 4
                                                color: Qt.rgba(warningColor.r, warningColor.g, warningColor.b, 0.3)
                                                anchors.verticalCenter: parent.verticalCenter
                                                visible: modelData.removable

                                                Text {
                                                    id: removableLabel
                                                    anchors.centerIn: parent
                                                    text: qsTr("REMOVABLE")
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                    color: textColor
                                                }
                                            }
                                        }

                                        Text {
                                            text: qsTr("Size: ") + modelData.size
                                            font.pixelSize: 14
                                            color: textMutedColor
                                        }
                                    }
                                }

                                // Histobar: geometric layout of this disk (partitions + free space)
                                Item {
                                    id: histoBar
                                    width: parent.width
                                    // Match the cumulative height of the 2 text lines (name 18 + size 14 + spacing 4)
                                    implicitHeight: 36
                                    height: implicitHeight

                                    // Capture disk-level data so the inner Repeater's modelData
                                    // (a segment) does not shadow it.
                                    property var diskData: modelData
                                    property real diskBytes: (diskData && diskData.sizeBytes > 0) ? diskData.sizeBytes : 0
                                    // Ordered segments (kind: "partition" | "free") covering the
                                    // whole disk, positioned as they physically sit on the medium.
                                    property var segs: (diskData && diskData.segments) ? diskData.segments : []

                                    Rectangle {
                                        id: histoBarBg
                                        anchors.fill: parent
                                        radius: 6
                                        color: colorFree
                                        clip: true

                                        Row {
                                            anchors.fill: parent
                                            spacing: 0

                                            Repeater {
                                                model: histoBar.segs

                                                Rectangle {
                                                    height: histoBarBg.height
                                                    width: histoBar.diskBytes > 0
                                                        ? histoBarBg.width * (modelData.sizeBytes > 0 ? modelData.sizeBytes : 0) / histoBar.diskBytes
                                                        : 0
                                                    color: modelData.kind === "free"
                                                        ? colorFree
                                                        : partitionColor(modelData.partType, modelData.fstype)
                                                    border.color: modelData.kind === "free" ? "transparent" : Qt.rgba(0, 0, 0, 0.25)
                                                    border.width: modelData.kind === "free" ? 0 : 1

                                                    ToolTip.visible: segHover.hovered && width > 4
                                                    ToolTip.text: modelData.kind === "free"
                                                        ? qsTr("Free space") + "  " + humanSize(modelData.sizeBytes)
                                                        : (modelData.name || "")
                                                            + "  " + humanSize(modelData.sizeBytes)
                                                            + (modelData.fstype ? "  (" + modelData.fstype + ")" : "")

                                                    HoverHandler { id: segHover }
                                                }
                                            }
                                        }
                                    }

                                    // Subtle outline on the whole bar
                                    Rectangle {
                                        anchors.fill: parent
                                        radius: 6
                                        color: "transparent"
                                        border.color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.25)
                                        border.width: 1
                                    }
                                }

                                // Selected indicator. Always takes up space
                                // (opacity toggles, not visible) so every disk
                                // card in the list shares the same height
                                // regardless of which one is selected.
                                Rectangle {
                                    width: parent.width
                                    height: 30
                                    radius: 8
                                    color: Qt.rgba(successColor.r, successColor.g, successColor.b, 0.2)
                                    opacity: selectedDisk === modelData.name ? 1 : 0

                                    Row {
                                        anchors.centerIn: parent
                                        spacing: 6

                                        Text {
                                            text: "\u2713"
                                            font.pixelSize: 15
                                            color: successColor
                                            anchors.verticalCenter: parent.verticalCenter
                                        }

                                        Text {
                                            text: qsTr("Selected for installation")
                                            font.pixelSize: 13
                                            font.bold: true
                                            color: successColor
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Empty state
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        radius: 16
                        color: surfaceColor
                        visible: disksModel.length === 0

                        Column {
                            anchors.centerIn: parent
                            spacing: 16

                            Text {
                                text: "\u{1F4BE}"
                                font.pixelSize: 48
                                anchors.horizontalCenter: parent.horizontalCenter
                            }

                            Text {
                                text: qsTr("No disks found")
                                font.pixelSize: 18
                                font.bold: true
                                color: textColor
                                anchors.horizontalCenter: parent.horizontalCenter
                            }

                            Text {
                                text: qsTr("Please ensure a disk is connected and detected by the system")
                                font.pixelSize: 14
                                color: textMutedColor
                                anchors.horizontalCenter: parent.horizontalCenter
                            }
                        }
                    }
                }

                // Histobar legend (compact)
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 12
                    visible: disksModel.length > 0

                    Repeater {
                        model: [
                            { c: colorEfi, label: qsTr("EFI") },
                            { c: colorLinux, label: qsTr("Linux") },
                            { c: colorBtrfs, label: qsTr("btrfs") },
                            { c: colorWindows, label: qsTr("Windows") },
                            { c: colorSwap, label: qsTr("Swap") },
                            { c: colorFree, label: qsTr("Free") }
                        ]

                        Row {
                            spacing: 5

                            Rectangle {
                                width: 10
                                height: 10
                                radius: 3
                                color: modelData.c
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: modelData.label
                                font.pixelSize: 11
                                color: textMutedColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }
                } // end diskCardColumn
                } // end diskCard

                // ===== Card 2: mode + options. Only appears once a disk is
                // picked, directly below Card 1 - never overlapping it,
                // never requiring the page to scroll to reach it. =====
                Rectangle {
                    id: configCard
                    visible: selectedDisk !== ""
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredHeight: visible ? configCardColumn.implicitHeight + 28 : 0
                    radius: 20
                    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                    border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                    border.width: 2

                ColumnLayout {
                    id: configCardColumn
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 14
                    spacing: 8

                Text {
                    text: qsTr("Options")
                    font.pixelSize: 17
                    font.bold: true
                    color: textColor
                }

                // --- Mode toggle: Auto (erase + layout) vs Manual (editor) ---
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 10

                    RadioButton {
                        objectName: "partitionModeAutoRadio"
                        text: qsTr("Automatic")
                        checked: partitionMode !== "manual"
                        onClicked: engine.setPartitionMode("auto")
                        contentItem: Text {
                            text: parent.text
                            color: textColor
                            font.pixelSize: 14
                            font.bold: true
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: parent.indicator.width + parent.spacing
                        }
                    }

                    RadioButton {
                        objectName: "partitionModeManualRadio"
                        text: qsTr("Manual")
                        checked: partitionMode === "manual"
                        onClicked: {
                            engine.setPartitionMode("manual")
                            manualEditorPopup.open()
                        }
                        contentItem: Text {
                            text: parent.text
                            color: textColor
                            font.pixelSize: 14
                            font.bold: true
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: parent.indicator.width + parent.spacing
                        }
                    }
                }

                // --- Filesystem / Swap / EFI, side by side (auto mode only) ---
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 24
                    visible: partitionMode !== "manual"

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                RowLayout {
                                    spacing: 6
                                    Text {
                                        text: "\u{1F5C2}\u{FE0F}"
                                        font.pixelSize: 15
                                    }
                                    Text {
                                        text: qsTr("Filesystem")
                                        font.pixelSize: 13
                                        font.bold: true
                                        color: textColor
                                    }
                                }

                                Row {
                                    spacing: 20

                                    RadioButton {
                                        text: qsTr("ext4")
                                        checked: filesystem === "ext4"
                                        onClicked: filesystemSelected("ext4")
                                        contentItem: Text {
                                            text: parent.text
                                            color: textColor
                                            font.pixelSize: 14
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: parent.indicator.width + parent.spacing
                                        }
                                    }

                                    RadioButton {
                                        text: qsTr("xfs")
                                        checked: filesystem === "xfs"
                                        onClicked: filesystemSelected("xfs")
                                        contentItem: Text {
                                            text: parent.text
                                            color: textColor
                                            font.pixelSize: 14
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: parent.indicator.width + parent.spacing
                                        }
                                    }

                                    RadioButton {
                                        visible: btrfsAvailable
                                        text: qsTr("btrfs")
                                        checked: filesystem === "btrfs"
                                        onClicked: filesystemSelected("btrfs")
                                        contentItem: Text {
                                            text: parent.text
                                            color: textColor
                                            font.pixelSize: 14
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: parent.indicator.width + parent.spacing
                                        }
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                RowLayout {
                                    spacing: 6
                                    Text {
                                        text: "\u{1F4A4}"
                                        font.pixelSize: 15
                                    }
                                    Text {
                                        text: qsTr("Swap")
                                        font.pixelSize: 13
                                        font.bold: true
                                        color: textColor
                                    }
                                }

                                Row {
                                    spacing: 14

                                    RadioButton {
                                        text: qsTr("File (auto)")
                                        checked: swapStrategy === "file"
                                        onClicked: swapStrategySelected("file")
                                        contentItem: Text {
                                            text: parent.text
                                            color: textColor
                                            font.pixelSize: 14
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: parent.indicator.width + parent.spacing
                                        }
                                    }

                                    RadioButton {
                                        text: qsTr("None")
                                        checked: swapStrategy === "none"
                                        onClicked: swapStrategySelected("none")
                                        contentItem: Text {
                                            text: parent.text
                                            color: textColor
                                            font.pixelSize: 14
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: parent.indicator.width + parent.spacing
                                        }
                                    }

                                    RadioButton {
                                        text: qsTr("Hibernation")
                                        checked: swapStrategy === "hibernate"
                                        onClicked: swapStrategySelected("hibernate")
                                        contentItem: Text {
                                            text: parent.text
                                            color: textColor
                                            font.pixelSize: 14
                                            verticalAlignment: Text.AlignVCenter
                                            leftPadding: parent.indicator.width + parent.spacing
                                        }
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                RowLayout {
                                    spacing: 6
                                    Text {
                                        text: "\u{1F50C}"
                                        font.pixelSize: 15
                                    }
                                    Text {
                                        text: qsTr("EFI Partition")
                                        font.pixelSize: 13
                                        font.bold: true
                                        color: textColor
                                    }
                                }

                                Text {
                                    text: qsTr("2 GiB (mounted on /boot/efi)")
                                    font.pixelSize: 12
                                    color: textMutedColor
                                }
                            }
                }

                        // --- LUKS encryption (auto mode only - the manual
                        // backend path never calls _setup_luks) ---
                        Column {
                            Layout.fillWidth: true
                            spacing: 6
                            visible: partitionMode !== "manual"
                            height: visible ? implicitHeight : 0

                            CheckBox {
                                id: encryptionCheck
                                text: qsTr("Enable encryption (LUKS)")
                                checked: encryptionEnabled
                                onToggled: encryptionToggled(checked)
                                contentItem: Text {
                                    text: encryptionCheck.text
                                    color: textColor
                                    font.pixelSize: 13
                                    font.bold: true
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: encryptionCheck.indicator.width + encryptionCheck.spacing
                                }
                            }

                            // Passphrase fields (collapse when disabled)
                            ColumnLayout {
                                width: parent.width
                                spacing: 5
                                visible: encryptionEnabled
                                // collapse: zero height contribution when hidden
                                height: visible ? implicitHeight : 0

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 10

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 1
                                        Layout.alignment: Qt.AlignTop
                                        spacing: 8

                                        TextField {
                                            id: encPassField
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 48
                                            placeholderText: qsTr("Passphrase (min 8 characters)")
                                            font.pixelSize: 13
                                            echoMode: showEncPassCheck.checked ? TextInput.Normal : TextInput.Password

                                            onTextChanged: {
                                                if (encryptionEnabled)
                                                    encryptionPassphraseSet(text)
                                            }

                                            background: Rectangle {
                                                radius: 8
                                                color: encPassField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                                border.color: {
                                                    if (!encPassField.activeFocus) return textMutedColor;
                                                    if (encPassField.text.length === 0) return primaryColor;
                                                    return encPassField.text.length >= 8 ? successColor : errorColor;
                                                }
                                                border.width: 2
                                                Behavior on border.color { ColorAnimation { duration: 150 } }
                                            }

                                            color: textColor
                                            selectionColor: primaryColor
                                            selectedTextColor: textColor
                                            leftPadding: 16
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 1
                                        Layout.alignment: Qt.AlignTop
                                        spacing: 8

                                        TextField {
                                            id: encPassConfirmField
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 48
                                            placeholderText: qsTr("Confirm passphrase")
                                            font.pixelSize: 13
                                            echoMode: showEncPassCheck.checked ? TextInput.Normal : TextInput.Password

                                            onTextChanged: {
                                                if (encryptionEnabled)
                                                    encryptionPassphraseSet(encPassField.text)
                                            }

                                            background: Rectangle {
                                                radius: 8
                                                color: encPassConfirmField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                                border.color: {
                                                    if (!encPassConfirmField.activeFocus) return textMutedColor;
                                                    if (encPassConfirmField.text.length === 0) return primaryColor;
                                                    return (encPassConfirmField.text === encPassField.text) ? successColor : errorColor;
                                                }
                                                border.width: 2
                                                Behavior on border.color { ColorAnimation { duration: 150 } }
                                            }

                                            color: textColor
                                            selectionColor: primaryColor
                                            selectedTextColor: textColor
                                            leftPadding: 16
                                        }

                                        // Match indicator
                                        Row {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            visible: encPassConfirmField.text.length > 0

                                            Text {
                                                text: (encPassConfirmField.text === encPassField.text) ? "✓" : "✗"
                                                font.pixelSize: 11
                                                color: (encPassConfirmField.text === encPassField.text) ? successColor : errorColor
                                                anchors.verticalCenter: parent.verticalCenter
                                            }

                                            Text {
                                                text: (encPassConfirmField.text === encPassField.text)
                                                    ? qsTr("Passphrases match")
                                                    : qsTr("Passphrases do not match")
                                                font.pixelSize: 10
                                                color: (encPassConfirmField.text === encPassField.text) ? successColor : errorColor
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                        }
                                    }
                                }

                                CheckBox {
                                    id: showEncPassCheck
                                    text: qsTr("Show passphrase")
                                    contentItem: Text {
                                        text: showEncPassCheck.text
                                        color: textMutedColor
                                        font.pixelSize: 11
                                        verticalAlignment: Text.AlignVCenter
                                        leftPadding: showEncPassCheck.indicator.width + showEncPassCheck.spacing
                                    }
                                }

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
                            }
                        }

                // --- Manual mode: compact status row, full editor lives in a
                // scrollable popup (see manualEditorPopup below) - the old
                // inline fixed-height box was too cramped for the histobar +
                // table + dialogs to breathe. ---
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    visible: partitionMode === "manual"
                    spacing: 10

                    Text {
                        Layout.fillWidth: true
                        text: engine.manualPlanError !== ""
                            ? "⚠️ " + engine.manualPlanError
                            : qsTr("Manual partition plan looks valid.")
                        color: engine.manualPlanError !== "" ? errorColor : successColor
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }

                    Button {
                        text: qsTr("Open Manual Editor")
                        onClicked: manualEditorPopup.open()
                        contentItem: Text {
                            text: parent.text
                            color: textColor
                            font.pixelSize: 13
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            implicitHeight: 36
                            radius: 8
                            color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                            border.color: primaryColor
                            border.width: 1
                        }
                    }
                }

                } // end configCardColumn
                } // end configCard

                Item { height: 8 }
            } // end pageColumn
        } // end outer Item

    // ===== Manual partition editor (full-size, scrollable popup) =====
    Popup {
        id: manualEditorPopup
        objectName: "manualEditorPopup"
        anchors.centerIn: parent
        width: parent.width * 0.9
        height: parent.height * 0.88
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        // Explicit dim scrim: the default Fusion-style modal overlay reads as
        // barely-there against this app's own dark theme, letting the page
        // behind (disk list, legend) show through distractingly at the edges.
        Overlay.modal: Rectangle {
            color: Qt.rgba(0, 0, 0, 0.65)
        }

        background: Rectangle {
            radius: 16
            color: backgroundColor
            border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
            border.width: 2
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: qsTr("Manual Partitioning")
                    font.pixelSize: 20
                    font.bold: true
                    color: textColor
                }
                Button {
                    text: qsTr("Close")
                    onClicked: manualEditorPopup.close()
                    contentItem: Text {
                        text: parent.text
                        color: textColor
                        font.pixelSize: 13
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        implicitWidth: 80
                        implicitHeight: 32
                        radius: 8
                        color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                        border.color: textMutedColor
                        border.width: 1
                    }
                }
            }

            // Manual partitioning is new and destructive - flagged as
            // experimental regardless of plan validity, not just an error state.
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: experimentalPillText.implicitHeight + 14
                radius: height / 2
                color: Qt.rgba(warningColor.r, warningColor.g, warningColor.b, 0.12)
                border.color: Qt.rgba(warningColor.r, warningColor.g, warningColor.b, 0.4)
                border.width: 1

                Text {
                    id: experimentalPillText
                    anchors.centerIn: parent
                    width: parent.width - 28
                    text: "⚠️ " + qsTr("Manual Partitioning is still in the experimental stage, USE AT OWN RISK !")
                    font.pixelSize: 13
                    color: warningColor
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            Text {
                Layout.fillWidth: true
                visible: engine.manualPlanError !== ""
                text: "⚠️ " + engine.manualPlanError
                color: errorColor
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                // The vertical scrollbar isn't a floating overlay here - it
                // reserves real width inside the viewport once content is
                // tall enough to scroll (editor height can exceed 760, see
                // below). A width calculated purely from the popup, with no
                // allowance for that reservation, renders slightly too wide
                // and clips its own rightmost strip - only visible once
                // content actually gets tall enough to need the scrollbar,
                // which is why it didn't show up until a second pending
                // change pushed things past that threshold.
                ManualPartitionEditor {
                    width: manualEditorPopup.width - 60
                    height: Math.max(implicitHeight, 760)
                    primaryColor: root.primaryColor
                    surfaceColor: root.surfaceColor
                    backgroundColor: root.backgroundColor
                    textColor: root.textColor
                    textMutedColor: root.textMutedColor
                    successColor: root.successColor
                    warningColor: root.warningColor
                    errorColor: root.errorColor
                    colorEfi: root.colorEfi
                    colorLinux: root.colorLinux
                    colorBtrfs: root.colorBtrfs
                    colorSwap: root.colorSwap
                    colorFree: root.colorFree
                    hasInternet: root.btrfsAvailable
                }
            }
        }
    }
}
