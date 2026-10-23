/*
 * Xen Installer - Main QML Interface
 *
 * Modern, fluid UI with dynamic branding support.
 * Colors and text are loaded from branding configuration.
 *
 * Wizard Steps:
 * 0 - Welcome (requirements check, system language)
 * 1 - Locale (timezone)
 * 2 - Keyboard (layout, variant)
 * 3 - Packages (optional software; skipped entirely when offline)
 * 4 - Users (username, password, options)
 * 5 - Partition (disk selection)
 * 6 - Summary (review selections)
 * 7 - Progress (installation)
 * 8 - Finished (reboot/shutdown)
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

ApplicationWindow {
    id: root

    // Sized from the actual screen instead of a fixed 1200x800 - on a
    // 1366x768 laptop that fixed size left barely any room for window
    // decorations before Plasma reflows it; on a 4K screen it looked tiny.
    width: Math.min(1600, Math.max(minimumWidth, Screen.width * 0.75))
    height: Math.min(1000, Math.max(minimumHeight, Screen.height * 0.8))
    minimumWidth: 1024
    minimumHeight: 640
    visible: true
    title: qsTr("%1 Installer").arg(branding.name)

    readonly property bool rightToLeft: Qt.locale(translator.currentLocale).textDirection === Qt.RightToLeft
    LayoutMirroring.enabled: rightToLeft
    LayoutMirroring.childrenInherit: true

    // Global scale for font sizes/card widths, used by every step view.
    // The height term is calibrated on the tallest page (Partition with a
    // disk selected: ~140px fixed + ~420px per unit of scale, inside a
    // viewport of height - 80 - 80 * scale); tests/integration/
    // test_page_fit.py fails if any page stops fitting.
    readonly property real uiScale: Math.min(2.0, Math.max(0.8,
        Math.min((width - 40) / 1200, (height - 230) / 510)))

    // System font for non-Latin languages (CJK, Arabic, Hebrew, etc.)
    // Uses Noto Sans when a non-Latin locale is selected, otherwise system default
    readonly property string systemFontFamily: engine.systemFontFamily
    readonly property bool needsUnicodeFont: engine.needsUnicodeFont

    // Apply font globally: theme primary font for Latin, Noto Sans for non-Latin
    // scripts. Empty string falls back to the Qt system default.
    font.family: needsUnicodeFont && systemFontFamily ? systemFontFamily : (branding.fontPrimary || "")

    // Le style Fusion rend le texte des champs via palette.text (noir par
    // défaut) en ignorant la propriété color. On force un gris clair lisible
    // sur le thème sombre, propagé à tous les TextField de l'application.
    palette.text: "#d1d5db"
    palette.placeholderText: "#9CA3AF"

    // Always the branding's dark palette; the desktop color scheme is ignored.
    readonly property color primaryColor: branding.primaryColor
    readonly property color accentColor: branding.accentColor
    readonly property color accentGradientEnd: branding.accentGradientEnd
    readonly property color backgroundGradientStart: branding.backgroundGradientStart
    readonly property color backgroundGradientEnd: branding.backgroundGradientEnd
    readonly property color backgroundColor: branding.backgroundColor
    readonly property color surfaceColor: branding.surfaceColor
    readonly property color textColor: branding.textColor
    readonly property color textMutedColor: branding.textMutedColor
    readonly property color successColor: branding.successColor || "#10B981"
    readonly property color warningColor: branding.warningColor || "#F59E0B"
    readonly property color errorColor: branding.errorColor || "#EF4444"

    // Wizard state
    property int currentStep: 0
    readonly property int totalSteps: 9  // 0-8
    readonly property var stepNames: [qsTr("Welcome"), qsTr("Locale"), qsTr("Keyboard"), qsTr("Packages"), qsTr("Users"),
                                      qsTr("Partition"), qsTr("Summary"), qsTr("Installing"), qsTr("Finished")]

    property bool installationSuccess: false

    // Semi-transparent so KWin's blur-behind hint has something to blur;
    // fully opaque on platforms where blur_behind.py couldn't enable it.
    color: Qt.rgba(backgroundColor.r, backgroundColor.g, backgroundColor.b, 0.82)

    // Background wallpaper (with gradient fallback)
    Image {
        id: backgroundImage
        anchors.fill: parent
        source: branding.backgroundUrl
        fillMode: Image.PreserveAspectCrop
        visible: status === Image.Ready

        // Dark overlay for readability
        Rectangle {
            anchors.fill: parent
            color: backgroundColor
            opacity: 0.3
        }
    }

    // Fallback gradient when no wallpaper - brand gradient (blue-to-red
    // for Intel/AMD, muted green-to-grey for nvidia - the raw brand green
    // washed across a whole page reads as sickly, not sleek), shared here
    // so every screen besides Welcome (which has its own) gets it too.
    Rectangle {
        anchors.fill: parent
        visible: backgroundImage.status !== Image.Ready
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Qt.darker(backgroundGradientStart, 2.0) }
            GradientStop { position: 1.0; color: Qt.darker(backgroundGradientEnd, 2.0) }
        }
    }

    // Main content. Welcome (0) and Finished (8) are full-bleed screens with
    // their own internal padding; the other steps need room for the header.
    readonly property bool isFullBleedStep: currentStep === 0 || currentStep === 8

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.isFullBleedStep ? 0 : 20
        spacing: 16

        // Header (hidden on Welcome and Finished)
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            visible: currentStep > 0 && currentStep < 7  // hidden on Welcome, Progress(7), Finished(8)

            // Logo
            Item {
                Layout.preferredWidth: 48 * uiScale
                Layout.preferredHeight: 48 * uiScale

                Image {
                    id: headerLogo
                    anchors.fill: parent
                    source: branding.logoSmallUrl
                    fillMode: Image.PreserveAspectFit
                    visible: status === Image.Ready
                }

                // Fallback when logo not available
                Rectangle {
                    anchors.fill: parent
                    radius: 12
                    color: primaryColor
                    visible: headerLogo.status !== Image.Ready

                    Text {
                        anchors.centerIn: parent
                        text: branding.name.charAt(0)
                        font.pixelSize: 24 * uiScale
                        font.bold: true
                        color: textColor
                    }
                }
            }

            Column {
                Layout.fillWidth: true
                spacing: 4

                Text {
                    text: qsTr("%1 Installer").arg(branding.name)
                    font.pixelSize: 20 * uiScale
                    font.bold: true
                    color: textColor
                }

                Text {
                    text: stepNames[currentStep] || ""
                    font.pixelSize: 14 * uiScale
                    color: textMutedColor
                }
            }

            // Step indicator (steps 1-6 only; Packages is skipped offline)
            Row {
                spacing: 8
                visible: currentStep >= 1 && currentStep <= 6

                Repeater {
                    model: engine.hasInternet ? [1, 2, 3, 4, 5, 6] : [1, 2, 4, 5, 6]

                    Rectangle {
                        required property int modelData
                        width: 40 * uiScale
                        height: 4
                        radius: 2
                        color: modelData < currentStep ? primaryColor :
                               modelData === currentStep ? accentColor : Qt.darker(surfaceColor, 1.2)

                        Behavior on color {
                            ColorAnimation { duration: 300 }
                        }
                    }
                }
            }
        }

        // Content area
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Step 0: Welcome
            WelcomeView {
                anchors.fill: parent
                visible: currentStep === 0
                opacity: visible ? 1 : 0

                welcomeWallpaper: branding.backgroundUrl
                logoUrl: branding.logoUrl
                welcomeTitle: branding.welcomeTitle
                welcomeSubtitle: branding.welcomeSubtitle
                installButtonText: branding.installButton
                brandingEdition: branding.editionLabel
                websiteUrl: branding.websiteUrl
                websiteLabel: branding.websiteLabel

                showRequirements: engine.showRequirements
                requirements: engine.requirementsModel
                canProceed: engine.canProceed
                isCheckingRequirements: engine.isCheckingRequirements
                hasInternet: engine.hasInternet

                localesModel: engine.localesModel
                localesModelNative: engine.localesModelNative
                // Installer language only: the system locale and keyboard have their own pages
                onLocaleSelected: function(locale) {
                    translator.setLocale(locale.split(".")[0])
                }

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                accentColor: root.accentColor
                accentGradientEnd: root.accentGradientEnd
                backgroundGradientStart: root.backgroundGradientStart
                backgroundGradientEnd: root.backgroundGradientEnd
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                successColor: root.successColor
                warningColor: root.warningColor
                errorColor: root.errorColor

                onInstallClicked: {
                    // Locale data already loaded at startup for early detection
                    currentStep = 1
                }

                onRequirementsChecked: {
                    engine.checkRequirements()
                }

                onConfigureNetworkRequested: {
                    console.log("Launching network configuration...")
                    engine.launchNetworkSettings()
                    // Schedule a recheck after a delay to allow user to configure network
                    networkRecheckTimer.start()
                }

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 1: Locale
            LocaleView {
                anchors.fill: parent
                visible: currentStep === 1
                opacity: visible ? 1 : 0

                timezonesModel: engine.timezonesModel

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                accentColor: root.accentColor

                onTimezoneSelected: function(timezone) {
                    engine.setSelectedTimezone(timezone)
                }

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 2: Keyboard
            KeyboardView {
                anchors.fill: parent
                visible: currentStep === 2
                opacity: visible ? 1 : 0

                keymapsModel: engine.keymapsModel
                keyboardVariantsModel: engine.keyboardVariantsModel
                // selectedKeymap/selectedKeyboardVariant sont lus directement
                // depuis engine par la vue (miroirs readonly).

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                accentColor: root.accentColor

                onKeymapSelected: function(keymap) {
                    engine.setSelectedKeymap(keymap)
                    engine.applyKeyboardLayout(engine.selectedKeymap, engine.selectedKeyboardVariant)
                }
                onKeyboardVariantSelected: function(variant) {
                    engine.setSelectedKeyboardVariant(variant)
                    engine.applyKeyboardLayout(engine.selectedKeymap, engine.selectedKeyboardVariant)
                }

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 3: Packages (skipped entirely when offline - see navigateNext/Back)
            PackageChooserView {
                anchors.fill: parent
                visible: currentStep === 3
                opacity: visible ? 1 : 0

                groups: engine.packageGroupsModel

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 4: Users
            UsersView {
                id: usersView
                anchors.fill: parent
                visible: currentStep === 4
                opacity: visible ? 1 : 0

                branding: branding

                // Source de vérité unique : la vue lit/écrit directement via
                // `engine.*` (setUsername/setFullName/…). Plus de propriétés
                // miroir ni de handlers onXChanged ici, qui cassaient le binding
                // descendant dès la première saisie. Les mots de passe restent
                // write-only côté vue (aucun binding descendant depuis engine).

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                errorColor: root.errorColor
                successColor: root.successColor
                warningColor: root.warningColor

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 5: Partition
            PartitionView {
                id: partitionView
                objectName: "partitionView"
                anchors.fill: parent
                visible: currentStep === 5
                opacity: visible ? 1 : 0

                disksModel: engine.disksModel
                // selectedDisk / partitionMode / filesystem / swapStrategy /
                // encryption sont lus directement depuis engine par la vue
                // (miroirs readonly), plus besoin de les passer ici.

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                successColor: root.successColor
                warningColor: root.warningColor
                errorColor: root.errorColor

                onDiskSelected: function(disk) {
                    engine.setSelectedDisk(disk)
                }
                onFilesystemSelected: function(fs) {
                    engine.setFilesystem(fs)
                }
                onSwapStrategySelected: function(strategy) {
                    engine.setSwapStrategy(strategy)
                }
                onEncryptionToggled: function(enabled) {
                    engine.setEncryption(enabled)
                }
                onEncryptionPassphraseSet: function(passphrase) {
                    engine.setEncryptionPassphrase(passphrase)
                }

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 6: Summary
            SummaryView {
                id: summaryView
                anchors.fill: parent
                visible: currentStep === 6
                opacity: visible ? 1 : 0

                // Bind chaque champ du résumé aux getters SCALAIRES notifiés du
                // bridge (propagation fiable sur selectionsChanged) plutôt qu'au
                // dict engine.selections dont les sous-propriétés ne se
                // ré-évaluaient pas dans QML (fix persistance du résumé).
                localeValue: engine.selectedLocale
                timezoneValue: engine.selectedTimezone
                keymapValue: engine.selectedKeymap
                usernameValue: engine.username
                fullNameValue: engine.fullName
                hostnameValue: engine.hostname
                diskValue: engine.selectedDisk
                diskSizeValue: engine.selectedDiskSize
                partitionModeValue: engine.partitionMode
                filesystemValue: engine.filesystem
                encryptionValue: engine.encryption

                // ITEM 2: reflète et arme la confirmation finale.
                confirmed: engine.confirmed
                onConfirmedToggled: function(value) { engine.setConfirmed(value) }

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                accentColor: root.accentColor
                successColor: root.successColor
                warningColor: root.warningColor

                onEditLocale: currentStep = 1
                onEditUsers: currentStep = 4
                onEditPartition: currentStep = 5

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 7: Progress
            ProgressView {
                id: progressView
                anchors.fill: parent
                visible: currentStep === 7
                opacity: visible ? 1 : 0

                overallProgress: engine.overallProgress
                currentJobName: engine.currentJobName
                currentJobMessage: engine.currentJobMessage
                isStalled: engine.isStalled

                distroLogo: branding.logoSmallUrl

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                warningColor: root.warningColor

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }

            // Step 8: Finished
            FinishedView {
                id: finishedView
                anchors.fill: parent
                visible: currentStep === 8
                opacity: visible ? 1 : 0

                success: installationSuccess
                errorMessage: engine.errorMessage
                summary: engine.installationSummary

                distroName: branding.name
                distroLogo: branding.logoUrl
                websiteUrl: branding.websiteUrl
                websiteLabel: branding.websiteLabel

                finishedTitle: branding.finishedTitle
                finishedSubtitle: branding.finishedSubtitle
                finishedMessage: branding.finishedMessage

                uiScale: root.uiScale
                primaryColor: root.primaryColor
                backgroundColor: root.backgroundColor
                surfaceColor: root.surfaceColor
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                accentColor: root.accentColor
                successColor: root.successColor
                errorColor: root.errorColor

                onRebootClicked: engine.executeFinishAction("reboot")
                onShutdownClicked: engine.executeFinishAction("shutdown")
                onContinueClicked: Qt.quit()
                onViewLogsClicked: fullLogDialog.open()
                onRetryClicked: {
                    engine.resetInstallation()
                    // Alongside and Manual choices describe the disk as it was before this attempt.
                    if (engine.partitionMode !== "auto") {
                        engine.refreshDisks()
                        currentStep = 5
                        return
                    }
                    startInstallation()
                }

                Behavior on opacity {
                    NumberAnimation { duration: 300 }
                }
            }
        }

        // Footer with navigation (hidden on Welcome, Progress, Finished)
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            visible: currentStep >= 1 && currentStep <= 6

            Text {
                text: qsTr("Powered by Xen Installer")
                font.pixelSize: 12 * uiScale
                color: textMutedColor
                Layout.leftMargin: 6
            }

            Item { Layout.fillWidth: true }

            // Website link
            Text {
                text: branding.websiteLabel || branding.websiteUrl
                font.pixelSize: 12 * uiScale
                color: accentColor

                MouseArea {
                    id: websiteLinkMouseArea
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    hoverEnabled: true
                    onClicked: Qt.openUrlExternally(branding.websiteUrl)
                }

                // Underline on hover
                Rectangle {
                    width: parent.width
                    height: 1
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: -2
                    color: accentColor
                    visible: websiteLinkMouseArea.containsMouse
                }

                // Brighten on hover
                opacity: websiteLinkMouseArea.containsMouse ? 0.8 : 1.0
                Behavior on opacity {
                    NumberAnimation { duration: 150 }
                }
            }

            // Back button (bottom-right, before Next/Install)
            Button {
                id: backButton
                text: qsTr("Back")
                onClicked: navigateBack()

                background: Rectangle {
                    implicitWidth: 100 * uiScale
                    implicitHeight: 40 * uiScale
                    radius: 8
                    color: backButton.pressed ? Qt.darker(surfaceColor, 1.2) :
                           backButton.hovered ? Qt.lighter(surfaceColor, 1.1) : surfaceColor
                    border.color: textMutedColor
                    border.width: 1
                }

                contentItem: Text {
                    text: backButton.text
                    font.pixelSize: 14 * uiScale
                    color: textColor
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            // Next/Install button (bottom-right corner)
            Button {
                id: nextButton
                objectName: "nextInstallButton"
                text: currentStep === 6 ? qsTr("Install") : qsTr("Next")
                enabled: canProceedToNext()

                background: Rectangle {
                    implicitWidth: 120 * uiScale
                    implicitHeight: 40 * uiScale
                    radius: 8
                    color: !nextButton.enabled ? Qt.darker(surfaceColor, 1.3) :
                           nextButton.pressed ? Qt.darker(primaryColor, 1.2) :
                           nextButton.hovered ? Qt.lighter(primaryColor, 1.1) : primaryColor
                }

                contentItem: Text {
                    text: nextButton.text
                    font.pixelSize: 14 * uiScale
                    font.bold: true
                    color: nextButton.enabled ? textColor : textMutedColor
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: navigateNext()
            }
        }
    }

    // Navigation functions
    function navigateBack() {
        // Calamares' PartitionViewStep::back(): Back from the manual editor
        // returns to the choice page, it does not leave the Partition step.
        if (currentStep === 5 && partitionView.manualEditorActive) {
            partitionView.exitManualEditor()
            return
        }
        if (currentStep > 0 && currentStep <= 6) {
            if (currentStep === 1) {
                currentStep = 0  // Back to Welcome
            } else if (currentStep === 4 && !engine.hasInternet) {
                currentStep = 2  // Skip Packages (3): nothing to change offline
            } else {
                currentStep--
            }
        }
    }

    function navigateNext() {
        // Calamares' PartitionViewStep::next(): Next on the choice page,
        // with Manual selected, swaps in the editor instead of leaving the
        // step - see PartitionView.qml's enterManualEditor().
        if (currentStep === 5 && engine.partitionMode === "manual" && !partitionView.manualEditorActive) {
            partitionView.enterManualEditor()
            return
        }
        if (currentStep === 6) {
            // Start installation
            startInstallation()
        } else if (currentStep < 6) {
            // The pre-selected layout too, so the Users page types with it
            if (currentStep === 2)
                engine.applyKeyboardLayout(engine.selectedKeymap, engine.selectedKeyboardVariant)
            if (currentStep === 2 && !engine.hasInternet) {
                currentStep = 4  // Skip Packages (3): nothing to fetch offline
                return
            }
            // Load data for next step if needed
            if (currentStep === 4) {
                engine.refreshDisks()  // Load disks before partition step (5)
            }
            currentStep++
        }
    }

    function canProceedToNext() {
        // The scan mounts and checks the partitions the install is about to change.
        if ((currentStep === 5 || currentStep === 6) && engine.disksScanning)
            return false
        if (engine.skipValidation)
            return true
        switch (currentStep) {
            case 1:  // Locale
                return engine.selectedLocale !== "" &&
                       engine.selectedTimezone !== ""
            case 2:  // Keyboard
                return engine.selectedKeymap !== ""
            case 4:  // Users
                return usersView.isValid
            case 5:  // Partition
                if (engine.partitionMode === "manual") {
                    // Mirrors Calamares' PartitionViewStep::isNextEnabled():
                    // on the choice page, Next just needs a disk picked (it
                    // enters the editor); once the editor itself is active,
                    // Next needs a valid plan to actually proceed.
                    if (!partitionView.manualEditorActive)
                        return engine.selectedDisk !== ""
                    return engine.selectedDisk !== "" && engine.planValid
                }
                return partitionView.canProceed
            case 6:  // Summary
                // ITEM 2: n'arme l'installation que si l'utilisateur a coché la
                // case de confirmation (garde-fou destructif côté backend aussi).
                return engine.confirmed
            default:
                return true
        }
    }

    function startInstallation() {
        engine.applySelectionsToContext()
        currentStep = 7  // Progress view
        engine.startInstallation()
    }

    // Step change handler
    onCurrentStepChanged: {
        console.log("Step changed to:", currentStep, "-", stepNames[currentStep])
    }

    // Engine connections
    Connections {
        target: engine

        function onInstallationFinished(success) {
            installationSuccess = success
            currentStep = 8  // Go to Finished view
        }

        function onErrorOccurred(jobName, errorMessage) {
            console.error("Error in", jobName + ":", errorMessage)
        }
    }

    // Translator connections for live language switching
    Connections {
        target: translator

        function onLanguageChanged() {
            console.log("Language changed to:", translator.currentLocale)
            // QML will automatically retranslate qsTr() strings
            // Also retranslate branding strings from Python translator
            branding.retranslate()
        }
    }

    // Keyboard shortcuts
    Shortcut {
        sequence: "Escape"
        enabled: currentStep > 0 && currentStep <= 6
        onActivated: navigateBack()
    }

    Shortcut {
        sequence: "Return"
        enabled: currentStep >= 1 && currentStep <= 6 && canProceedToNext()
        onActivated: navigateNext()
    }

    // Timer to recheck network status after user configures WiFi
    Timer {
        id: networkRecheckTimer
        interval: 5000  // 5 seconds delay to allow network connection
        repeat: false
        onTriggered: {
            console.log("Rechecking internet connectivity...")
            engine.recheckInternetStatus()
        }
    }

    // Full installation log dialog (opened from FinishedView "View Full Logs")
    Dialog {
        id: fullLogDialog

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: parent ? parent.width * 0.9 : 900
        height: parent ? parent.height * 0.9 : 650
        modal: true
        focus: true
        padding: 20
        title: qsTr("Installation Logs")

        // Upload state, local to this dialog
        property bool uploadInProgress: false
        property string uploadUrl: ""
        property string uploadError: ""

        background: Rectangle {
            color: surfaceColor
            radius: 12
            border.color: textMutedColor
            border.width: 1
        }

        header: Rectangle {
            color: "transparent"
            implicitHeight: dialogTitle.implicitHeight + 24

            Text {
                id: dialogTitle
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 24
                text: fullLogDialog.title
                font.pixelSize: 20 * uiScale
                font.bold: true
                color: textColor
            }
        }

        onOpened: {
            uploadUrl = ""
            uploadError = ""
            uploadInProgress = false
        }

        Connections {
            target: engine
            function onLogUploadFinished(url, ok, error) {
                fullLogDialog.uploadInProgress = false
                if (ok) {
                    fullLogDialog.uploadUrl = url
                    fullLogDialog.uploadError = ""
                } else {
                    fullLogDialog.uploadUrl = ""
                    fullLogDialog.uploadError = error
                }
            }
        }

        contentItem: ColumnLayout {
            spacing: 16

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                TextArea {
                    id: fullLogTextArea
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextArea.Wrap
                    font.family: branding.fontMonospace
                    font.pixelSize: 12 * uiScale
                    color: textColor
                    text: engine.installationLog

                    background: Rectangle {
                        color: backgroundColor
                        radius: 8
                    }
                }
            }

            // Upload result (URL sélectionnable + copie), affiché sous le journal
            RowLayout {
                Layout.fillWidth: true
                visible: fullLogDialog.uploadInProgress || fullLogDialog.uploadUrl.length > 0 || fullLogDialog.uploadError.length > 0
                spacing: 8

                Text {
                    visible: fullLogDialog.uploadInProgress
                    text: qsTr("Sending…")
                    color: textMutedColor
                    font.pixelSize: 13 * uiScale
                }

                TextField {
                    id: uploadUrlField
                    Layout.fillWidth: true
                    visible: !fullLogDialog.uploadInProgress && fullLogDialog.uploadUrl.length > 0
                    readOnly: true
                    selectByMouse: true
                    text: fullLogDialog.uploadUrl
                    color: textColor
                    font.pixelSize: 13 * uiScale

                    // Le style Fusion par défaut affiche un fond clair pour
                    // les TextField (palette embossée), ce qui rend le texte
                    // clair (`color: textColor`) illisible sur le thème
                    // sombre du dialogue. Fond explicite pour le contraste.
                    background: Rectangle {
                        color: backgroundColor
                        radius: 8
                        border.color: textMutedColor
                        border.width: 1
                    }
                }

                Button {
                    id: copyUrlButton
                    text: qsTr("Copy")
                    visible: !fullLogDialog.uploadInProgress && fullLogDialog.uploadUrl.length > 0
                    Layout.preferredHeight: 32 * uiScale

                    background: Rectangle {
                        radius: 8
                        color: copyUrlButton.pressed ? Qt.darker(backgroundColor, 1.2) : backgroundColor
                        border.color: textMutedColor
                        border.width: 1
                    }

                    contentItem: Text {
                        text: copyUrlButton.text
                        font.pixelSize: 13 * uiScale
                        color: textColor
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    onClicked: {
                        uploadUrlField.selectAll()
                        uploadUrlField.copy()
                        uploadUrlField.deselect()
                    }
                }

                Text {
                    visible: !fullLogDialog.uploadInProgress && fullLogDialog.uploadError.length > 0
                    text: qsTr("Sending failed: ") + fullLogDialog.uploadError
                    color: errorColor
                    font.pixelSize: 13 * uiScale
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }

        footer: Item {
            implicitHeight: footerRow.implicitHeight + 24

            RowLayout {
                id: footerRow
                anchors.fill: parent
                anchors.margins: 12
                spacing: 12

                Button {
                    text: qsTr("Copy")
                    onClicked: {
                        fullLogTextArea.selectAll()
                        fullLogTextArea.copy()
                        fullLogTextArea.deselect()
                    }
                }

                Button {
                    text: qsTr("Send Logs")
                    enabled: !fullLogDialog.uploadInProgress
                    onClicked: {
                        fullLogDialog.uploadInProgress = true
                        fullLogDialog.uploadUrl = ""
                        fullLogDialog.uploadError = ""
                        engine.uploadInstallLog()
                    }
                }

                Item { Layout.fillWidth: true }

                Button {
                    text: qsTr("Close")
                    onClicked: fullLogDialog.close()
                }
            }
        }
    }

    // Initialize
    Component.onCompleted: {
        console.log("Xen Installer started")
        console.log("Debug mode:", engine.debugMode)
        console.log("Dry run:", engine.dryRun)

        // Early locale detection for immediate UI translation
        engine.loadLocaleData()

        // Apply detected locale for UI translation
        if (engine.detectedLocale && engine.isUiLanguageAvailable(engine.detectedLocale)) {
            const detectedBase = engine.detectedLocale.split(".")[0]
            console.log("Early locale detection - applying UI language:", detectedBase)
            translator.setLocale(detectedBase)
        }

        // Requirements check is triggered by WelcomeView itself once it
        // finishes loading (see its requirementsChecked signal, handled
        // below) - calling it here too ran the whole check twice.
    }
}
