/*
 * OnScreenKey - a single key on OnScreenKeyboard.
 *
 * Restyled port of Calamares' keyboardq/data/Key.qml (same
 * mainLabel/secondaryLabels/keyColor/keyPressedColor property shape, so
 * OnScreenKeyboard.qml can drive it the same way Calamares' own
 * Keyboard.qml drives Key.qml) - adapted to this app's own theme colors
 * instead of Calamares' hardcoded hex values, with the pixmap-background
 * mechanism (keyImageLeft/Right/Center) dropped in favor of a plain
 * rounded Rectangle, and a `flashed` state added for the keyboard test
 * field to highlight a key on physical keypress (see flashKeyForChar in
 * OnScreenKeyboard.qml).
 */

import QtQuick

Item {
    id: key

    property string mainLabel: "A"
    property var secondaryLabels: []

    property color keyColor: "#32373c"
    property color keyBorderColor: "#9CA3AF"
    property color keyPressedColor: "#5597e6"
    property color keyFlashColor: "#6b9ce8"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    property real uiScale: 1.0
    property int keyBounds: 2
    property real mainFontSize: 13 * uiScale
    property real secondaryFontSize: 9 * uiScale

    property bool isChekable: false
    property bool isChecked: false
    property bool flashed: false

    signal clicked()
    signal alternatesClicked(string symbol)

    Timer {
        id: flashTimer
        interval: 250
        onTriggered: key.flashed = false
    }

    onFlashedChanged: if (flashed) flashTimer.restart()

    Rectangle {
        id: backgroundItem
        anchors.fill: parent
        anchors.margins: key.keyBounds
        radius: 6
        color: key.flashed ? key.keyFlashColor
            : (key.isChecked || mouseArea.pressed) ? key.keyPressedColor
            : key.keyColor
        border.color: key.keyBorderColor
        border.width: 1
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    Column {
        anchors.centerIn: backgroundItem
        spacing: 1

        Text {
            anchors.right: parent.right
            visible: secondaryLabels.length > 0
            text: secondaryLabels.length > 0 ? secondaryLabels : ""
            color: key.textMutedColor
            font.pixelSize: key.secondaryFontSize
        }

        Text {
            id: mainLabelItem
            anchors.horizontalCenter: parent.horizontalCenter
            text: key.mainLabel
            color: key.textColor
            font.pixelSize: key.mainFontSize
            font.bold: true
        }
    }

    Row {
        id: alternatesRow
        property int selectedIndex: -1
        visible: false
        anchors.bottom: backgroundItem.top
        anchors.left: backgroundItem.left

        Repeater {
            model: key.secondaryLabels.length

            Rectangle {
                property bool isSelected: alternatesRow.selectedIndex === index
                color: isSelected ? key.keyPressedColor : key.keyColor
                border.color: key.keyBorderColor
                border.width: 1
                radius: 4
                height: backgroundItem.height
                width: backgroundItem.width

                Text {
                    anchors.centerIn: parent
                    text: key.secondaryLabels[index]
                    font: mainLabelItem.font
                    color: key.textColor
                }
            }
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        onPressAndHold: if (key.secondaryLabels.length > 0) alternatesRow.visible = true
        onClicked: {
            if (key.isChekable) key.isChecked = !key.isChecked
            key.clicked()
        }
        onReleased: {
            alternatesRow.visible = false
            if (alternatesRow.selectedIndex > -1)
                key.alternatesClicked(key.secondaryLabels[alternatesRow.selectedIndex])
        }
        onPositionChanged: {
            alternatesRow.selectedIndex =
                (mouseY < 0 && mouseX > 0 && mouseX < alternatesRow.width) ?
                    Math.floor(mouseX / backgroundItem.width) : -1
        }
    }
}
