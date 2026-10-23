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

Item {
    id: root
    objectName: "usersView"

    // Required property for branding access (icons)
    required property var branding

    // Mot de passe root : par défaut identique au compte utilisateur. Les
    // champs root ne sont révélés que si rootSameAsUser est décoché.
    readonly property bool rootSameAsUser: engine.rootSameAsUser
    // Confirmations : purement locales (jamais envoyées à engine), servent aux
    // indicateurs de correspondance. Les mots de passe eux-mêmes sont write-only.
    property string passwordConfirm: ""
    property string rootPasswordConfirm: ""

    // Full name / hostname auto-fill from username: stays live until the
    // user edits that field themselves, at which point their edit wins and
    // further username changes stop overwriting it. _syncingX guards tell
    // the field's own onTextChanged that a change came from this sync, not
    // a real user edit, so it doesn't mistake its own write for one.
    property bool autoFullNameSynced: true
    property bool autoHostnameSynced: true
    property bool _syncingFullName: false
    property bool _syncingHostname: false

    function capitalizeFirst(s) {
        return s.length > 0 ? s.charAt(0).toUpperCase() + s.slice(1) : s
    }

    function syncFromUsername() {
        var name = usernameField.text
        if (autoFullNameSynced) {
            _syncingFullName = true
            fullNameField.text = capitalizeFirst(name)
            _syncingFullName = false
        }
        if (autoHostnameSynced) {
            _syncingHostname = true
            hostnameField.text = name.length > 0 ? name.toLowerCase() + "-pc" : ""
            _syncingHostname = false
        }
    }

    // Theme colors
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
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
        "systemd-timesync", "systemd-coredump", "nm-openvpn", "dbus", "liveuser"
    ]
    readonly property bool usernameReserved: reservedUsernames.indexOf(usernameField.text) !== -1
    readonly property bool usernameValid: usernameField.text.length > 0
        && usernameField.text.length <= 32
        && /^[a-z_][a-z0-9_-]*$/.test(usernameField.text)
        && !usernameReserved
    // Mirrors UsersJob._validate_hostname: dot-separated labels of 1-63
    // lowercase letters, digits or hyphens, never starting or ending with a
    // hyphen, 253 characters max overall.
    readonly property bool hostnameValid: {
        var h = hostnameField.text
        if (h.length === 0 || h.length > 253 || h === "localhost") return false
        return h.split(".").every(function(label) {
            return /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(label)
        })
    }

    // Password criteria (NIST SP 800-63B inspired). Only non-empty is
    // required to proceed - a weak password is allowed, but the strength
    // meter below recommends a strong one instead of blocking install.
    readonly property bool pwdHasMinLength: passwordField.text.length >= 8
    readonly property bool pwdHasUppercase: /[A-Z]/.test(passwordField.text)
    readonly property bool pwdHasLowercase: /[a-z]/.test(passwordField.text)
    readonly property bool pwdHasNumber: /[0-9]/.test(passwordField.text)
    readonly property bool pwdHasSpecial: /[^a-zA-Z0-9]/.test(passwordField.text)
    // GECOS is ":"-separated and chpasswd reads "name:password" lines.
    readonly property bool fullNameValid: !/[:\r\n]/.test(fullNameField.text)
    readonly property bool passwordHasLineBreak: /[\r\n]/.test(passwordField.text)
    readonly property bool rootPasswordHasLineBreak: /[\r\n]/.test(rootPasswordField.text)
    readonly property bool passwordValid: passwordField.text.length > 0 && !passwordHasLineBreak
    readonly property bool passwordsMatch: passwordField.text === passwordConfirmField.text && passwordField.text.length > 0

    // Root password validation (only relevant when not reusing the user account
    // password). When rootSameAsUser is true, root credentials inherit the user
    // password, so no separate validation is required.
    readonly property bool rootPasswordValid: rootPasswordField.text.length > 0 && !rootPasswordHasLineBreak
    readonly property bool rootPasswordsMatch: rootPasswordField.text === rootPasswordConfirmField.text && rootPasswordField.text.length > 0
    readonly property bool rootValid: rootSameAsUser || (rootPasswordValid && rootPasswordsMatch)

    readonly property bool canProceed: usernameValid && fullNameValid && hostnameValid && passwordValid && passwordsMatch && rootValid
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
        width: 24 * uiScale
        height: 24 * uiScale
        sourceSize.width: width
        sourceSize.height: height
        anchors.verticalCenter: parent.verticalCenter
        visible: source.toString() !== ""
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
                var flickable = scrollView.contentItem as Flickable
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
            id: scrollContent
            width: scrollView.availableWidth
            implicitHeight: Math.max(heroCard.height + 32, root.height - 2 * scrollView.anchors.margins)
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

            // Frosted hero card - same recipe as WelcomeView's/LocaleView's.
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
                spacing: 6

                // Title
                Text {
                    text: qsTr("Create User Account")
                    font.pixelSize: 20 * uiScale
                    font.bold: true
                    color: textColor
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: qsTr("Set up your user account and system hostname")
                    font.pixelSize: 13 * uiScale
                    color: textMutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                }

                // Form container - grille 2 colonnes (Variante B). Flat
                // sections (no boxed card backgrounds/borders) separated by
                // generous spacing; a thin divider marks the password block.
                GridLayout {
                    columns: 2
                    columnSpacing: 18 * uiScale
                    rowSpacing: 10 * uiScale
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 900 * uiScale

                    // Username section (ligne 1, colonne 0)
                    ColumnLayout {
                        Layout.row: 0
                        Layout.column: 0
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 6

                        Row {
                            Layout.fillWidth: true
                            spacing: 12

                            SectionIcon {
                                iconSource: root.branding ? root.branding.iconUserUrl : ""
                            }

                            Text {
                                text: qsTr("Username")
                                font.pixelSize: 15 * uiScale
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: "*"
                                font.pixelSize: 15 * uiScale
                                color: errorColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        TextField {
                            id: usernameField
                            objectName: "usernameField"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 46 * uiScale
                            padding: 0
                            verticalAlignment: TextInput.AlignVCenter
                            placeholderText: qsTr("username (lowercase, no spaces)")
                            font.pixelSize: 16 * uiScale

                            // Source de vérité unique : lecture/écriture
                            // directes vers engine (pas de miroir local
                            // réassigné qui casserait le binding descendant).
                            text: engine.username
                            onTextChanged: {
                                engine.setUsername(text)
                                // Deferred: the sync writes back to engine, whose
                                // change signal also re-evaluates this field's own
                                // text binding (a binding loop if done inline).
                                Qt.callLater(root.syncFromUsername)
                            }

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
                            leftPadding: 16 * uiScale
                        }

                        Text {
                            objectName: "usernameHint"
                            Layout.fillWidth: true
                            text: usernameField.text.length > 0 && usernameReserved ?
                                  qsTr("This username is reserved by the system, please choose another") :
                                  usernameField.text.length > 0 && !usernameValid ?
                                  qsTr("Username must start with a lowercase letter or underscore, contain only lowercase letters, numbers, hyphens, or underscores, and be at most 32 characters") :
                                  qsTr("This will be your login name")
                            font.pixelSize: 11 * uiScale
                            color: usernameField.text.length > 0 && !usernameValid ? errorColor : textMutedColor
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Full name section (ligne 1, colonne 1)
                    ColumnLayout {
                        Layout.row: 0
                        Layout.column: 1
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 6

                        Row {
                            Layout.fillWidth: true
                            spacing: 12

                            SectionIcon {
                                iconSource: root.branding ? root.branding.iconFullnameUrl : ""
                            }

                            Text {
                                text: qsTr("Full Name")
                                font.pixelSize: 15 * uiScale
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        TextField {
                            id: fullNameField
                            objectName: "fullNameField"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 46 * uiScale
                            padding: 0
                            verticalAlignment: TextInput.AlignVCenter
                            placeholderText: qsTr("Your full name")
                            font.pixelSize: 16 * uiScale

                            text: engine.fullName
                            onTextChanged: {
                                engine.setFullName(text)
                                if (!root._syncingFullName) {
                                    root.autoFullNameSynced = false
                                }
                            }

                            background: Rectangle {
                                radius: 8
                                color: fullNameField.activeFocus ? Qt.darker(backgroundColor, 1.1) : backgroundColor
                                border.color: !fullNameValid ? errorColor : fullNameField.activeFocus ? primaryColor : textMutedColor
                                border.width: 2

                                Behavior on border.color {
                                    ColorAnimation { duration: 150 }
                                }
                            }

                            color: textColor
                            selectionColor: primaryColor
                            selectedTextColor: textColor
                            leftPadding: 16 * uiScale
                        }

                        Text {
                            objectName: "fullNameHint"
                            Layout.fillWidth: true
                            text: fullNameValid ? qsTr("Optional: Display name for your account")
                                                : qsTr("Full name cannot contain ':' or line breaks")
                            font.pixelSize: 11 * uiScale
                            color: fullNameValid ? textMutedColor : errorColor
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Hostname section (ligne 2, pleine largeur - la colonne
                    // "Account Options" qui l'accompagnait a été retirée : Xen
                    // n'a plus de bascule administrateur, cf. UsersJob).
                    ColumnLayout {
                        Layout.row: 1
                        Layout.column: 0
                        Layout.columnSpan: 2
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 6

                        Row {
                            Layout.fillWidth: true
                            spacing: 12

                            SectionIcon {
                                iconSource: root.branding ? root.branding.iconHostnameUrl : ""
                            }

                            Text {
                                text: qsTr("Computer Name")
                                font.pixelSize: 15 * uiScale
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: "*"
                                font.pixelSize: 15 * uiScale
                                color: errorColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        TextField {
                            id: hostnameField
                            objectName: "hostnameField"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 46 * uiScale
                            padding: 0
                            verticalAlignment: TextInput.AlignVCenter
                            placeholderText: qsTr("hostname (lowercase, no spaces)")
                            font.pixelSize: 16 * uiScale

                            text: engine.hostname
                            onTextChanged: {
                                engine.setHostname(text)
                                if (!root._syncingHostname) {
                                    root.autoHostnameSynced = false
                                }
                            }

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
                            leftPadding: 16 * uiScale
                        }

                        Text {
                            Layout.fillWidth: true
                            text: hostnameField.text.length > 0 && !hostnameValid ?
                                  qsTr("Hostname may contain only lowercase letters, numbers, hyphens, and dots; each part must not start or end with a hyphen and be at most 63 characters") :
                                  qsTr("Your computer's network name")
                            font.pixelSize: 11 * uiScale
                            color: hostnameField.text.length > 0 && !hostnameValid ? errorColor : textMutedColor
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Password section (ligne 3, pleine largeur)
                    ColumnLayout {
                        Layout.row: 2
                        Layout.column: 0
                        Layout.fillWidth: true
                        Layout.columnSpan: 2
                        spacing: 6

                            Row {
                                Layout.fillWidth: true
                                spacing: 12

                                SectionIcon {
                                    iconSource: root.branding ? root.branding.iconPasswordUrl : ""
                                }

                                Text {
                                    text: qsTr("Password")
                                    font.pixelSize: 15 * uiScale
                                    font.bold: true
                                    color: textColor
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                Text {
                                    text: "*"
                                    font.pixelSize: 15 * uiScale
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
                                    spacing: 6

                            TextField {
                                id: passwordField
                                objectName: "passwordField"
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46 * uiScale
                                padding: 0
                                verticalAlignment: TextInput.AlignVCenter
                                placeholderText: qsTr("Enter password")
                                font.pixelSize: 16 * uiScale
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
                                leftPadding: 16 * uiScale
                            }

                            Text {
                                objectName: "passwordLineBreakError"
                                Layout.fillWidth: true
                                visible: passwordHasLineBreak
                                text: qsTr("Passwords cannot contain line breaks")
                                font.pixelSize: 11 * uiScale
                                color: errorColor
                                wrapMode: Text.WordWrap
                            }
                                }  // fin colonne gauche (password)

                                // Colonne droite : confirmation + indicateur match
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 6

                                    TextField {
                                        id: passwordConfirmField
                                        objectName: "passwordConfirmField"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 46 * uiScale
                                        padding: 0
                                        verticalAlignment: TextInput.AlignVCenter
                                        placeholderText: qsTr("Confirm password")
                                        font.pixelSize: 16 * uiScale
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
                                        leftPadding: 16 * uiScale
                                    }

                                    // Password match indicator
                                    Row {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        visible: passwordConfirmField.text.length > 0

                                        Image {
                                            source: passwordsMatch ? (root.branding ? root.branding.iconCheckUrl : "") : (root.branding ? root.branding.iconCrossUrl : "")
                                            width: 16 * uiScale
                                            height: 16 * uiScale
                                            anchors.verticalCenter: parent.verticalCenter
                                            sourceSize.width: width
                                            sourceSize.height: height
                                            visible: source.toString() !== ""
                                        }

                                        Text {
                                            text: passwordsMatch ? qsTr("Passwords match") : qsTr("Passwords do not match")
                                            font.pixelSize: 11 * uiScale
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
                                font.pixelSize: 11 * uiScale
                                color: warningColor
                                wrapMode: Text.WordWrap
                            }

                            CheckBox {
                                id: showPasswordCheck
                                text: qsTr("Show password")
                                font.pixelSize: 14 * uiScale

                                indicator: Rectangle {
                                    implicitWidth: 20 * uiScale
                                    implicitHeight: 20 * uiScale
                                    x: showPasswordCheck.leftPadding
                                    y: parent.height / 2 - height / 2
                                    radius: 4
                                    border.color: showPasswordCheck.checked ? primaryColor : textMutedColor
                                    border.width: 2
                                    color: showPasswordCheck.checked ? primaryColor : "transparent"

                                    Image {
                                        anchors.centerIn: parent
                                        source: root.branding ? root.branding.iconCheckUrl : ""
                                        width: 12 * uiScale
                                        height: 12 * uiScale
                                        sourceSize.width: width
                                        sourceSize.height: height
                                        visible: showPasswordCheck.checked && source.toString() !== ""
                                    }

                                    // Fallback checkmark
                                    Text {
                                        anchors.centerIn: parent
                                        text: "\u2713"
                                        color: textColor
                                        font.pixelSize: 14 * uiScale
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
                                implicitHeight: 1
                                color: Qt.darker(surfaceColor, 1.3)
                            }

                            // Mot de passe root : réutiliser celui de l'utilisateur
                            // par défaut (coché). Décocher révèle deux champs root.
                            CheckBox {
                                id: rootSameAsUserCheck
                                checked: engine.rootSameAsUser
                                onCheckedChanged: engine.setRootSameAsUser(checked)

                                text: qsTr("Use the same password for the root account")
                                font.pixelSize: 14 * uiScale

                                indicator: Rectangle {
                                    implicitWidth: 20 * uiScale
                                    implicitHeight: 20 * uiScale
                                    x: rootSameAsUserCheck.leftPadding
                                    y: parent.height / 2 - height / 2
                                    radius: 4
                                    border.color: rootSameAsUserCheck.checked ? primaryColor : textMutedColor
                                    border.width: 2
                                    color: rootSameAsUserCheck.checked ? primaryColor : "transparent"

                                    Image {
                                        anchors.centerIn: parent
                                        source: root.branding ? root.branding.iconCheckUrl : ""
                                        width: 12 * uiScale
                                        height: 12 * uiScale
                                        sourceSize.width: width
                                        sourceSize.height: height
                                        visible: rootSameAsUserCheck.checked && source.toString() !== ""
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓"
                                        color: textColor
                                        font.pixelSize: 14 * uiScale
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

                            // Champs root (password + confirm) - collapse complet
                            // quand rootSameAsUser est coché (coût ~0px).
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                visible: !root.rootSameAsUser

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 6

                                    TextField {
                                        id: rootPasswordField
                                        objectName: "rootPasswordField"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 46 * uiScale
                                        padding: 0
                                        verticalAlignment: TextInput.AlignVCenter
                                        placeholderText: qsTr("Root password")
                                        font.pixelSize: 16 * uiScale
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
                                        leftPadding: 16 * uiScale
                                    }

                                    Text {
                                        objectName: "rootPasswordLineBreakError"
                                        Layout.fillWidth: true
                                        visible: rootPasswordHasLineBreak
                                        text: qsTr("Passwords cannot contain line breaks")
                                        font.pixelSize: 11 * uiScale
                                        color: errorColor
                                        wrapMode: Text.WordWrap
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 6

                                    TextField {
                                        id: rootPasswordConfirmField
                                        objectName: "rootPasswordConfirmField"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 46 * uiScale
                                        padding: 0
                                        verticalAlignment: TextInput.AlignVCenter
                                        placeholderText: qsTr("Confirm root password")
                                        font.pixelSize: 16 * uiScale
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
                                        leftPadding: 16 * uiScale
                                    }

                                    // Indicateur de correspondance des mots de passe root.
                                    Row {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        visible: rootPasswordConfirmField.text.length > 0

                                        Image {
                                            source: rootPasswordsMatch ? (root.branding ? root.branding.iconCheckUrl : "") : (root.branding ? root.branding.iconCrossUrl : "")
                                            width: 16 * uiScale
                                            height: 16 * uiScale
                                            anchors.verticalCenter: parent.verticalCenter
                                            sourceSize.width: width
                                            sourceSize.height: height
                                            visible: source.toString() !== ""
                                        }

                                        Text {
                                            text: rootPasswordsMatch ? qsTr("Passwords match") : qsTr("Passwords do not match")
                                            font.pixelSize: 11 * uiScale
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

                Item { implicitHeight: 8 }
            }
            }
        }
    }
}
