/*
 * RequirementChip - Compact pill showing one requirement check result.
 *
 * Used in a horizontal wrap under the welcome title, one per check
 * (CPU, RAM, storage, internet, ...).
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    signal chipClicked()

    property string name: ""
    property string description: ""
    property string status: "skip"  // pass, warn, fail, skip
    property string currentValue: ""
    property string requiredValue: ""
    property string details: ""

    property real uiScale: 1.0
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"

    readonly property color statusColor: {
        switch (status) {
            case "pass": return successColor
            case "warn": return warningColor
            case "fail": return errorColor
            default: return textMutedColor
        }
    }

    readonly property string statusGlyph: {
        switch (status) {
            case "pass": return "✓"
            case "warn": return "⚠"
            case "fail": return "✗"
            default: return "−"
        }
    }

    readonly property string categoryEmoji: {
        switch (name) {
            case "ram": return "🧠"        // Brain (memory)
            case "disk": return "💾"       // Floppy disk (storage)
            case "cpu_arch": return "🖥"   // Desktop
            case "cpu_cores": return "⚙"        // Gear
            case "efi": return "⚡"              // Lightning
            case "secure_boot": return "🔒" // Lock
            case "internet": return "🌐"   // Globe
            case "power": return "🔌"      // Plug
            case "battery": return "🔋"    // Battery
            case "gpu": return "🎮"        // Gamepad
            default: return "📋"           // Clipboard
        }
    }

    // Fixed size for every chip, regardless of label length, so the row
    // reads as a uniform grid rather than pills of different widths. Wide
    // enough for the longest label ("Internet Connection") without eliding.
    implicitWidth: 210 * uiScale
    implicitHeight: 68 * uiScale
    radius: 14
    color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
    border.color: Qt.rgba(statusColor.r, statusColor.g, statusColor.b, 0.4)
    border.width: 1

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12 * root.uiScale
        spacing: 10 * root.uiScale

        Text {
            text: root.categoryEmoji
            font.pixelSize: 20 * root.uiScale
            Layout.alignment: Qt.AlignVCenter
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 2

            Text {
                Layout.fillWidth: true
                text: root.description || root.name
                font.pixelSize: 13 * root.uiScale
                font.bold: true
                color: root.textColor
                elide: Text.ElideRight
            }

            Text {
                Layout.fillWidth: true
                text: root.currentValue
                font.pixelSize: 11 * root.uiScale
                color: root.textMutedColor
                elide: Text.ElideRight
                visible: root.currentValue !== ""
            }
        }

        Rectangle {
            Layout.preferredWidth: 20 * root.uiScale
            Layout.preferredHeight: 20 * root.uiScale
            Layout.alignment: Qt.AlignVCenter
            radius: width / 2
            color: Qt.rgba(root.statusColor.r, root.statusColor.g, root.statusColor.b, 0.2)

            Text {
                anchors.centerIn: parent
                text: root.statusGlyph
                font.pixelSize: 11 * root.uiScale
                font.bold: true
                color: root.statusColor
            }
        }
    }

    // Combines the check's details with its current/required values (when
    // present) into one tooltip string.
    readonly property string tooltipText: {
        var parts = []
        if (currentValue !== "") {
            parts.push(requiredValue !== "" ? qsTr("%1 / required %2").arg(currentValue).arg(requiredValue) : currentValue)
        }
        if (details !== "") {
            parts.push(details)
        }
        return parts.join(" · ")
    }

    MouseArea {
        id: hoverArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.tooltipText !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.chipClicked()
    }

    ToolTip {
        visible: hoverArea.containsMouse && root.tooltipText !== ""
        delay: 300
        timeout: 8000
        text: root.tooltipText
    }
}
