/*
 * SpaceInvadersGame - small distraction for the install progress screen.
 *
 * Fully self-contained: no install state in or out, just a score and level.
 * Levels escalate indefinitely (faster, lower-starting waves) rather than
 * ending at a "win" screen, so there's always another wave to play while
 * the install keeps running. Uses Canvas + a Timer game loop (same pattern
 * as ProgressView's matrix-rain animation) so it renders fine under the
 * app's QT_QUICK_BACKEND=software requirement - no shader effects here.
 */

import QtQuick
import QtQuick.Layouts

Item {
    id: root

    // Theme colors (passed from parent, for the overlay text only - the
    // game sprites deliberately use a bright arcade palette instead of the
    // app's brand colors, since the whole point was "make it colorful").
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property real uiScale: 1.0

    readonly property var rowColors: ["#ff5f56", "#ffb454", "#39ff8f", "#5bc0ff", "#c77dff"]
    // Each row is a different Linux-flavored "invader": terminal window,
    // package box, Tux, systemd-style gear - drawn as small vector icons
    // in paint(), not literal image assets.
    readonly property var rowIcons: ["terminal", "package", "tux", "gear"]
    readonly property color shipColor: "#39ff8f"
    readonly property color bulletColor: "#fffd6b"

    property int score: 0
    property int level: 1
    property bool running: false
    property bool gameOver: false
    property bool transitioning: false  // brief pause + banner between waves

    property real playerX: 0
    readonly property int playerW: 34
    readonly property int playerH: 14
    readonly property real playerSpeed: 9
    property bool moveLeft: false
    property bool moveRight: false

    property var bullets: []   // {x, y}
    property var aliens: []    // {x, y, w, h, alive, color, type}
    property int alienDir: 1
    readonly property int rows: 4
    readonly property int cols: 7

    // Difficulty ramps with level: faster march, bigger steps down, and
    // each wave starts a little lower (less reaction time) - capped so it
    // stays playable instead of becoming instantly unfair.
    readonly property real alienSpeed: Math.min(4.5, 1.3 + (level - 1) * 0.3)
    readonly property real alienStepDown: Math.min(26, 14 + (level - 1) * 1.6)
    readonly property real startY: Math.min(70, 26 + (level - 1) * 5)

    // Colorful falling-glyph backdrop, same technique as ProgressView's
    // matrix rain but multi-colored instead of monochrome.
    property var rainDrops: []
    property int rainCols: 0
    readonly property real rainColW: 16
    readonly property var rainGlyphs: "01$#&@%+=-_<>/~^".split("")

    focus: true
    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Left || event.key === Qt.Key_A) { moveLeft = true; event.accepted = true }
        else if (event.key === Qt.Key_Right || event.key === Qt.Key_D) { moveRight = true; event.accepted = true }
        else if (event.key === Qt.Key_Space) { shoot(); event.accepted = true }
        else if (event.key === Qt.Key_R && gameOver) { initGame(); event.accepted = true }
    }
    Keys.onReleased: function(event) {
        if (event.key === Qt.Key_Left || event.key === Qt.Key_A) { moveLeft = false; event.accepted = true }
        else if (event.key === Qt.Key_Right || event.key === Qt.Key_D) { moveRight = false; event.accepted = true }
    }

    function resetRain() {
        rainCols = Math.max(1, Math.floor(width / rainColW))
        var arr = []
        for (var i = 0; i < rainCols; i++) arr.push(Math.random() * -30)
        rainDrops = arr
    }

    function spawnWave() {
        var marginX = 24
        var spacingX = (width - marginX * 2) / cols
        var spacingY = 28
        var alienW = 22
        var alienH = 16
        var arr = []
        for (var r = 0; r < rows; r++) {
            for (var c = 0; c < cols; c++) {
                arr.push({ x: marginX + c * spacingX, y: startY + r * spacingY, w: alienW, h: alienH,
                           alive: true, color: rowColors[r % rowColors.length], type: rowIcons[r % rowIcons.length] })
            }
        }
        aliens = arr
        alienDir = 1
        bullets = []
    }

    function initGame() {
        score = 0
        level = 1
        gameOver = false
        transitioning = false
        moveLeft = false
        moveRight = false
        playerX = width / 2 - playerW / 2
        running = true
        if (rainCols === 0) resetRain()
        spawnWave()
        canvas.requestPaint()
    }

    function nextLevel() {
        level += 1
        transitioning = true
        running = false
        levelBanner.restart()
    }

    Timer {
        id: levelBanner
        interval: 1100
        onTriggered: { root.transitioning = false; root.running = true; root.spawnWave() }
    }

    function shoot() {
        if (!running) return
        if (bullets.length < 3) {
            var b = bullets.slice()
            b.push({ x: playerX + playerW / 2 - 2, y: height - 38 })
            bullets = b
        }
    }

    function tick() {
        // rain keeps falling even during the level-up pause and game over,
        // so the backdrop never looks frozen.
        var drops = rainDrops
        for (var ri = 0; ri < drops.length; ri++) {
            drops[ri] += 0.25 + (ri % 5) * 0.03
            if (drops[ri] * 14 > height && Math.random() > 0.985) drops[ri] = 0
        }

        if (!running) { canvas.requestPaint(); return }

        if (moveLeft) playerX = Math.max(4, playerX - playerSpeed)
        if (moveRight) playerX = Math.min(width - playerW - 4, playerX + playerSpeed)

        var liveBullets = []
        for (var bi = 0; bi < bullets.length; bi++) {
            var b = bullets[bi]
            b.y -= 11
            if (b.y > 0) liveBullets.push(b)
        }
        bullets = liveBullets

        var minX = Infinity, maxX = -Infinity, anyAlive = false, lowestY = 0
        for (var i = 0; i < aliens.length; i++) {
            var a = aliens[i]
            if (!a.alive) continue
            anyAlive = true
            minX = Math.min(minX, a.x)
            maxX = Math.max(maxX, a.x + a.w)
            lowestY = Math.max(lowestY, a.y + a.h)
        }

        if (!anyAlive) { nextLevel(); canvas.requestPaint(); return }

        var hitEdge = (maxX + alienDir * alienSpeed > width - 4 && alienDir > 0)
            || (minX + alienDir * alienSpeed < 4 && alienDir < 0)

        for (var j = 0; j < aliens.length; j++) {
            if (!aliens[j].alive) continue
            if (hitEdge) aliens[j].y += alienStepDown
            else aliens[j].x += alienDir * alienSpeed
        }
        if (hitEdge) alienDir *= -1

        if (lowestY + alienStepDown >= height - 30) { gameOver = true; running = false; canvas.requestPaint(); return }

        // bullet/alien collisions
        var survivors = []
        for (var k = 0; k < bullets.length; k++) {
            var bl = bullets[k]
            var hit = false
            for (var m = 0; m < aliens.length; m++) {
                var al = aliens[m]
                if (al.alive && bl.x > al.x && bl.x < al.x + al.w && bl.y > al.y && bl.y < al.y + al.h) {
                    al.alive = false
                    score += 10
                    hit = true
                    break
                }
            }
            if (!hit) survivors.push(bl)
        }
        bullets = survivors

        canvas.requestPaint()
    }

    Timer {
        interval: 33
        running: root.visible
        repeat: true
        onTriggered: root.tick()
    }

    onWidthChanged: if (width > 0 && aliens.length === 0) initGame()
    onHeightChanged: if (height > 0 && aliens.length === 0) initGame()
    Component.onCompleted: forceActiveFocus()

    Canvas {
        id: canvas
        anchors.fill: parent

        function withAlpha(hex, a) {
            var r = parseInt(hex.substr(1, 2), 16)
            var g = parseInt(hex.substr(3, 2), 16)
            var bl = parseInt(hex.substr(5, 2), 16)
            return "rgba(" + r + "," + g + "," + bl + "," + a + ")"
        }

        function drawAlien(ctx, a) {
            var x = a.x, y = a.y, w = a.w, h = a.h, c = a.color
            ctx.fillStyle = c
            if (a.type === "tux") {
                // Simplified Tux: round head + oval body, same silhouette
                // reads at this size better than a literal invader block.
                ctx.beginPath(); ctx.ellipse ? ctx.ellipse(x + w / 2, y + h * 0.62, w * 0.42, h * 0.46, 0, 0, 7)
                    : ctx.arc(x + w / 2, y + h * 0.6, w * 0.4, 0, 7)
                ctx.fill()
                ctx.beginPath(); ctx.arc(x + w / 2, y + h * 0.28, w * 0.26, 0, 7); ctx.fill()
                ctx.fillStyle = "#05070a"
                ctx.beginPath(); ctx.arc(x + w / 2 - 3, y + h * 0.26, 1.4, 0, 7); ctx.fill()
                ctx.beginPath(); ctx.arc(x + w / 2 + 3, y + h * 0.26, 1.4, 0, 7); ctx.fill()
                ctx.fillStyle = "#ffb454"
                ctx.beginPath(); ctx.moveTo(x + w / 2 - 3, y + h * 0.34); ctx.lineTo(x + w / 2 + 3, y + h * 0.34)
                ctx.lineTo(x + w / 2, y + h * 0.42); ctx.closePath(); ctx.fill()
            } else if (a.type === "gear") {
                var cx = x + w / 2, cy = y + h / 2, r = Math.min(w, h) * 0.38
                ctx.beginPath()
                for (var t = 0; t < 8; t++) {
                    var ang = (t / 8) * Math.PI * 2
                    var rr = (t % 2 === 0) ? r : r * 0.7
                    var px = cx + Math.cos(ang) * rr, py = cy + Math.sin(ang) * rr
                    if (t === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
                }
                ctx.closePath(); ctx.fill()
                ctx.fillStyle = "#05070a"
                ctx.beginPath(); ctx.arc(cx, cy, r * 0.35, 0, 7); ctx.fill()
            } else if (a.type === "package") {
                ctx.beginPath()
                ctx.roundRect ? ctx.roundRect(x, y, w, h, 3) : ctx.rect(x, y, w, h)
                ctx.fill()
                ctx.strokeStyle = "rgba(5,7,10,0.55)"
                ctx.lineWidth = 1.5
                ctx.beginPath(); ctx.moveTo(x + w / 2, y); ctx.lineTo(x + w / 2, y + h); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(x, y + h / 2); ctx.lineTo(x + w, y + h / 2); ctx.stroke()
            } else {
                // terminal window: a dark "screen" with a bright prompt
                ctx.beginPath()
                ctx.roundRect ? ctx.roundRect(x, y, w, h, 3) : ctx.rect(x, y, w, h)
                ctx.fill()
                ctx.fillStyle = "#05070a"
                ctx.fillRect(x + 2, y + 5, w - 4, h - 7)
                ctx.fillStyle = c
                ctx.font = "700 9px monospace"
                ctx.textAlign = "left"; ctx.textBaseline = "middle"
                ctx.fillText(">_", x + 3, y + h / 2 + 2)
            }
        }

        onPaint: {
            var ctx = getContext("2d")
            ctx.fillStyle = "#05070a"
            ctx.fillRect(0, 0, width, height)

            // colorful matrix rain backdrop
            ctx.font = "12px monospace"
            ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"
            for (var ri = 0; ri < root.rainDrops.length; ri++) {
                var g = root.rainGlyphs[Math.floor(Math.random() * root.rainGlyphs.length)]
                var ry = root.rainDrops[ri] * 14
                var rc = root.rowColors[ri % root.rowColors.length]
                ctx.fillStyle = withAlpha(rc, 0.35)
                ctx.fillText(g, ri * root.rainColW + 2, ry)
            }

            for (var i = 0; i < root.aliens.length; i++) {
                var a = root.aliens[i]
                if (!a.alive) continue
                drawAlien(ctx, a)
            }

            ctx.fillStyle = root.bulletColor
            for (var b = 0; b < root.bullets.length; b++) {
                ctx.fillRect(root.bullets[b].x, root.bullets[b].y, 3, 10)
            }

            // player ship
            ctx.fillStyle = root.shipColor
            ctx.beginPath()
            ctx.moveTo(root.playerX + root.playerW / 2, height - 40)
            ctx.lineTo(root.playerX, height - 40 + root.playerH)
            ctx.lineTo(root.playerX + root.playerW, height - 40 + root.playerH)
            ctx.closePath()
            ctx.fill()

            if (root.transitioning) {
                ctx.fillStyle = "rgba(5,7,10,0.72)"
                ctx.fillRect(0, 0, width, height)
                ctx.textAlign = "center"
                ctx.fillStyle = root.shipColor
                ctx.font = "700 22px sans-serif"
                ctx.fillText(qsTr("LEVEL %1").arg(root.level), width / 2, height / 2)
                ctx.textAlign = "left"
            } else if (root.gameOver) {
                ctx.fillStyle = "rgba(5,7,10,0.72)"
                ctx.fillRect(0, 0, width, height)
                ctx.textAlign = "center"
                ctx.fillStyle = "#ff5f56"
                ctx.font = "700 20px sans-serif"
                ctx.fillText(qsTr("GAME OVER"), width / 2, height / 2 - 14)
                ctx.fillStyle = "#d7e8d7"
                ctx.font = "13px sans-serif"
                ctx.fillText(qsTr("Reached level %1 · score %2").arg(root.level).arg(root.score), width / 2, height / 2 + 8)
                ctx.fillText(qsTr("Press R or the button below to play again"), width / 2, height / 2 + 28)
                ctx.textAlign = "left"
            }
        }
    }

    // HUD
    RowLayout {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 10 * root.uiScale
        spacing: 10 * root.uiScale

        Text {
            text: qsTr("Score: %1").arg(root.score)
            font.pixelSize: 14 * root.uiScale
            font.bold: true
            color: root.textColor
        }
        Text {
            text: qsTr("Level %1").arg(root.level)
            font.pixelSize: 13 * root.uiScale
            font.bold: true
            color: root.shipColor
        }
        Item { Layout.fillWidth: true }
        Text {
            text: qsTr("← → move · space to fire")
            font.pixelSize: 11 * root.uiScale
            color: root.textMutedColor
        }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: root.forceActiveFocus()
    }
}
