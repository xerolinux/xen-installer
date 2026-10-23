/*
 * PartitionView - Disk Selection and Partitioning
 *
 * Displays:
 * - List of available disks with details
 * - Options card: Erase disk / Install alongside / Manual partitioning
 * - Erase and Alongside options (filesystem, swap, encryption)
 * - The manual partitioning sub-page host
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "PartitionColors.js" as PartitionColors
import "Units.js" as Units

Item {
    id: root

    // Signals
    signal diskSelected(string disk)
    // Auto-mode option signals (engine slots added in parallel by bridge agent)
    signal filesystemSelected(string fs)          // "ext4" | "xfs" | "btrfs"
    signal swapStrategySelected(string strategy)  // "file" | "none" | "hibernate"
    signal encryptionToggled(bool enabled)
    signal encryptionPassphraseSet(string passphrase)

    // External properties
    property var disksModel: []  // Array of disk objects: {name, size, type, removable, partitions}
    // Sélections : miroirs descendants (lecture seule) de la source de vérité
    // `engine`. Jamais réassignés impérativement (les clics émettent des signaux
    // relayés par Main.qml vers engine.setX), donc ces bindings restent vivants
    // et reflètent toujours l'état courant après un retour arrière / le résumé.
    readonly property string selectedDisk: engine.selectedDisk
    readonly property string partitionMode: engine.partitionMode  // "auto" | "alongside" | "manual"
    readonly property string filesystem: engine.filesystem        // "ext4" | "xfs" | "btrfs"
    readonly property string swapStrategy: engine.swapStrategy     // "file" | "none" | "hibernate"
    readonly property bool encryptionEnabled: engine.encryption
    // Btrfs needs Snapper/grub-btrfs/btrfs-assistant, none of which exist
    // offline (not part of the live squashfs) - hidden entirely rather
    // than offering a half-configured Btrfs install.
    readonly property bool btrfsAvailable: engine.hasInternet
    // Distro config can switch these features off entirely (engine exposes
    // them as allowEncryption / allowManualPartitioning).
    readonly property bool encryptionAllowed: engine.allowEncryption !== false
    readonly property bool manualAllowed: engine.allowManualPartitioning !== false

    // Auto-mode option lists, hoisted to root (see feedback-qtquick-gotchas:
    // a readonly property on a non-root Item isn't reliably visible to its
    // unqualified descendants) - Calamares offers these as combo boxes, not
    // radio rows, so these back OptionCombo models below.
    readonly property var fsChoices: btrfsAvailable ? ["ext4", "xfs", "btrfs"] : ["ext4", "xfs"]
    readonly property var swapChoices: ["file", "none", "hibernate"]
    readonly property var swapChoiceLabels: [qsTr("File (auto)"), qsTr("None"), qsTr("Hibernation")]

    // Encryption passphrase validity (passphrase is NOT bound downward, security).
    // Only enforced while the Erase/Alongside LUKS fields are actually on screen.
    readonly property bool encryptionActive: encryptionEnabled && encryptionAllowed && partitionMode !== "manual"
    readonly property bool encryptionPassValid:
        !encryptionActive ||
        (encPassField.text.length >= 8 && encPassField.text === encPassConfirmField.text)

    // Theme colors
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"

    component ThemedRadio: RadioButton {
        id: themedRadio
        indicator: Rectangle {
            implicitWidth: 20 * uiScale
            implicitHeight: 20 * uiScale
            x: themedRadio.leftPadding
            y: (themedRadio.height - height) / 2
            radius: width / 2
            color: "transparent"
            border.color: themedRadio.checked ? primaryColor : textMutedColor
            border.width: 2
            Rectangle {
                anchors.centerIn: parent
                width: parent.width / 2
                height: width
                radius: width / 2
                color: primaryColor
                visible: themedRadio.checked
            }
        }
    }

    component ThemedCheck: CheckBox {
        id: themedCheck
        property color labelColor: textColor
        font.pixelSize: 13 * uiScale
        indicator: Rectangle {
            implicitWidth: 20 * uiScale
            implicitHeight: 20 * uiScale
            x: themedCheck.leftPadding
            y: (themedCheck.height - height) / 2
            radius: 4
            color: "transparent"
            border.color: themedCheck.checked ? primaryColor : textMutedColor
            border.width: 2
            Text {
                anchors.centerIn: parent
                visible: themedCheck.checked
                text: "\u2713"
                color: primaryColor
                font.pixelSize: 14 * uiScale
                font.bold: true
            }
        }
        contentItem: Text {
            text: themedCheck.text
            font: themedCheck.font
            color: themedCheck.labelColor
            verticalAlignment: Text.AlignVCenter
            leftPadding: themedCheck.indicator.width + themedCheck.spacing
        }
    }

    function parseSubvolumes(subvolumesJson) {
        try { return JSON.parse(subvolumesJson) } catch (e) { return [] }
    }
    // Config may list keys in any order, so compare values rather than the raw JSON.
    function subvolumesKey(list) {
        return JSON.stringify(list.map(function(sv) { return [sv.name, sv.mountpoint] }))
    }

    readonly property int selectedDiskIndex: {
        for (var i = 0; i < disksModel.length; i++)
            if (disksModel[i].name === selectedDisk) return i
        return -1
    }
    readonly property var selectedDiskData: selectedDiskIndex >= 0 ? disksModel[selectedDiskIndex] : null

    function diskFacts(disk) {
        if (!disk) return []
        var table = (disk.ptType || "").toLowerCase()
        var count = disk.partitions.length
        var facts = [{ text: disk.transport === "nvme" ? qsTr("NVMe SSD") : disk.type }]
        if (disk.removable) facts.push({ text: qsTr("Removable") })
        facts.push({ text: disk.size },
                   { text: table === "gpt" ? "GPT" : table === "dos" ? "MBR" : qsTr("No partition table") },
                   { text: count === 1 ? qsTr("1 partition") : qsTr("%1 partitions").arg(count) })
        var systems = []
        disk.partitions.forEach(function(p) {
            if (!p.osName || systems.indexOf(p.osName) >= 0) return
            systems.push(p.osName)
            facts.push({ text: p.osName, color: p.partType === "windows" ? PartitionColors.windows : PartitionColors.linux })
        })
        var free = 0
        disk.segments.forEach(function(seg) { if (seg.kind === "free") free += seg.sizeBytes })
        // Alignment gaps are not usable space
        facts.push({ text: free >= 1024 * 1024 * 1024 ? qsTr("%1 free").arg(Units.humanSize(free)) : qsTr("No free space") })
        return facts
    }

    readonly property bool canProceed: selectedDisk !== "" && encryptionPassValid && engine.planValid

    // Calamares' partition module is a QStackedWidget of two pages:
    // ChoicePage (disk + Erase/Manual choice) then, only once Manual is
    // picked and Next is clicked, PartitionPage takes over the WHOLE
    // window (PartitionViewStep::next()/back() swap between them - see
    // src/modules/partition/PartitionViewStep.cpp in the real app). This
    // mirrors that: the wizard's own Next/Back (Main.qml) call
    // enterManualEditor()/exitManualEditor() instead of advancing/
    // retreating a step, so the editor gets the full page - not squeezed
    // under the disk card (which overflowed) and not a popup (which
    // isn't how Calamares does it).
    property bool manualEditorActive: false
    onPartitionModeChanged: if (partitionMode !== "manual") manualEditorActive = false
    function enterManualEditor() { manualEditorActive = true }
    function exitManualEditor() { manualEditorActive = false }

    // Two-card flow: a disk card, then - once a disk is picked - a
    // mode+options card directly below it. Centered when it fits, scrolls
    // when it doesn't (small windows, many disks, encryption fields open).
    ScrollView {
        id: pageScroll
        objectName: "partitionPageScroll"
        anchors.fill: parent
        visible: !manualEditorActive
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        WheelHandler {
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(event) {
                var flickable = pageScroll.contentItem
                var newY = flickable.contentY - (event.angleDelta.y * 3.0 / 120.0 * 40)
                flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, newY))
                event.accepted = true
            }
        }

        // No ambient glow here (unlike Welcome/Locale's single centered
        // card): with two cards stacked 14px apart, even a subtle glow
        // ring is big enough to bridge the gap and read as a phantom
        // third border wrapping both cards. Their own 2px solid borders
        // already read clearly against the page background.
        Item {
            id: scrollContent
            width: pageScroll.availableWidth
            implicitHeight: Math.max(pageColumn.implicitHeight + 16, root.height)
            height: implicitHeight

            ColumnLayout {
                id: pageColumn
                anchors.centerIn: parent
                width: Math.min(parent.width - 40, 1100 * uiScale)
                spacing: 10 * uiScale

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3 * uiScale

                    Text {
                        text: qsTr("Select Installation Disk")
                        font.pixelSize: 22 * uiScale
                        font.bold: true
                        color: textColor
                        Layout.alignment: Qt.AlignHCenter
                    }

                    Text {
                        text: qsTr("Choose where to install the system")
                        font.pixelSize: 13 * uiScale
                        color: textMutedColor
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                // ===== Card 1: disk selection =====
                Rectangle {
                    id: diskCard
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredHeight: diskCardColumn.implicitHeight + 28 * uiScale
                    radius: 20
                    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                    border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                    border.width: 2

                ColumnLayout {
                    id: diskCardColumn
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 14 * uiScale
                    spacing: 8

                // Warning banner
                Rectangle {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 1100 * uiScale
                    Layout.preferredHeight: warningColumn.implicitHeight + 12 * uiScale
                    radius: 12
                    color: Qt.rgba(errorColor.r, errorColor.g, errorColor.b, 0.15)
                    border.color: errorColor
                    border.width: 2
                    visible: selectedDisk !== "" && partitionMode !== "alongside"

                    Column {
                        id: warningColumn
                        anchors.fill: parent
                        anchors.margins: 6 * uiScale
                        spacing: 2

                        Row {
                            spacing: 8 * uiScale

                            Text {
                                text: "\u{26A0}\u{FE0F}"  // Warning sign
                                font.pixelSize: 15 * uiScale
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("WARNING: All data on the selected disk will be erased!")
                                font.pixelSize: 13 * uiScale
                                font.bold: true
                                color: errorColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        Text {
                            text: qsTr("Make sure you have backed up all important data before proceeding. This operation cannot be undone.")
                            font.pixelSize: 11 * uiScale
                            color: textColor
                            wrapMode: Text.WordWrap
                            width: parent.width
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10 * uiScale

                    Text {
                        text: qsTr("Storage device:")
                        font.pixelSize: 13 * uiScale
                        font.bold: true
                        color: textColor
                    }

                    OptionCombo {
                        id: diskCombo
                        objectName: "diskCombo"
                        Layout.fillWidth: true
                        Layout.maximumWidth: 420 * uiScale
                        uiScale: root.uiScale
                        primaryColor: root.primaryColor
                        backgroundColor: root.backgroundColor
                        surfaceColor: root.surfaceColor
                        textColor: root.textColor
                        textMutedColor: root.textMutedColor
                        model: disksModel.map(function(d) {
                            return [d.name, d.model, d.size].filter(function(part) { return !!part }).join("  ")
                        })
                        currentIndex: selectedDiskIndex
                        displayText: selectedDiskIndex < 0 ? qsTr("Select a disk") : model[selectedDiskIndex]
                        onActivated: function(index) {
                            diskSelected(disksModel[index].name)
                            // ComboBox overwrites currentIndex on activation, which drops the binding.
                            currentIndex = Qt.binding(function() { return selectedDiskIndex })
                        }
                    }

                    Row {
                        objectName: "disksScanningIndicator"
                        visible: engine.disksScanning
                        spacing: 6 * uiScale

                        BusyIndicator {
                            width: 18 * uiScale
                            height: 18 * uiScale
                            running: parent.visible
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Text {
                            text: qsTr("Scanning disks...")
                            font.pixelSize: 12 * uiScale
                            color: textMutedColor
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }

                    Item { Layout.fillWidth: true }

                    Row {
                        spacing: 12 * uiScale
                        visible: selectedDiskData !== null

                        Repeater {
                            model: [
                                { c: PartitionColors.efi, label: qsTr("EFI") },
                                { c: PartitionColors.linux, label: qsTr("Linux") },
                                { c: PartitionColors.btrfs, label: qsTr("btrfs") },
                                { c: PartitionColors.windows, label: qsTr("Windows") },
                                { c: PartitionColors.swap, label: qsTr("Swap") },
                                { c: PartitionColors.free, label: qsTr("Free") }
                            ]

                            Row {
                                spacing: 5 * uiScale

                                Rectangle {
                                    width: 10 * uiScale
                                    height: 10 * uiScale
                                    radius: 3
                                    color: modelData.c
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: modelData.label
                                    font.pixelSize: 11 * uiScale
                                    color: textMutedColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }
                    }
                }

                Column {
                    objectName: "noDisksState"
                    Layout.fillWidth: true
                    visible: disksModel.length === 0 && !engine.disksScanning
                    spacing: 2 * uiScale

                    Text {
                        text: qsTr("No disks found")
                        font.pixelSize: 14 * uiScale
                        font.bold: true
                        color: errorColor
                    }

                    Text {
                        width: parent.width
                        text: qsTr("Please ensure a disk is connected and detected by the system")
                        font.pixelSize: 12 * uiScale
                        color: textMutedColor
                        wrapMode: Text.WordWrap
                    }
                }

                // In alongside mode the panel's clickable "Current:" bar shows this disk instead.
                PartitionBar {
                    Layout.fillWidth: true
                    Layout.preferredHeight: implicitHeight
                    visible: selectedDiskData !== null && partitionMode !== "alongside"
                    uiScale: root.uiScale
                    textColor: root.textColor
                    outlineColor: root.textMutedColor
                    segments: selectedDiskData ? selectedDiskData.segments : []
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: 6 * uiScale
                    visible: selectedDiskData !== null

                    Repeater {
                        model: diskFacts(selectedDiskData)

                        Rectangle {
                            readonly property color tint: modelData.color || primaryColor
                            width: factText.implicitWidth + 16 * uiScale
                            height: factText.implicitHeight + 6 * uiScale
                            radius: height / 2
                            color: Qt.rgba(tint.r, tint.g, tint.b, 0.18)
                            border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.6)
                            border.width: 1

                            Text {
                                id: factText
                                objectName: "diskFactChip"
                                anchors.centerIn: parent
                                text: modelData.text
                                font.pixelSize: 11 * uiScale
                                color: textColor
                            }
                        }
                    }
                }
                } // end diskCardColumn
                } // end diskCard

                Rectangle {
                    objectName: "diskHintCard"
                    visible: selectedDisk === "" && disksModel.length > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? hintColumn.implicitHeight + 28 * uiScale : 0
                    radius: 20
                    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                    border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                    border.width: 2

                    Column {
                        id: hintColumn
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 14 * uiScale
                        spacing: 8 * uiScale

                        Text {
                            text: "\u{1F4A1} " + qsTr("Pick a disk to see your options")
                            font.pixelSize: 15 * uiScale
                            font.bold: true
                            color: textColor
                        }

                        Repeater {
                            model: [
                                { icon: "\u{1F5D1}\u{FE0F}", name: qsTr("Erase disk"), info: qsTr("%1 takes the whole drive.").arg(branding.name) },
                                { icon: "\u{2194}\u{FE0F}", name: qsTr("Install alongside"), info: qsTr("Shrink Windows or another Linux and keep it bootable.") },
                                { icon: "\u{1F527}", name: qsTr("Manual partitioning"), info: qsTr("Your own partition layout, for advanced users.") }
                            ]

                            Row {
                                spacing: 8 * uiScale

                                Text {
                                    text: modelData.icon
                                    font.pixelSize: 14 * uiScale
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: "<b>" + modelData.name + "</b>: " + modelData.info
                                    textFormat: Text.StyledText
                                    font.pixelSize: 12 * uiScale
                                    color: textMutedColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }
                    }
                }

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
                    anchors.margins: 14 * uiScale
                    spacing: 8

                Text {
                    text: qsTr("Options")
                    font.pixelSize: 17 * uiScale
                    font.bold: true
                    color: textColor
                }

                // Calamares' ChoicePage: a plain list of radio rows, each
                // with its options nested directly under it.
                component ModeChoice: Item {
                    id: choice
                    property string mode
                    property string radioName
                    property string icon
                    property string title
                    property string description

                    Layout.fillWidth: true
                    implicitHeight: choiceRow.implicitHeight

                    RowLayout {
                        id: choiceRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        spacing: 10 * uiScale

                        ThemedRadio {
                            objectName: choice.radioName
                            checked: partitionMode === choice.mode
                            onClicked: engine.setPartitionMode(choice.mode)
                        }
                        Text { text: choice.icon; font.pixelSize: 18 * uiScale }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text { text: choice.title; color: textColor; font.pixelSize: 14 * uiScale; font.bold: true }
                            Text {
                                Layout.fillWidth: true
                                text: choice.description
                                color: textMutedColor
                                font.pixelSize: 12 * uiScale
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    // Calamares' ClickableLabel+buddy: the whole row is clickable.
                    MouseArea {
                        anchors.fill: parent
                        onClicked: engine.setPartitionMode(choice.mode)
                    }
                }

                component FilesystemCombos: Row {
                    spacing: 10 * uiScale

                    Text {
                        height: fsCombo.height
                        verticalAlignment: Text.AlignVCenter
                        text: qsTr("File system:")
                        font.pixelSize: 12 * uiScale
                        color: textColor
                    }
                    OptionCombo {
                        id: fsCombo
                        width: 120 * uiScale
                        uiScale: root.uiScale
                        primaryColor: root.primaryColor
                        backgroundColor: root.backgroundColor
                        surfaceColor: root.surfaceColor
                        textColor: root.textColor
                        textMutedColor: root.textMutedColor
                        model: fsChoices
                        currentIndex: Math.max(0, fsChoices.indexOf(filesystem))
                        onActivated: filesystemSelected(fsChoices[currentIndex])
                    }
                    Text {
                        height: fsCombo.height
                        verticalAlignment: Text.AlignVCenter
                        text: qsTr("Swap:")
                        font.pixelSize: 12 * uiScale
                        color: textColor
                    }
                    OptionCombo {
                        width: 140 * uiScale
                        uiScale: root.uiScale
                        primaryColor: root.primaryColor
                        backgroundColor: root.backgroundColor
                        surfaceColor: root.surfaceColor
                        textColor: root.textColor
                        textMutedColor: root.textMutedColor
                        model: swapChoiceLabels
                        currentIndex: Math.max(0, swapChoices.indexOf(swapStrategy))
                        onActivated: swapStrategySelected(swapChoices[currentIndex])
                    }
                }

                component SubvolumesControl: Row {
                    readonly property var current: parseSubvolumes(engine.btrfsSubvolumes)
                    readonly property bool isDefault:
                        subvolumesKey(current) === subvolumesKey(parseSubvolumes(engine.defaultBtrfsSubvolumes))
                    visible: filesystem === "btrfs"
                    spacing: 8 * uiScale

                    Text {
                        height: editSubvolumesButton.height
                        verticalAlignment: Text.AlignVCenter
                        text: qsTr("Subvolumes:")
                        font.pixelSize: 12 * uiScale
                        color: textColor
                    }
                    Text {
                        objectName: "subvolumesSummary"
                        height: editSubvolumesButton.height
                        verticalAlignment: Text.AlignVCenter
                        text: (isDefault ? qsTr("Default (%1)") : qsTr("Custom (%1)")).arg(current.length)
                        font.pixelSize: 12 * uiScale
                        color: textMutedColor
                    }
                    Button {
                        id: editSubvolumesButton
                        objectName: "subvolumesEditButton"
                        text: qsTr("Edit…")
                        implicitHeight: 36 * uiScale
                        leftPadding: 12 * uiScale
                        rightPadding: 12 * uiScale
                        onClicked: subvolumeDialog.open(engine.btrfsSubvolumes)
                        contentItem: Text {
                            text: editSubvolumesButton.text
                            color: textColor
                            font.pixelSize: 12 * uiScale
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 8
                            color: editSubvolumesButton.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                            border.color: primaryColor
                            border.width: 1
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6 * uiScale

                    ModeChoice {
                        mode: "auto"
                        radioName: "partitionModeAutoRadio"
                        icon: "\u{1F5D1}\u{FE0F}"
                        title: qsTr("Erase disk")
                        description: qsTr("This will delete all data currently present on the selected storage device.")
                    }

                    Flow {
                        Layout.fillWidth: true
                        Layout.leftMargin: 54 * uiScale
                        visible: partitionMode === "auto"
                        spacing: 10 * uiScale

                        FilesystemCombos {}
                        SubvolumesControl {}

                        Text {
                            objectName: "eraseEfiNote"
                            height: 36 * uiScale
                            verticalAlignment: Text.AlignVCenter
                            text: qsTr("EFI: %1 MiB (/boot/efi)").arg(engine.efiSizeMb)
                            font.pixelSize: 11 * uiScale
                            color: textMutedColor
                        }
                    }

                    // Kept while selected so a disk change never hides the active choice.
                    ModeChoice {
                        visible: engine.alongsideAvailable || partitionMode === "alongside"
                        mode: "alongside"
                        radioName: "partitionModeAlongsideRadio"
                        icon: "↔\u{FE0F}"
                        title: qsTr("Install alongside")
                        description: qsTr("Shrink an existing partition to make room for %1. Windows or your other Linux stays bootable from the GRUB menu.").arg(branding.name)
                    }

                    AlongsidePanel {
                        visible: partitionMode === "alongside"
                        Layout.fillWidth: true
                        Layout.leftMargin: 54 * uiScale
                        uiScale: root.uiScale
                        distroName: branding.name
                        primaryColor: root.primaryColor
                        backgroundColor: root.backgroundColor
                        surfaceColor: root.surfaceColor
                        textColor: root.textColor
                        textMutedColor: root.textMutedColor
                        errorColor: root.errorColor

                        FilesystemCombos {}
                        SubvolumesControl {}
                    }

                    ModeChoice {
                        visible: manualAllowed
                        mode: "manual"
                        radioName: "partitionModeManualRadio"
                        icon: "\u{1F527}"
                        title: qsTr("Manual partitioning")
                        description: qsTr("Advanced: create, resize and assign partitions yourself.")
                    }
                }

                Column {
                    objectName: "planMessages"
                    Layout.fillWidth: true
                    visible: partitionMode !== "manual" && (engine.planErrors.length > 0 || engine.planWarnings.length > 0)
                    spacing: 2

                    Repeater {
                        model: engine.planErrors
                        Text {
                            width: parent.width
                            text: "✗ " + modelData
                            color: errorColor
                            font.pixelSize: 12 * uiScale
                            wrapMode: Text.WordWrap
                        }
                    }
                    Repeater {
                        model: engine.planWarnings
                        Text {
                            width: parent.width
                            text: "⚠ " + modelData
                            color: warningColor
                            font.pixelSize: 12 * uiScale
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // Calamares' hLine: a plain separator between the choice
                // list and the encryption toggle below it.
                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    implicitHeight: 1
                    visible: encryptionAllowed && partitionMode !== "manual"
                    color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.25)
                }

                // LUKS for Erase and Alongside; Manual sets encryption per partition.
                Column {
                    Layout.fillWidth: true
                    spacing: 4 * uiScale
                    visible: encryptionAllowed && partitionMode !== "manual"

                    RowLayout {
                        width: parent.width
                        spacing: 10 * uiScale

                        ThemedCheck {
                            id: encryptionCheck
                            objectName: "encryptionCheck"
                            text: qsTr("Enable encryption (LUKS)")
                            font.bold: true
                            checked: encryptionEnabled
                            onToggled: encryptionToggled(checked)
                        }

                        Item {
                            Layout.fillWidth: true
                            visible: !encryptionEnabled
                        }

                        component PassphraseField: TextField {
                            id: passField
                            property bool valid: true
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.preferredHeight: 34 * uiScale
                            visible: encryptionEnabled
                            font.pixelSize: 13 * uiScale
                            echoMode: showEncPassCheck.checked ? TextInput.Normal : TextInput.Password
                            color: textColor
                            selectionColor: primaryColor
                            selectedTextColor: textColor
                            leftPadding: 12 * uiScale
                            background: Rectangle {
                                radius: 8
                                color: passField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                border.color: {
                                    if (!passField.activeFocus) return textMutedColor;
                                    if (passField.text.length === 0) return primaryColor;
                                    return passField.valid ? successColor : errorColor;
                                }
                                border.width: 2
                                Behavior on border.color { ColorAnimation { duration: 150 } }
                            }
                            onTextChanged: {
                                if (encryptionEnabled)
                                    encryptionPassphraseSet(encPassField.text)
                            }
                        }

                        PassphraseField {
                            id: encPassField
                            placeholderText: qsTr("Passphrase (min 8 characters)")
                            valid: text.length >= 8
                        }

                        PassphraseField {
                            id: encPassConfirmField
                            placeholderText: qsTr("Confirm passphrase")
                            valid: text === encPassField.text
                        }

                        ThemedCheck {
                            id: showEncPassCheck
                            visible: encryptionEnabled
                            text: qsTr("Show passphrase")
                            labelColor: textMutedColor
                            font.pixelSize: 12 * uiScale
                        }
                    }

                    RowLayout {
                        width: parent.width
                        spacing: 10 * uiScale
                        visible: encryptionEnabled

                        Text {
                            id: grubKdfNote
                            objectName: "grubKdfNote"
                            Layout.fillWidth: true
                            visible: encryptionEnabled && engine.bootloaderType === "grub"
                            text: qsTr("GRUB can't unlock Argon2id-protected volumes directly - using PBKDF2 for compatibility.")
                            font.pixelSize: 10 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                        }

                        Item {
                            Layout.fillWidth: true
                            visible: !grubKdfNote.visible
                        }

                        Text {
                            readonly property bool matches: encPassConfirmField.text === encPassField.text
                            visible: encPassConfirmField.text.length > 0
                            text: (matches ? "✓ " : "✗ ")
                                  + (matches ? qsTr("Passphrases match") : qsTr("Passphrases do not match"))
                            font.pixelSize: 10 * uiScale
                            color: matches ? successColor : errorColor
                        }
                    }
                }

                } // end configCardColumn
                } // end configCard

            } // end pageColumn
        }
    } // end pageScroll

    BtrfsSubvolumeDialog {
        id: subvolumeDialog
        defaultsJson: engine.defaultBtrfsSubvolumes
        uiScale: root.uiScale
        primaryColor: root.primaryColor
        backgroundColor: root.backgroundColor
        surfaceColor: root.surfaceColor
        textColor: root.textColor
        textMutedColor: root.textMutedColor
        errorColor: root.errorColor
        commit: function(subvolumesJson) { return engine.setBtrfsSubvolumes(subvolumesJson) }
    }

    // Calamares' PartitionPage: swapped in by the wizard's own Next/Back
    // (enterManualEditor()/exitManualEditor()) so it gets the full page.
    ManualPartitionPage {
        anchors.fill: parent
        anchors.margins: 24 * uiScale
        visible: manualEditorActive
        disksModel: root.disksModel
        uiScale: root.uiScale
        primaryColor: root.primaryColor
        backgroundColor: root.backgroundColor
        surfaceColor: root.surfaceColor
        textColor: root.textColor
        textMutedColor: root.textMutedColor
        successColor: root.successColor
        warningColor: root.warningColor
        errorColor: root.errorColor
        onDiskSelected: function(disk) { root.diskSelected(disk) }
    }
}
