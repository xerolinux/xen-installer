import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Units.js" as Units

Rectangle {
    id: page
    objectName: "manualPartitionPage"

    signal diskSelected(string disk)

    property var disksModel: []
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"

    readonly property string diskName: engine.selectedDisk
    readonly property int diskIndex: {
        for (var i = 0; i < disksModel.length; i++)
            if (disksModel[i].name === diskName) return i
        return -1
    }
    readonly property var disk: diskIndex >= 0 ? disksModel[diskIndex] : null
    readonly property int alignSectors: disk && disk.sectorSize ? 1048576 / disk.sectorSize : 2048
    readonly property var segments: engine.planPreview.filter(function(s) { return !s.pendingDelete })
    property string selectedKey: ""
    readonly property var selected: {
        for (var i = 0; i < segments.length; i++)
            if (rowKey(segments[i]) === selectedKey) return segments[i]
        return null
    }
    readonly property bool freeSelected: selected !== null && selected.kind === "free"
    readonly property bool partitionSelected: selected !== null && selected.kind === "partition"
    // The checklist already shows these two as red lines.
    readonly property var checklistErrors: [
        qsTranslate("Messages", "No partition is assigned to root (/)."),
        qsTranslate("Messages", "No EFI system partition is assigned to /boot/efi.")
    ]
    readonly property var shownErrors: engine.planErrors.filter(function(e) { return checklistErrors.indexOf(e) < 0 })

    onDiskNameChanged: selectedKey = ""

    radius: 20
    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
    border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
    border.width: 2

    function rowKey(seg) {
        if (seg.kind === "free") return "free:" + seg.startSector
        return seg.newRef ? "new:" + seg.newRef : String(seg.number)
    }

    function manualRow(seg) {
        var rows = engine.manualRows
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i]
            if (seg.newRef ? (r.kind === "new" && r.ref === seg.newRef) : (r.kind === "existing" && r.number === seg.number))
                return r
        }
        return null
    }

    function originalPartition(number) {
        var parts = disk ? disk.partitions : []
        for (var i = 0; i < parts.length; i++)
            if (parts[i].number === number) return parts[i]
        return null
    }

    function endAfter(seg) {
        var key = rowKey(seg)
        var i = -1
        for (var j = 0; j < segments.length; j++)
            if (rowKey(segments[j]) === key) i = j
        var next = i >= 0 && i + 1 < segments.length ? segments[i + 1] : null
        return next && next.kind === "free" ? next.endSector : seg.endSector
    }

    function nextRef() {
        var highest = 0
        var rows = engine.manualRows
        for (var i = 0; i < rows.length; i++) {
            var m = rows[i].kind === "new" ? /^r(\d+)$/.exec(rows[i].ref) : null
            if (m) highest = Math.max(highest, parseInt(m[1]))
        }
        return "r" + (highest + 1)
    }

    function displayFs(fs) {
        var names = { vfat: "fat32", fat: "fat32", swap: "linuxswap" }
        return names[fs] || fs || ""
    }

    function checklistText(text) {
        var known = { "Root (/) assigned": qsTr("Root (/) assigned"), "EFI partition at /boot/efi": qsTr("EFI partition at /boot/efi") }
        return known[text] || text
    }

    function createSelected() {
        if (!freeSelected) return
        partitionDialog.alignSectors = alignSectors
        partitionDialog.openCreate({ start: selected.startSector, end: selected.endSector }, null, nextRef())
    }

    function editSelected() {
        if (!partitionSelected) return
        partitionDialog.alignSectors = alignSectors
        var row = manualRow(selected)
        if (selected.newRef) {
            partitionDialog.openCreate({ start: row.start, end: endAfter(selected) }, row, row.ref)
            return
        }
        var part = originalPartition(selected.number)
        if (part) partitionDialog.openEdit(part, row, endAfter(selected))
    }

    function deleteSelected() {
        if (!partitionSelected) return
        if (selected.newRef)
            engine.removeManualRow(selected.newRef)
        else
            engine.setManualRow(JSON.stringify({ kind: "existing", number: selected.number, action: "delete" }))
        selectedKey = ""
    }

    component PageButton: Button {
        id: pageButton
        property bool primary: false
        implicitHeight: 32 * uiScale
        leftPadding: 14 * uiScale
        rightPadding: 14 * uiScale
        contentItem: Text {
            text: pageButton.text
            color: !pageButton.enabled ? textMutedColor : pageButton.primary ? backgroundColor : textColor
            font.pixelSize: 12 * uiScale
            font.bold: pageButton.primary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: pageButton.primary
                   ? (pageButton.pressed ? Qt.darker(primaryColor, 1.2) : primaryColor)
                   : (pageButton.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor)
            border.color: pageButton.enabled ? primaryColor : textMutedColor
            border.width: 1
            opacity: pageButton.enabled ? 1 : 0.45
        }
    }

    component Tag: Rectangle {
        property alias text: tagText.text
        property color tint: successColor
        width: tagText.implicitWidth + 10 * uiScale
        height: tagText.implicitHeight + 2 * uiScale
        radius: 4
        color: Qt.rgba(tint.r, tint.g, tint.b, 0.25)
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
        Text {
            id: tagText
            anchors.centerIn: parent
            color: parent.tint
            font.pixelSize: 10 * uiScale
        }
    }

    component Cell: Text {
        verticalAlignment: Text.AlignVCenter
        leftPadding: 6 * uiScale
        color: textColor
        font.pixelSize: 12 * uiScale
        elide: Text.ElideRight
    }

    readonly property var columnShares: [0.22, 0.14, 0.17, 0.28, 0.19]

    RowLayout {
        anchors.fill: parent
        anchors.margins: 16 * uiScale
        spacing: 14 * uiScale

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10 * uiScale

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
                    objectName: "manualDiskCombo"
                    Layout.fillWidth: true
                    Layout.maximumWidth: 420 * uiScale
                    Layout.minimumWidth: 120 * uiScale
                    uiScale: page.uiScale
                    primaryColor: page.primaryColor
                    backgroundColor: page.backgroundColor
                    surfaceColor: page.surfaceColor
                    textColor: page.textColor
                    textMutedColor: page.textMutedColor
                    model: disksModel.map(function(d) {
                        return [d.name, d.model, d.size].filter(function(part) { return !!part }).join("  ")
                    })
                    currentIndex: diskIndex
                    onActivated: function(index) {
                        page.diskSelected(disksModel[index].name)
                        currentIndex = Qt.binding(function() { return diskIndex })
                    }
                }
                Item { Layout.fillWidth: true }
                PageButton {
                    objectName: "manualRevertButton"
                    text: qsTr("Revert All Changes")
                    enabled: engine.manualRows.length > 0 || engine.manualNewTable
                    onClicked: {
                        engine.revertManual()
                        page.selectedKey = ""
                    }
                }
            }

            PartitionBar {
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                uiScale: page.uiScale
                textColor: page.textColor
                outlineColor: page.textMutedColor
                segments: engine.planPreview
                interactive: true
                newLabel: qsTr("New")
                selectedNumber: partitionSelected && !selected.newRef ? selected.number : 0
                onSegmentClicked: function(seg) { page.selectedKey = page.rowKey(seg) }
            }

            Rectangle {
                id: tableFrame
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 80 * uiScale
                radius: 10
                color: Qt.rgba(backgroundColor.r, backgroundColor.g, backgroundColor.b, 0.35)
                border.color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.3)
                border.width: 1

                readonly property real innerWidth: width - 16 * uiScale

                Row {
                    id: tableHeader
                    x: 8 * uiScale
                    y: 6 * uiScale
                    Repeater {
                        model: [qsTr("Name"), qsTr("File System"), qsTr("Label"), qsTr("Mount Point"), qsTr("Size")]
                        Text {
                            required property int index
                            required property string modelData
                            width: tableFrame.innerWidth * page.columnShares[index]
                            leftPadding: 6 * uiScale
                            text: modelData
                            color: textMutedColor
                            font.pixelSize: 12 * uiScale
                            elide: Text.ElideRight
                        }
                    }
                }
                Rectangle {
                    x: 8 * uiScale
                    y: tableHeader.y + tableHeader.height + 4 * uiScale
                    width: tableFrame.innerWidth
                    height: 1
                    color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.3)
                }

                ListView {
                    id: table
                    objectName: "manualTable"
                    x: 8 * uiScale
                    y: tableHeader.y + tableHeader.height + 8 * uiScale
                    width: tableFrame.innerWidth
                    height: tableFrame.height - y - 8 * uiScale
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: page.segments
                    ScrollBar.vertical: ScrollBar {}

                    delegate: Rectangle {
                        id: tableRow
                        required property int index
                        required property var modelData
                        readonly property var seg: modelData
                        readonly property bool free: seg.kind === "free"
                        readonly property bool fresh: !!seg.newRef
                        readonly property var row: free ? null : page.manualRow(seg)
                        property string rowKey: page.rowKey(seg)
                        objectName: "manualRow_" + index
                        width: table.width
                        height: 30 * uiScale
                        radius: 5
                        color: rowKey === page.selectedKey
                            ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.28)
                            : (rowMouse.containsMouse ? Qt.rgba(textColor.r, textColor.g, textColor.b, 0.05) : "transparent")

                        Row {
                            Cell {
                                width: table.width * page.columnShares[0]
                                height: tableRow.height
                                text: tableRow.free ? qsTr("Free Space") : tableRow.fresh ? qsTr("New partition") : (tableRow.seg.name || "")
                                color: tableRow.free ? textMutedColor : textColor
                                font.bold: tableRow.fresh
                            }
                            Cell { width: table.width * page.columnShares[1]; height: tableRow.height; text: tableRow.free ? "" : page.displayFs(tableRow.seg.fstype) }
                            Cell { width: table.width * page.columnShares[2]; height: tableRow.height; text: tableRow.free ? "" : (tableRow.seg.label || "") }
                            Item {
                                width: table.width * page.columnShares[3]
                                height: tableRow.height
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    leftPadding: 6 * uiScale
                                    spacing: 6 * uiScale
                                    Text {
                                        readonly property string mount: tableRow.seg.mountpoint || ""
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: tableRow.free || mount === "swap" ? "" : (mount || qsTr("(no mount point)"))
                                        color: mount ? textColor : textMutedColor
                                        font.pixelSize: 12 * uiScale
                                    }
                                    Tag {
                                        visible: !tableRow.free && !tableRow.fresh && !!tableRow.seg.mountpoint && !tableRow.seg.format
                                        text: qsTr("keep")
                                    }
                                    Tag {
                                        visible: !tableRow.free && !tableRow.fresh && !!tableRow.seg.format
                                        text: qsTr("format")
                                        tint: warningColor
                                    }
                                }
                            }
                            Item {
                                width: table.width * page.columnShares[4]
                                height: tableRow.height
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    leftPadding: 6 * uiScale
                                    spacing: 6 * uiScale
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: Units.humanSize(tableRow.seg.sizeBytes)
                                        color: textColor
                                        font.pixelSize: 12 * uiScale
                                    }
                                    Tag {
                                        visible: !!tableRow.row && tableRow.row.newEnd != null
                                        text: qsTr("resize")
                                        tint: primaryColor
                                    }
                                }
                            }
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: page.selectedKey = tableRow.rowKey
                            onDoubleClicked: {
                                page.selectedKey = tableRow.rowKey
                                if (tableRow.free) page.createSelected()
                                else page.editSelected()
                            }
                        }
                    }
                }
            }

            Flow {
                Layout.fillWidth: true
                spacing: 8 * uiScale

                PageButton {
                    objectName: "manualNewTableButton"
                    text: qsTr("New Partition Table")
                    enabled: page.disk !== null
                    onClicked: newTableConfirm.open()
                }
                PageButton {
                    objectName: "manualCreateButton"
                    text: qsTr("Create")
                    enabled: page.freeSelected
                    onClicked: page.createSelected()
                }
                PageButton {
                    objectName: "manualEditButton"
                    text: qsTr("Edit")
                    enabled: page.partitionSelected
                    onClicked: page.editSelected()
                }
                PageButton {
                    objectName: "manualDeleteButton"
                    text: qsTr("Delete")
                    enabled: page.partitionSelected
                    onClicked: page.deleteSelected()
                }
            }
        }

        Rectangle {
            Layout.preferredWidth: Math.max(300 * uiScale, page.width * 0.37)
            Layout.fillHeight: true
            radius: 12
            color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.08)
            border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.4)
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12 * uiScale
                spacing: 8 * uiScale

                GuideAccordion {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    uiScale: page.uiScale
                    primaryColor: page.primaryColor
                    textColor: page.textColor
                    textMutedColor: page.textMutedColor
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.3)
                }

                Column {
                    Layout.fillWidth: true
                    spacing: 3 * uiScale

                    Repeater {
                        model: engine.planChecklist
                        Row {
                            required property int index
                            required property var modelData
                            property bool ok: modelData.ok
                            property string text: page.checklistText(modelData.text)
                            objectName: "checklistItem_" + index
                            spacing: 6 * uiScale
                            Text {
                                text: parent.ok ? "✓" : "✗"
                                color: parent.ok ? successColor : errorColor
                                font.pixelSize: 12 * uiScale
                                font.bold: true
                            }
                            Text {
                                text: parent.text
                                color: parent.ok ? successColor : errorColor
                                font.pixelSize: 12 * uiScale
                            }
                        }
                    }
                    Repeater {
                        model: page.shownErrors
                        Text {
                            required property int index
                            required property string modelData
                            objectName: "planErrorLine_" + index
                            width: parent.width
                            text: "✗ " + modelData
                            color: errorColor
                            font.pixelSize: 11.5 * uiScale
                            wrapMode: Text.WordWrap
                        }
                    }
                    Repeater {
                        model: engine.planWarnings
                        Text {
                            required property int index
                            required property string modelData
                            objectName: "planWarningLine_" + index
                            width: parent.width
                            text: "⚠ " + modelData
                            color: warningColor
                            font.pixelSize: 11.5 * uiScale
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: newTableConfirm
        objectName: "newTableConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(420 * uiScale, parent ? parent.width - 40 * uiScale : 420 * uiScale)
        padding: 18 * uiScale
        modal: true
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.55) }
        background: Rectangle {
            radius: 14
            color: surfaceColor
            border.color: primaryColor
            border.width: 2
        }

        contentItem: ColumnLayout {
            spacing: 12 * uiScale
            Text {
                Layout.fillWidth: true
                text: qsTr("New Partition Table")
                color: textColor
                font.pixelSize: 16 * uiScale
                font.bold: true
            }
            Text {
                objectName: "newTableConfirmText"
                Layout.fillWidth: true
                text: qsTr("This removes every partition on this disk when you click Install.")
                color: textColor
                font.pixelSize: 12 * uiScale
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8 * uiScale
                PageButton {
                    objectName: "newTableCancelButton"
                    text: qsTr("Cancel")
                    onClicked: newTableConfirm.close()
                }
                PageButton {
                    objectName: "newTableConfirmButton"
                    primary: true
                    text: qsTr("New Partition Table")
                    onClicked: {
                        engine.setManualNewTable(true)
                        page.selectedKey = ""
                        newTableConfirm.close()
                    }
                }
            }
        }
    }

    ManualPartitionDialog {
        id: partitionDialog
        uiScale: page.uiScale
        primaryColor: page.primaryColor
        backgroundColor: page.backgroundColor
        surfaceColor: page.surfaceColor
        textColor: page.textColor
        textMutedColor: page.textMutedColor
        warningColor: page.warningColor
        errorColor: page.errorColor
        defaultSubvolumesJson: engine.defaultBtrfsSubvolumes
    }
}
