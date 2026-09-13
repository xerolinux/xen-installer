/*
 * SearchableComboBox - ComboBox with integrated search functionality
 *
 * Features:
 * - Text field for filtering items
 * - Keyboard navigation (arrow keys, Enter)
 * - Case-insensitive search
 * - Highlights matching items
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    // Public properties
    property var model: []
    property string currentValue: ""
    property string placeholder: qsTr("Select...")
    property string searchPlaceholder: qsTr("Type to search...")

    // Theme colors (passed from parent)
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    // Signal emitted when user selects a value
    signal valueSelected(string value)

    // Internal state
    property var filteredModel: model
    property int highlightedIndex: -1

    implicitHeight: 40
    implicitWidth: 200

    // Filter function with enhanced timezone search
    function filterItems(searchText) {
        if (!searchText || searchText.length === 0) {
            filteredModel = model
        } else {
            var lowerSearch = searchText.toLowerCase()
            var filtered = []
            for (var i = 0; i < model.length; i++) {
                var item = model[i].toLowerCase()
                // Direct substring match
                if (item.indexOf(lowerSearch) !== -1) {
                    filtered.push(model[i])
                    continue
                }
                // Enhanced timezone search: match continent or city separately
                if (item.indexOf("/") !== -1) {
                    var parts = item.split("/")
                    for (var j = 0; j < parts.length; j++) {
                        if (parts[j].indexOf(lowerSearch) !== -1) {
                            filtered.push(model[i])
                            break
                        }
                    }
                }
            }
            filteredModel = filtered
        }
        highlightedIndex = filteredModel.length > 0 ? 0 : -1
    }

    // Find display text
    function getDisplayText() {
        if (currentValue && currentValue.length > 0) {
            return currentValue
        }
        return ""
    }

    // Main button
    Rectangle {
        id: mainButton
        anchors.fill: parent
        radius: 8
        color: mouseArea.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
        border.color: popup.visible ? primaryColor : Qt.darker(surfaceColor, 1.2)
        border.width: 2

        Behavior on border.color {
            ColorAnimation { duration: 150 }
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 8
            spacing: 8

            Text {
                Layout.fillWidth: true
                text: getDisplayText() || placeholder
                font.pixelSize: 14
                color: getDisplayText() ? textColor : textMutedColor
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }

            Text {
                text: popup.visible ? "\u25B2" : "\u25BC"
                font.pixelSize: 10
                color: textMutedColor
            }
        }

        MouseArea {
            id: mouseArea
            anchors.fill: parent
            onClicked: {
                if (popup.visible) {
                    popup.close()
                } else {
                    searchField.text = ""
                    filterItems("")
                    popup.open()
                    searchField.forceActiveFocus()
                }
            }
        }
    }

    // Popup with search
    Popup {
        id: popup
        y: mainButton.height + 4
        width: mainButton.width
        height: Math.min(searchField.height + listView.contentHeight + 24, 350)
        padding: 8

        background: Rectangle {
            radius: 8
            color: surfaceColor
            border.color: primaryColor
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: 8

            // Search field
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                radius: 6
                color: backgroundColor
                border.color: searchField.activeFocus ? primaryColor : Qt.darker(surfaceColor, 1.3)
                border.width: 1

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 8

                    Text {
                        text: "\u{1F50D}"
                        font.pixelSize: 14
                        color: textMutedColor
                    }

                    TextField {
                        id: searchField
                        Layout.fillWidth: true
                        placeholderText: searchPlaceholder
                        placeholderTextColor: textMutedColor
                        color: textColor
                        font.pixelSize: 14
                        background: Item {}
                        leftPadding: 0
                        rightPadding: 0

                        onTextChanged: {
                            filterItems(text)
                        }

                        Keys.onDownPressed: {
                            if (highlightedIndex < filteredModel.length - 1) {
                                highlightedIndex++
                                listView.positionViewAtIndex(highlightedIndex, ListView.Contain)
                            }
                        }

                        Keys.onUpPressed: {
                            if (highlightedIndex > 0) {
                                highlightedIndex--
                                listView.positionViewAtIndex(highlightedIndex, ListView.Contain)
                            }
                        }

                        Keys.onReturnPressed: {
                            if (highlightedIndex >= 0 && highlightedIndex < filteredModel.length) {
                                var selectedValue = filteredModel[highlightedIndex]
                                currentValue = selectedValue
                                valueSelected(selectedValue)
                                popup.close()
                            }
                        }

                        Keys.onEscapePressed: {
                            popup.close()
                        }
                    }

                    // Clear button
                    Text {
                        text: "\u2715"
                        font.pixelSize: 12
                        color: searchField.text.length > 0 ? textMutedColor : "transparent"
                        visible: searchField.text.length > 0

                        MouseArea {
                            anchors.fill: parent
                            anchors.margins: -4
                            onClicked: {
                                searchField.text = ""
                                searchField.forceActiveFocus()
                            }
                        }
                    }
                }
            }

            // Results count
            Text {
                Layout.fillWidth: true
                text: filteredModel.length === model.length
                      ? qsTr("%1 items").arg(filteredModel.length)
                      : qsTr("%1 of %2 items").arg(filteredModel.length).arg(model.length)
                font.pixelSize: 11
                color: textMutedColor
                visible: searchField.text.length > 0
            }

            // List view
            ListView {
                id: listView
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: Math.min(contentHeight, 250)
                clip: true
                model: filteredModel

                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                }

                // Improve wheel scroll speed (3x faster)
                WheelHandler {
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    onWheel: function(event) {
                        var multiplier = 3.0
                        var deltaY = event.angleDelta.y * multiplier
                        var newY = listView.contentY - (deltaY / 120.0 * 40)
                        listView.contentY = Math.max(0, Math.min(listView.contentHeight - listView.height, newY))
                        event.accepted = true
                    }
                }

                delegate: Rectangle {
                    width: listView.width
                    height: 36
                    radius: 4
                    color: {
                        if (index === highlightedIndex) {
                            return Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.3)
                        } else if (modelData === currentValue) {
                            return Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.15)
                        } else if (itemMouseArea.containsMouse) {
                            return Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.1)
                        }
                        return "transparent"
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 8

                        Text {
                            Layout.fillWidth: true
                            text: modelData
                            font.pixelSize: 14
                            color: index === highlightedIndex || modelData === currentValue ? textColor : textMutedColor
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }

                        Text {
                            text: "\u2713"
                            font.pixelSize: 14
                            color: primaryColor
                            visible: modelData === currentValue
                        }
                    }

                    MouseArea {
                        id: itemMouseArea
                        anchors.fill: parent
                        hoverEnabled: true

                        onClicked: {
                            currentValue = modelData
                            valueSelected(modelData)
                            popup.close()
                        }

                        onEntered: {
                            highlightedIndex = index
                        }
                    }
                }

                // Empty state
                Text {
                    anchors.centerIn: parent
                    text: qsTr("No results found")
                    font.pixelSize: 14
                    color: textMutedColor
                    visible: filteredModel.length === 0
                }
            }
        }

        onOpened: {
            searchField.forceActiveFocus()
        }
    }
}
