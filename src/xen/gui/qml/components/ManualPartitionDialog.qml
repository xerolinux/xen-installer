import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Units.js" as Units
import "PartitionColors.js" as PartitionColors

Popup {
    id: dialog
    objectName: "manualPartitionDialog"

    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"
    property string defaultSubvolumesJson: "[]"

    // "create": a new partition in free space (or a queued one being changed); "edit": an existing partition.
    property string mode: "create"
    property string title: ""
    property int alignSectors: 2048
    property int startSector: 0
    property int minMiB: 1
    property int maxMiB: 1
    property int originalMiB: 0
    property string ref: ""
    property int number: 0
    property string partFs: ""
    property string partLabel: ""
    property string resizeReason: ""
    property string lockedReason: ""
    property string content: "keep"
    property string fsName: "ext4"
    property string mount: ""
    property string subvolumesJson: "[]"
    property bool espSized: false

    readonly property var fsChoices: engine.hasInternet
        ? ["btrfs", "ext4", "xfs", "fat32", "linuxswap"] : ["ext4", "xfs", "fat32", "linuxswap"]
    readonly property var mountChoices: ["/", "/boot", "/boot/efi", "/home", "/opt", "/srv", "/var"]

    readonly property bool editing: mode === "edit"
    readonly property string keepReason: !editing ? ""
        : lockedReason !== "" ? lockedReason
        : mount === "/" ? qsTr("The root (/) partition must be formatted: keeping its contents isn't supported.") : ""
    readonly property bool formatting: !editing || content === "format" || keepReason !== ""
    readonly property string shownFs: formatting ? fsName : displayFs(partFs)
    readonly property bool isSwap: shownFs === "linuxswap"
    readonly property bool resizeEnabled: editing ? !formatting && resizeReason === "" : fits
    readonly property string resizeNote: !editing ? ""
        : resizeReason !== "" ? resizeReason
        : formatting ? qsTr("Resize or format, not both: delete it and create a new one instead.") : ""
    readonly property bool encryptShown: formatting && fsName !== "fat32"
    readonly property bool encrypting: encryptShown && encryptCheck.checked
    readonly property bool askPassphrase: encrypting && mount === "/"
    readonly property bool passphraseOk: !askPassphrase
        || (passField.text.length >= 8 && passField.text === passConfirmField.text)
    readonly property bool subvolumesShown: formatting && fsName === "btrfs" && mount === "/"
    readonly property var subvolumes: { try { return JSON.parse(subvolumesJson) } catch (e) { return [] } }
    readonly property int sizeMiB: sizeSpin.value
    readonly property bool fits: maxMiB >= 1
    readonly property bool valid: fits && sizeMiB >= minMiB && sizeMiB <= maxMiB && passphraseOk

    function displayFs(fs) {
        var names = { vfat: "fat32", fat: "fat32", swap: "linuxswap" }
        return names[fs] || fs
    }

    function parseList(json) { try { return JSON.parse(json) } catch (e) { return [] } }

    function reset(row) {
        fsName = row && row.fstype && fsChoices.indexOf(displayFs(row.fstype)) >= 0
            ? displayFs(row.fstype) : fsChoices[0]
        setMount(row ? (row.mountpoint || "") : "")
        labelField.text = row ? (row.label || "") : ""
        encryptCheck.checked = !!(row && row.encrypt)
        passField.text = ""
        passConfirmField.text = ""
        subvolumesJson = row && row.subvolumes && row.subvolumes.length
            ? JSON.stringify(row.subvolumes) : defaultSubvolumesJson
        espSized = !!row
    }

    function setMount(value) {
        mount = value
        mountCombo.currentIndex = mountChoices.indexOf(value)
        mountCombo.editText = value
    }

    // region: {start, end} in sectors; row: the queued new partition being changed, or null.
    function openCreate(region, row, newRef) {
        mode = "create"
        title = row ? qsTr("Edit New Partition") : qsTr("Create a Partition")
        ref = row ? row.ref : newRef
        startSector = row ? row.start : Math.ceil(region.start / alignSectors) * alignSectors
        minMiB = 0
        maxMiB = Math.max(0, Math.floor((region.end - startSector + 1) / alignSectors))
        minMiB = Math.min(1, maxMiB)
        reset(row)
        sizeSpin.value = row ? Math.floor((row.end - row.start + 1) / alignSectors) : maxMiB
        visible = true
    }

    // maxEnd: last sector the partition may grow to (end of the free space after it).
    function openEdit(part, row, maxEnd) {
        mode = "edit"
        title = qsTr("Edit Partition %1").arg(part.name)
        number = part.number
        partFs = part.fstype || ""
        partLabel = part.label || ""
        resizeReason = part.resizeBlockedReason || ""
        lockedReason = partFs === "crypto_LUKS" || partFs === "BitLocker" ? resizeReason : ""
        startSector = part.startSector
        originalMiB = Math.floor((part.endSector - part.startSector + 1) / alignSectors)
        var current = row && row.newEnd != null
            ? Math.floor((row.newEnd - part.startSector + 1) / alignSectors) : originalMiB
        minMiB = part.canShrink !== false ? Math.max(1, engine.minShrinkMiB(part.minSizeBytes || 0)) : originalMiB
        maxMiB = part.canGrow !== false
            ? Math.max(current, Math.floor((maxEnd - part.startSector + 1) / alignSectors)) : originalMiB
        minMiB = Math.min(minMiB, current)
        reset(row && row.action === "format" ? row : null)
        content = row && row.action === "format" ? "format" : "keep"
        setMount(row ? (row.mountpoint || "") : "")
        if (!row || row.action !== "format") {
            labelField.text = partLabel
            if (fsChoices.indexOf(displayFs(partFs)) >= 0) fsName = displayFs(partFs)
        }
        sizeSpin.value = current
        visible = true
    }

    function buildRow() {
        var common = {
            mountpoint: isSwap ? "" : mount,
            encrypt: encrypting,
            subvolumes: subvolumesShown ? subvolumes : []
        }
        if (!editing) {
            return Object.assign({
                kind: "new",
                ref: ref,
                start: startSector,
                end: startSector + sizeMiB * alignSectors - 1,
                fstype: fsName,
                label: labelField.text
            }, common)
        }
        var row = Object.assign({
            kind: "existing",
            number: number,
            action: formatting ? "format" : "keep",
            fstype: formatting ? fsName : "",
            label: formatting ? labelField.text : ""
        }, common)
        if (!formatting && sizeMiB !== originalMiB)
            row.newEnd = startSector + sizeMiB * alignSectors - 1
        return row
    }

    function accept() {
        if (!valid) return
        if (askPassphrase) engine.setManualRootPassphrase(passField.text)
        if (engine.setManualRow(JSON.stringify(buildRow()))) close()
    }

    // Resize and format can't be combined, so Format drops any pending resize.
    onFormattingChanged: if (editing && formatting) sizeSpin.value = originalMiB
    onClosed: {
        passField.text = ""
        passConfirmField.text = ""
    }
    onFsNameChanged: sizeEsp()
    onMountChanged: sizeEsp()
    // A new EFI partition is recommended at 2000 MiB.
    function sizeEsp() {
        if (!visible || editing || espSized || fsName !== "fat32" || mount !== "/boot/efi") return
        espSized = true
        sizeSpin.value = Math.min(2000, maxMiB)
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(500 * uiScale, parent ? parent.width - 40 * uiScale : 500 * uiScale)
    padding: 18 * uiScale
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape

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
        leftPadding: 14 * uiScale
        rightPadding: 14 * uiScale
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

    component SpinStep: Rectangle {
        property string glyph
        property bool pressed: false
        implicitWidth: 28 * uiScale
        height: parent ? parent.height : 0
        radius: 6
        color: pressed ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.3) : "transparent"
        Text {
            anchors.centerIn: parent
            text: parent.glyph
            color: textColor
            font.pixelSize: 15 * uiScale
        }
    }

    component FieldLabel: Text {
        Layout.preferredWidth: 104 * uiScale
        Layout.alignment: Qt.AlignVCenter
        color: textColor
        font.pixelSize: 12 * uiScale
        wrapMode: Text.WordWrap
    }

    component Note: Text {
        Layout.fillWidth: true
        Layout.columnSpan: 2
        leftPadding: 114 * uiScale
        font.pixelSize: 11 * uiScale
        wrapMode: Text.WordWrap
    }

    component InputField: TextField {
        id: input
        Layout.fillWidth: true
        Layout.preferredHeight: 32 * uiScale
        font.pixelSize: 12 * uiScale
        color: enabled ? textColor : textMutedColor
        selectionColor: primaryColor
        selectedTextColor: textColor
        placeholderTextColor: textMutedColor
        leftPadding: 10 * uiScale
        topPadding: 0
        bottomPadding: 0
        verticalAlignment: Text.AlignVCenter
        background: Rectangle {
            radius: 6
            color: input.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
            border.color: input.activeFocus ? primaryColor : textMutedColor
            border.width: 1
            opacity: input.enabled ? 1 : 0.6
        }
    }

    component ThemedRadio: RadioButton {
        id: radio
        // Checked state is driven by bindings; a click only reports intent.
        checkable: false
        font.pixelSize: 12 * uiScale
        opacity: enabled ? 1 : 0.45
        indicator: Rectangle {
            implicitWidth: 18 * uiScale
            implicitHeight: 18 * uiScale
            x: radio.leftPadding
            y: (radio.height - height) / 2
            radius: width / 2
            color: "transparent"
            border.color: radio.checked ? primaryColor : textMutedColor
            border.width: 2
            Rectangle {
                anchors.centerIn: parent
                width: parent.width / 2
                height: width
                radius: width / 2
                color: primaryColor
                visible: radio.checked
            }
        }
        contentItem: Text {
            text: radio.text
            font: radio.font
            color: textColor
            verticalAlignment: Text.AlignVCenter
            leftPadding: radio.indicator.width + radio.spacing
        }
    }

    contentItem: GridLayout {
        columns: 2
        columnSpacing: 10 * uiScale
        rowSpacing: 8 * uiScale

        Text {
            Layout.columnSpan: 2
            Layout.fillWidth: true
            Layout.bottomMargin: 4 * uiScale
            text: dialog.title
            color: textColor
            font.pixelSize: 16 * uiScale
            font.bold: true
            elide: Text.ElideRight
        }

        FieldLabel { text: qsTr("Size") }
        RowLayout {
            Layout.fillWidth: true
            spacing: 10 * uiScale

            Item {
                id: resizeBar
                objectName: "dialogResizeBar"
                Layout.fillWidth: true
                Layout.preferredHeight: 24 * uiScale
                enabled: dialog.resizeEnabled
                opacity: enabled ? 1 : 0.45
                readonly property real fraction: dialog.maxMiB > 0 ? Math.min(1, dialog.sizeMiB / dialog.maxMiB) : 0

                Rectangle {
                    anchors.fill: parent
                    radius: 6
                    color: PartitionColors.free
                    border.color: Qt.rgba(textMutedColor.r, textMutedColor.g, textMutedColor.b, 0.35)
                    border.width: 1
                }
                Rectangle {
                    width: Math.max(height, parent.width * resizeBar.fraction)
                    height: parent.height
                    radius: 6
                    color: dialog.editing ? PartitionColors.forPartition("", dialog.shownFs) : PartitionColors.created
                }
                Rectangle {
                    x: Math.max(0, Math.min(parent.width - width, parent.width * resizeBar.fraction - width / 2))
                    anchors.verticalCenter: parent.verticalCenter
                    width: 6 * uiScale
                    height: parent.height + 6 * uiScale
                    radius: 3 * uiScale
                    color: textColor
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.SizeHorCursor
                    function drag(x) {
                        var v = Math.round(Math.max(0, Math.min(1, x / width)) * dialog.maxMiB)
                        sizeSpin.value = Math.max(dialog.minMiB, Math.min(dialog.maxMiB, v))
                    }
                    onPressed: function(mouse) { drag(mouse.x) }
                    onPositionChanged: function(mouse) { drag(mouse.x) }
                }
            }

            SpinBox {
                id: sizeSpin
                objectName: "dialogSizeSpin"
                Layout.preferredWidth: 150 * uiScale
                Layout.preferredHeight: 32 * uiScale
                from: dialog.minMiB
                to: dialog.maxMiB
                editable: true
                enabled: dialog.resizeEnabled
                font.pixelSize: 12 * uiScale
                opacity: enabled ? 1 : 0.5
                textFromValue: function(value) { return value + " " + qsTr("MiB") }
                valueFromText: function(text) { return parseInt(text.replace(/[^0-9]/g, "")) || from }
                contentItem: TextInput {
                    text: sizeSpin.displayText
                    font: sizeSpin.font
                    color: textColor
                    selectionColor: primaryColor
                    selectedTextColor: textColor
                    horizontalAlignment: Qt.AlignHCenter
                    verticalAlignment: Qt.AlignVCenter
                    readOnly: !sizeSpin.editable
                    validator: sizeSpin.validator
                    inputMethodHints: Qt.ImhDigitsOnly
                }
                up.indicator: SpinStep { glyph: "+"; x: sizeSpin.width - width; pressed: sizeSpin.up.pressed }
                down.indicator: SpinStep { glyph: "\u2212"; x: 0; pressed: sizeSpin.down.pressed }
                background: Rectangle {
                    radius: 6
                    color: backgroundColor
                    border.color: sizeSpin.activeFocus ? primaryColor : textMutedColor
                    border.width: 1
                }
            }
        }

        Note {
            visible: dialog.resizeEnabled && dialog.editing
            text: qsTr("From %1 to %2.").arg(Units.humanSize(dialog.minMiB * 1048576)).arg(Units.humanSize(dialog.maxMiB * 1048576))
            color: textMutedColor
        }
        Note {
            objectName: "dialogFitReason"
            visible: !dialog.fits
            text: qsTr("Less than 1 MiB is free here once aligned, too little for a partition.")
            color: errorColor
        }
        Note {
            objectName: "dialogResizeReason"
            visible: text !== ""
            text: dialog.resizeNote
            color: textMutedColor
        }

        FieldLabel { text: qsTr("Content"); visible: dialog.editing }
        ColumnLayout {
            visible: dialog.editing
            Layout.fillWidth: true
            spacing: 2 * uiScale

            Row {
                spacing: 16 * uiScale
                ThemedRadio {
                    objectName: "dialogKeepRadio"
                    text: qsTr("Keep")
                    enabled: dialog.keepReason === ""
                    checked: !dialog.formatting
                    onClicked: dialog.content = "keep"
                }
                ThemedRadio {
                    objectName: "dialogFormatRadio"
                    text: qsTr("Format")
                    checked: dialog.formatting
                    onClicked: dialog.content = "format"
                }
            }
            Text {
                objectName: "dialogKeepReason"
                Layout.fillWidth: true
                visible: dialog.keepReason !== ""
                text: dialog.keepReason
                color: textMutedColor
                font.pixelSize: 11 * uiScale
                wrapMode: Text.WordWrap
            }
            Text {
                objectName: "dialogFormatWarning"
                Layout.fillWidth: true
                visible: dialog.formatting
                text: "⚠ " + qsTr("Formatting erases everything on this partition.")
                color: warningColor
                font.pixelSize: 11 * uiScale
                wrapMode: Text.WordWrap
            }
        }

        FieldLabel { text: qsTr("File System") }
        OptionCombo {
            id: fsCombo
            objectName: "dialogFsCombo"
            Layout.fillWidth: true
            uiScale: dialog.uiScale
            primaryColor: dialog.primaryColor
            backgroundColor: dialog.backgroundColor
            surfaceColor: dialog.surfaceColor
            textColor: dialog.textColor
            textMutedColor: dialog.textMutedColor
            enabled: dialog.formatting
            opacity: enabled ? 1 : 0.6
            model: dialog.fsChoices
            currentIndex: dialog.fsChoices.indexOf(dialog.fsName)
            displayText: dialog.shownFs || qsTr("unknown")
            onActivated: function(index) {
                dialog.fsName = dialog.fsChoices[index]
                currentIndex = Qt.binding(function() { return dialog.fsChoices.indexOf(dialog.fsName) })
            }
        }

        FieldLabel { text: qsTr("Subvolumes"); visible: dialog.subvolumesShown }
        Row {
            objectName: "dialogSubvolumesRow"
            visible: dialog.subvolumesShown
            spacing: 10 * uiScale

            Text {
                height: subvolumesButton.height
                verticalAlignment: Text.AlignVCenter
                text: (JSON.stringify(dialog.subvolumes) === JSON.stringify(parseList(dialog.defaultSubvolumesJson))
                       ? qsTr("Default (%1)") : qsTr("Custom (%1)")).arg(dialog.subvolumes.length)
                color: textMutedColor
                font.pixelSize: 12 * uiScale
            }
            DialogButton {
                id: subvolumesButton
                objectName: "dialogSubvolumesEditButton"
                text: qsTr("Edit…")
                onClicked: subvolumeDialog.open(dialog.subvolumesJson)
            }
        }

        FieldLabel { text: qsTr("Mount Point"); visible: !dialog.isSwap }
        ComboBox {
            id: mountCombo
            objectName: "dialogMountCombo"
            visible: !dialog.isSwap
            Layout.fillWidth: true
            Layout.preferredHeight: 32 * uiScale
            editable: true
            model: dialog.mountChoices
            font.pixelSize: 12 * uiScale
            // Editable combos only commit on Enter or a pick; follow every keystroke instead.
            onEditTextChanged: dialog.mount = editText.trim()
            onActivated: function(index) { dialog.mount = textAt(index) }
            contentItem: TextField {
                text: mountCombo.editText
                placeholderText: qsTr("(no mount point)")
                placeholderTextColor: textMutedColor
                color: textColor
                selectionColor: primaryColor
                selectedTextColor: textColor
                font: mountCombo.font
                leftPadding: 10 * uiScale
                rightPadding: 28 * uiScale
                verticalAlignment: Text.AlignVCenter
                background: null
            }
            indicator: Text {
                x: mountCombo.width - width - 12 * uiScale
                y: (mountCombo.height - height) / 2
                text: "▾"
                color: textMutedColor
                font.pixelSize: 14 * uiScale
            }
            background: Rectangle {
                radius: 6
                color: backgroundColor
                border.color: mountCombo.activeFocus ? primaryColor : textMutedColor
                border.width: 1
            }
            popup: Popup {
                y: mountCombo.height
                width: mountCombo.width
                implicitHeight: Math.min(contentItem.implicitHeight, 200 * uiScale)
                padding: 1
                background: Rectangle { radius: 6; color: surfaceColor; border.color: primaryColor; border.width: 1 }
                contentItem: ListView {
                    clip: true
                    implicitHeight: contentHeight
                    model: mountCombo.popup.visible ? mountCombo.delegateModel : null
                    currentIndex: mountCombo.highlightedIndex
                }
            }
            delegate: ItemDelegate {
                required property int index
                required property string modelData
                width: mountCombo.width
                highlighted: mountCombo.highlightedIndex === index
                contentItem: Text { text: modelData; color: textColor; font.pixelSize: 12 * uiScale; leftPadding: 10 }
                background: Rectangle { color: highlighted ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.25) : "transparent" }
            }
        }

        FieldLabel { text: qsTr("FS Label") }
        InputField {
            id: labelField
            objectName: "dialogLabelField"
            enabled: dialog.formatting
            placeholderText: qsTr("optional")
        }

        Item { Layout.preferredWidth: 1; visible: dialog.encryptShown }
        CheckBox {
            id: encryptCheck
            objectName: "dialogEncryptCheck"
            visible: dialog.encryptShown
            text: qsTr("Encrypt")
            font.pixelSize: 12 * uiScale
            indicator: Rectangle {
                implicitWidth: 18 * uiScale
                implicitHeight: 18 * uiScale
                x: encryptCheck.leftPadding
                y: (encryptCheck.height - height) / 2
                radius: 4
                color: "transparent"
                border.color: encryptCheck.checked ? primaryColor : textMutedColor
                border.width: 2
                Text {
                    anchors.centerIn: parent
                    visible: encryptCheck.checked
                    text: "✓"
                    color: primaryColor
                    font.pixelSize: 13 * uiScale
                    font.bold: true
                }
            }
            contentItem: Text {
                text: encryptCheck.text
                font: encryptCheck.font
                color: textColor
                verticalAlignment: Text.AlignVCenter
                leftPadding: encryptCheck.indicator.width + encryptCheck.spacing
            }
        }

        FieldLabel { text: qsTr("Passphrase"); visible: dialog.askPassphrase }
        RowLayout {
            visible: dialog.askPassphrase
            Layout.fillWidth: true
            spacing: 8 * uiScale
            InputField {
                id: passField
                objectName: "dialogPassField"
                Layout.preferredWidth: 1
                echoMode: TextInput.Password
                placeholderText: qsTr("Passphrase")
            }
            InputField {
                id: passConfirmField
                objectName: "dialogPassConfirmField"
                Layout.preferredWidth: 1
                echoMode: TextInput.Password
                placeholderText: qsTr("Confirm passphrase")
            }
        }
        Note {
            visible: dialog.askPassphrase
            text: qsTr("At least 8 characters. It also unlocks the other encrypted partitions.")
            color: textMutedColor
        }
        Note {
            visible: dialog.askPassphrase && passConfirmField.text.length > 0 && passField.text !== passConfirmField.text
            text: qsTr("Passphrases do not match.")
            color: errorColor
        }

        RowLayout {
            Layout.columnSpan: 2
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: 6 * uiScale
            spacing: 8 * uiScale

            DialogButton {
                objectName: "dialogCancelButton"
                text: qsTr("Cancel")
                onClicked: dialog.close()
            }
            DialogButton {
                objectName: "dialogOkButton"
                primary: true
                enabled: dialog.valid
                text: qsTr("OK")
                onClicked: dialog.accept()
            }
        }
    }

    BtrfsSubvolumeDialog {
        id: subvolumeDialog
        defaultsJson: dialog.defaultSubvolumesJson
        uiScale: dialog.uiScale
        primaryColor: dialog.primaryColor
        backgroundColor: dialog.backgroundColor
        surfaceColor: dialog.surfaceColor
        textColor: dialog.textColor
        textMutedColor: dialog.textMutedColor
        errorColor: dialog.errorColor
        commit: function(json) { dialog.subvolumesJson = json; return true }
    }
}
