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

                // Distro logo - shown on success as the celebratory branding
                // moment; omitted on failure to keep focus on the error icon.
                Image {
                    id: finishedLogo
                    visible: success && status === Image.Ready
                    anchors.horizontalCenter: parent.horizontalCenter
                    source: distroLogo
                    fillMode: Image.PreserveAspectFit
                    sourceSize.height: 44
                    height: 44
                }

                // Animated icon
                Rectangle {
                    width: 84
                    height: 84
                    radius: 42
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: success ?
                           Qt.rgba(successColor.r, successColor.g, successColor.b, 0.2) :
                           Qt.rgba(errorColor.r, errorColor.g, errorColor.b, 0.2)

                    // Ambient glow ring instead of a MultiEffect shadow -
                    // this installer always runs with QT_QUICK_BACKEND=
                    // software (see WelcomeView.qml), under which
                    // layer.effect silently fails to render at all, taking
                    // this whole circle (and its children) down with it.
                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.width + 12
                        height: parent.height + 12
                        radius: width / 2
                        color: "transparent"
                        border.width: 5
                        border.color: Qt.rgba(
                            (success ? successColor : errorColor).r,
                            (success ? successColor : errorColor).g,
                            (success ? successColor : errorColor).b, 0.15)
                    }

                    // Confetti burst - a handful of particles fly outward
                    // from the circle and fade, once, on arrival. Plain
                    // NumberAnimation on x/y/opacity (no shader), so it
                    // renders under the software Quick backend too.
                    Repeater {
                        model: success ? 10 : 0

                        delegate: Text {
                            id: particle
                            readonly property real angle: (index / 10) * 2 * Math.PI
                            readonly property real dist: 42 + (index % 3) * 13
                            text: ["\u{1F389}", "✨", "\u{1F38A}", "⭐"][index % 4]
                            font.pixelSize: 12
                            // Centered on the circle, then animated outward
                            // by x/y (not anchors - PropertyAnimation can't
                            // target a grouped anchors sub-property).
                            x: parent.width / 2 - width / 2
                            y: parent.height / 2 - height / 2
                            opacity: 0

                            SequentialAnimation {
                                running: success
                                PauseAnimation { duration: 250 + index * 25 }
                                ParallelAnimation {
                                    NumberAnimation {
                                        target: particle
                                        property: "x"
                                        to: particle.parent.width / 2 - particle.width / 2 + Math.cos(particle.angle) * particle.dist
                                        duration: 700
                                        easing.type: Easing.OutCubic
                                    }
                                    NumberAnimation {
                                        target: particle
                                        property: "y"
                                        to: particle.parent.height / 2 - particle.height / 2 + Math.sin(particle.angle) * particle.dist
                                        duration: 700
                                        easing.type: Easing.OutCubic
                                    }
                                    SequentialAnimation {
                                        NumberAnimation { target: particle; property: "opacity"; to: 1; duration: 150 }
                                        PauseAnimation { duration: 300 }
                                        NumberAnimation { target: particle; property: "opacity"; to: 0; duration: 350 }
                                    }
                                }
                            }
                        }
                    }

                    // Success checkmark animation
                    Canvas {
                        id: successCanvas
                        anchors.fill: parent
                        visible: success

                        property real progress: 0

                        SequentialAnimation {
                            running: success
                            PauseAnimation { duration: 300 }
                            NumberAnimation {
                                target: successCanvas
                                property: "progress"
                                from: 0
                                to: 1
                                duration: 600
                                easing.type: Easing.OutCubic
                            }
                        }

                        onProgressChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d");
                            ctx.clearRect(0, 0, width, height);

                            ctx.strokeStyle = successColor;
                            ctx.lineWidth = 5;
                            ctx.lineCap = "round";
                            ctx.lineJoin = "round";

                            // Draw checkmark
                            var startX = width * 0.25;
                            var startY = height * 0.5;
                            var midX = width * 0.45;
                            var midY = height * 0.65;
                            var endX = width * 0.75;
                            var endY = height * 0.35;

                            ctx.beginPath();
                            if (progress < 0.5) {
                                var p = progress * 2;
                                ctx.moveTo(startX, startY);
                                ctx.lineTo(startX + (midX - startX) * p, startY + (midY - startY) * p);
                            } else {
                                var p = (progress - 0.5) * 2;
                                ctx.moveTo(startX, startY);
                                ctx.lineTo(midX, midY);
                                ctx.lineTo(midX + (endX - midX) * p, midY + (endY - midY) * p);
                            }
                            ctx.stroke();
                        }
                    }

                    // Error X animation
                    Item {
                        anchors.fill: parent
                        visible: !success

                        Rectangle {
                            width: parent.width * 0.6
                            height: 5
                            radius: 3
                            color: errorColor
                            anchors.centerIn: parent
                            rotation: 45

                            scale: 0
                            SequentialAnimation on scale {
                                running: !success
                                PauseAnimation { duration: 300 }
                                NumberAnimation {
                                    from: 0
                                    to: 1
                                    duration: 400
                                    easing.type: Easing.OutBack
                                }
                            }
                        }

                        Rectangle {
                            width: parent.width * 0.6
                            height: 5
                            radius: 3
                            color: errorColor
                            anchors.centerIn: parent
                            rotation: -45

                            scale: 0
                            SequentialAnimation on scale {
                                running: !success
                                PauseAnimation { duration: 300 }
                                NumberAnimation {
                                    from: 0
                                    to: 1
                                    duration: 400
                                    easing.type: Easing.OutBack
                                }
                            }
                        }
                    }

                    // Pulse animation
                    SequentialAnimation on scale {
                        running: true
                        loops: 1
                        NumberAnimation { from: 0.8; to: 1.0; duration: 500; easing.type: Easing.OutCubic }
                    }
                }

                // Status text - no drop shadow (MultiEffect never renders
                // under this installer's always-on software Quick backend
                // and previously took the whole Text down with it).
                Text {
                    text: success ? (finishedTitle || qsTr("Installation Complete!")) : qsTr("Installation Failed")
                    font.pixelSize: 26
                    font.bold: true
                    color: textColor
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    text: success ?
                          (finishedSubtitle || qsTr("The system has been successfully installed on your computer")) :
                          qsTr("An error occurred during installation")
                    font.pixelSize: 13
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
