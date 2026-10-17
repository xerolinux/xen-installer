/*
 * LocaleView - Locale, Timezone, and Keyboard Configuration
 *
 * Displays:
 * - System locale selection
 * - Timezone selection with search
 * - Keyboard layout selection
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

Item {
    id: root

    // Signals
    signal localeSelected(string locale)
    signal timezoneSelected(string timezone)
    signal keymapSelected(string keymap)
    signal keyboardVariantSelected(string variant)

    // External properties (data models)
    property var localesModel: []
    property var localesModelNative: []  // Array of {code: string, name: string}
    property var timezonesModel: []
    property var keymapsModel: []
    property var keyboardVariantsModel: []

    // Scales fonts/spacing/card width with the window size (see Main.qml) -
    // keeps small windows from clipping content and stops the card from
    // staying a fixed small island on large, high-res displays.
    property real uiScale: 1.0

    // Helper function to get native name from locale code
    function getNativeNameForLocale(localeCode) {
        for (var i = 0; i < localesModelNative.length; i++) {
            if (localesModelNative[i].code === localeCode) {
                return localesModelNative[i].name
            }
        }
        return localeCode  // Fallback to code if not found
    }

    // Helper function to get locale code from native name
    function getLocaleCodeForNativeName(nativeName) {
        for (var i = 0; i < localesModelNative.length; i++) {
            if (localesModelNative[i].name === nativeName) {
                return localesModelNative[i].code
            }
        }
        return nativeName  // Fallback to name if not found
    }

    // Get display model for locale combo (native names)
    function getLocaleDisplayModel() {
        var names = []
        for (var i = 0; i < localesModelNative.length; i++) {
            names.push(localesModelNative[i].name)
        }
        return names.length > 0 ? names : localesModel
    }

    // Sélections courantes : miroirs descendants (lecture seule) de la source
    // de vérité `engine`. Jamais réassignés impérativement (les handlers
    // émettent des signaux consommés par Main.qml qui appelle engine.setX),
    // donc ces bindings restent vivants et reflètent toujours l'état courant.
    readonly property string selectedLocale: engine.selectedLocale
    readonly property string selectedTimezone: engine.selectedTimezone
    readonly property string selectedKeymap: engine.selectedKeymap
    readonly property string selectedKeyboardVariant: engine.selectedKeyboardVariant

    // Theme colors
    property color primaryColor: "#5597e6"
    property color secondaryColor: "#3a7bc8"
    property color accentColor: "#6b9ce8"
    property color accentGradientEnd: "#ED1C24"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    // Content sits directly on Main.qml's shared brand-gradient background -
    // the frosted card below provides its own contrast, so no separate
    // full-view dark overlay is needed here.
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
                var flickable = scrollView.contentItem
                var multiplier = 3.0
                var deltaY = event.angleDelta.y * multiplier
                var newY = flickable.contentY - (deltaY / 120.0 * 40)
                flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, newY))
                event.accepted = true
            }
        }

        // Wrapper sized to at least the viewport height so a short card
        // centers vertically instead of hugging the top with dead space
        // below on tall windows; a taller card still scrolls normally.
        Item {
            width: scrollView.availableWidth
            height: Math.max(heroCard.height + 24, scrollView.availableHeight)

            // Ambient glow behind the card - plain stacked rectangles, not a
            // MultiEffect shadow: the installer always runs with
            // QT_QUICK_BACKEND=software (see WelcomeView.qml), under which
            // shader-based layer.effect never renders.
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

            // Frosted hero card - same recipe as WelcomeView's.
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

                    // Title section
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Text {
                            text: qsTr("Country & Language")
                            font.pixelSize: 20 * uiScale
                            font.bold: true
                            color: textColor
                            Layout.alignment: Qt.AlignHCenter
                        }

                        Text {
                            text: qsTr("Configure your system language, timezone, and keyboard layout")
                            font.pixelSize: 12 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }

                    // ===== System language =====
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        spacing: 5

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            Text {
                                text: "\u{1F310}"
                                font.pixelSize: 18 * uiScale
                            }

                            Text {
                                text: qsTr("System Language")
                                font.pixelSize: 14 * uiScale
                                font.bold: true
                                color: textColor
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Select your preferred system language and locale")
                            font.pixelSize: 10 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                        }

                        SearchableComboBox {
                            id: localeCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34 * uiScale

                            model: getLocaleDisplayModel()
                            currentValue: getNativeNameForLocale(selectedLocale)
                            placeholder: qsTr("Select language...")
                            searchPlaceholder: qsTr("Search languages...")

                            primaryColor: root.primaryColor
                            backgroundColor: root.backgroundColor
                            surfaceColor: root.surfaceColor
                            textColor: root.textColor
                            textMutedColor: root.textMutedColor

                            onValueSelected: function(nativeName) {
                                // Convert native name back to locale code.
                                // On n'assigne PAS selectedLocale (miroir
                                // readonly=engine) : Main.qml relaie vers
                                // engine.setSelectedLocale via localeSelected.
                                var localeCode = getLocaleCodeForNativeName(nativeName)
                                localeSelected(localeCode)
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.topMargin: 3
                        Layout.bottomMargin: 3
                        height: 1
                        color: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.08)
                    }

                    // ===== Timezone =====
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            Text {
                                text: "\u{1F30D}"
                                font.pixelSize: 18 * uiScale
                            }

                            Text {
                                text: qsTr("Timezone")
                                font.pixelSize: 14 * uiScale
                                font.bold: true
                                color: textColor
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Select your timezone for accurate time display")
                            font.pixelSize: 10 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                        }

                        SearchableComboBox {
                            id: timezoneCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34 * uiScale

                            model: timezonesModel
                            currentValue: selectedTimezone
                            placeholder: qsTr("Select timezone...")
                            searchPlaceholder: qsTr("Search timezones...")

                            primaryColor: root.primaryColor
                            backgroundColor: root.backgroundColor
                            surfaceColor: root.surfaceColor
                            textColor: root.textColor
                            textMutedColor: root.textMutedColor

                            onValueSelected: function(value) {
                                timezoneSelected(value)
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.topMargin: 3
                        Layout.bottomMargin: 3
                        height: 1
                        color: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.08)
                    }

                    // ===== Keyboard layout & variant (combined, side-by-side) =====
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            Text {
                                text: "\u{2328}\u{FE0F}"
                                font.pixelSize: 18 * uiScale
                            }

                            Text {
                                text: qsTr("Keyboard Configuration")
                                font.pixelSize: 14 * uiScale
                                font.bold: true
                                color: textColor
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Select your keyboard layout and variant")
                            font.pixelSize: 10 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                        }

                        // Side-by-side layout (50/50)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            // Left: Keyboard Layout (50%)
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
                                    id: keymapCombo
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32 * uiScale

                                    model: keymapsModel
                                    currentValue: selectedKeymap
                                    placeholder: qsTr("Select...")
                                    searchPlaceholder: qsTr("Search layouts...")

                                    primaryColor: root.primaryColor
                                    backgroundColor: root.backgroundColor
                                    surfaceColor: root.surfaceColor
                                    textColor: root.textColor
                                    textMutedColor: root.textMutedColor

                                    onValueSelected: function(value) {
                                        keymapSelected(value)
                                    }
                                }
                            }

                            // Right: Keyboard Variant (50%)
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

                                ComboBox {
                                    id: variantCombo
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32 * uiScale

                                    model: keyboardVariantsModel
                                    currentIndex: {
                                        const idx = keyboardVariantsModel.indexOf(selectedKeyboardVariant)
                                        return idx >= 0 ? idx : 0
                                    }

                                    background: Rectangle {
                                        color: backgroundColor
                                        radius: 6
                                        border.color: variantCombo.activeFocus ? primaryColor : Qt.rgba(textColor.r, textColor.g, textColor.b, 0.2)
                                        border.width: variantCombo.activeFocus ? 2 : 1
                                    }

                                    contentItem: Text {
                                        leftPadding: 12
                                        text: variantCombo.displayText || qsTr("Default")
                                        color: textColor
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                    }

                                    delegate: ItemDelegate {
                                        width: variantCombo.width
                                        contentItem: Text {
                                            text: modelData || qsTr("Default")
                                            color: textColor
                                            elide: Text.ElideRight
                                        }
                                        highlighted: variantCombo.highlightedIndex === index
                                        background: Rectangle {
                                            color: highlighted ? primaryColor : "transparent"
                                        }
                                    }

                                    popup: Popup {
                                        y: variantCombo.height
                                        width: variantCombo.width
                                        implicitHeight: contentItem.implicitHeight
                                        padding: 1

                                        contentItem: ListView {
                                            clip: true
                                            implicitHeight: Math.min(contentHeight, 200)
                                            model: variantCombo.popup.visible ? variantCombo.delegateModel : null
                                            currentIndex: variantCombo.highlightedIndex
                                            ScrollIndicator.vertical: ScrollIndicator {}
                                        }

                                        background: Rectangle {
                                            color: surfaceColor
                                            border.color: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.2)
                                            radius: 6
                                        }
                                    }

                                    onActivated: function(index) {
                                        const variant = keyboardVariantsModel[index]
                                        keyboardVariantSelected(variant)
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
