/*
 * UsersView - User Account Configuration
 *
 * Displays:
 * - Username input with validation
 * - Full name input
 * - Hostname input with validation
 * - Password fields with a simple weak-password hint
 * - Admin privileges checkbox
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

Item {
    id: root

    // Required property for branding access (icons)
    required property var branding

    // Miroirs descendants (lecture seule) de la source de vérité `engine`.
    // Ils ne sont JAMAIS réassignés impérativement : les champs écrivent
    // directement via engine.setX(), donc ces bindings ne se cassent pas et
    // reflètent toujours l'état courant (résout la non-persistance des vues).
    readonly property string username: engine.username
    readonly property string fullName: engine.fullName
    readonly property string hostname: engine.hostname
    // Mot de passe root : par défaut identique au compte utilisateur. Les
    // champs root ne sont révélés que si rootSameAsUser est décoché.
    readonly property bool rootSameAsUser: engine.rootSameAsUser
    // Confirmations : purement locales (jamais envoyées à engine), servent aux
    // indicateurs de correspondance. Les mots de passe eux-mêmes sont write-only.
    property string passwordConfirm: ""
    property string rootPasswordConfirm: ""

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

    // Validation states
    // Aligné sur UsersJob.USERNAME_PATTERN côté Python : un underscore est
    // autorisé en première position (^[a-z_][a-z0-9_-]*$).
    // Noms réservés par le système (alignés sur UsersJob.RESERVED_USERNAMES) :
    // les réutiliser fait échouer useradd (UID/GID déjà pris) ou détourne le
    // compte système existant.
    readonly property var reservedUsernames: [
        "root", "nobody", "daemon", "bin", "sys", "messagebus", "sshd",
        "nscd", "polkituser", "rtkit", "avahi", "systemd-network", "systemd-resolve",
        "systemd-timesync", "systemd-coredump", "nm-openvpn", "dbus"
    ]
    readonly property bool usernameReserved: reservedUsernames.indexOf(usernameField.text) !== -1
    readonly property bool usernameValid: usernameField.text.length > 0
        && /^[a-z_][a-z0-9_-]*$/.test(usernameField.text)
        && !usernameReserved
    readonly property bool hostnameValid: hostnameField.text.length > 0
        && /^[a-z][a-z0-9-]*$/.test(hostnameField.text)
        && hostnameField.text !== "localhost"

    // Password criteria (NIST SP 800-63B inspired). Only non-empty is
    // required to proceed - a weak password is allowed, but the strength
    // meter below recommends a strong one instead of blocking install.
    readonly property bool pwdHasMinLength: passwordField.text.length >= 8
    readonly property bool pwdHasUppercase: /[A-Z]/.test(passwordField.text)
    readonly property bool pwdHasLowercase: /[a-z]/.test(passwordField.text)
    readonly property bool pwdHasNumber: /[0-9]/.test(passwordField.text)
    readonly property bool pwdHasSpecial: /[^a-zA-Z0-9]/.test(passwordField.text)
    readonly property bool passwordValid: passwordField.text.length > 0
    readonly property bool passwordsMatch: passwordField.text === passwordConfirmField.text && passwordField.text.length > 0

    // Root password validation (only relevant when not reusing the user account
    // password). When rootSameAsUser is true, root credentials inherit the user
    // password, so no separate validation is required.
    readonly property bool rootPasswordValid: rootPasswordField.text.length > 0
    readonly property bool rootPasswordsMatch: rootPasswordField.text === rootPasswordConfirmField.text && rootPasswordField.text.length > 0
    readonly property bool rootValid: rootSameAsUser || (rootPasswordValid && rootPasswordsMatch)

    readonly property bool canProceed: usernameValid && hostnameValid && passwordValid && passwordsMatch && rootValid
    readonly property alias isValid: root.canProceed  // Alias for Main.qml compatibility

    // Password strength calculation
    readonly property int passwordStrength: {
        var pwd = passwordField.text;
        if (pwd.length === 0) return 0;
        var strength = 0;
        if (pwdHasMinLength) strength += 25;
        if (pwd.length >= 12) strength += 25;
        if (pwdHasLowercase && pwdHasUppercase) strength += 25;
        if (pwdHasNumber) strength += 15;
        if (pwdHasSpecial) strength += 10;
        return Math.min(100, strength);
    }

    // Reusable component for section icon
    component SectionIcon: Image {
        property string iconSource: ""

        source: iconSource
        width: 24
        height: 24
        sourceSize.width: 24
        sourceSize.height: 24
        anchors.verticalCenter: parent.verticalCenter
        visible: source != ""

        layer.enabled: !engine.softwareRendering
        layer.effect: MultiEffect {
            colorization: 1.0
            colorizationColor: textColor
        }
    }

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
            height: Math.max(heroCard.height + 32, scrollView.availableHeight)

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

            // Frosted hero card - same recipe as WelcomeView's/LocaleView's.
            Rectangle {
                id: heroCard
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 880)
                height: cardColumn.implicitHeight + 64
                radius: 20
                color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
                border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
                border.width: 2

            ColumnLayout {
                id: cardColumn
                anchors.centerIn: parent
                width: parent.width - 64
                spacing: 8

                // Title
                Text {
                    text: qsTr("Create User Account")
                    font.pixelSize: 24
                    font.bold: true
                    color: textColor
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: qsTr("Set up your user account and system hostname")
                    font.pixelSize: 13
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                }

                // Form container — grille 2 colonnes (Variante B). Flat
                // sections (no boxed card backgrounds/borders) separated by
                // generous spacing; a thin divider marks the password block.
                GridLayout {
                    columns: 2
                    columnSpacing: 24
                    rowSpacing: 16
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 900

                    // Username section (ligne 1, colonne 0)
                    ColumnLayout {
                        id: usernameColumn
                        Layout.row: 0
                        Layout.column: 0
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 8

                        Row {
                            Layout.fillWidth: true
                            spacing: 12

                            SectionIcon {
                                iconSource: root.branding ? root.branding.iconUserUrl : ""
                            }

                            Text {
                                text: qsTr("Username")
                                font.pixelSize: 15
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: "*"
                                font.pixelSize: 15
                                color: errorColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        TextField {
                            id: usernameField
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            padding: 0
                            verticalAlignment: TextInput.AlignVCenter
                            placeholderText: qsTr("username (lowercase, no spaces)")
                            font.pixelSize: 16

                            // Source de vérité unique : lecture/écriture
                            // directes vers engine (pas de miroir local
                            // réassigné qui casserait le binding descendant).
                            text: engine.username
                            onTextChanged: engine.setUsername(text)

                            background: Rectangle {
                                radius: 8
                                color: usernameField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                border.color: {
                                    if (!usernameField.activeFocus) return textMutedColor;
                                    if (usernameField.text.length === 0) return primaryColor;
                                    return usernameValid ? successColor : errorColor;
                                }
                                border.width: 2

                                Behavior on border.color {
                                    ColorAnimation { duration: 150 }
                                }
                            }

                            color: textColor
                            selectionColor: primaryColor
                            selectedTextColor: textColor
                            leftPadding: 16
                        }

                        Text {
                            Layout.fillWidth: true
                            text: usernameField.text.length > 0 && usernameReserved ?
                                  qsTr("This username is reserved by the system — please choose another") :
                                  usernameField.text.length > 0 && !usernameValid ?
                                  qsTr("Username must start with a letter and contain only lowercase letters, numbers, hyphens, or underscores") :
                                  qsTr("This will be your login name")
                            font.pixelSize: 11
                            color: usernameField.text.length > 0 && !usernameValid ? errorColor : textMutedColor
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Full name section (ligne 1, colonne 1)
                    ColumnLayout {
                        id: fullNameColumn
                        Layout.row: 0
                        Layout.column: 1
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 8

                        Row {
                            Layout.fillWidth: true
                            spacing: 12

                            SectionIcon {
                                iconSource: root.branding ? root.branding.iconFullnameUrl : ""
                            }

                            Text {
                                text: qsTr("Full Name")
                                font.pixelSize: 15
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        TextField {
                            id: fullNameField
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            padding: 0
                            verticalAlignment: TextInput.AlignVCenter
                            placeholderText: qsTr("Your full name")
                            font.pixelSize: 16

                            text: engine.fullName
                            onTextChanged: engine.setFullName(text)

                            background: Rectangle {
                                radius: 8
                                color: fullNameField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                border.color: fullNameField.activeFocus ? primaryColor : textMutedColor
                                border.width: 2

                                Behavior on border.color {
                                    ColorAnimation { duration: 150 }
                                }
                            }

                            color: textColor
                            selectionColor: primaryColor
                            selectedTextColor: textColor
                            leftPadding: 16
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Optional: Display name for your account")
                            font.pixelSize: 11
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Hostname section (ligne 2, pleine largeur - la colonne
                    // "Account Options" qui l'accompagnait a été retirée : Xen
                    // n'a plus de bascule administrateur, cf. UsersJob).
                    ColumnLayout {
                        id: hostnameColumn
                        Layout.row: 1
                        Layout.column: 0
                        Layout.columnSpan: 2
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 8

                        Row {
                            Layout.fillWidth: true
                            spacing: 12

                            SectionIcon {
                                iconSource: root.branding ? root.branding.iconHostnameUrl : ""
                            }

                            Text {
                                text: qsTr("Computer Name")
                                font.pixelSize: 15
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: "*"
                                font.pixelSize: 15
                                color: errorColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        TextField {
                            id: hostnameField
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            padding: 0
                            verticalAlignment: TextInput.AlignVCenter
                            placeholderText: qsTr("hostname (lowercase, no spaces)")
                            font.pixelSize: 16

                            text: engine.hostname
                            onTextChanged: engine.setHostname(text)

                            background: Rectangle {
                                radius: 8
                                color: hostnameField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                border.color: {
                                    if (!hostnameField.activeFocus) return textMutedColor;
                                    if (hostnameField.text.length === 0) return primaryColor;
                                    return hostnameValid ? successColor : errorColor;
                                }
                                border.width: 2

                                Behavior on border.color {
                                    ColorAnimation { duration: 150 }
                                }
                            }

                            color: textColor
                            selectionColor: primaryColor
                            selectedTextColor: textColor
                            leftPadding: 16
                        }

                        Text {
                            Layout.fillWidth: true
                            text: hostnameField.text.length > 0 && !hostnameValid ?
                                  qsTr("Hostname must start with a letter and contain only lowercase letters, numbers, or hyphens") :
                                  qsTr("Your computer's network name")
                            font.pixelSize: 11
                            color: hostnameField.text.length > 0 && !hostnameValid ? errorColor : textMutedColor
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Password section (ligne 3, pleine largeur)
                    ColumnLayout {
                        id: passwordColumn
                        Layout.row: 2
                        Layout.column: 0
                        Layout.fillWidth: true
                        Layout.columnSpan: 2
                        spacing: 8

                            Row {
                                Layout.fillWidth: true
                                spacing: 12

                                SectionIcon {
                                    iconSource: root.branding ? root.branding.iconPasswordUrl : ""
                                }

                                Text {
                                    text: qsTr("Password")
                                    font.pixelSize: 15
                                    font.bold: true
                                    color: textColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: "*"
                                    font.pixelSize: 15
                                    color: errorColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }

                            // Password + Confirm côte à côte (Variante B)
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 8

                            TextField {
                                id: passwordField
                                Layout.fillWidth: true
                                Layout.preferredHeight: 56
                                padding: 0
                                verticalAlignment: TextInput.AlignVCenter
                                placeholderText: qsTr("Enter password")
                                font.pixelSize: 16
                                echoMode: showPasswordCheck.checked ? TextInput.Normal : TextInput.Password

                                // SÉCURITÉ : mot de passe write-only. On écrit
                                // vers engine mais on ne binde JAMAIS la valeur en
                                // descendant (pas de text: engine.password). Le
                                // champ garde sa propre valeur locale de saisie.
                                onTextChanged: engine.setPassword(text)

                                background: Rectangle {
                                    radius: 8
                                    color: passwordField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                    border.color: {
                                        if (!passwordField.activeFocus) return textMutedColor;
                                        if (passwordField.text.length === 0) return primaryColor;
                                        return passwordValid ? successColor : errorColor;
                                    }
                                    border.width: 2

                                    Behavior on border.color {
                                        ColorAnimation { duration: 150 }
                                    }
                                }

                                color: textColor
                                selectionColor: primaryColor
                                selectedTextColor: textColor
                                leftPadding: 16
                            }
                                }  // fin colonne gauche (password)

                                // Colonne droite : confirmation + indicateur match
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 8

                                    TextField {
                                        id: passwordConfirmField
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 56
                                        padding: 0
                                        verticalAlignment: TextInput.AlignVCenter
                                        placeholderText: qsTr("Confirm password")
                                        font.pixelSize: 16
                                        echoMode: showPasswordCheck.checked ? TextInput.Normal : TextInput.Password

                                        text: root.passwordConfirm
                                        onTextChanged: root.passwordConfirm = text

                                        background: Rectangle {
                                            radius: 8
                                            color: passwordConfirmField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                            border.color: {
                                                if (!passwordConfirmField.activeFocus) return textMutedColor;
                                                if (passwordConfirmField.text.length === 0) return primaryColor;
                                                return passwordsMatch ? successColor : errorColor;
                                            }
                                            border.width: 2

                                            Behavior on border.color {
                                                ColorAnimation { duration: 150 }
                                            }
                                        }

                                        color: textColor
                                        selectionColor: primaryColor
                                        selectedTextColor: textColor
                                        leftPadding: 16
                                    }

                                    // Password match indicator
                                    Row {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        visible: passwordConfirmField.text.length > 0

                                        Image {
                                            source: passwordsMatch ? (root.branding ? root.branding.iconCheckUrl : "") : (root.branding ? root.branding.iconCrossUrl : "")
                                            width: 16
                                            height: 16
                                            anchors.verticalCenter: parent.verticalCenter
                                            sourceSize.width: 16
                                            sourceSize.height: 16
                                            visible: source != ""

                                            layer.enabled: !engine.softwareRendering
                                            layer.effect: MultiEffect {
                                                colorization: 1.0
                                                colorizationColor: passwordsMatch ? successColor : errorColor
                                            }
                                        }

                                        Text {
                                            text: passwordsMatch ? qsTr("Passwords match") : qsTr("Passwords do not match")
                                            font.pixelSize: 11
                                            color: passwordsMatch ? successColor : errorColor
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }  // fin colonne droite (confirm)
                            }  // fin RowLayout password/confirm

                            // Simple weak-password hint (no strength meter or criteria checklist).
                            Text {
                                Layout.fillWidth: true
                                visible: passwordField.text.length > 0 && passwordStrength < 70
                                text: qsTr("We Recommend a Stronger Password")
                                font.pixelSize: 11
                                color: warningColor
                                wrapMode: Text.WordWrap
                            }

                            CheckBox {
                                id: showPasswordCheck
                                text: qsTr("Show password")
                                font.pixelSize: 14

                                indicator: Rectangle {
                                    implicitWidth: 20
                                    implicitHeight: 20
                                    x: showPasswordCheck.leftPadding
                                    y: parent.height / 2 - height / 2
                                    radius: 4
                                    border.color: showPasswordCheck.checked ? primaryColor : textMutedColor
                                    border.width: 2
                                    color: showPasswordCheck.checked ? primaryColor : "transparent"

                                    Image {
                                        anchors.centerIn: parent
                                        source: root.branding ? root.branding.iconCheckUrl : ""
                                        width: 12
                                        height: 12
                                        sourceSize.width: 12
                                        sourceSize.height: 12
                                        visible: showPasswordCheck.checked && source != ""

                                        layer.enabled: !engine.softwareRendering
                                        layer.effect: MultiEffect {
                                            colorization: 1.0
                                            colorizationColor: textColor
                                        }
                                    }

                                    // Fallback checkmark
                                    Text {
                                        anchors.centerIn: parent
                                        text: "\u2713"
                                        color: textColor
                                        font.pixelSize: 14
                                        visible: showPasswordCheck.checked && (!root.branding || root.branding.iconCheckUrl === "")
                                    }
                                }

                                contentItem: Text {
                                    text: showPasswordCheck.text
                                    font: showPasswordCheck.font
                                    color: textColor
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: showPasswordCheck.indicator.width + showPasswordCheck.spacing
                                }
                            }

                            // Séparateur léger avant la section root.
                            Rectangle {
                                Layout.fillWidth: true
                                height: 1
                                color: Qt.darker(surfaceColor, 1.3)
                            }

                            // Mot de passe root : réutiliser celui de l'utilisateur
                            // par défaut (coché). Décocher révèle deux champs root.
                            CheckBox {
                                id: rootSameAsUserCheck
                                checked: engine.rootSameAsUser
                                onCheckedChanged: engine.setRootSameAsUser(checked)

                                text: qsTr("Use the same password for the root account")
                                font.pixelSize: 14

                                indicator: Rectangle {
                                    implicitWidth: 20
                                    implicitHeight: 20
                                    x: rootSameAsUserCheck.leftPadding
                                    y: parent.height / 2 - height / 2
                                    radius: 4
                                    border.color: rootSameAsUserCheck.checked ? primaryColor : textMutedColor
                                    border.width: 2
                                    color: rootSameAsUserCheck.checked ? primaryColor : "transparent"

                                    Image {
                                        anchors.centerIn: parent
                                        source: root.branding ? root.branding.iconCheckUrl : ""
                                        width: 12
                                        height: 12
                                        sourceSize.width: 12
                                        sourceSize.height: 12
                                        visible: rootSameAsUserCheck.checked && source != ""

                                        layer.enabled: !engine.softwareRendering
                                        layer.effect: MultiEffect {
                                            colorization: 1.0
                                            colorizationColor: textColor
                                        }
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓"
                                        color: textColor
                                        font.pixelSize: 14
                                        visible: rootSameAsUserCheck.checked && (!root.branding || root.branding.iconCheckUrl === "")
                                    }
                                }

                                contentItem: Text {
                                    text: rootSameAsUserCheck.text
                                    font: rootSameAsUserCheck.font
                                    color: textColor
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: rootSameAsUserCheck.indicator.width + rootSameAsUserCheck.spacing
                                    wrapMode: Text.WordWrap
                                }
                            }

                            // Champs root (password + confirm) — collapse complet
                            // quand rootSameAsUser est coché (coût ~0px).
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                visible: !root.rootSameAsUser

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 8

                                    TextField {
                                        id: rootPasswordField
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 56
                                        padding: 0
                                        verticalAlignment: TextInput.AlignVCenter
                                        placeholderText: qsTr("Root password")
                                        font.pixelSize: 16
                                        echoMode: showPasswordCheck.checked ? TextInput.Normal : TextInput.Password

                                        // SÉCURITÉ : write-only (cf. password).
                                        onTextChanged: engine.setRootPassword(text)

                                        background: Rectangle {
                                            radius: 8
                                            color: rootPasswordField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                            border.color: {
                                                if (!rootPasswordField.activeFocus) return textMutedColor;
                                                if (rootPasswordField.text.length === 0) return primaryColor;
                                                return rootPasswordValid ? successColor : errorColor;
                                            }
                                            border.width: 2

                                            Behavior on border.color {
                                                ColorAnimation { duration: 150 }
                                            }
                                        }

                                        color: textColor
                                        selectionColor: primaryColor
                                        selectedTextColor: textColor
                                        leftPadding: 16
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 8

                                    TextField {
                                        id: rootPasswordConfirmField
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 56
                                        padding: 0
                                        verticalAlignment: TextInput.AlignVCenter
                                        placeholderText: qsTr("Confirm root password")
                                        font.pixelSize: 16
                                        echoMode: showPasswordCheck.checked ? TextInput.Normal : TextInput.Password

                                        text: root.rootPasswordConfirm
                                        onTextChanged: root.rootPasswordConfirm = text

                                        background: Rectangle {
                                            radius: 8
                                            color: rootPasswordConfirmField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                            border.color: {
                                                if (!rootPasswordConfirmField.activeFocus) return textMutedColor;
                                                if (rootPasswordConfirmField.text.length === 0) return primaryColor;
                                                return rootPasswordsMatch ? successColor : errorColor;
                                            }
                                            border.width: 2

                                            Behavior on border.color {
                                                ColorAnimation { duration: 150 }
                                            }
                                        }

                                        color: textColor
                                        selectionColor: primaryColor
                                        selectedTextColor: textColor
                                        leftPadding: 16
                                    }

                                    // Indicateur de correspondance des mots de passe root.
                                    Row {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        visible: rootPasswordConfirmField.text.length > 0

                                        Image {
                                            source: rootPasswordsMatch ? (root.branding ? root.branding.iconCheckUrl : "") : (root.branding ? root.branding.iconCrossUrl : "")
                                            width: 16
                                            height: 16
                                            anchors.verticalCenter: parent.verticalCenter
                                            sourceSize.width: 16
                                            sourceSize.height: 16
                                            visible: source != ""

                                            layer.enabled: !engine.softwareRendering
                                            layer.effect: MultiEffect {
                                                colorization: 1.0
                                                colorizationColor: rootPasswordsMatch ? successColor : errorColor
                                            }
                                        }

                                        Text {
                                            text: rootPasswordsMatch ? qsTr("Passwords match") : qsTr("Passwords do not match")
                                            font.pixelSize: 11
                                            color: rootPasswordsMatch ? successColor : errorColor
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }
                            }
                    }
                }

                // Note : la navigation (Précédent/Suivant) est assurée par le
                // footer global de Main.qml, qui consomme usersView.isValid via
                // canProceedToNext(). Pas de barre de navigation interne ici.

                Item { height: 8 }
            }
            }
        }
    }
}
