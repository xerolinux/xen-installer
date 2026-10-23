/*
 * ProgressView - Installation Progress Tracking
 *
 * Displays:
 * - Branded logo and a matrix-rain canvas animation with a centered,
 *   type-once message that holds while the rain keeps falling
 * - Single combined overall progress bar with the current job's live message
 * - Live installation log, opened on demand (see the "Show Progress Output" popup)
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "progressView"

    property int overallProgress: 0  // 0-100
    property string currentJobName: ""
    property string currentJobMessage: ""
    property bool isStalled: false
    readonly property bool animating: root.visible && !gamePopup.visible

    property string distroLogo: ""

    // Theme colors
    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color warningColor: "#F59E0B"

    component ThemedButton: Button {
        id: themedButton
        property real minWidth: 90
        property real minHeight: 34
        property real fontSize: 13
        property color fill: backgroundColor
        property color borderColor: textMutedColor
        property color labelColor: textColor

        background: Rectangle {
            implicitWidth: themedButton.minWidth * uiScale
            implicitHeight: themedButton.minHeight * uiScale
            radius: 8
            color: themedButton.pressed ? Qt.darker(themedButton.fill, 1.2)
                 : themedButton.hovered ? Qt.lighter(themedButton.fill, 1.1) : themedButton.fill
            border.color: themedButton.borderColor
            border.width: 1
        }

        contentItem: Text {
            text: themedButton.text
            font.pixelSize: themedButton.fontSize * uiScale
            color: themedButton.labelColor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component FrostedCard: Rectangle {
        radius: 20
        color: Qt.rgba(surfaceColor.r, surfaceColor.g, surfaceColor.b, 0.55)
        border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
        border.width: 2
    }

    // Centered when it fits, scrolls when the window is too short for it.
    ScrollView {
        id: pageScroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Item {

            id: scrollContent

            width: pageScroll.availableWidth
            implicitHeight: Math.max(pageColumn.implicitHeight + 16, root.height)
            height: implicitHeight

        ColumnLayout {
            id: pageColumn
            anchors.centerIn: parent
            width: Math.min(parent.width - 40, 900 * uiScale)
            spacing: 14 * uiScale

            // Title
            Text {
                text: qsTr("Installing...")
                font.pixelSize: 22 * uiScale
                font.bold: true
                color: textColor
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: qsTr("Please wait while the system is being installed")
                font.pixelSize: 13 * uiScale
                color: textMutedColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                horizontalAlignment: Text.AlignHCenter
            }

                // Hero card: logo + matrix rain, framed like every other
                // step's frosted card (Locale/Users/Partition) instead of
                // floating loose on the page background.
                FrostedCard {
                    Layout.fillWidth: true
                    Layout.preferredHeight: heroColumn.implicitHeight + 36 * uiScale

                    ColumnLayout {
                        id: heroColumn
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 18 * uiScale
                        spacing: 10 * uiScale

                // Branded logo
                Image {
                    Layout.alignment: Qt.AlignHCenter
                    source: distroLogo
                    visible: distroLogo.length > 0 && status === Image.Ready
                    fillMode: Image.PreserveAspectFit
                    sourceSize.height: height
                    Layout.preferredHeight: 110 * uiScale
                }

                // Matrix rain animation band - transparent canvas (never
                // an opaque fillRect) so the card background shows
                // through. Tinted per-profile: Intel/AMD's blue/red brand
                // pair, or nVidia's muted grey-green pair (same colors as
                // the page background gradient - reusing them keeps this
                // consistent instead of introducing a third accent).
                Canvas {
                    id: animCanvas
                    Layout.fillWidth: true
                    Layout.preferredHeight: 90 * uiScale

                    readonly property string message: qsTr("Installing And Booting The Matrix...")
                    property var drops: []
                    property int cols: 0
                    readonly property real colW: 18 * uiScale
                    property string typed: ""
                    property int typedIdx: 0
                    property int frameCount: 0

                    readonly property var glyphs: "01XEN{}<>/#$%&*+=-_ARCH".split("")

                    function resetDrops() {
                        cols = Math.max(1, Math.floor(width / colW))
                        var arr = []
                        for (var i = 0; i < cols; i++) arr.push(Math.random() * -30)
                        drops = arr
                    }

                    onWidthChanged: resetDrops()
                    Component.onCompleted: resetDrops()

                    // rgba() string from a QML color, at the given alpha -
                    // colorA/colorB are the brand's own gradient pair
                    // (blue/red for Intel-AMD, muted grey-green for nVidia),
                    // reused here instead of introducing a third accent.
                    function colorStr(c, a) {
                        return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + ","
                            + Math.round(c.b * 255) + "," + a + ")"
                    }

                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)

                        // branding's gradient getters return plain hex strings
                        // (Qt.color() promotes them to real color objects so
                        // .r/.g/.b below resolve instead of coming back NaN).
                        var colorA = Qt.color(branding.backgroundGradientStart)
                        var colorB = Qt.color(branding.backgroundGradientEnd)

                        // Matrix rain, background layer - slow, gentle fall.
                        ctx.font = Math.round(13 * uiScale) + "px '" + branding.fontMonospace + "'"
                        for (var i = 0; i < cols; i++) {
                            var g = glyphs[Math.floor(Math.random() * glyphs.length)]
                            var y = drops[i] * 16 * uiScale
                            var lead = Math.random() < 0.1
                            var base = (i % 2 === 0) ? colorA : colorB
                            ctx.fillStyle = colorStr(base, lead ? 1.0 : 0.5)
                            ctx.fillText(g, i * colW + 2, y)
                            if (y > height && Math.random() > 0.99) drops[i] = 0
                            else drops[i] += 0.18 + Math.random() * 0.12
                        }

                        // Centered message, typed out once then held.
                        frameCount++
                        if (typedIdx < message.length && frameCount % 3 === 0) {
                            typedIdx++
                            typed = message.substring(0, typedIdx)
                        }

                        ctx.font = "600 " + Math.round(20 * uiScale) + "px '" + branding.fontMonospace + "'"
                        ctx.textAlign = "center"
                        var showCursor = typedIdx < message.length && (frameCount % 30 < 15)
                        ctx.fillStyle = colorStr(textColor, 0.85)
                        ctx.fillText(typed + (showCursor ? "█" : ""), width / 2, height / 2)
                        ctx.textAlign = "left"
                    }

                    Timer {
                        interval: 60
                        running: root.animating
                        repeat: true
                        onTriggered: animCanvas.requestPaint()
                    }
                }
                    } // end heroColumn
                }

                // "Did you know" card - real XeroLinux facts, rephrased in
                // the installer's own voice, rotating while jobs run.
                FrostedCard {
                    id: factCard
                    Layout.fillWidth: true
                    Layout.preferredHeight: factColumn.implicitHeight + 28 * uiScale

                    readonly property var facts: [
                        qsTr("I'm building you Arch, with the sharp edges filed off."),
                        qsTr("I'm setting up a fully configured KDE Plasma 6 desktop on top of Arch, ready to go."),
                        qsTr("Once I'm done, you'll have full pacman and the AUR at your fingertips - the whole Arch ecosystem, no extra setup."),
                        qsTr("I include a Kernel Manager, so you can swap in CachyOS or other tuned kernels whenever you like."),
                        qsTr("I'm laying down a performance profile with real, measurable gains for gaming and latency-sensitive work."),
                        qsTr("Sane defaults today, full tuning power later - I'm giving you both."),
                        qsTr("I'm installing the XeroLinux Toolkit too, so post-install setup stays out of the terminal unless you want it there."),
                        qsTr("This is a rolling release - once I finish, updates just keep flowing straight from upstream Arch.")
                    ]
                    property int factIndex: 0

                    Timer {
                        interval: 7000
                        running: root.animating
                        repeat: true
                        onTriggered: factCard.factIndex = (factCard.factIndex + 1) % factCard.facts.length
                    }

                    Column {
                        id: factColumn
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 14 * uiScale
                        spacing: 6 * uiScale

                        Row {
                            spacing: 10 * uiScale

                            Text {
                                text: "\u{1F4A1}"
                                font.pixelSize: 16 * uiScale
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                text: qsTr("Did you know?")
                                font.pixelSize: 14 * uiScale
                                font.bold: true
                                color: textColor
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        Text {
                            width: parent.width
                            text: factCard.facts[factCard.factIndex]
                            font.pixelSize: 13 * uiScale
                            font.italic: true
                            color: textMutedColor
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // Combined overall progress - single bar, single number.
                FrostedCard {
                    Layout.fillWidth: true
                    Layout.preferredHeight: overallProgressColumn.implicitHeight + 36 * uiScale

                    Column {
                        id: overallProgressColumn
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 18 * uiScale
                        spacing: 10 * uiScale

                        RowLayout {
                            width: parent.width
                            spacing: 12 * uiScale

                            Text {
                                Layout.fillWidth: true
                                text: (currentJobName.length > 0 ? currentJobName : qsTr("Preparing")) + qsTr("...")
                                font.pixelSize: 18 * uiScale
                                font.bold: true
                                color: textColor
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "overallPercentText"
                                text: overallProgress + "%"
                                font.pixelSize: 24 * uiScale
                                font.bold: true
                                color: primaryColor
                            }
                        }

                        // Real, per-step status straight from the running job
                        // (report_progress messages), always visible so the
                        // user sees exactly what's happening, not a generic
                        // filler.
                        Text {
                            width: parent.width
                            text: currentJobMessage || qsTr("Working...")
                            font.pixelSize: 14 * uiScale
                            color: warningColor
                            wrapMode: Text.WordWrap
                        }

                        Text {
                            width: parent.width
                            visible: root.isStalled
                            text: qsTr("This can take several minutes; the interface may seem slow.")
                            font.pixelSize: 13 * uiScale
                            font.italic: true
                            color: warningColor
                            wrapMode: Text.WordWrap
                        }

                        // Overall progress bar
                        Rectangle {
                            id: overallTrack
                            width: parent.width
                            height: 16 * uiScale
                            radius: height / 2
                            color: backgroundColor
                            clip: true

                            Rectangle {
                                id: overallFill
                                visible: !root.isStalled
                                width: parent.width * (overallProgress / 100)
                                height: parent.height
                                radius: parent.radius
                                color: primaryColor
                                clip: true  // keep the shimmer inside the filled bar

                                Behavior on width {
                                    NumberAnimation { duration: 300; easing.type: Easing.OutCubic }
                                }

                                // Animated gradient
                                Rectangle {
                                    anchors.fill: parent
                                    radius: parent.radius
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.2) }
                                        GradientStop { position: 0.5; color: "transparent" }
                                        GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.1) }
                                    }
                                }

                                // Shimmer effect
                                Rectangle {
                                    id: shimmer
                                    width: parent.width * 0.3
                                    height: parent.height
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: "transparent" }
                                        GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.3) }
                                        GradientStop { position: 1.0; color: "transparent" }
                                    }

                                    SequentialAnimation on x {
                                        running: root.animating && overallProgress < 100 && !root.isStalled
                                        loops: Animation.Infinite
                                        NumberAnimation {
                                            from: -shimmer.width
                                            to: overallFill.width
                                            duration: 2000
                                            easing.type: Easing.InOutQuad
                                        }
                                        PauseAnimation { duration: 500 }
                                    }
                                }
                            }

                            Rectangle {
                                id: indeterminateBar
                                visible: root.isStalled
                                width: overallTrack.width * 0.35
                                height: parent.height
                                radius: parent.radius
                                x: -width
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.0; color: "transparent" }
                                    GradientStop { position: 0.5; color: primaryColor }
                                    GradientStop { position: 1.0; color: "transparent" }
                                }

                                SequentialAnimation on x {
                                    running: root.animating && root.isStalled
                                    loops: Animation.Infinite
                                    NumberAnimation {
                                        from: -indeterminateBar.width
                                        to: overallTrack.width
                                        duration: 1400
                                        easing.type: Easing.InOutQuad
                                    }
                                }
                            }
                        }
                    }
                }

                // Raw job output and the waiting-room game both stay out of
                // the way by default - opened on demand instead of always
                // taking up the main progress view.
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 12 * uiScale

                    ThemedButton {
                        text: qsTr("Show Progress Output")
                        minWidth: 220
                        minHeight: 42
                        fontSize: 14
                        fill: surfaceColor
                        onClicked: logPopup.open()
                    }

                    // Totally separate from the install itself - a pure
                    // waiting-room distraction while jobs run in the
                    // background (see SpaceInvadersGame.qml).
                    ThemedButton {
                        text: qsTr("\u{1F47E} Play a Game")
                        minWidth: 180
                        minHeight: 42
                        fontSize: 14
                        fill: primaryColor
                        borderColor: Qt.lighter(primaryColor, 1.3)
                        labelColor: textColor
                        onClicked: gamePopup.open()
                    }
                }

            // Info text
            Text {
                text: qsTr("Do not turn off your computer during installation")
                font.pixelSize: 14 * uiScale
                color: textMutedColor
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }
        }
        }
    }

    // Raw installation log, shown on demand via the "Show Progress Output"
    // button instead of always occupying the main progress view.
    Popup {
        id: logPopup
        objectName: "logPopup"
        anchors.centerIn: parent
        width: root.width * 0.85
        height: root.height * 0.8
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 16
            color: surfaceColor
            border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
            border.width: 2
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20 * uiScale
            spacing: 14 * uiScale

            RowLayout {
                Layout.fillWidth: true
                spacing: 12 * uiScale

                Text {
                    text: "\u{1F4DD}"  // Memo
                    font.pixelSize: 24 * uiScale
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Installation Log")
                    font.pixelSize: 20 * uiScale
                    font.bold: true
                    color: textColor
                }

                ThemedButton {
                    text: qsTr("Close")
                    minWidth: 90
                    minHeight: 34
                    fontSize: 13
                    onClicked: logPopup.close()
                }
            }

            ScrollView {
                id: logScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded

                TextArea {
                    width: logScroll.availableWidth
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextArea.Wrap
                    font.family: branding.fontMonospace
                    font.pixelSize: 12 * uiScale
                    color: textColor
                    // Single source for the live log: a throttled (~5x/s)
                    // property exposed by the engine, holding the last ~300
                    // lines. The old per-line logMessageAppended signal is no
                    // longer emitted (it saturated the GUI thread on >10k
                    // line runs).
                    text: engine.logTail

                    background: Rectangle {
                        color: backgroundColor
                        radius: 8
                    }

                    // Always follows the bottom: the tail always shows the
                    // most recent lines, so re-pin on every update.
                    onTextChanged: cursorPosition = length
                }
            }
        }
    }

    // Waiting-room mini-game - entirely separate from the install, just a
    // distraction while jobs run. Space Invaders, colorful on purpose.
    Popup {
        id: gamePopup
        objectName: "gamePopup"
        anchors.centerIn: parent
        width: Math.min(root.width * 0.7, 560 * uiScale)
        height: Math.min(root.height * 0.85, 520 * uiScale)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 16
            color: surfaceColor
            border.color: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.5)
            border.width: 2
        }

        onOpened: { game.initGame(); game.forceActiveFocus() }
        onClosed: game.running = false

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18 * uiScale
            spacing: 12 * uiScale

            RowLayout {
                Layout.fillWidth: true
                spacing: 12 * uiScale

                Text {
                    text: "\u{1F47E}"
                    font.pixelSize: 22 * uiScale
                }

                Text {
                    text: qsTr("Space Invaders")
                    font.pixelSize: 18 * uiScale
                    font.bold: true
                    color: textColor
                }
            }

            SpaceInvadersGame {
                id: game
                Layout.fillWidth: true
                Layout.fillHeight: true
                textColor: root.textColor
                textMutedColor: root.textMutedColor
                uiScale: root.uiScale
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10 * uiScale

                ThemedButton {
                    text: qsTr("Restart")
                    minWidth: 100
                    minHeight: 34
                    fontSize: 13
                    fill: surfaceColor
                    onClicked: { game.initGame(); game.forceActiveFocus() }
                }

                Item { Layout.fillWidth: true }

                ThemedButton {
                    text: qsTr("Close")
                    minWidth: 90
                    minHeight: 34
                    fontSize: 13
                    onClicked: gamePopup.close()
                }
            }
        }
    }
}
