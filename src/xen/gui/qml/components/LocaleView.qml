/*
 * LocaleView - Timezone (interactive map + search)
 *
 * System language is picked on the Welcome page (as in Calamares), and
 * keyboard layout/variant is its own wizard step - see KeyboardView.qml -
 * matching real Calamares, whose `locale`/`localeq` and `keyboard`/
 * `keyboardq` modules are two separate ViewSteps, never one combined page.
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    signal timezoneSelected(string timezone)

    property var timezonesModel: []

    // Scales fonts/spacing/card width with the window size (see Main.qml) -
    // keeps small windows from clipping content and stops the card from
    // staying a fixed small island on large, high-res displays.
    property real uiScale: 1.0

    // Sélection courante : miroir descendant (lecture seule) de la source de
    // vérité `engine`. Jamais réassigné impérativement (les handlers émettent
    // des signaux consommés par Main.qml qui appelle engine.setX), donc ce
    // binding reste vivant et reflète toujours l'état courant.
    readonly property string selectedTimezone: engine.selectedTimezone

    // Theme colors
    property color primaryColor: "#5597e6"
    property color accentColor: "#6b9ce8"
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

            // Frosted hero card - same recipe as WelcomeView's. Fills the
            // page height (clear of its glow) so the map can take all the
            // room left over by the other controls.
            Rectangle {
                id: heroCard
                anchors.centerIn: parent
                width: Math.min(parent.width - 64, 1100 * uiScale)
                height: Math.max(cardColumn.implicitHeight + 2 * cardColumn.anchors.topMargin,
                                 root.height - 2 * scrollView.anchors.margins - 72)
                radius: 20
                color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                border.width: 2

                ColumnLayout {
                    id: cardColumn
                    anchors.fill: parent
                    anchors.leftMargin: 32
                    anchors.rightMargin: 32
                    anchors.topMargin: 22 * uiScale
                    anchors.bottomMargin: 22 * uiScale
                    spacing: 6

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Text {
                            text: qsTr("Location")
                            font.pixelSize: 20 * uiScale
                            font.bold: true
                            color: textColor
                            Layout.alignment: Qt.AlignHCenter
                        }

                        Text {
                            text: qsTr("Pick your timezone on the map or search for it below")
                            font.pixelSize: 12 * uiScale
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }

                    TimezoneMap {
                        objectName: "timezoneMap"
                        // Takes all the leftover space; the map keeps its own
                        // 2:1 whole-world aspect inside it, centered.
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 150
                        Layout.preferredHeight: 150
                        // Clears the map's drop shadow on both sides.
                        Layout.topMargin: 10 * uiScale
                        Layout.bottomMargin: 8 * uiScale
                        selectedTimezone: root.selectedTimezone
                        uiScale: root.uiScale
                        primaryColor: root.primaryColor
                        accentColor: root.accentColor
                        surfaceColor: root.surfaceColor
                        backgroundColor: root.backgroundColor
                        textColor: root.textColor

                        onTimezonePicked: function(tz) { timezoneSelected(tz) }
                    }

                    Text {
                        text: qsTr("Zoom in on the map to see more locations")
                        font.pixelSize: 12 * uiScale
                        color: textMutedColor
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.bottomMargin: 12 * uiScale
                        horizontalAlignment: Text.AlignHCenter
                    }

                    SearchableComboBox {
                        id: timezoneCombo
                        Layout.fillWidth: true
                        Layout.preferredHeight: 34 * uiScale

                        model: timezonesModel
                        currentValue: selectedTimezone
                        placeholder: qsTr("Select timezone...")
                        searchPlaceholder: qsTr("Search timezones...")

                        uiScale: root.uiScale
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
            }
        }
    }
}
