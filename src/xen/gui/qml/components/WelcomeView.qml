/*
 * WelcomeView - Full welcome screen with wallpaper and requirements overlay
 *
 * Displays:
 * - Wallpaper background
 * - Logo and welcome text
 * - Requirements overlay panel
 * - Install button (enabled when requirements met)
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    // Signals
    signal installClicked()
    signal requirementsChecked()
    signal configureNetworkRequested()
    signal localeSelected(string locale)

    // System language picker (lives here, like Calamares' welcome page,
    // so the rest of the installer is already in the chosen language).
    property var localesModel: []
    property var localesModelNative: []  // [{code, name}]
    // Read-only mirror of engine state; selection goes out through
    // localeSelected and Main.qml calls engine.setSelectedLocale.
    readonly property string selectedLocale: translator.currentLocale + ".UTF-8"

    function nativeNameForLocale(localeCode) {
        for (var i = 0; i < localesModelNative.length; i++) {
            if (localesModelNative[i].code === localeCode)
                return localesModelNative[i].name
        }
        return localeCode
    }

    function localeCodeForNativeName(nativeName) {
        for (var i = 0; i < localesModelNative.length; i++) {
            if (localesModelNative[i].name === nativeName)
                return localesModelNative[i].code
        }
        return nativeName
    }

    function localeDisplayModel() {
        var names = []
        for (var i = 0; i < localesModelNative.length; i++)
            names.push(localesModelNative[i].name)
        return names.length > 0 ? names : localesModel
    }

    // External properties
    property string welcomeWallpaper: ""
    property string logoUrl: ""
    property string welcomeTitle: ""
    property string welcomeSubtitle: ""
    property string installButtonText: ""
    property string brandingEdition: ""
    property bool showRequirements: true
    property var requirements: []
    property bool canProceed: true
    property bool isCheckingRequirements: false
    property string websiteUrl: ""
    property string websiteLabel: ""
    property bool hasInternet: true

    // Theme colors (from branding)
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color accentColor: "#6b9ce8"
    property color accentGradientEnd: "#ED1C24"
    property color backgroundGradientStart: "#5597e6"
    property color backgroundGradientEnd: "#ED1C24"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color successColor: "#10B981"
    property color warningColor: "#F59E0B"
    property color errorColor: "#EF4444"
    readonly property color onGradientTextColor: "#ffffff"

    // Full-screen wallpaper background
    Image {
        id: wallpaperImage
        anchors.fill: parent
        source: welcomeWallpaper
        fillMode: Image.PreserveAspectCrop
        visible: status === Image.Ready

        // Smooth loading animation
        opacity: status === Image.Ready ? 1 : 0
        Behavior on opacity {
            NumberAnimation { duration: 500; easing.type: Easing.OutCubic }
        }
    }

    // Gradient fallback - brand gradient (blue-to-red for Intel/AMD,
    // muted green-to-grey for nvidia - the raw brand green washed across
    // a whole page reads as sickly, not sleek) darkened for a moody
    // backdrop, further dimmed by the dark overlay below.
    Rectangle {
        anchors.fill: parent
        visible: wallpaperImage.status !== Image.Ready
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Qt.darker(backgroundGradientStart, 2.0) }
            GradientStop { position: 1.0; color: Qt.darker(backgroundGradientEnd, 2.0) }
        }
    }

    // Dark overlay for better readability
    Rectangle {
        anchors.fill: parent
        color: backgroundColor
        opacity: 0.4
    }

    // Ambient glow behind the hero card, built from plain stacked
    // rectangles rather than a MultiEffect drop shadow. The installer
    // always runs with QT_QUICK_BACKEND=software (main.py sets it
    // unconditionally - arbitrary live-boot hardware has no guaranteed
    // GL/EGL stack), and shader-based layer.effect never renders under
    // that backend, so a MultiEffect shadow here would never actually be
    // visible on the real installer. Concentric, progressively larger and
    // more transparent rounded rectangles approximate a soft glow purely
    // through alpha compositing, which works on every backend.
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

    Item {
        id: heroArea
        anchors {
            left: parent.left
            right: parent.right
            top: parent.top
            bottom: offlineNotice.visible ? offlineNotice.top : footerBar.top
        }
    }

    // Frosted hero card: contains all content in one calm panel over the
    // dimmed wallpaper, instead of free-floating text.
    Rectangle {
        id: heroCard
        anchors.centerIn: heroArea
        // Never shrinks below the 720 baseline (only grows): the chips'
        // widths come from the card while their text scales with uiScale,
        // so a narrower card at uiScale < 1 would elide their labels.
        width: Math.min(parent.width - 96, 720 * Math.max(1.0, uiScale))
        height: heroColumn.implicitHeight + 72 * uiScale
        radius: 20
        color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
        border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
        border.width: 2

        ColumnLayout {
            id: heroColumn
            anchors.centerIn: parent
            width: parent.width - 72
            spacing: 18

            // Logo - centered
            Item {
                Layout.preferredWidth: 96 * uiScale
                Layout.preferredHeight: 96 * uiScale
                Layout.alignment: Qt.AlignHCenter

                Image {
                    id: logoImage
                    anchors.fill: parent
                    source: logoUrl
                    fillMode: Image.PreserveAspectFit
                    visible: status === Image.Ready
                }

                // Fallback logo placeholder
                Rectangle {
                    anchors.fill: parent
                    radius: 48
                    color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.3)
                    visible: logoImage.status !== Image.Ready

                    Text {
                        anchors.centerIn: parent
                        text: welcomeTitle.charAt(welcomeTitle.indexOf(" ") + 1) || "O"
                        font.pixelSize: 40 * uiScale
                        font.bold: true
                        color: textColor
                    }
                }
            }

            // Welcome text - centered
            Column {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                spacing: 4

                // Edition badge - vendor-branded profiles (nvidia) show
                // their own editionLabel in one color; the Intel/AMD
                // profile has no single vendor to name, so it spells out
                // both, colored individually.
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: brandingEdition
                    font.pixelSize: 13 * uiScale
                    font.bold: true
                    color: primaryColor
                    visible: brandingEdition !== ""
                }

                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: brandingEdition === ""
                    spacing: 2

                    Text {
                        text: "Intel"
                        font.pixelSize: 13 * uiScale
                        font.bold: true
                        color: primaryColor
                    }
                    Text {
                        text: "/"
                        font.pixelSize: 13 * uiScale
                        font.bold: true
                        color: textMutedColor
                    }
                    Text {
                        text: "AMD"
                        font.pixelSize: 13 * uiScale
                        font.bold: true
                        color: accentGradientEnd
                    }
                    Text {
                        text: " " + qsTr("Edition")
                        font.pixelSize: 13 * uiScale
                        font.bold: true
                        color: textColor
                    }
                }

                // Main title: "Welcome to XeroLinux"
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: welcomeTitle
                    font.pixelSize: 27 * uiScale
                    font.bold: true
                    color: textColor
                    horizontalAlignment: Text.AlignHCenter
                }

                // Subtitle
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: welcomeSubtitle
                    font.pixelSize: 14 * uiScale
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            // Requirements - compact chips, chunked into centered rows so an
            // incomplete last row (e.g. 5 checks = rows of 3 + 2) still centers
            // instead of hugging the left edge like a Flow would.
            ColumnLayout {
                id: requirementsColumn
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                spacing: 8
                visible: showRequirements

                readonly property int chipsPerRow: 3
                readonly property var requirementRows: {
                    var rows = []
                    for (var i = 0; i < root.requirements.length; i += chipsPerRow) {
                        rows.push(root.requirements.slice(i, i + chipsPerRow))
                    }
                    return rows
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    visible: isCheckingRequirements
                    text: qsTr("Checking system requirements...")
                    font.pixelSize: 12 * uiScale
                    color: textMutedColor
                }

                Repeater {
                    model: requirementsColumn.requirementRows

                    Row {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 8
                        visible: !isCheckingRequirements

                        Repeater {
                            model: modelData

                            RequirementChip {
                                // Overrides the component's own fixed 210px
                                // default so a row of chipsPerRow always
                                // fits exactly, regardless of card width
                                // tuning. Driven off heroColumn.width, not
                                // requirementsColumn.width: a ColumnLayout
                                // child's Layout.fillWidth measured width
                                // was not resolving correctly here (chips
                                // rendered wider than expected and overflowed
                                // the card) - heroColumn's own width is set
                                // directly from heroCard, not layout-derived,
                                // so it is unambiguous.
                                width: (heroColumn.width - (requirementsColumn.chipsPerRow - 1) * 8) / requirementsColumn.chipsPerRow
                                name: modelData.name || ""
                                description: modelData.description || ""
                                status: modelData.status || "skip"
                                currentValue: modelData.currentValue || ""
                                requiredValue: modelData.requiredValue || ""
                                details: modelData.details || ""

                                surfaceColor: root.surfaceColor
                                textColor: root.textColor
                                textMutedColor: root.textMutedColor
                                successColor: root.successColor
                                warningColor: root.warningColor
                                errorColor: root.errorColor
                                uiScale: root.uiScale

                                onChipClicked: {
                                    if (name === "internet" && (status === "warn" || status === "fail")) {
                                        root.configureNetworkRequested()
                                    }
                                }
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: 4
                spacing: 10

                Text {
                    text: "\u{1F310}"
                    font.pixelSize: 18 * uiScale
                }

                SearchableComboBox {
                    objectName: "welcomeLocaleCombo"
                    Layout.preferredWidth: 320 * uiScale
                    Layout.preferredHeight: 36 * uiScale

                    model: root.localeDisplayModel()
                    currentValue: root.nativeNameForLocale(root.selectedLocale)
                    placeholder: qsTr("Select language...")
                    searchPlaceholder: qsTr("Search languages...")

                    uiScale: root.uiScale
                    primaryColor: root.primaryColor
                    backgroundColor: root.backgroundColor
                    surfaceColor: root.surfaceColor
                    textColor: root.textColor
                    textMutedColor: root.textMutedColor

                    onValueSelected: function(nativeName) {
                        root.localeSelected(root.localeCodeForNativeName(nativeName))
                    }
                }
            }

            Rectangle {
                objectName: "uiLanguageNote"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: noteText.implicitWidth + 24 * uiScale
                Layout.maximumWidth: 600 * uiScale
                Layout.preferredHeight: noteText.implicitHeight + 10 * uiScale
                radius: 10 * uiScale
                color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.15)
                border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                border.width: 1

                Text {
                    id: noteText
                    anchors.centerIn: parent
                    width: Math.min(implicitWidth, 576 * uiScale)
                    text: "\u{2139}\u{FE0F} " + qsTr("Only changes the installer's language. System language and keyboard come next.")
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: 11 * uiScale
                    color: textMutedColor
                }
            }

            // Install button - centered
            Button {
                id: installButton
                Layout.preferredWidth: 220 * uiScale
                Layout.preferredHeight: 52 * uiScale
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: 4
                enabled: canProceed && !isCheckingRequirements

                text: installButtonText || qsTr("Install")
                font.pixelSize: 16 * uiScale
                font.bold: true

                background: Rectangle {
                    radius: 14
                    // Brand gradient: blue-to-red for Intel/AMD, solid-
                    // looking green-to-green for nvidia (primaryColor ==
                    // accentGradientEnd there).
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop {
                            position: 0.0
                            color: installButton.enabled ? primaryColor : Qt.darker(surfaceColor, 1.2)
                        }
                        GradientStop {
                            position: 1.0
                            color: installButton.enabled ? accentGradientEnd : Qt.darker(surfaceColor, 1.2)
                        }
                    }
                    border.color: installButton.enabled ? Qt.rgba(1, 1, 1, 0.25) : "transparent"
                    border.width: 1
                }

                contentItem: Item {
                    implicitWidth: buttonRow.implicitWidth
                    implicitHeight: buttonRow.implicitHeight

                    Row {
                        id: buttonRow
                        anchors.centerIn: parent
                        spacing: 8

                        Text {
                            text: installButton.text
                            font: installButton.font
                            color: installButton.enabled ? root.onGradientTextColor : textMutedColor
                            verticalAlignment: Text.AlignVCenter
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Text {
                            text: "\u2192"  // Right arrow
                            font.pixelSize: 18 * uiScale
                            color: root.onGradientTextColor
                            verticalAlignment: Text.AlignVCenter
                            anchors.verticalCenter: parent.verticalCenter
                            visible: installButton.enabled
                        }
                    }
                }

                onClicked: root.installClicked()
            }

            // Disabled hint
            Text {
                Layout.alignment: Qt.AlignHCenter
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                visible: !canProceed && !isCheckingRequirements
                text: qsTr("Please resolve system requirements before installing")
                font.pixelSize: 12 * uiScale
                color: warningColor
                wrapMode: Text.WordWrap
            }
        }
    }

    // Offline notice - install proceeds either way (no blocking); this just
    // sets expectations: no package chooser, no repo sync during install.
    Text {
        id: offlineNotice
        visible: !root.hasInternet
        width: parent.width - 52
        wrapMode: Text.WordWrap
        anchors {
            bottom: footerBar.top
            bottomMargin: 8
            horizontalCenter: parent.horizontalCenter
        }
        text: qsTr("No internet connection detected: installing offline (no optional software, no system update)")
        font.pixelSize: 12 * uiScale
        color: warningColor
        horizontalAlignment: Text.AlignHCenter
    }

    // Same placement as Main.qml's footer row (20px window margin, 6px text inset).
    Item {
        id: footerBar
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            leftMargin: 26
            rightMargin: 20
            bottomMargin: 20
        }
        height: 40 * uiScale

        RowLayout {
            anchors.fill: parent
            spacing: 16

            // "Powered by Xen Installer" (left)
            Text {
                text: qsTr("Powered by Xen Installer")
                font.pixelSize: 12 * uiScale
                color: textMutedColor
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            // Website URL (right)
            Text {
                id: websiteLink
                text: root.websiteLabel || root.websiteUrl
                font.pixelSize: 12 * uiScale
                color: accentColor
                visible: root.websiteUrl !== ""
                Layout.alignment: Qt.AlignVCenter

                MouseArea {
                    id: footerWebsiteMouseArea
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    hoverEnabled: true
                    onClicked: Qt.openUrlExternally(root.websiteUrl)
                }

                // Underline on hover
                Rectangle {
                    width: parent.width
                    height: 1
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: -2
                    color: accentColor
                    visible: footerWebsiteMouseArea.containsMouse
                }

                opacity: footerWebsiteMouseArea.containsMouse ? 0.8 : 1.0
                Behavior on opacity {
                    NumberAnimation { duration: 150 }
                }
            }
        }
    }

    // Trigger requirements check on component completion
    Component.onCompleted: {
        if (showRequirements) {
            requirementsChecked()
        }
    }
}
