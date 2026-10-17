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
    property string backgroundUrl: ""
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
    property color secondaryColor: "#3a7bc8"
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

    // Content container
    Rectangle {
        anchors.fill: parent
        color: "transparent"

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 10

            Item { Layout.fillHeight: true }

            // Mega card: icon, message, summary/error details and the
            // action buttons all nested inside one frosted panel, same
            // recipe as PartitionView/SummaryView, ending cleanly right
            // under the last button instead of floating loose.
            Rectangle {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: 960
                Layout.preferredHeight: megaCardColumn.implicitHeight + 28
                radius: 20
                color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.45)
                border.color: Qt.rgba(
                    (success ? successColor : errorColor).r,
                    (success ? successColor : errorColor).g,
                    (success ? successColor : errorColor).b, 0.45)
                border.width: 2

            ColumnLayout {
                id: megaCardColumn
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 16
                spacing: 14

            // Status icon and message
            Column {
                Layout.alignment: Qt.AlignHCenter
                spacing: 10

                // Distro logo, always shown (success or failure) - a small
                // status badge in its lower-right corner carries the actual
                // result instead of the old big animated check/X circle.
                Item {
                    id: logoWithBadge
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: finishedLogo.status === Image.Ready ? finishedLogo.width : statusBadge.width
                    height: finishedLogo.status === Image.Ready ? finishedLogo.height : statusBadge.height

                    Image {
                        id: finishedLogo
                        source: distroLogo
                        fillMode: Image.PreserveAspectFit
                        sourceSize.height: 96
                        height: 96
                    }

                    Rectangle {
                        id: statusBadge
                        width: 30
                        height: 30
                        radius: 15
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.rightMargin: -4
                        anchors.bottomMargin: -4
                        color: success ? successColor : errorColor
                        border.color: backgroundColor
                        border.width: 2

                        scale: 0
                        SequentialAnimation on scale {
                            running: true
                            PauseAnimation { duration: 200 }
                            NumberAnimation { from: 0; to: 1; duration: 350; easing.type: Easing.OutBack }
                        }

                        Text {
                            anchors.centerIn: parent
                            text: success ? "✓" : "✕"
                            color: "white"
                            font.pixelSize: 16
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
                    width: 500
                }
            }

            // Summary/Error details
            Rectangle {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: 900
                Layout.preferredHeight: detailsColumn.implicitHeight + 24
                radius: 14
                color: surfaceColor

                Column {
                    id: detailsColumn
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    // Success summary
                    Column {
                        width: parent.width
                        spacing: 10
                        visible: success

                        Row {
                            spacing: 12

                            Text {
                                text: "\u{1F389}"  // Party popper
                                font.pixelSize: 18
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("Installation Summary")
                                font.pixelSize: 15
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
                                    font.pixelSize: 12
                                    color: textMutedColor
                                    width: 140
                                }

                                Text {
                                    text: installationSummary.distribution || installationSummary.distroName || "Linux"
                                    font.pixelSize: 12
                                    font.bold: true
                                    color: textColor
                                }
                            }

                            Row {
                                width: parent.width
                                spacing: 10

                                Text {
                                    text: qsTr("Installation Target:")
                                    font.pixelSize: 12
                                    color: textMutedColor
                                    width: 140
                                }

                                Text {
                                    text: installationSummary.targetDisk || qsTr("Unknown")
                                    font.pixelSize: 12
                                    font.bold: true
                                    color: textColor
                                }
                            }

                            Row {
                                width: parent.width
                                spacing: 10

                                Text {
                                    text: qsTr("Installation Time:")
                                    font.pixelSize: 12
                                    color: textMutedColor
                                    width: 140
                                }

                                Text {
                                    text: installationSummary.installationTime || qsTr("Unknown")
                                    font.pixelSize: 12
                                    font.bold: true
                                    color: textColor
                                }
                            }

                            Row {
                                width: parent.width
                                spacing: 12
                                visible: (installationSummary.installedPackages || 0) > 0

                                Text {
                                    text: qsTr("Packages Installed:")
                                    font.pixelSize: 12
                                    color: textMutedColor
                                    width: 140
                                }

                                Text {
                                    text: (installationSummary.installedPackages || 0).toString()
                                    font.pixelSize: 12
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
                                font.pixelSize: 18
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("Error Details")
                                font.pixelSize: 15
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
                                font.pixelSize: 12
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
                                height: 30

                                background: Rectangle {
                                    radius: 8
                                    color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                                    border.color: textMutedColor
                                    border.width: 1
                                }

                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 12
                                    color: textColor
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                onClicked: root.viewLogsClicked()
                            }

                            Button {
                                text: qsTr("Send Logs")
                                height: 30
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
                                    font.pixelSize: 12
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
                            font.pixelSize: 11
                            font.italic: true
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                            width: 400
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
                                font.pixelSize: 11
                                color: textMutedColor
                            }

                            Row {
                                visible: !root.logUploadInProgress && root.logUploadUrl.length > 0
                                spacing: 8

                                TextField {
                                    id: logUploadUrlField
                                    width: 300
                                    readOnly: true
                                    selectByMouse: true
                                    text: root.logUploadUrl
                                    color: textColor
                                    font.pixelSize: 12

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
                                    height: 30

                                    background: Rectangle {
                                        radius: 8
                                        color: parent.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                                        border.color: textMutedColor
                                        border.width: 1
                                    }

                                    contentItem: Text {
                                        text: parent.text
                                        font.pixelSize: 12
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
                                font.pixelSize: 11
                                color: errorColor
                                wrapMode: Text.WordWrap
                                width: 400
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
                Layout.maximumWidth: 900
                spacing: 8

                // Success actions
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16
                    visible: success

                    Button {
                        id: rebootButton
                        text: qsTr("Reboot Now")
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        font.pixelSize: 14
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
                                    font.pixelSize: 16
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
                        Layout.preferredHeight: 44
                        font.pixelSize: 14

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
                                    font.pixelSize: 16
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
                        Layout.preferredHeight: 44
                        font.pixelSize: 14

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
                                    font.pixelSize: 18
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
                        Layout.preferredHeight: 44
                        font.pixelSize: 14
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
                                    font.pixelSize: 16
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
                        Layout.preferredHeight: 44
                        font.pixelSize: 14

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
                    font.pixelSize: 12
                    color: textColor
                    wrapMode: Text.WordWrap
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 600
                    horizontalAlignment: Text.AlignHCenter
                }

                // Info text
                Text {
                    text: success ?
                          qsTr("Please remove the installation media before rebooting") :
                          qsTr("Check the logs for more details about the error")
                    font.pixelSize: 11
                    color: textMutedColor
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            } // end megaCardColumn
            } // end megaCard

            Item { Layout.fillHeight: true }
        }
    }

    // Footer bar - transparent (anchored at bottom, aligned with Main.qml footer)
    Item {
        id: footerBar
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            margins: 0  // No additional margins - parent already has 32px from ColumnLayout
        }
        height: 48

        RowLayout {
            anchors.fill: parent
            spacing: 16

            // "Powered by Xen Installer" (left)
            Text {
                text: qsTr("Powered by Xen Installer")
                font.pixelSize: 12
                color: textMutedColor
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            // Website URL (right)
            Text {
                id: websiteLink
                text: root.websiteLabel || root.websiteUrl
                font.pixelSize: 12
                color: primaryColor
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
                    color: primaryColor
                    visible: footerWebsiteMouseArea.containsMouse
                }

                opacity: footerWebsiteMouseArea.containsMouse ? 0.7 : 1.0
                Behavior on opacity {
                    NumberAnimation { duration: 150 }
                }
            }
        }
    }
}
