/*
 * FinishedView - Installation Completion
 *
 * Displays:
 * - Success/failure status with icon
 * - Installation summary
 * - Action buttons (Reboot/Shutdown/Continue)
 * - Error details for failed installations
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "finishedView"

    // Signals
    signal rebootClicked()
    signal shutdownClicked()
    signal continueClicked()
    signal retryClicked()
    signal viewLogsClicked()

    // External properties
    property bool success: true
    property string errorMessage: ""
    property var summary: ({})  // Installation summary from engine
    property var installationSummary: summary  // Alias for backwards compatibility

    // Distro info
    property string distroName: ""
    property string distroLogo: ""
    property string websiteUrl: ""
    property string websiteLabel: ""

    // Branded, translated finished-screen copy (falls back to generic text
    // below if a profile doesn't set these).
    property string finishedTitle: ""
    property string finishedSubtitle: ""
    property string finishedMessage: ""

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
    property color errorColor: "#EF4444"

    // Log upload state (bouton "Send Logs" en état échec)
    property bool logUploadInProgress: false
    property string logUploadUrl: ""
    property string logUploadError: ""

    Connections {
        target: engine
        function onLogUploadFinished(url, ok, error) {
            root.logUploadInProgress = false
            if (ok) {
                root.logUploadUrl = url
                root.logUploadError = ""
            } else {
                root.logUploadUrl = ""
                root.logUploadError = error
            }
        }
    }

    ScrollView {
        id: pageScroll
        anchors.fill: parent
        anchors.bottomMargin: footerBar.height + footerBar.anchors.bottomMargin
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Item {

            id: scrollContent

            width: pageScroll.availableWidth
            implicitHeight: Math.max(pageColumn.implicitHeight + 32 * uiScale, root.height - pageScroll.anchors.bottomMargin)
            height: implicitHeight

        ColumnLayout {
            id: pageColumn
            anchors.centerIn: parent
            width: parent.width - 80 * uiScale
            spacing: 10 * uiScale

            // Mega card: icon, message, summary/error details and the
            // action buttons all nested inside one frosted panel, same
            // recipe as PartitionView/SummaryView, ending cleanly right
            // under the last button instead of floating loose.
            Rectangle {
                id: megaCard
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: 960 * uiScale
                Layout.preferredHeight: megaCardColumn.implicitHeight + 32 * uiScale
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
                anchors.margins: 16 * uiScale
                spacing: 14 * uiScale

            // Status icon and message
            Column {
                Layout.alignment: Qt.AlignHCenter
                spacing: 10

                // Distro logo, always shown (success or failure) - a small
                // status badge in its lower-right corner carries the actual
                // result instead of the old big animated check/X circle.
                Item {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: finishedLogo.status === Image.Ready ? finishedLogo.width : statusBadge.width
                    height: finishedLogo.status === Image.Ready ? finishedLogo.height : statusBadge.height

                    Image {
                        id: finishedLogo
                        source: distroLogo
                        fillMode: Image.PreserveAspectFit
                        sourceSize.height: height
                        height: 96 * uiScale
                    }

                    Rectangle {
                        id: statusBadge
                        width: 30 * uiScale
                        height: 30 * uiScale
                        radius: width / 2
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.rightMargin: -4
                        anchors.bottomMargin: -4
                        color: success ? successColor : errorColor
                        border.color: backgroundColor
                        border.width: 2

                        scale: 0
                        SequentialAnimation on scale {
                            id: badgeAnimation
                            running: false
                            PauseAnimation { duration: 200 }
                            NumberAnimation { from: 0; to: 1; duration: 350; easing.type: Easing.OutBack }
                        }

                        Text {
                            anchors.centerIn: parent
                            text: success ? "✓" : "✕"
                            color: textColor
                            font.pixelSize: 16 * uiScale
                            font.bold: true
                        }
                    }
                }

                // Status text - no drop shadow (MultiEffect never renders
                // under this installer's always-on software Quick backend
                // and previously took the whole Text down with it).
                Text {
                    text: success ? (finishedTitle || qsTr("Installation Complete!")) : qsTr("Installation Failed")
                    font.pixelSize: 26 * uiScale
                    font.bold: true
                    color: textColor
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: success ?
                          (finishedSubtitle || qsTr("The system has been successfully installed on your computer")) :
                          qsTr("An error occurred during installation")
                    font.pixelSize: 13 * uiScale
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                    anchors.horizontalCenter: parent.horizontalCenter
                    horizontalAlignment: Text.AlignHCenter
                    width: Math.min(500 * uiScale, megaCardColumn.width)
                }
            }

            // Summary/Error details
            Rectangle {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: 900 * uiScale
                Layout.preferredHeight: detailsColumn.implicitHeight + 24 * uiScale
                radius: 14
                color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                border.width: 2

                Column {
                    id: detailsColumn
                    anchors.fill: parent
                    anchors.margins: 12 * uiScale
                    spacing: 10 * uiScale

                    // Success summary
                    Column {
                        width: parent.width
                        spacing: 10
                        visible: success

                        Row {
                            spacing: 12

                            Text {
                                text: "\u{1F389}"  // Party popper
                                font.pixelSize: 18 * uiScale
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("Installation Summary")
                                font.pixelSize: 15 * uiScale
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        // Summary items
                        Column {
                            width: parent.width
                            spacing: 8

                            Row {
                                width: parent.width
                                spacing: 10

                                Text {
                                    text: qsTr("Distribution:")
                                    font.pixelSize: 12 * uiScale
                                    color: textMutedColor
                                    width: 140 * uiScale
                                }

                                Text {
                                    text: installationSummary.distribution || installationSummary.distroName || "Linux"
                                    font.pixelSize: 12 * uiScale
                                    font.bold: true
                                    color: textColor
                                }
                            }

                            Row {
                                width: parent.width
                                spacing: 10

                                Text {
                                    text: qsTr("Installation Target:")
                                    font.pixelSize: 12 * uiScale
                                    color: textMutedColor
                                    width: 140 * uiScale
                                }

                                Text {
                                    text: installationSummary.targetDisk || qsTr("Unknown")
                                    font.pixelSize: 12 * uiScale
                                    font.bold: true
                                    color: textColor
                                }
                            }

                            Row {
                                width: parent.width
                                spacing: 10

                                Text {
                                    text: qsTr("Installation Time:")
                                    font.pixelSize: 12 * uiScale
                                    color: textMutedColor
                                    width: 140 * uiScale
                                }

                                Text {
                                    text: installationSummary.installationTime || qsTr("Unknown")
                                    font.pixelSize: 12 * uiScale
                                    font.bold: true
                                    color: textColor
                                }
                            }
                        }
                    }

                    // Error details
                    Column {
                        width: parent.width
                        spacing: 10
                        visible: !success

                        Row {
                            spacing: 12

                            Text {
                                text: "\u{1F6A8}"  // Police car light
                                font.pixelSize: 18 * uiScale
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("Error Details")
                                font.pixelSize: 15 * uiScale
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        Rectangle {
                            width: parent.width
                            height: errorText.implicitHeight + 20
                            radius: 8
                            color: Qt.rgba(errorColor.r, errorColor.g, errorColor.b, 0.15)
                            border.color: errorColor
                            border.width: 1

                            Text {
                                id: errorText
                                anchors.fill: parent
                                anchors.margins: 10
                                text: errorMessage || qsTr("An unknown error occurred during installation")
                                font.pixelSize: 12 * uiScale
                                font.family: branding.fontMonospace
                                color: textColor
                                wrapMode: Text.Wrap
                            }
                        }

                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 12

                            Button {
                                text: qsTr("View Full Logs")
                                implicitHeight: 30 * uiScale

                                background: Rectangle {
                                    radius: 8
                                    color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                                    border.color: textMutedColor
                                    border.width: 1
                                }

                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 12 * uiScale
                                    color: textColor
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                onClicked: root.viewLogsClicked()
                            }

                            Button {
                                text: qsTr("Send Logs")
                                implicitHeight: 30 * uiScale
                                enabled: !root.logUploadInProgress

                                background: Rectangle {
                                    radius: 8
                                    color: {
                                        if (!parent.enabled) return Qt.darker(errorColor, 1.4)
                                        if (parent.pressed) return Qt.darker(errorColor, 1.2)
                                        if (parent.hovered) return Qt.lighter(errorColor, 1.1)
                                        return errorColor
                                    }
                                    border.color: Qt.lighter(errorColor, 1.3)
                                    border.width: 1
                                }

                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 12 * uiScale
                                    color: textColor
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                onClicked: {
                                    root.logUploadInProgress = true
                                    root.logUploadUrl = ""
                                    root.logUploadError = ""
                                    engine.uploadInstallLog()
                                }
                            }
                        }

                        // Avertissement discret : le journal part vers un service
                        // de collage public (pas de stockage local persistant).
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: qsTr("The log will be sent to a public paste service.")
                            font.pixelSize: 11 * uiScale
                            font.italic: true
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                            width: Math.min(400 * uiScale, detailsColumn.width)
                            horizontalAlignment: Text.AlignHCenter
                        }

                        // Résultat de l'envoi : en cours, URL sélectionnable + copie, ou erreur
                        Column {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 8
                            visible: root.logUploadInProgress || root.logUploadUrl.length > 0 || root.logUploadError.length > 0

                            Text {
                                visible: root.logUploadInProgress
                                text: qsTr("Sending…")
                                font.pixelSize: 11 * uiScale
                                color: textMutedColor
                            }

                            Row {
                                visible: !root.logUploadInProgress && root.logUploadUrl.length > 0
                                spacing: 8

                                TextField {
                                    id: logUploadUrlField
                                    width: 300 * uiScale
                                    readOnly: true
                                    selectByMouse: true
                                    text: root.logUploadUrl
                                    color: textColor
                                    font.pixelSize: 12 * uiScale

                                    // Le style Fusion par défaut rend le champ
                                    // avec un fond clair (palette embossée),
                                    // ce qui rend `color: textColor` (texte
                                    // clair) illisible sur thème sombre. Fond
                                    // explicite pour garantir le contraste.
                                    background: Rectangle {
                                        color: backgroundColor
                                        radius: 8
                                        border.color: textMutedColor
                                        border.width: 1
                                    }
                                }

                                Button {
                                    text: qsTr("Copy")
                                    implicitHeight: 30 * uiScale

                                    background: Rectangle {
                                        radius: 8
                                        color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                                        border.color: textMutedColor
                                        border.width: 1
                                    }

                                    contentItem: Text {
                                        text: parent.text
                                        font.pixelSize: 12 * uiScale
                                        color: textColor
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                    }

                                    onClicked: {
                                        logUploadUrlField.selectAll()
                                        logUploadUrlField.copy()
                                        logUploadUrlField.deselect()
                                    }
                                }
                            }

                            Text {
                                visible: !root.logUploadInProgress && root.logUploadError.length > 0
                                text: qsTr("Sending failed: ") + root.logUploadError
                                font.pixelSize: 11 * uiScale
                                color: errorColor
                                wrapMode: Text.WordWrap
                                width: Math.min(400 * uiScale, detailsColumn.width)
                                horizontalAlignment: Text.AlignHCenter
                            }
                        }
                    }
                }
            }

            // Action buttons
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: 900 * uiScale
                spacing: 8 * uiScale

                // Success actions
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16
                    visible: success

                    Button {
                        id: rebootButton
                        text: qsTr("Reboot Now")
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44 * uiScale
                        font.pixelSize: 14 * uiScale
                        font.bold: true

                        background: Rectangle {
                            radius: 12
                            color: {
                                if (parent.pressed) return Qt.darker(primaryColor, 1.3)
                                if (parent.hovered) return Qt.lighter(primaryColor, 1.15)
                                return primaryColor
                            }
                            border.color: Qt.lighter(primaryColor, 1.3)
                            border.width: 2

                            Behavior on color {
                                ColorAnimation { duration: 150 }
                            }

                            Rectangle {
                                anchors.fill: parent
                                radius: parent.radius
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.15) }
                                    GradientStop { position: 0.5; color: "transparent" }
                                }
                            }
                        }

                        // contentItem est positionné/dimensionné par le Control
                        // (x/y/width/height liés à padding + availableWidth/Height).
                        // Un Row utilisé DIRECTEMENT comme contentItem ne doit pas
                        // porter `anchors.centerIn: parent` : cela entre en
                        // conflit avec les bindings internes du Control sur x/y,
                        // d'où le texte/icône mal centrés observés. On enveloppe
                        // donc le Row dans un Item neutre (qui reçoit les
                        // bindings du Control sans conflit) et on centre le Row
                        // à l'intérieur de cet Item.
                        contentItem: Item {
                            implicitWidth: rebootContentRow.implicitWidth
                            implicitHeight: rebootContentRow.implicitHeight

                            Row {
                                id: rebootContentRow
                                anchors.centerIn: parent
                                spacing: 8

                                Text {
                                    text: "\u{1F504}"  // Counterclockwise arrows
                                    font.pixelSize: 16 * uiScale
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: rebootButton.text
                                    font: rebootButton.font
                                    color: textColor
                                    verticalAlignment: Text.AlignVCenter
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }

                        onClicked: root.rebootClicked()
                    }

                    Button {
                        id: shutdownButton
                        text: qsTr("Shutdown")
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44 * uiScale
                        font.pixelSize: 14 * uiScale

                        background: Rectangle {
                            radius: 12
                            color: parent.pressed ? Qt.darker(surfaceColor, 1.2) : surfaceColor
                            border.color: textMutedColor
                            border.width: 2

                            Behavior on color {
                                ColorAnimation { duration: 150 }
                            }
                        }

                        contentItem: Item {
                            implicitWidth: shutdownContentRow.implicitWidth
                            implicitHeight: shutdownContentRow.implicitHeight

                            Row {
                                id: shutdownContentRow
                                anchors.centerIn: parent
                                spacing: 8

                                Text {
                                    // Glyphe monochrome (dingbat, pas un emoji
                                    // couleur) : sans `color:` explicite il
                                    // hérite du noir par défaut de Text et
                                    // devient invisible sur fond sombre. Rouge
                                    // sémantique "éteindre".
                                    text: "\u{23FB}"  // Power symbol
                                    font.pixelSize: 16 * uiScale
                                    color: errorColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: shutdownButton.text
                                    font: shutdownButton.font
                                    color: textColor
                                    verticalAlignment: Text.AlignVCenter
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }

                        onClicked: root.shutdownClicked()
                    }

                    Button {
                        id: continueButton
                        text: qsTr("Continue")
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44 * uiScale
                        font.pixelSize: 14 * uiScale

                        background: Rectangle {
                            radius: 12
                            color: parent.pressed ? Qt.darker(surfaceColor, 1.2) : surfaceColor
                            border.color: textMutedColor
                            border.width: 2

                            Behavior on color {
                                ColorAnimation { duration: 150 }
                            }
                        }

                        contentItem: Item {
                            implicitWidth: continueContentRow.implicitWidth
                            implicitHeight: continueContentRow.implicitHeight

                            Row {
                                id: continueContentRow
                                anchors.centerIn: parent
                                spacing: 8

                                Text {
                                    // L'ancien glyphe (U+1F5D7) n'est pas un
                                    // caractère Unicode assigné : il s'affichait
                                    // comme un glyphe manquant tout noir. Flèche
                                    // simple, colorée (vert "continuer"), fiable
                                    // même sans police d'emoji couleur.
                                    text: "\u{2192}"  // Rightwards arrow
                                    font.pixelSize: 18 * uiScale
                                    font.bold: true
                                    color: successColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: continueButton.text
                                    font: continueButton.font
                                    color: textColor
                                    verticalAlignment: Text.AlignVCenter
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }

                        onClicked: root.continueClicked()
                    }
                }

                // Failure actions
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16
                    visible: !success

                    Button {
                        id: retryButton
                        text: qsTr("Retry Installation")
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44 * uiScale
                        font.pixelSize: 14 * uiScale
                        font.bold: true

                        background: Rectangle {
                            radius: 12
                            color: {
                                if (parent.pressed) return Qt.darker(warningColor, 1.3)
                                if (parent.hovered) return Qt.lighter(warningColor, 1.15)
                                return warningColor
                            }
                            border.color: Qt.lighter(warningColor, 1.3)
                            border.width: 2

                            Behavior on color {
                                ColorAnimation { duration: 150 }
                            }
                        }

                        contentItem: Item {
                            implicitWidth: retryContentRow.implicitWidth
                            implicitHeight: retryContentRow.implicitHeight

                            Row {
                                id: retryContentRow
                                anchors.centerIn: parent
                                spacing: 8

                                Text {
                                    text: "\u{1F504}"  // Counterclockwise arrows
                                    font.pixelSize: 16 * uiScale
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: retryButton.text
                                    font: retryButton.font
                                    color: textColor
                                    verticalAlignment: Text.AlignVCenter
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                        }

                        onClicked: root.retryClicked()
                    }

                    Button {
                        text: qsTr("Exit Installer")
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44 * uiScale
                        font.pixelSize: 14 * uiScale

                        background: Rectangle {
                            radius: 12
                            color: parent.pressed ? Qt.darker(surfaceColor, 1.2) : surfaceColor
                            border.color: textMutedColor
                            border.width: 2

                            Behavior on color {
                                ColorAnimation { duration: 150 }
                            }
                        }

                        contentItem: Text {
                            text: parent.text
                            font: parent.font
                            color: textColor
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        onClicked: root.continueClicked()
                    }
                }

                // Branded closing message (success only)
                Text {
                    visible: success && finishedMessage.length > 0
                    text: finishedMessage
                    font.pixelSize: 12 * uiScale
                    color: textColor
                    wrapMode: Text.WordWrap
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 600 * uiScale
                    horizontalAlignment: Text.AlignHCenter
                }

                // Info text
                Text {
                    text: success ?
                          qsTr("Please remove the installation media before rebooting") :
                          qsTr("Check the logs for more details about the error")
                    font.pixelSize: 11 * uiScale
                    color: textMutedColor
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            } // end megaCardColumn
            } // end megaCard
        }
        }
    }

    onVisibleChanged: if (visible) badgeAnimation.restart()
    Component.onCompleted: if (visible) badgeAnimation.restart()

    // Matches Main.qml's wizard footer: Main lays it out inside its 20px
    // page margins plus a 6px inset, but this step is full-bleed, so the
    // same offsets are applied here directly.
    Item {
        id: footerBar
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            leftMargin: 26
            rightMargin: 26
            bottomMargin: 20
        }
        height: 40 * uiScale

        RowLayout {
            anchors.fill: parent
            spacing: 16

            Text {
                text: qsTr("Powered by Xen Installer")
                font.pixelSize: 12 * uiScale
                color: textMutedColor
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

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
}
