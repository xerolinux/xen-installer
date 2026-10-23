/*
 * KeyboardView - Keyboard layout/variant selection with live preview + test.
 *
 * A separate wizard step from Locale, matching real Calamares (its
 * `locale`/`localeq` module and `keyboard`/`keyboardq` module are two
 * distinct ViewSteps/pages, never combined into one) - this used to be
 * folded into LocaleView.qml as one oversized page; split out here.
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    signal keymapSelected(string keymap)
    signal keyboardVariantSelected(string variant)

    property var keymapsModel: []
    property var keyboardVariantsModel: []

    property real uiScale: 1.0

    readonly property string selectedKeymap: engine.selectedKeymap
    readonly property string selectedKeyboardVariant: engine.selectedKeyboardVariant

    readonly property string defaultVariantLabel: qsTr("Default")

    property color primaryColor: "#5597e6"
    property color accentColor: "#6b9ce8"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    ScrollView {
        id: scrollView
        anchors.fill: parent
        anchors.margins: 12
        contentWidth: availableWidth
        clip: true

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

        Item {
            width: scrollView.availableWidth
            implicitHeight: Math.max(heroCard.height + 72, root.height - 2 * scrollView.anchors.margins)
            height: implicitHeight

            Repeater {
                model: 4
                delegate: Rectangle {
                    readonly property int step: index + 1
                    anchors.centerIn: heroCard
                    width: heroCard.width + step * 18
                    height: heroCard.height + step * 18
                    radius: heroCard.radius + step * 9
                    color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.05)
                }
            }

            Rectangle {
                id: heroCard
                anchors.centerIn: parent
                width: Math.min(parent.width - 64, 1100 * uiScale)
                height: cardColumn.implicitHeight + 48 * uiScale
                radius: 20
                color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                border.width: 2

                ColumnLayout {
                    id: cardColumn
                    anchors.centerIn: parent
                    width: parent.width - 64
                    spacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Text {
                            text: qsTr("Keyboard Configuration")
                            font.pixelSize: 20 * uiScale
                            font.bold: true
                            color: textColor
                            Layout.alignment: Qt.AlignHCenter
                        }

                        Text {
                            text: qsTr("Select your keyboard layout and variant")
                            font.pixelSize: 12 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }

                    // Preview first, like Calamares' own KeyboardPage.ui
                    // (KBPreviewLayout sits at the very top, above the
                    // layout/variant pickers).
                    OnScreenKeyboard {
                        id: oskeyboard
                        objectName: "onScreenKeyboard"
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: Math.min(560 * uiScale, parent ? parent.width : 560 * uiScale)
                        Layout.topMargin: 10
                        uiScale: root.uiScale
                        xkbLayout: selectedKeymap
                        xkbVariant: selectedKeyboardVariant
                        target: keyboardTestField
                        primaryColor: root.primaryColor
                        surfaceColor: root.surfaceColor
                        backgroundColor: root.backgroundColor
                        textColor: root.textColor
                        textMutedColor: root.textMutedColor
                    }

                    TextField {
                        id: keyboardTestField
                        objectName: "keyboardTestField"
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: Math.min(560 * uiScale, parent ? parent.width : 560 * uiScale)
                        Layout.topMargin: 4
                        Layout.preferredHeight: 36 * uiScale
                        placeholderText: qsTr("Type here to test your keyboard…")
                        placeholderTextColor: textMutedColor
                        color: textColor
                        font.pixelSize: 14 * uiScale
                        leftPadding: 12
                        rightPadding: 12
                        background: Rectangle {
                            radius: 8
                            color: backgroundColor
                            border.color: keyboardTestField.activeFocus ? primaryColor : Qt.darker(surfaceColor, 1.2)
                            border.width: 2
                        }
                        onTextChanged: if (text.length > 0) oskeyboard.flashKeyForChar(text.charAt(text.length - 1))

                        // Keeps Main.qml's Return/Escape wizard shortcuts from
                        // firing while the user is testing those keys here.
                        Keys.onShortcutOverride: function(event) {
                            event.accepted = event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Escape
                        }
                        Keys.onReturnPressed: function(event) { event.accepted = true }
                        Keys.onEnterPressed: function(event) { event.accepted = true }
                        Keys.onEscapePressed: function(event) { event.accepted = true }
                    }

                    RowLayout {
                        Layout.topMargin: 14
                        Layout.fillWidth: true
                        spacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 100
                            spacing: 4

                            Text {
                                text: qsTr("Layout")
                                font.pixelSize: 11 * uiScale
                                font.bold: true
                                color: textMutedColor
                            }

                            SearchableComboBox {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32 * uiScale

                                model: keymapsModel
                                currentValue: selectedKeymap
                                placeholder: qsTr("Select...")
                                searchPlaceholder: qsTr("Search layouts...")

                                uiScale: root.uiScale
                                primaryColor: root.primaryColor
                                backgroundColor: root.backgroundColor
                                surfaceColor: root.surfaceColor
                                textColor: root.textColor
                                textMutedColor: root.textMutedColor

                                onValueSelected: function(value) {
                                    // Main.qml's onKeymapSelected already calls
                                    // engine.applyKeyboardLayout() in response to
                                    // this signal - calling it again here would
                                    // just double-apply the same layout.
                                    keymapSelected(value)
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 100
                            spacing: 4

                            Text {
                                text: qsTr("Variant")
                                font.pixelSize: 11 * uiScale
                                font.bold: true
                                color: textMutedColor
                            }

                            SearchableComboBox {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32 * uiScale

                                model: keyboardVariantsModel.map(v => v === "" ? root.defaultVariantLabel : v)
                                currentValue: selectedKeyboardVariant === "" ? root.defaultVariantLabel : selectedKeyboardVariant
                                placeholder: root.defaultVariantLabel
                                searchPlaceholder: qsTr("Search variants...")

                                uiScale: root.uiScale
                                primaryColor: root.primaryColor
                                backgroundColor: root.backgroundColor
                                surfaceColor: root.surfaceColor
                                textColor: root.textColor
                                textMutedColor: root.textMutedColor

                                onValueSelected: function(value) {
                                    keyboardVariantSelected(value === root.defaultVariantLabel ? "" : value)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
