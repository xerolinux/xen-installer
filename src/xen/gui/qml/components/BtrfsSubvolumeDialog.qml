import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: dialog
    objectName: "btrfsSubvolumeDialog"

    property string defaultsJson: "[]"
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color errorColor: "#EF4444"

    // Called by Save; the dialog only closes when it returns true.
    property var commit: function(subvolumesJson) { return true }

    readonly property real fieldHeight: 30 * uiScale
    readonly property real rowSpacing: 6 * uiScale
    readonly property real removeWidth: 30 * uiScale
    property int revision: 0
    property bool saveAttempted: false
    property bool followNewRow: false
    readonly property string error: { revision; return engine.validateBtrfsSubvolumes(currentJson(false)) }
    // Rows added but not typed into yet don't nag; they still keep Save disabled.
    readonly property string shownError: {
        revision
        return saveAttempted ? error : engine.validateBtrfsSubvolumes(currentJson(true))
    }

    // Shadows Popup.open() so callers always seed the rows; visible = true is what open() does.
    function open(subvolumesJson) {
        load(subvolumesJson)
        visible = true
    }

    function load(subvolumesJson) {
        var list = []
        try { list = JSON.parse(subvolumesJson) } catch (e) { list = [] }
        rows.clear()
        var rootIndex = -1
        for (var i = 0; i < list.length; i++) {
            var mount = String(list[i].mountpoint)
            if (rootIndex < 0 && (mount === "/" || mount === "")) rootIndex = i
            rows.append({ name: String(list[i].name), mountpoint: mount, root: rootIndex === i, pristine: false })
        }
        saveAttempted = false
        revision++
    }

    function addRow() {
        rows.append({ name: "", mountpoint: "", root: false, pristine: true })
        revision++
        followNewRow = true
    }

    function scrollRowsToEnd() {
        var flick = rowsScroll.contentItem
        flick.contentY = Math.max(0, flick.contentHeight - flick.height)
    }

    function currentJson(skipPristine) {
        var list = []
        for (var i = 0; i < rows.count; i++) {
            var row = rows.get(i)
            if (!(skipPristine && row.pristine))
                list.push({ name: row.name, mountpoint: row.mountpoint })
        }
        return JSON.stringify(list)
    }

    function setField(index, role, value) {
        if (rows.get(index)[role] === value) return
        rows.setProperty(index, role, value)
        rows.setProperty(index, "pristine", false)
        revision++
    }

    // Runs in the dialog's scope: the clicked delegate is destroyed by rows.remove().
    function removeRow(index) {
        rows.remove(index)
        revision++
    }

    function save() {
        saveAttempted = true
        if (error === "" && commit(currentJson(false)))
            close()
    }

    ListModel { id: rows }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(420 * uiScale, parent ? parent.width - 40 * uiScale : 420 * uiScale)
    height: Math.min(implicitHeight, parent ? parent.height - 40 * uiScale : implicitHeight)
    padding: 18 * uiScale
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.55) }

    background: Rectangle {
        radius: 14
        color: surfaceColor
        border.color: primaryColor
        border.width: 2
    }

    component DialogButton: Button {
        id: dialogButton
        property bool primary: false
        implicitHeight: 32 * uiScale
        leftPadding: 12 * uiScale
        rightPadding: 12 * uiScale
        contentItem: Text {
            text: dialogButton.text
            color: !dialogButton.enabled ? textMutedColor : dialogButton.primary ? backgroundColor : textColor
            font.pixelSize: 12 * uiScale
            font.bold: dialogButton.primary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: dialogButton.primary
                   ? (dialogButton.enabled ? (dialogButton.pressed ? Qt.darker(primaryColor, 1.2) : primaryColor) : Qt.darker(surfaceColor, 1.3))
                   : (dialogButton.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor)
            border.color: dialogButton.enabled ? primaryColor : textMutedColor
            border.width: 1
            opacity: dialogButton.enabled ? 1 : 0.6
        }
    }

    component SubvolumeField: TextField {
        id: field
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        Layout.preferredHeight: dialog.fieldHeight
        font.pixelSize: 12 * uiScale
        color: textColor
        selectionColor: primaryColor
        selectedTextColor: textColor
        placeholderTextColor: textMutedColor
        leftPadding: 8 * uiScale
        topPadding: 0
        bottomPadding: 0
        verticalAlignment: Text.AlignVCenter
        background: Rectangle {
            radius: 5
            color: field.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
            border.color: field.activeFocus ? primaryColor : textMutedColor
            border.width: 1
        }
    }

    contentItem: ColumnLayout {
        spacing: 8 * uiScale

        Text {
            Layout.fillWidth: true
            text: qsTr("Btrfs subvolumes")
            color: textColor
            font.pixelSize: 16 * uiScale
            font.bold: true
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.rightMargin: dialog.removeWidth + dialog.rowSpacing
            spacing: dialog.rowSpacing
            Text {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                text: qsTr("Subvolume")
                color: textMutedColor
                font.pixelSize: 12 * uiScale
            }
            Text {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                text: qsTr("Mount point")
                color: textMutedColor
                font.pixelSize: 12 * uiScale
            }
        }

        ScrollView {
            id: rowsScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 2 * (dialog.fieldHeight + dialog.rowSpacing)
            Layout.preferredHeight: rowsColumn.implicitHeight
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            Column {
                id: rowsColumn
                width: rowsScroll.availableWidth
                spacing: dialog.rowSpacing
                // The Column only grows in its next polish, after addRow() returns.
                onImplicitHeightChanged: {
                    if (!dialog.followNewRow) return
                    dialog.followNewRow = false
                    Qt.callLater(dialog.scrollRowsToEnd)
                }

                Repeater {
                    model: rows
                    delegate: RowLayout {
                        id: rowItem
                        required property int index
                        required property string name
                        required property string mountpoint
                        required property bool root
                        width: rowsColumn.width
                        spacing: dialog.rowSpacing

                        SubvolumeField {
                            objectName: "subvolumeName_" + rowItem.index
                            text: rowItem.name
                            placeholderText: "@data"
                            onTextChanged: dialog.setField(rowItem.index, "name", text)
                        }
                        SubvolumeField {
                            objectName: "subvolumeMount_" + rowItem.index
                            text: rowItem.mountpoint
                            placeholderText: "/data"
                            onTextChanged: dialog.setField(rowItem.index, "mountpoint", text)
                        }
                        Item {
                            Layout.preferredWidth: dialog.removeWidth
                            Layout.preferredHeight: dialog.fieldHeight

                            Button {
                                objectName: "subvolumeRemove_" + rowItem.index
                                anchors.fill: parent
                                visible: !rowItem.root
                                Accessible.name: qsTr("Remove subvolume")
                                onClicked: dialog.removeRow(rowItem.index)
                                contentItem: Text {
                                    text: "✕"
                                    color: errorColor
                                    font.pixelSize: 13 * uiScale
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 5
                                    color: parent.hovered ? Qt.rgba(errorColor.r, errorColor.g, errorColor.b, 0.15) : "transparent"
                                }
                            }
                        }
                    }
                }
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 8 * uiScale

            DialogButton {
                objectName: "subvolumesAddButton"
                text: qsTr("+ Add")
                onClicked: dialog.addRow()
            }
            DialogButton {
                objectName: "subvolumesRestoreButton"
                text: qsTr("Restore defaults")
                onClicked: dialog.load(dialog.defaultsJson)
            }
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("@ (/) is required. @swap is used for the swap file.")
            color: textMutedColor
            font.pixelSize: 11 * uiScale
            wrapMode: Text.WordWrap
        }

        Text {
            objectName: "subvolumesError"
            Layout.fillWidth: true
            visible: dialog.shownError !== ""
            text: dialog.shownError
            color: errorColor
            font.pixelSize: 12 * uiScale
            wrapMode: Text.WordWrap
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 8 * uiScale

            DialogButton {
                objectName: "subvolumesCancelButton"
                text: qsTr("Cancel")
                onClicked: dialog.close()
            }
            DialogButton {
                objectName: "subvolumesSaveButton"
                primary: true
                enabled: dialog.error === ""
                text: qsTr("Save")
                onClicked: dialog.save()
            }
        }
    }
}
