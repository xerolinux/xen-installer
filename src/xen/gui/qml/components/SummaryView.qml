/*
 * SummaryView - Installation Summary and Confirmation
 *
 * Displays:
 * - Complete summary of all selections
 * - Edit buttons to modify specific sections
 * - Final confirmation and Install button
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Units.js" as Units

Item {
    id: root
    objectName: "summaryView"

    // Signals
    signal editLocale()
    signal editUsers()
    signal editPartition()

    // Final confirmation gate (ITEM 2): emitted when the user (dis)arms the
    // destructive installation via the confirmation checkbox. Main.qml wires
    // this to engine.setConfirmed() and only enables "Install" when checked.
    signal confirmedToggled(bool confirmed)

    // Two-way reflectable state of the confirmation checkbox. Bound in Main.qml
    // to engine.confirmed so the gate survives navigation back/forth.
    property bool confirmed: false

    // External properties - selections from previous steps.
    //
    // NOTE (fix persistance résumé): ces valeurs sont exposées comme des
    // propriétés SCALAIRES individuelles et bindées dans Main.qml directement
    // aux getters notifiés du bridge (engine.username, engine.hostname, ...).
    // On N'utilise PLUS un unique `property var selections` (dict) : un
    // @Property(object) renvoie une nouvelle copie de dict à chaque lecture, si
    // bien que les bindings `text: selections.X` ne se ré-évaluaient jamais sur
    // selectionsChanged (les sous-propriétés d'un objet JS ne sont pas suivies
    // par QML). Des propriétés scalaires notifiées se propagent de façon fiable.
    property string localeValue: "en_US.UTF-8"
    property string timezoneValue: "UTC"
    property string keymapValue: "us"
    property string usernameValue: ""
    property string fullNameValue: ""
    property string hostnameValue: ""
    property string diskValue: ""
    property string diskSizeValue: ""
    property string partitionModeValue: "auto"
    property string filesystemValue: ""
    property bool encryptionValue: false
    readonly property bool manualMode: partitionModeValue === "manual"

    readonly property bool alongsideMode: partitionModeValue === "alongside"
    readonly property string partitionModeText: manualMode ? qsTr("Manual")
        : alongsideMode ? qsTr("Install alongside") : qsTr("Erase disk")

    function fsName(fs) {
        var names = { vfat: "fat32", fat: "fat32", swap: "linuxswap" }
        return names[fs] || fs || ""
    }

    // What Install will do to each partition, from the bridge's simulated plan.
    readonly property var manualPlanRows: {
        var rows = []
        if (!manualMode) return rows
        if (engine.manualNewTable)
            rows.push({ device: qsTr("Partition table:"), target: qsTr("new GPT table, every existing partition is removed") })
        var segments = engine.planPreview
        for (var i = 0; i < segments.length; i++) {
            var seg = segments[i]
            if (seg.kind !== "partition") continue
            var device = seg.newRef ? qsTr("New partition") : (seg.path || "/dev/" + seg.name)
            if (seg.pendingDelete) {
                if (!engine.manualNewTable) rows.push({ device: device, target: qsTr("delete") })
                continue
            }
            if (!seg.mountpoint && !seg.format && !seg.pending) continue
            var parts = []
            if (seg.mountpoint) parts.push(seg.mountpoint)
            parts.push(seg.format ? qsTr("format as %1").arg(fsName(seg.fstype)) : qsTr("keep %1").arg(fsName(seg.fstype)))
            if (seg.pending && !seg.format && !seg.newRef) parts.push(qsTr("resize to %1").arg(Units.humanSize(seg.sizeBytes)))
            rows.push({ device: device, target: parts[0] + (parts.length > 1 ? "  (" + parts.slice(1).join(", ") + ")" : "") })
        }
        return rows
    }

    readonly property string alongsideText: {
        if (!alongsideMode) return ""
        var kept = null, created = null
        var segments = engine.planPreview
        for (var i = 0; i < segments.length; i++) {
            if (segments[i].newRef) created = segments[i]
            else if (segments[i].pending && segments[i].kind === "partition") kept = segments[i]
        }
        if (!kept || !created) return ""
        return qsTr("%1 will be shrunk to %2 and a new %3 partition will be created for %4.")
            .arg(kept.path).arg(Units.humanSize(kept.sizeBytes))
            .arg(Units.humanSize(created.sizeBytes)).arg(branding.name)
    }

    // Theme colors
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color accentColor: "#6b9ce8"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"

    // Reusable "Edit" button - styling only, callers own onClicked.
    component EditButton: Button {
        id: editButton
        text: qsTr("Edit")
        implicitWidth: 64 * root.uiScale
        implicitHeight: 30 * root.uiScale
        Layout.alignment: Qt.AlignVCenter

        background: Rectangle {
            radius: 8
            color: editButton.pressed ? Qt.darker(root.backgroundColor, 1.2) : root.backgroundColor
            border.color: root.accentColor
            border.width: 1

            Behavior on color {
                ColorAnimation { duration: 150 }
            }
        }

        contentItem: Text {
            text: editButton.text
            font.pixelSize: 13 * root.uiScale
            color: root.textColor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // Reusable "Label:   Value" row used inside every section card.
    component FieldRow: RowLayout {
        property string fieldLabel: ""
        property string fieldValue: ""
        Layout.fillWidth: true
        spacing: 10 * root.uiScale

        Text {
            text: fieldLabel
            font.pixelSize: 12 * root.uiScale
            color: root.textMutedColor
            Layout.preferredWidth: 140 * root.uiScale
            Layout.alignment: Qt.AlignTop
            elide: Text.ElideRight
        }

        Text {
            text: fieldValue
            font.pixelSize: 12 * root.uiScale
            font.bold: true
            color: root.textColor
            Layout.fillWidth: true
            wrapMode: Text.Wrap
        }
    }

    // Reusable section card - frosted, same recipe as PartitionView's
    // disk/options cards, nested inside the mega card below.
    component SectionCard: Rectangle {
        default property alias content: sectionColumn.children

        Layout.fillWidth: true
        Layout.preferredHeight: sectionColumn.implicitHeight + 18
        radius: 14
        color: Qt.rgba(root.surfaceColor.r, root.surfaceColor.g, root.surfaceColor.b, 0.55)
        border.color: Qt.rgba(root.primaryColor.r, root.primaryColor.g, root.primaryColor.b, 0.5)
        border.width: 2

        ColumnLayout {
            id: sectionColumn
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: 9
            spacing: 5
        }
    }

    // Content sits directly on Main.qml's shared brand-gradient background,
    // same as LocaleView/PartitionView.
    ScrollView {
        id: scrollView
        anchors.fill: parent
        anchors.margins: 12
        contentWidth: availableWidth
        clip: true

        // Improve wheel scroll speed (3x faster)
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(event) {
                var flickable = scrollView.contentItem as Flickable
                var multiplier = 3.0
                var deltaY = event.angleDelta.y * multiplier
                var newY = flickable.contentY - (deltaY / 120.0 * 40)
                flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, newY))
                event.accepted = true
            }
        }

        // Wrapper sized to at least the viewport height so short content
        // centers vertically instead of hugging the top with dead space
        // below on tall windows; taller content still scrolls normally.
        Item {
            id: scrollContent
            width: scrollView.availableWidth
            implicitHeight: Math.max(pageColumn.implicitHeight, root.height - 2 * scrollView.anchors.margins)
            height: implicitHeight

            ColumnLayout {
                id: pageColumn
                anchors.centerIn: parent
                width: Math.min(parent.width - 80, 940 * uiScale)
                spacing: 8

                // Title
                Text {
                    text: qsTr("Review Installation")
                    font.pixelSize: 22 * root.uiScale
                    font.bold: true
                    color: textColor
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: qsTr("Please review your selections before starting the installation")
                    font.pixelSize: 13 * root.uiScale
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                }

                // ===== Mega card: backdrop panel behind every section card,
                // ending right under the last one (the confirmation card) -
                // no separate glow layer (see PartitionView.qml for why). =====
                Rectangle {
                    id: megaCard
                    Layout.fillWidth: true
                    Layout.preferredHeight: megaCardColumn.implicitHeight + 20
                    radius: 20
                    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                    border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                    border.width: 2

                    // Ambient glow (z: -1 draws it behind the card itself).
                    Repeater {
                        model: 4
                        delegate: Rectangle {
                            readonly property int step: index + 1
                            z: -1
                            anchors.centerIn: parent
                            width: megaCard.width + step * 18
                            height: megaCard.height + step * 18
                            radius: megaCard.radius + step * 9
                            color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.05)
                        }
                    }

                    ColumnLayout {
                        id: megaCardColumn
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 10
                        spacing: 7

                        // ----- System -----
                        SectionCard {
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    RowLayout {
                                        spacing: 12
                                        Text { text: "\u{1F5A5}\u{FE0F}"; font.pixelSize: 15 * root.uiScale }
                                        Text {
                                            text: qsTr("System")
                                            font.pixelSize: 14 * root.uiScale
                                            font.bold: true
                                            color: textColor
                                        }
                                    }

                                    FieldRow { fieldLabel: qsTr("Computer Name:"); fieldValue: hostnameValue || qsTr("Not set") }
                                }

                                EditButton { onClicked: root.editUsers() }
                            }
                        }

                        // ----- Locale & Keyboard -----
                        SectionCard {
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    RowLayout {
                                        spacing: 12
                                        Text { text: "\u{1F310}"; font.pixelSize: 15 * root.uiScale }
                                        Text {
                                            text: qsTr("Locale & Keyboard")
                                            font.pixelSize: 14 * root.uiScale
                                            font.bold: true
                                            color: textColor
                                        }
                                    }

                                    FieldRow { fieldLabel: qsTr("Language:"); fieldValue: localeValue || qsTr("Not set") }
                                    FieldRow { fieldLabel: qsTr("Timezone:"); fieldValue: timezoneValue || qsTr("Not set") }
                                    FieldRow { fieldLabel: qsTr("Keyboard Layout:"); fieldValue: keymapValue || qsTr("Not set") }
                                }

                                EditButton { onClicked: root.editLocale() }
                            }
                        }

                        // ----- User Account -----
                        SectionCard {
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    RowLayout {
                                        spacing: 12
                                        Text { text: "\u{1F464}"; font.pixelSize: 15 * root.uiScale }
                                        Text {
                                            text: qsTr("User Account")
                                            font.pixelSize: 14 * root.uiScale
                                            font.bold: true
                                            color: textColor
                                        }
                                    }

                                    FieldRow { fieldLabel: qsTr("Username:"); fieldValue: usernameValue || qsTr("Not set") }
                                    FieldRow {
                                        fieldLabel: qsTr("Full Name:")
                                        fieldValue: fullNameValue || ""
                                        visible: (fullNameValue || "").length > 0
                                    }
                                }

                                EditButton { onClicked: root.editUsers() }
                            }
                        }

                        // ----- Storage -----
                        SectionCard {
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    RowLayout {
                                        spacing: 12
                                        Text { text: "\u{1F4BE}"; font.pixelSize: 15 * root.uiScale }
                                        Text {
                                            text: qsTr("Storage")
                                            font.pixelSize: 14 * root.uiScale
                                            font.bold: true
                                            color: textColor
                                        }
                                    }

                                    FieldRow {
                                        fieldLabel: qsTr("Installation Disk:")
                                        fieldValue: diskValue
                                            ? (filesystemValue && !manualMode ? diskValue + " > " + filesystemValue.toUpperCase() : diskValue)
                                            : qsTr("Not set")
                                    }
                                    FieldRow {
                                        fieldLabel: qsTr("Size:")
                                        fieldValue: diskSizeValue || qsTr("Unknown")
                                        visible: (diskSizeValue || "").length > 0
                                    }
                                    FieldRow {
                                        fieldLabel: qsTr("Partitioning:")
                                        fieldValue: partitionModeText
                                    }
                                    FieldRow {
                                        visible: alongsideText !== ""
                                        fieldLabel: qsTr("Changes:")
                                        fieldValue: alongsideText
                                    }
                                    FieldRow {
                                        visible: !manualMode
                                        fieldLabel: qsTr("Encryption:")
                                        fieldValue: encryptionValue ? qsTr("Enabled") : qsTr("Disabled")
                                    }
                                    Repeater {
                                        model: manualPlanRows
                                        FieldRow {
                                            fieldLabel: modelData.device
                                            fieldValue: modelData.target
                                        }
                                    }
                                }

                                EditButton { onClicked: root.editPartition() }
                            }
                        }

                        // ----- Final warning + confirmation -----
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: finalWarningColumn.implicitHeight + 16
                            radius: 14
                            color: Qt.rgba(warningColor.r, warningColor.g, warningColor.b, 0.15)
                            border.color: warningColor
                            border.width: 2

                            Column {
                                id: finalWarningColumn
                                anchors.top: parent.top
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.margins: 8
                                spacing: 5

                                Row {
                                    spacing: 10

                                    Text {
                                        text: "\u{1F4E2}"
                                        font.pixelSize: 14 * root.uiScale
                                        anchors.verticalCenter: parent.verticalCenter
                                    }

                                    Text {
                                        text: qsTr("Ready to Install")
                                        font.pixelSize: 14 * root.uiScale
                                        font.bold: true
                                        color: textColor
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }

                                Text {
                                    text: qsTr("The installation will begin once you click the Install button. This process will modify your disk and cannot be undone. Please ensure all data is backed up.")
                                    font.pixelSize: 12 * root.uiScale
                                    color: textColor
                                    wrapMode: Text.WordWrap
                                    width: parent.width
                                }

                                // ITEM 2: mandatory confirmation checkbox. The "Install"
                                // action stays disabled until this is checked.
                                CheckBox {
                                    id: confirmCheckBox
                                    width: parent.width
                                    checked: root.confirmed
                                    onToggled: root.confirmedToggled(checked)
                                    spacing: 8

                                    // Explicit indicator pinned top-left: the default one
                                    // is vertically centred over the wrapped (2-line) label,
                                    // which lands the box in the middle of the text.
                                    indicator: Rectangle {
                                        implicitWidth: 22 * root.uiScale
                                        implicitHeight: 22 * root.uiScale
                                        x: 0
                                        y: 0
                                        radius: 4
                                        color: "transparent"
                                        border.color: confirmCheckBox.checked ? successColor : textMutedColor
                                        border.width: 2

                                        Text {
                                            anchors.centerIn: parent
                                            visible: confirmCheckBox.checked
                                            text: "✓"
                                            color: successColor
                                            font.pixelSize: 15 * root.uiScale
                                            font.bold: true
                                        }
                                    }

                                    contentItem: Text {
                                        text: manualMode || alongsideMode
                                            ? qsTr("I understand that the partitions listed above will be used as shown, and any partition marked for formatting will be erased.")
                                            : qsTr("I understand that the selected disk (%1) will be modified and erased.")
                                                .arg(diskValue || qsTr("target disk"))
                                        font.pixelSize: 12 * root.uiScale
                                        font.bold: true
                                        color: textColor
                                        wrapMode: Text.WordWrap
                                        leftPadding: confirmCheckBox.indicator.width + confirmCheckBox.spacing
                                        width: parent.width
                                    }
                                }
                            }
                        }
                    }
                }

            }
        }
    }
}
