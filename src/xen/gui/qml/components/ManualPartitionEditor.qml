/*
 * ManualPartitionEditor - manual partitioning, deliberately mirroring
 * Calamares' own PartitionPage/CreatePartitionDialog/
 * EditExistingPartitionDialog layout (see calamaresx-app's
 * src/modules/partition/gui/*.ui) rather than inventing a new one: users
 * already know that tool. Backed by xen.gui.bridge EngineBridge, which
 * wraps xen.jobs.partition's create/delete/resize/format/setflag operation
 * model.
 *
 * Two independent, deliberately separate actions per partition:
 *
 * - Physical layout (destructive): New / Edit / Delete queue operations via
 *   engine.addPartitionOperation(). Nothing touches the disk until "Apply
 *   Changes" runs them for real (engine.applyPartitionOperations()).
 * - Mount assignment (non-destructive): set from the Edit dialog's Mount
 *   Point field (OK button), calling engine.setPartitionMount() directly -
 *   takes effect immediately, independent of whether Apply was ever clicked.
 *
 * A data partition's filesystem choice is hardcoded to exactly ext4/xfs/btrfs
 * (btrfs hidden offline, matching the auto-mode restriction) - never more,
 * never less. fat32 (EFI) and linux-swap are separate structural entries in
 * the same File System dropdown, not extra "data" choices.
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "manualPartitionEditor"

    // Theme (mirrors PartitionView's palette - a standalone component has no
    // access to a sibling Item's properties, so these are passed in).
    property color primaryColor: "#5597e6"
    property color surfaceColor: "#32373c"
    property color backgroundColor: "#1a1a1a"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"
    property color colorEfi: "#5597e6"
    property color colorLinux: "#10B981"
    property color colorBtrfs: "#F59E0B"
    property color colorSwap: "#A78BFA"
    property color colorFree: Qt.rgba(0.196, 0.216, 0.235, 0.6)

    // Exactly these three for a DATA partition - see file header. Never read
    // dynamically from anywhere else, so the offered list can never grow.
    readonly property var dataFilesystems: hasInternet ? ["ext4", "xfs", "btrfs"] : ["ext4", "xfs"]
    property bool hasInternet: true
    // Full File System dropdown: data filesystems plus the two structural
    // roles (EFI, swap) Calamares also lists alongside real filesystems.
    readonly property var fsOptions: dataFilesystems.concat(["fat32", "linux-swap"])

    readonly property var segments: engine.simulatedSegments
    readonly property var pending: engine.pendingOperations
    readonly property bool hasPending: pending.length > 0

    // Selection by stable key, not array index: `segments` is rebuilt fresh
    // (new array, possibly reordered) every time an operation is queued, so
    // an index-based selection could silently point at the wrong row right
    // after the click that set it.
    function segKey(seg) {
        return seg.kind === "free" ? ("free:" + seg.startSector) : seg.name
    }
    property string selectedKey: ""
    readonly property var selectedSeg: {
        for (var i = 0; i < segments.length; i++)
            if (segKey(segments[i]) === selectedKey) return segments[i]
        return null
    }

    function humanSize(bytes) {
        if (!bytes || bytes <= 0) return "0 B"
        var units = ["B", "KB", "MB", "GB", "TB"]
        var i = 0
        var v = bytes
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
        return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + " " + units[i]
    }

    function segColor(seg) {
        if (seg.kind === "free") return colorFree
        var f = (seg.fstype || "").toLowerCase()
        var t = (seg.partType || "").toLowerCase()
        if (t === "efi" || f === "vfat" || f === "fat32") return colorEfi
        if (t === "swap" || f === "swap") return colorSwap
        if (f === "btrfs") return colorBtrfs
        return colorLinux
    }

    function devicePath(name) {
        return "/dev/" + name
    }

    // "fat32"/"linux-swap" are this dropdown's own structural spelling;
    // the backend/parted vocabulary is "vfat"/"swap".
    function backendFstype(choice) {
        if (choice === "fat32") return "vfat"
        if (choice === "linux-swap") return "swap"
        return choice
    }

    function queueOp(type, target, params) {
        engine.addPartitionOperation(JSON.stringify({ type: type, target: target, params: params }))
        selectedKey = ""
    }

    // engine.partitionMount() is a plain Slot (no NOTIFY signal of its own),
    // so a binding that only calls it would never refresh after the Edit
    // dialog saves a new value. setPartitionMount() emits
    // partitionOperationsChanged - the same signal `pending` is notified
    // by - so reading pending.length here (unused otherwise) makes this
    // function's callers re-evaluate on every mount-point change too.
    function displayMountpoint(seg) {
        var _dep = pending.length
        if (seg.kind === "existing") return engine.partitionMount(seg.name) || ""
        return seg.mountpoint || ""
    }

    function describeOp(op) {
        var p = op.params || {}
        switch (op.type) {
            case "create":
                return qsTr("Create") + " " + (p.fstype || "") + " • " + humanSize((p.size_sectors || 0) * 512)
            case "delete":
                return qsTr("Delete") + " " + op.target
            case "format":
                return qsTr("Format") + " " + op.target + " " + qsTr("as") + " " + p.fstype
            case "resize":
                return qsTr("Resize") + " " + op.target + " " + qsTr("to") + " " + humanSize((p.new_size_sectors || 0) * 512)
            case "setflag":
                return (p.state ? qsTr("Set") : qsTr("Clear")) + " " + p.flag + " " + qsTr("flag on") + " " + op.target
            default:
                return op.type
        }
    }

    component PartitionActionButton: Button {
        Layout.preferredHeight: 34
        contentItem: Text {
            text: parent.text
            color: parent.enabled ? textColor : textMutedColor
            font.pixelSize: 12
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 8
            color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
            border.color: parent.enabled ? primaryColor : textMutedColor
            border.width: 1
            opacity: parent.enabled ? 1 : 0.5
        }
    }

    component StyledField: TextField {
        Layout.fillWidth: true
        font.pixelSize: 12
        color: textColor
        background: Rectangle {
            radius: 6
            color: backgroundColor
            border.color: textMutedColor
            border.width: 1
        }
        leftPadding: 8
        topPadding: 9
        bottomPadding: 9
        verticalAlignment: Text.AlignVCenter
    }

    component StyledCombo: ComboBox {
        id: styledCombo
        Layout.fillWidth: true
        // Without this, QtQuick Layouts treats the combo's own full,
        // unclipped content width (e.g. a long "diskname (size)" entry) as
        // non-negotiable - once that plus its RowLayout siblings exceeds
        // the popup width, the whole row overflows past the edge instead
        // of the combo shrinking. Same overflow class the toolbar hit
        // earlier (fixed with Flow there); here the control itself needs
        // to admit it CAN shrink.
        Layout.minimumWidth: 0
        font.pixelSize: 12
        background: Rectangle {
            radius: 6
            color: backgroundColor
            border.color: textMutedColor
            border.width: 1
        }
        contentItem: Text {
            text: styledCombo.displayText
            color: textColor
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            leftPadding: 8
            elide: Text.ElideRight
        }
        // Default Fusion popup renders light-on-light against this app's
        // dark theme; restyle to match (standard QQC2 customization idiom -
        // self-referencing styledCombo.popup/delegateModel is intentional).
        popup: Popup {
            y: styledCombo.height
            width: styledCombo.width
            implicitHeight: Math.min(contentItem.implicitHeight, 200)
            padding: 1
            background: Rectangle {
                radius: 6
                color: surfaceColor
                border.color: primaryColor
                border.width: 1
            }
            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: styledCombo.popup.visible ? styledCombo.delegateModel : null
                currentIndex: styledCombo.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator {}
            }
        }
        delegate: ItemDelegate {
            width: styledCombo.width
            highlighted: styledCombo.highlightedIndex === index
            contentItem: Text {
                text: modelData
                color: textColor
                font.pixelSize: 12
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: highlighted ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.25) : "transparent"
            }
        }
    }

    // Checkable GPT flags (esp/boot/swap), mirroring CreatePartitionDialog's
    // / EditExistingPartitionDialog's m_listFlags. We can't read a real
    // partition's CURRENT flags (disk_detector doesn't expose them), so
    // every checkbox always starts unchecked: ticking one queues "set it",
    // leaving one unticked queues nothing (never an implicit "clear").
    // Mirrors Calamares' EditExistingPartitionDialog flags list (a bounded,
    // scrollable checkable list box, not a column of loose checkboxes).
    // Flag set matches every flag this installer's parted backend actually
    // accepts (see _PARTED_FLAGS in jobs/partition.py); "bios_grub" is left
    // out because validate_operations always rejects it (UEFI/systemd-boot
    // only), and flags like "root" that only exist in other partitioning
    // libraries (not parted) are left out since setting them would be a
    // silent no-op here.
    component FlagsChecklist: ColumnLayout {
        id: flagsChecklist
        readonly property var allFlags: ["esp", "boot", "swap", "raid", "lvm", "legacy_boot", "hidden", "msftres", "diag"]
        property var checked: ({})
        spacing: 4

        function toggle(flag, state) {
            var copy = Object.assign({}, checked)
            copy[flag] = state
            checked = copy
        }
        function checkedFlagNames() {
            return Object.keys(checked).filter(function(f) { return checked[f] })
        }

        Text { text: qsTr("Flags"); color: textColor; font.pixelSize: 12; font.bold: true }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: flagsGrid.implicitHeight + 16
            radius: 6
            color: Qt.darker(surfaceColor, 1.15)
            border.color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.3)
            border.width: 1

            GridLayout {
                id: flagsGrid
                anchors.fill: parent
                anchors.margins: 8
                columns: 3
                rowSpacing: 6
                columnSpacing: 12

                Repeater {
                    model: flagsChecklist.allFlags

                    RowLayout {
                        id: flagRow
                        readonly property bool isChecked: !!flagsChecklist.checked[modelData]
                        Layout.fillWidth: true
                        spacing: 6

                        Rectangle {
                            width: 16
                            height: 16
                            radius: 4
                            color: flagRow.isChecked ? primaryColor : "transparent"
                            border.color: flagRow.isChecked ? primaryColor : Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.6)
                            border.width: 1

                            Text {
                                anchors.centerIn: parent
                                visible: flagRow.isChecked
                                text: "✓"
                                color: "#ffffff"
                                font.pixelSize: 11
                                font.bold: true
                            }
                        }
                        Text {
                            text: modelData
                            color: textColor
                            font.pixelSize: 12
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }

                        TapHandler {
                            onTapped: flagsChecklist.toggle(modelData, !flagRow.isChecked)
                        }
                    }
                }
            }
        }
    }

    // User-defined Btrfs subvolume layout ("the way they see fit" - free-form
    // name/mountpoint rows), pre-seeded with this installer's own '@'-prefixed
    // convention (see PartitionJob._BTRFS_SUBVOLUMES) as an editable default.
    // Schema is enforced server-side by validate_btrfs_subvolumes(); this is
    // just the editor, no client-side rule beyond letting the user edit freely.
    component BtrfsSubvolumeEditor: ColumnLayout {
        id: subvolEditor
        property var subvolumes: []
        spacing: 4

        function reset() {
            subvolumes = [
                { name: "@", mountpoint: "/" },
                { name: "@home", mountpoint: "/home" },
                { name: "@root", mountpoint: "/root" },
                { name: "@srv", mountpoint: "/srv" },
                { name: "@cache", mountpoint: "/var/cache" },
                { name: "@tmp", mountpoint: "/var/tmp" },
                { name: "@log", mountpoint: "/var/log" },
                { name: "@swap", mountpoint: "/swap" }
            ]
        }

        function addRow() {
            var copy = subvolumes.slice()
            copy.push({ name: "@", mountpoint: "" })
            subvolumes = copy
        }

        function removeRow(idx) {
            var copy = subvolumes.slice()
            copy.splice(idx, 1)
            subvolumes = copy
        }

        function updateRow(idx, key, value) {
            var copy = subvolumes.slice()
            var row = Object.assign({}, copy[idx])
            row[key] = value
            copy[idx] = row
            subvolumes = copy
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: qsTr("Btrfs subvolumes")
                color: textColor
                font.pixelSize: 12
                font.bold: true
            }
            PartitionActionButton {
                text: qsTr("Defaults")
                Layout.preferredWidth: 90
                onClicked: subvolEditor.reset()
            }
            PartitionActionButton {
                text: qsTr("+ Add")
                Layout.preferredWidth: 70
                onClicked: subvolEditor.addRow()
            }
        }

        // Bounded + scrollable so a full default layout (8 rows) doesn't
        // balloon the whole Edit/Create dialog's height - the row count is
        // user-editable (+ Add / Defaults), so an unbounded list would make
        // the popup grow without limit.
        ListView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(subvolEditor.subvolumes.length * 44, 230)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            spacing: 6
            model: subvolEditor.subvolumes
            delegate: RowLayout {
                width: ListView.view.width
                spacing: 6
                TextField {
                    Layout.preferredWidth: 100
                    topPadding: 9
                    bottomPadding: 9
                    text: modelData.name
                    font.pixelSize: 12
                    color: textColor
                    background: Rectangle { radius: 4; color: backgroundColor; border.color: textMutedColor; border.width: 1 }
                    onEditingFinished: subvolEditor.updateRow(index, "name", text)
                }
                TextField {
                    Layout.fillWidth: true
                    topPadding: 9
                    bottomPadding: 9
                    text: modelData.mountpoint
                    placeholderText: qsTr("/ for root")
                    font.pixelSize: 12
                    color: textColor
                    background: Rectangle { radius: 4; color: backgroundColor; border.color: textMutedColor; border.width: 1 }
                    onEditingFinished: subvolEditor.updateRow(index, "mountpoint", text)
                }
                PartitionActionButton {
                    text: "✕"
                    Layout.preferredWidth: 26
                    onClicked: subvolEditor.removeRow(index)
                }
            }
        }
    }

    // Sized to actual content, not stretched to fill a forced parent height -
    // a fixed forced height left a large dead gap when there was little to
    // show. The popup's ScrollView only kicks in once real content exceeds
    // its viewport.
    implicitHeight: contentColumn.implicitHeight

    ColumnLayout {
        id: contentColumn
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: 10

        // ===== Storage device + Revert (PartitionPage.ui's top row) =====
        RowLayout {
            Layout.fillWidth: true
            Layout.rightMargin: 48
            spacing: 10

            Text {
                text: qsTr("Storage device:")
                color: textColor
                font.pixelSize: 13
                font.bold: true
            }
            StyledCombo {
                id: deviceCombo
                objectName: "deviceCombo"
                // Moderate cap, not full fillWidth: leaves Revert All
                // Changes some breathing room instead of pinned flush
                // against the row's far edge.
                Layout.fillWidth: false
                Layout.preferredWidth: 380
                model: engine.disksModel.map(function(d) { return d.name + "  (" + d.size + ")" })
                currentIndex: {
                    var sel = engine.selectedDisk || ""
                    var short = sel.indexOf("/dev/") === 0 ? sel.slice(5) : sel
                    var names = engine.disksModel.map(function(d) { return d.name })
                    var i = names.indexOf(short)
                    return i >= 0 ? i : 0
                }
                onActivated: {
                    var disks = engine.disksModel
                    if (currentIndex >= 0 && currentIndex < disks.length)
                        engine.setSelectedDisk(disks[currentIndex].name)
                }
            }
            PartitionActionButton {
                text: qsTr("Revert All Changes")
                enabled: hasPending && !engine.partitionApplying
                onClicked: engine.resetPartitionOperations()
            }
        }

        // ===== Histobar (PartitionBarsView equivalent) =====
        Item {
            id: bar
            // Measured: fillWidth + rightMargin only left a gap on the
            // right (0px left, 48px right) - not centered, just lopsided.
            // Centered properly now: fixed symmetric margin each side,
            // still responsive to the popup's actual width.
            Layout.fillWidth: false
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.max(200, contentColumn.width - 96)
            Layout.preferredHeight: 40

            readonly property real totalBytes: {
                var t = 0
                for (var i = 0; i < segments.length; i++) t += (segments[i].sizeBytes || 0)
                return t
            }

            // A leading/trailing zero-size segment (e.g. a table starting
            // exactly at sector 0) renders at zero width - rounding index 0
            // then rounds an invisible sliver, leaving the first VISIBLE
            // segment square. Round the first/last segment that actually
            // has width instead of the first/last array index.
            readonly property int firstVisibleIndex: {
                for (var i = 0; i < segments.length; i++) {
                    if ((segments[i].sizeBytes || 0) > 0) return i
                }
                return 0
            }
            readonly property int lastVisibleIndex: {
                for (var i = segments.length - 1; i >= 0; i--) {
                    if ((segments[i].sizeBytes || 0) > 0) return i
                }
                return segments.length - 1
            }

            Rectangle {
                anchors.fill: parent
                radius: 10
                color: colorFree
                clip: true

                Row {
                    anchors.fill: parent
                    spacing: 0

                    Repeater {
                        model: segments

                        Rectangle {
                            height: parent.height
                            width: bar.totalBytes > 0 ? parent.width * (modelData.sizeBytes || 0) / bar.totalBytes : 0
                            color: segColor(modelData)
                            opacity: modelData.pendingDelete ? 0.3 : 1
                            border.color: segKey(modelData) === selectedKey ? textColor : Qt.rgba(0, 0, 0, 0.25)
                            border.width: segKey(modelData) === selectedKey ? 2 : 1
                            // The software Qt Quick backend (forced app-wide
                            // for live-ISO compatibility, see main.py) only
                            // clips to a plain rectangle, never to a rounded
                            // one - the container's radius+clip never
                            // actually rounded these segments. Each end
                            // segment rounds its own outer corners directly
                            // instead, which the software renderer DOES draw
                            // correctly (no clip/effect involved).
                            topLeftRadius: index === bar.firstVisibleIndex ? 10 : 0
                            bottomLeftRadius: index === bar.firstVisibleIndex ? 10 : 0
                            topRightRadius: index === bar.lastVisibleIndex ? 10 : 0
                            bottomRightRadius: index === bar.lastVisibleIndex ? 10 : 0

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: selectedKey = segKey(modelData)
                            }

                            ToolTip.visible: hov.hovered && width > 4
                            ToolTip.text: (modelData.kind === "free" ? qsTr("Free space") : (modelData.name || qsTr("New partition")))
                                + "  " + humanSize(modelData.sizeBytes)
                                + (modelData.fstype ? "  (" + modelData.fstype + ")" : "")
                                + (modelData.pendingDelete ? "  " + qsTr("(pending delete)") : "")
                            HoverHandler { id: hov }
                        }
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                radius: 10
                color: "transparent"
                border.color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.25)
                border.width: 1
            }
        }

        // ===== Partition table (PartitionLabelsView + partitionTreeView) =====
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 4
            Text { Layout.preferredWidth: 130; text: qsTr("Device"); color: textMutedColor; font.pixelSize: 11; font.bold: true }
            Text { Layout.preferredWidth: 80; text: qsTr("Type"); color: textMutedColor; font.pixelSize: 11; font.bold: true }
            Text { Layout.preferredWidth: 80; text: qsTr("Size"); color: textMutedColor; font.pixelSize: 11; font.bold: true }
            Text { Layout.fillWidth: true; text: qsTr("Mount Point"); color: textMutedColor; font.pixelSize: 11; font.bold: true }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Qt.darker(surfaceColor, 1.3) }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Repeater {
                model: segments

                Rectangle {
                    id: rowBg
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 32 : 0
                    visible: modelData.kind !== "free"
                    opacity: modelData.pendingDelete ? 0.4 : 1
                    radius: 6
                    color: segKey(modelData) === selectedKey
                        ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.18)
                        : "transparent"
                    border.color: segKey(modelData) === selectedKey ? primaryColor : "transparent"
                    border.width: 1

                    // The whole row selects the partition - not just the
                    // (often tiny) matching histobar sliver.
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: selectedKey = segKey(modelData)
                        onDoubleClicked: {
                            if (modelData.kind === "existing" && !modelData.pendingDelete) {
                                selectedKey = segKey(modelData)
                                editDialog.openFor(modelData)
                            }
                        }
                    }

                    RowLayout {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 6
                        spacing: 10

                        Rectangle {
                            width: 10
                            height: 10
                            radius: 3
                            color: segColor(modelData)
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Text {
                            Layout.preferredWidth: 116
                            text: modelData.name || qsTr("(new)")
                            color: textColor
                            font.pixelSize: 12
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.preferredWidth: 80
                            text: modelData.pendingDelete ? qsTr("deleting…") : (modelData.fstype || "—")
                            color: modelData.pendingDelete ? errorColor : textMutedColor
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.preferredWidth: 80
                            text: humanSize(modelData.sizeBytes)
                            color: textMutedColor
                            font.pixelSize: 12
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.kind === "new"
                                ? qsTr("(set after Apply)")
                                : (displayMountpoint(modelData) || "—")
                            color: textMutedColor
                            font.pixelSize: 12
                            font.italic: modelData.kind === "new"
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: segments.filter(function(s) { return s.kind !== "free" }).length === 0
                text: qsTr("No partitions on this disk yet - select the free space above and click New.")
                color: textMutedColor
                font.pixelSize: 12
                font.italic: true
            }
        }

        // ===== Toolbar (New Partition Table / New / Edit / Delete, plus our
        // own Apply queue controls Calamares doesn't need - it applies the
        // whole install plan at the very end instead) =====
        // Wrapped in a fillWidth Item with the Flow anchored to its
        // horizontal center directly, rather than relying on
        // Layout.alignment inside the ColumnLayout - a direct anchor is
        // unambiguous regardless of how the surrounding layout resolves.
        Item {
            Layout.fillWidth: true
            Layout.topMargin: 6
            Layout.preferredHeight: toolbarFlow.implicitHeight

            Flow {
                id: toolbarFlow
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width, 700)
                spacing: 8

            PartitionActionButton {
                text: qsTr("New Partition Table")
                enabled: (engine.selectedDisk || "") !== ""
                onClicked: newTableDialog.open()
            }
            PartitionActionButton {
                text: qsTr("New")
                enabled: selectedSeg !== null && selectedSeg.kind === "free"
                onClicked: newDialog.openFor(selectedSeg)
            }
            PartitionActionButton {
                text: qsTr("Edit")
                enabled: selectedSeg !== null && selectedSeg.kind === "existing" && !selectedSeg.pendingDelete
                onClicked: editDialog.openFor(selectedSeg)
            }
            PartitionActionButton {
                // Toggles: queues a delete on a live partition, or un-queues
                // it if it's already pending delete.
                text: (selectedSeg && selectedSeg.pendingDelete) ? qsTr("Undo Delete") : qsTr("Delete")
                enabled: selectedSeg !== null && selectedSeg.kind === "existing"
                onClicked: {
                    if (selectedSeg.pendingDelete) {
                        var idx = pending.findIndex(function(op) {
                            return op.type === "delete" && op.target === devicePath(selectedSeg.name)
                        })
                        if (idx !== -1) engine.removePartitionOperation(idx)
                    } else {
                        engine.addPartitionOperation(JSON.stringify({
                            type: "delete",
                            target: devicePath(selectedSeg.name),
                            params: { number: selectedSeg.number }
                        }))
                    }
                }
            }
            PartitionActionButton {
                text: engine.partitionApplying ? qsTr("Applying…") : qsTr("Apply Changes")
                enabled: hasPending && engine.operationsApplicable && !engine.partitionApplying
                onClicked: engine.applyPartitionOperations()
            }
            }
        }

        // Apply-gating banner (structural: alignment/overlap/busy device).
        Text {
            Layout.fillWidth: true
            visible: hasPending && !engine.operationsApplicable
            text: "⚠️ " + engine.operationsApplicableError
            color: warningColor
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }

        // ===== Pending changes =====
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            visible: hasPending

            Text {
                text: qsTr("Pending changes") + " (" + pending.length + ")"
                font.pixelSize: 13
                font.bold: true
                color: textColor
            }

            // Per-row undo removed - "Revert All Changes" above already
            // covers clearing the pending plan, and two separate ways to
            // undo the same thing is redundant.
            Repeater {
                model: pending
                Text {
                    text: "• " + describeOp(modelData)
                    color: textMutedColor
                    font.pixelSize: 12
                }
            }
        }
    }

    // ===== New Partition Table confirmation (CreatePartitionTableDialog) ====
    Popup {
        id: newTableDialog
        anchors.centerIn: Overlay.overlay
        width: 380
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle { radius: 16; color: surfaceColor; border.color: errorColor; border.width: 1 }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "⚠️ " + qsTr("Create a new partition table?")
                font.pixelSize: 15
                font.bold: true
                color: errorColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Text {
                text: qsTr("This queues deletion of every existing partition on this disk. Nothing is erased until you click Apply Changes.")
                font.pixelSize: 12
                color: textMutedColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8
                PartitionActionButton { text: qsTr("Cancel"); onClicked: newTableDialog.close() }
                PartitionActionButton {
                    text: qsTr("OK")
                    onClicked: {
                        segments.filter(function(s) { return s.kind === "existing" && !s.pendingDelete }).forEach(function(seg) {
                            queueOp("delete", devicePath(seg.name), { number: seg.number })
                        })
                        newTableDialog.close()
                    }
                }
            }
        }
    }

    // ===== Per-partition subvolume mount points (Btrfs, post-Apply) =====
    Popup {
        id: subvolMountDialog
        anchors.centerIn: Overlay.overlay
        width: 440
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        property string partName: ""

        function openFor(name) {
            partName = name
            var raw = engine.partitionSubvolumes(name)
            var parsed = []
            try { parsed = JSON.parse(raw) } catch (e) { parsed = [] }
            rowSubvolEditor.subvolumes = parsed
            if (parsed.length === 0)
                rowSubvolEditor.reset()
            open()
        }

        background: Rectangle { radius: 16; color: surfaceColor; border.color: Qt.darker(surfaceColor, 1.3); border.width: 1 }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: qsTr("Subvolume mount points") + " — " + subvolMountDialog.partName
                font.pixelSize: 16
                font.bold: true
                color: textColor
            }
            Text {
                text: qsTr("Names must match the subvolumes already created on this partition.")
                font.pixelSize: 11
                color: textMutedColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            BtrfsSubvolumeEditor { id: rowSubvolEditor; Layout.fillWidth: true }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8
                PartitionActionButton { text: qsTr("Cancel"); onClicked: subvolMountDialog.close() }
                PartitionActionButton {
                    text: qsTr("Save")
                    onClicked: {
                        engine.setPartitionSubvolumes(subvolMountDialog.partName, JSON.stringify(rowSubvolEditor.subvolumes))
                        subvolMountDialog.close()
                    }
                }
            }
        }
    }

    // ===== New Partition dialog (CreatePartitionDialog) =====
    Popup {
        id: newDialog
        objectName: "newPartitionDialog"
        anchors.centerIn: Overlay.overlay
        width: 480
        height: Math.min(newDialogColumn.implicitHeight + 40, (Overlay.overlay ? Overlay.overlay.height - 40 : 800))
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        property var freeSeg: null
        property int sizeMib: 0
        property string fsChoice: fsOptions.length > 0 ? fsOptions[0] : "ext4"
        property string labelText: ""

        function openFor(seg) {
            freeSeg = seg
            sizeMib = Math.max(1, Math.floor(seg.sizeBytes / (1024 * 1024)))
            fsChoice = fsOptions.length > 0 ? fsOptions[0] : "ext4"
            labelText = ""
            newMountField.text = ""
            newFlagsChecklist.checked = {}
            newSubvolEditor.reset()
            open()
        }

        background: Rectangle { radius: 16; color: surfaceColor; border.color: Qt.darker(surfaceColor, 1.3); border.width: 1 }

        ColumnLayout {
            id: newDialogColumn
            anchors.fill: parent
            anchors.margins: 20
            spacing: 10

            Text { text: qsTr("Create a Partition"); font.pixelSize: 16; font.bold: true; color: textColor }
            Text {
                text: qsTr("Free space available: ") + (newDialog.freeSeg ? humanSize(newDialog.freeSeg.sizeBytes) : "")
                font.pixelSize: 12
                color: textMutedColor
            }

            RowLayout {
                spacing: 10
                Text { Layout.preferredWidth: 100; text: qsTr("Size (MiB)"); color: textColor; font.pixelSize: 12 }
                SpinBox {
                    id: newSizeSpin
                    objectName: "newSizeSpin"
                    Layout.fillWidth: true
                    from: 1
                    to: newDialog.freeSeg ? Math.floor(newDialog.freeSeg.sizeBytes / (1024 * 1024)) : 1
                    value: newDialog.sizeMib
                    onValueChanged: newDialog.sizeMib = value
                    editable: true
                }
            }

            RowLayout {
                spacing: 10
                Text { Layout.preferredWidth: 100; text: qsTr("File System"); color: textColor; font.pixelSize: 12 }
                StyledCombo {
                    id: newFsCombo
                    objectName: "newFsCombo"
                    model: fsOptions
                    currentIndex: Math.max(0, fsOptions.indexOf(newDialog.fsChoice))
                    onActivated: newDialog.fsChoice = fsOptions[currentIndex]
                }
            }

            RowLayout {
                spacing: 10
                Text { Layout.preferredWidth: 100; text: qsTr("FS Label"); color: textColor; font.pixelSize: 12 }
                StyledField {
                    id: newLabelField
                    objectName: "newLabelField"
                    maximumLength: 16
                    placeholderText: qsTr("optional")
                    text: newDialog.labelText
                    onTextChanged: newDialog.labelText = text
                }
            }

            RowLayout {
                objectName: "newEncryptRow"
                spacing: 8
                visible: newDialog.fsChoice !== "fat32"
                CheckBox {
                    id: newEncryptCheck
                    objectName: "newEncryptCheck"
                    text: qsTr("Encrypt this partition (LUKS2)")
                    contentItem: Text {
                        text: parent.text
                        color: textColor
                        font.pixelSize: 12
                        leftPadding: parent.indicator.width + parent.spacing
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: newEncryptCheck.checked && newMountField.text.trim() === "/"
                spacing: 6
                StyledField {
                    id: newEncryptPassField
                    objectName: "newEncryptPassField"
                    echoMode: TextInput.Password
                    placeholderText: qsTr("Encryption passphrase (min 8 characters)")
                }
                StyledField {
                    id: newEncryptPassConfirmField
                    objectName: "newEncryptPassConfirmField"
                    echoMode: TextInput.Password
                    placeholderText: qsTr("Confirm passphrase")
                }
            }
            Text {
                Layout.fillWidth: true
                visible: newEncryptCheck.checked && newMountField.text.trim() !== "/"
                text: newMountField.text.trim() === "swap"
                    ? qsTr("Encrypted with a fresh random key every boot - nothing to remember.")
                    : qsTr("A random key is generated automatically and unlocked at boot - nothing to remember.")
                font.pixelSize: 10
                color: textMutedColor
                wrapMode: Text.WordWrap
            }

            RowLayout {
                spacing: 10
                Text { Layout.preferredWidth: 100; text: qsTr("Mount Point"); color: textColor; font.pixelSize: 12 }
                StyledField {
                    id: newMountField
                    objectName: "newMountField"
                    placeholderText: qsTr("optional, e.g. / or /home")
                }
            }
            Text {
                text: qsTr("Preview only - this partition doesn't exist yet. Re-confirm its mount point from the table once Apply Changes has created it.")
                font.pixelSize: 10
                font.italic: true
                color: textMutedColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            FlagsChecklist { id: newFlagsChecklist; Layout.fillWidth: true }

            BtrfsSubvolumeEditor {
                id: newSubvolEditor
                Layout.fillWidth: true
                visible: newDialog.fsChoice === "btrfs"
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8
                PartitionActionButton { text: qsTr("Cancel"); onClicked: newDialog.close() }
                PartitionActionButton {
                    text: qsTr("Create")
                    onClicked: {
                        var seg = newDialog.freeSeg
                        var fstype = backendFstype(newDialog.fsChoice)
                        var params = {
                            start_sector: seg.startSector,
                            size_sectors: newDialog.sizeMib * 2048,
                            fstype: fstype,
                            name: "NEW-" + Date.now(),
                            flags: newFlagsChecklist.checkedFlagNames()
                        }
                        if (newDialog.labelText.trim() !== "") params.label = newDialog.labelText.trim()
                        if (newMountField.text.trim() !== "") params.mountpoint = newMountField.text.trim()
                        if (fstype === "btrfs" && newSubvolEditor.subvolumes.length > 0)
                            params.subvolumes = newSubvolEditor.subvolumes
                        if (newEncryptCheck.checked) {
                            params.encrypt = true
                            if (newMountField.text.trim() === "/") {
                                params.encryption_passphrase = newEncryptPassField.text
                            }
                        }
                        queueOp("create", "free:" + seg.startSector, params)
                        newDialog.close()
                    }
                }
            }
        }
    }

    // ===== Edit Existing Partition dialog (EditExistingPartitionDialog) -
    // consolidates what used to be three separate dialogs (Resize/Format/
    // Flags) into the one Calamares uses, including the Mount Point field
    // (committed on OK, not live-as-you-type - sidesteps the editable-
    // ComboBox-doesn't-commit-on-blur trap the old per-row field had). =====
    Popup {
        id: editDialog
        objectName: "editPartitionDialog"
        anchors.centerIn: Overlay.overlay
        width: 480
        // Popup doesn't grow to fit a child that uses anchors.fill: parent -
        // that anchor makes the child's rendered size follow the popup
        // instead of the other way round, so the popup's own auto-height
        // never accounts for it and content (the OK/Cancel row, here) spills
        // out past the background. implicitHeight is unaffected by that
        // anchor binding, so drive the popup's height from it explicitly.
        height: Math.min(editDialogColumn.implicitHeight + 40, (Overlay.overlay ? Overlay.overlay.height - 40 : 800))
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        property var seg: null
        property int sizeMib: 0
        property bool growOnly: false
        property bool doFormat: false
        property string fsChoice: fsOptions.length > 0 ? fsOptions[0] : "ext4"
        property string labelText: ""

        function openFor(s) {
            seg = s
            growOnly = (s.fstype || "").toLowerCase() === "xfs"
            sizeMib = Math.floor(s.sizeSectors / 2048)
            doFormat = false
            var current = (s.fstype || "").toLowerCase()
            fsChoice = fsOptions.indexOf(current) !== -1 ? current : (fsOptions.length > 0 ? fsOptions[0] : "ext4")
            labelText = ""
            editMountField.text = engine.partitionMount(s.name)
            editFlagsChecklist.checked = {}
            editSubvolEditor.reset()
            open()
        }

        // Named (not inlined in the OK button's onClicked) so it's directly
        // invokable - from a test via QMetaObject.invokeMethod, same as any
        // other QML function - without having to simulate a real click.
        function confirm() {
            var seg = editDialog.seg
            var path = devicePath(seg.name)

            engine.setPartitionMount(seg.name, editMountField.text.trim())

            editFlagsChecklist.checkedFlagNames().forEach(function(flag) {
                queueOp("setflag", path, { number: seg.number, flag: flag, state: true })
            })

            var newSectors = editDialog.sizeMib * 2048
            if (newSectors !== seg.sizeSectors) {
                queueOp("resize", path, {
                    path: path,
                    number: seg.number,
                    new_size_sectors: newSectors,
                    old_size_sectors: seg.sizeSectors,
                    start_sector: seg.startSector,
                    fstype: seg.fstype,
                    mountpoint: seg.mountpoint,
                    shrink: newSectors < seg.sizeSectors
                })
            }

            if (editDialog.doFormat) {
                var fstype = backendFstype(editDialog.fsChoice)
                var params = { path: path, fstype: fstype }
                if (editDialog.labelText.trim() !== "") params.label = editDialog.labelText.trim()
                if (fstype === "btrfs" && editSubvolEditor.subvolumes.length > 0)
                    params.subvolumes = editSubvolEditor.subvolumes
                if (editEncryptCheck.checked) {
                    params.encrypt = true
                    if (editMountField.text.trim() === "/") {
                        params.encryption_passphrase = editEncryptPassField.text
                    }
                }
                queueOp("format", path, params)
            }

            editDialog.close()
        }

        background: Rectangle { radius: 16; color: surfaceColor; border.color: Qt.darker(surfaceColor, 1.3); border.width: 1 }

        ColumnLayout {
            id: editDialogColumn
            anchors.fill: parent
            anchors.margins: 20
            spacing: 10

            Text {
                text: qsTr("Edit Partition") + " — " + (editDialog.seg ? editDialog.seg.name : "")
                font.pixelSize: 16
                font.bold: true
                color: textColor
            }

            RowLayout {
                spacing: 10
                visible: editDialog.seg ? (editDialog.seg.fstype || "").toLowerCase() !== "ntfs" : true
                Text { Layout.preferredWidth: 100; text: qsTr("Size (MiB)"); color: textColor; font.pixelSize: 12 }
                SpinBox {
                    id: editSizeSpin
                    objectName: "editSizeSpin"
                    Layout.fillWidth: true
                    from: editDialog.growOnly && editDialog.seg
                        ? Math.floor(editDialog.seg.sizeSectors / 2048)
                        : (editDialog.seg ? Math.max(1, Math.floor(editDialog.seg.minSizeSectors / 2048)) : 1)
                    to: editDialog.seg
                        ? Math.floor((editDialog.seg.sizeSectors + editDialog.seg.freeAfterSectors) / 2048)
                        : 1
                    value: editDialog.sizeMib
                    onValueChanged: editDialog.sizeMib = value
                    editable: true
                }
            }
            Text {
                visible: editDialog.growOnly
                text: qsTr("XFS can only grow, not shrink.")
                font.pixelSize: 11
                color: warningColor
            }

            RowLayout {
                spacing: 16
                Text { text: qsTr("Content:"); color: textColor; font.pixelSize: 12 }
                RadioButton {
                    text: qsTr("Keep")
                    checked: !editDialog.doFormat
                    onClicked: editDialog.doFormat = false
                    contentItem: Text { text: parent.text; color: textColor; leftPadding: parent.indicator.width + parent.spacing; verticalAlignment: Text.AlignVCenter }
                }
                RadioButton {
                    text: qsTr("Format")
                    checked: editDialog.doFormat
                    onClicked: editDialog.doFormat = true
                    contentItem: Text { text: parent.text; color: textColor; leftPadding: parent.indicator.width + parent.spacing; verticalAlignment: Text.AlignVCenter }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: editDialog.doFormat
                spacing: 8

                Text {
                    text: qsTr("Warning: formatting this partition will erase all existing data.")
                    color: errorColor
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                RowLayout {
                    spacing: 10
                    Text { Layout.preferredWidth: 100; text: qsTr("File System"); color: textColor; font.pixelSize: 12 }
                    StyledCombo {
                        id: editFsCombo
                        objectName: "editFsCombo"
                        model: fsOptions
                        currentIndex: Math.max(0, fsOptions.indexOf(editDialog.fsChoice))
                        onActivated: editDialog.fsChoice = fsOptions[currentIndex]
                    }
                }
                RowLayout {
                    spacing: 10
                    Text { Layout.preferredWidth: 100; text: qsTr("FS Label"); color: textColor; font.pixelSize: 12 }
                    StyledField {
                        id: editLabelField
                        objectName: "editLabelField"
                        maximumLength: 16
                        placeholderText: qsTr("optional")
                        text: editDialog.labelText
                        onTextChanged: editDialog.labelText = text
                    }
                }
                RowLayout {
                    objectName: "editEncryptRow"
                    spacing: 8
                    visible: editDialog.fsChoice !== "fat32"
                    CheckBox {
                        id: editEncryptCheck
                        objectName: "editEncryptCheck"
                        text: qsTr("Encrypt this partition (LUKS2)")
                        contentItem: Text {
                            text: parent.text
                            color: textColor
                            font.pixelSize: 12
                            leftPadding: parent.indicator.width + parent.spacing
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: editEncryptCheck.checked && editMountField.text.trim() === "/"
                    spacing: 6
                    StyledField {
                        id: editEncryptPassField
                        objectName: "editEncryptPassField"
                        echoMode: TextInput.Password
                        placeholderText: qsTr("Encryption passphrase (min 8 characters)")
                    }
                    StyledField {
                        id: editEncryptPassConfirmField
                        objectName: "editEncryptPassConfirmField"
                        echoMode: TextInput.Password
                        placeholderText: qsTr("Confirm passphrase")
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: editEncryptCheck.checked && editMountField.text.trim() !== "/"
                    text: editMountField.text.trim() === "swap"
                        ? qsTr("Encrypted with a fresh random key every boot - nothing to remember.")
                        : qsTr("A random key is generated automatically and unlocked at boot - nothing to remember.")
                    font.pixelSize: 10
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                }
                BtrfsSubvolumeEditor {
                    id: editSubvolEditor
                    Layout.fillWidth: true
                    visible: editDialog.fsChoice === "btrfs"
                }
            }

            RowLayout {
                spacing: 10
                Text { Layout.preferredWidth: 100; text: qsTr("Mount Point"); color: textColor; font.pixelSize: 12 }
                StyledField {
                    id: editMountField
                    objectName: "editMountField"
                    placeholderText: qsTr("e.g. /, /home, swap")
                }
            }

            FlagsChecklist { id: editFlagsChecklist; Layout.fillWidth: true }

            PartitionActionButton {
                visible: !editDialog.doFormat && editDialog.seg && (editDialog.seg.fstype || "").toLowerCase() === "btrfs"
                text: {
                    if (!editDialog.seg) return ""
                    var raw = engine.partitionSubvolumes(editDialog.seg.name)
                    var n = 0
                    try { n = JSON.parse(raw).length } catch (e) { n = 0 }
                    return n > 0 ? qsTr("Subvolume mount points (%1)").arg(n) : qsTr("Set subvolume mount points")
                }
                onClicked: subvolMountDialog.openFor(editDialog.seg.name)
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8
                PartitionActionButton { text: qsTr("Cancel"); onClicked: editDialog.close() }
                PartitionActionButton {
                    objectName: "editOkButton"
                    text: qsTr("OK")
                    onClicked: editDialog.confirm()
                }
            }
        }
    }
}
