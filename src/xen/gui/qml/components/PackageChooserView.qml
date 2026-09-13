/*
 * PackageChooserView - Optional Package Selection
 *
 * Sidebar rail (every group, flat) + detail panel on the right. Only
 * shown when internet is available - packagechooser.py has nothing to
 * fetch offline. Critical groups are always installed and shown locked.
 *
 * Two kinds of group, both surfaced in the same flat rail list:
 * - Bundle groups (isMultiChoice: false): a single package (e.g. "Mozilla
 *   Firefox") - toggled directly from its rail row, nothing to expand.
 * - Multi-choice groups (isMultiChoice: true): a category (e.g. "Graphics
 *   & Design") - selecting its rail row shows its packages, each with its
 *   own checkbox, in the panel.
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var groups: []

    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    // Which rail row's detail is shown in the panel. Defaults to the
    // first group once the model arrives.
    property string activeGroupId: ""
    readonly property var activeGroup: {
        for (var i = 0; i < groups.length; i++) {
            if (groups[i].id === activeGroupId) return groups[i]
        }
        return groups.length > 0 ? groups[0] : null
    }

    onGroupsChanged: {
        if (groups.length > 0 && activeGroupId === "") {
            activeGroupId = groups[0].id
        }
    }

    // One glyph per group id, matching RequirementChip's emoji-glyph
    // convention (no image assets, renders identically under the
    // software Quick backend).
    function categoryIcon(groupId) {
        switch (groupId) {
            case "internet-browsers": return "🌐"
            case "social-communication": return "💬"
            case "libreoffice": return "📄"
            case "development-tools": return "💻"
            case "password-managers": return "🔐"
            case "android-development": return "🤖"
            case "graphics-design": return "🎨"
            case "audio-tools": return "🎵"
            case "video-production": return "🎬"
            case "additional-fonts": return "🔤"
            default: return "📦"
        }
    }

    // Reusable checkbox visual (indicator only - callers own placement/click).
    component CheckIndicator: Rectangle {
        property bool checked: false
        // `enabled` is inherited from Item - do not redeclare it (shadows
        // the real one and breaks input event handling).

        implicitWidth: 20
        implicitHeight: 20
        radius: 4
        border.color: checked ? primaryColor : textMutedColor
        border.width: 2
        color: checked ? primaryColor : "transparent"
        opacity: enabled ? 1.0 : 0.6

        Text {
            anchors.centerIn: parent
            text: "✓"
            color: textColor
            font.pixelSize: 14
            visible: parent.checked
        }
    }

    // One rail row: bundle groups get an inline checkbox (click toggles
    // it directly, nothing to expand); category groups get a chevron and
    // package count (click selects it as the panel's active category).
    component RailRow: Item {
        id: railRow

        required property var groupData
        readonly property bool multiChoice: !!groupData.isMultiChoice
        readonly property bool active: groupData.id === root.activeGroupId

        Layout.fillWidth: true
        implicitHeight: 40

        Rectangle {
            anchors.fill: parent
            radius: 8
            color: railRow.active ? Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.16) : "transparent"
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 10
            spacing: 10

            Text {
                text: root.categoryIcon(railRow.groupData.id)
                font.pixelSize: 16
                Layout.alignment: Qt.AlignVCenter
            }

            CheckIndicator {
                id: railCheck
                visible: !railRow.multiChoice
                checked: railRow.groupData.critical || railRow.groupData.selected
                enabled: !railRow.groupData.critical

                onCheckedChanged: {
                    if (!railRow.multiChoice && !railRow.groupData.critical) {
                        engine.setPackageGroupSelected(railRow.groupData.id, checked)
                    }
                }
            }

            Text {
                visible: railRow.multiChoice
                text: "▸"
                font.pixelSize: 11
                color: railRow.active ? primaryColor : textMutedColor
            }

            Text {
                text: railRow.groupData.name
                font.pixelSize: 13
                font.bold: railRow.active
                color: railRow.active ? textColor : textMutedColor
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Text {
                visible: railRow.multiChoice
                text: "(" + railRow.groupData.packages.length + ")"
                font.pixelSize: 10
                color: textMutedColor
            }
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                root.activeGroupId = railRow.groupData.id
                if (!railRow.multiChoice && !railRow.groupData.critical) {
                    railCheck.checked = !railCheck.checked
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Text {
            text: qsTr("Optional Software")
            font.pixelSize: 24
            font.bold: true
            color: textColor
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            text: qsTr("Pick any extra software to install alongside the base system")
            font.pixelSize: 13
            color: textMutedColor
            Layout.alignment: Qt.AlignHCenter
        }

        Text {
            visible: root.groups.length === 0
            text: qsTr("No optional software groups configured.")
            font.pixelSize: 13
            color: textMutedColor
            Layout.alignment: Qt.AlignHCenter
            Layout.fillHeight: true
        }

        // Ambient glow + frosted card - same recipe as WelcomeView/
        // LocaleView. Plain stacked rectangles, not a MultiEffect shadow:
        // the installer always runs with QT_QUICK_BACKEND=software, under
        // which shader-based layer.effect never renders.
        Item {
            id: cardWrap
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.maximumWidth: 1000
            Layout.alignment: Qt.AlignHCenter
            visible: root.groups.length > 0

            Repeater {
                model: 4
                delegate: Rectangle {
                    readonly property int step: index + 1
                    anchors.centerIn: heroCard
                    width: heroCard.width + step * 16
                    height: heroCard.height + step * 16
                    radius: heroCard.radius + step * 8
                    color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.05)
                }
            }

            Rectangle {
                id: heroCard
                anchors.fill: parent
                radius: 20
                color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                border.width: 2
                clip: true

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 3
                    spacing: 0

                    // ===== Rail: every group, flat =====
                    ScrollView {
                        id: railScroll
                        Layout.preferredWidth: 264
                        Layout.fillHeight: true
                        contentWidth: availableWidth
                        // ScrollView can't infer content height from a
                        // manually x/y-offset child - without this it
                        // silently clips instead of scrolling past the
                        // viewport (packages list bug).
                        contentHeight: railColumn.implicitHeight + 16
                        clip: true

                        ColumnLayout {
                            id: railColumn
                            width: railScroll.availableWidth - 16
                            x: 8
                            y: 8
                            spacing: 2

                            Repeater {
                                model: root.groups
                                delegate: RailRow {
                                    required property var modelData
                                    groupData: modelData
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.preferredWidth: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 8
                        Layout.bottomMargin: 8
                        color: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.1)
                    }

                    // ===== Panel: active group's detail =====
                    ScrollView {
                        id: panelScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        contentWidth: availableWidth
                        // Same fix as railScroll above - explicit
                        // contentHeight so a long package list actually
                        // scrolls instead of clipping at the bottom.
                        contentHeight: panelColumn.implicitHeight + 40
                        clip: true

                        ColumnLayout {
                            id: panelColumn
                            width: panelScroll.availableWidth - 48
                            x: 24
                            y: 20
                            spacing: 14

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                visible: root.activeGroup !== null

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8

                                    Text {
                                        text: root.activeGroup ? root.categoryIcon(root.activeGroup.id) : ""
                                        font.pixelSize: 18
                                    }

                                    Text {
                                        text: root.activeGroup ? root.activeGroup.name : ""
                                        font.pixelSize: 18
                                        font.bold: true
                                        color: textColor
                                    }

                                    Text {
                                        visible: root.activeGroup && root.activeGroup.critical
                                        text: qsTr("(required)")
                                        font.pixelSize: 12
                                        color: textMutedColor
                                    }
                                }

                                Text {
                                    text: root.activeGroup ? root.activeGroup.description : ""
                                    font.pixelSize: 12
                                    color: textMutedColor
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                    visible: text.length > 0
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                height: 1
                                color: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.08)
                                visible: root.activeGroup && root.activeGroup.isMultiChoice
                            }

                            Rectangle {
                                id: screenshotFrame
                                visible: root.activeGroup && !root.activeGroup.isMultiChoice && root.activeGroup.screenshot !== ""
                                Layout.fillWidth: true
                                // Matches the real screenshot's own aspect ratio so the
                                // frame hugs the image edge-to-edge instead of leaving
                                // empty side gutters at a fixed, unrelated height.
                                Layout.preferredHeight: visible ? width * (1019 / 1876) + 2 : 0
                                color: "transparent"
                                border.color: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.12)
                                border.width: 1
                                clip: true

                                Image {
                                    anchors.fill: parent
                                    anchors.margins: 1
                                    source: (root.activeGroup && root.activeGroup.screenshot !== "")
                                            ? branding.themeIconUrl(root.activeGroup.screenshot) : ""
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                }
                            }

                            Text {
                                visible: root.activeGroup && !root.activeGroup.isMultiChoice && root.activeGroup.details !== ""
                                text: root.activeGroup ? root.activeGroup.details : ""
                                font.pixelSize: 12
                                color: textMutedColor
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                            }

                            Text {
                                visible: root.activeGroup && !root.activeGroup.isMultiChoice && root.activeGroup.screenshot === ""
                                text: qsTr("Single package - toggle it from the list on the left.")
                                font.pixelSize: 12
                                font.italic: true
                                color: textMutedColor
                            }

                            // Individual package checkboxes (multi-choice groups only)
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                visible: root.activeGroup !== null && root.activeGroup.isMultiChoice

                                Repeater {
                                    model: (root.activeGroup && root.activeGroup.isMultiChoice) ? root.activeGroup.packages : []

                                    delegate: Item {
                                        required property var modelData

                                        Layout.fillWidth: true
                                        implicitHeight: pkgRow.implicitHeight

                                        RowLayout {
                                            id: pkgRow
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            spacing: 12

                                            CheckIndicator {
                                                id: pkgCheck
                                                checked: modelData.selected

                                                onCheckedChanged: engine.setPackageSelected(
                                                    root.activeGroup.id,
                                                    modelData.package,
                                                    checked
                                                )
                                            }

                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: 1

                                                Text {
                                                    text: modelData.package
                                                    font.pixelSize: 13
                                                    font.bold: true
                                                    color: textColor
                                                }

                                                Text {
                                                    text: modelData.description
                                                    font.pixelSize: 11
                                                    color: textMutedColor
                                                    wrapMode: Text.WordWrap
                                                    Layout.fillWidth: true
                                                    visible: text.length > 0
                                                }
                                            }
                                        }

                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: pkgCheck.checked = !pkgCheck.checked
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
}
