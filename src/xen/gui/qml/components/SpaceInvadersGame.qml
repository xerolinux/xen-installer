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
    // Geeky invaders drawn as small vector icons in paint(), ordered from
    // friendly to creepy: each of the first five levels brings four new ones.
    readonly property var invaderTypes: [
        "package", "gear", "terminal", "tux",
        "floppy", "mouse", "gamepad", "coffee",
        "paperclip", "harddrive", "cdrom", "ram",
        "cpu", "pacman", "ghost", "bug",
        "usb", "wifi", "database", "bsod"
    ]

    function waveTypes(lvl) {
        var n = invaderTypes.length
        if (lvl * 4 <= n)
            return invaderTypes.slice((lvl - 1) * 4, lvl * 4)
        var wave = []
        for (var r = 0; r < 4; r++)
            wave.push(invaderTypes[(lvl * 3 + r * 5) % n])
        return wave
    }

    // 0 at level 1, up to 5: angry eyes, then fangs, red glow, spikes, blood-red tint
    function menaceFor(lvl) {
        return Math.min(5, lvl - 1)
    }
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
        var types = waveTypes(level)
        var arr = []
        for (var r = 0; r < rows; r++) {
            for (var c = 0; c < cols; c++) {
                arr.push({ x: marginX + c * spacingX, y: startY + r * spacingY, w: alienW, h: alienH,
                           alive: true, color: rowColors[r % rowColors.length], type: types[r % types.length] })
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

        function box(ctx, x, y, w, h, r) {
            ctx.beginPath()
            if (ctx.roundedRect) ctx.roundedRect(x, y, w, h, r, r)
            else ctx.rect(x, y, w, h)
        }

        function dot(ctx, x, y, r) {
            ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
        }

        // Blend a #rrggbb color toward blood red as menace rises
        function tint(hex, menace) {
            var k = menace * 0.12
            var r = parseInt(hex.substr(1, 2), 16), g = parseInt(hex.substr(3, 2), 16), b = parseInt(hex.substr(5, 2), 16)
            return "rgb(" + Math.round(r + (230 - r) * k) + "," + Math.round(g * (1 - k)) + "," + Math.round(b * (1 - k)) + ")"
        }

        function drawShape(ctx, a, c) {
            var x = a.x, y = a.y, w = a.w, h = a.h, cx = x + w / 2, cy = y + h / 2
            var ink = "#05070a"
            ctx.fillStyle = c
            ctx.strokeStyle = c
            switch (a.type) {
            case "tux":
                dot(ctx, cx, y + h * 0.64, w * 0.38)
                dot(ctx, cx, y + h * 0.28, w * 0.26)
                ctx.fillStyle = ink; dot(ctx, cx - 3, y + h * 0.26, 1.4); dot(ctx, cx + 3, y + h * 0.26, 1.4)
                ctx.fillStyle = "#ffb454"
                ctx.beginPath(); ctx.moveTo(cx - 3, y + h * 0.34); ctx.lineTo(cx + 3, y + h * 0.34)
                ctx.lineTo(cx, y + h * 0.42); ctx.closePath(); ctx.fill()
                break
            case "gear":
                var r = Math.min(w, h) * 0.46
                ctx.beginPath()
                for (var t = 0; t < 12; t++) {
                    var ang = (t / 12) * Math.PI * 2, rr = (t % 2 === 0) ? r : r * 0.72
                    if (t === 0) ctx.moveTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr)
                    else ctx.lineTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr)
                }
                ctx.closePath(); ctx.fill()
                ctx.fillStyle = ink; dot(ctx, cx, cy, r * 0.32)
                break
            case "package":
                box(ctx, x, y, w, h, 3); ctx.fill()
                ctx.strokeStyle = "rgba(5,7,10,0.55)"; ctx.lineWidth = 1.5
                ctx.beginPath(); ctx.moveTo(cx, y); ctx.lineTo(cx, y + h); ctx.moveTo(x, cy); ctx.lineTo(x + w, cy); ctx.stroke()
                break
            case "terminal":
                box(ctx, x, y, w, h, 3); ctx.fill()
                ctx.fillStyle = ink; ctx.fillRect(x + 2, y + 5, w - 4, h - 7)
                ctx.fillStyle = c; ctx.font = "700 9px monospace"; ctx.textAlign = "left"; ctx.textBaseline = "middle"
                ctx.fillText(">_", x + 3, cy + 2)
                break
            case "floppy":
                box(ctx, x + 2, y, w - 4, h, 2); ctx.fill()
                ctx.fillStyle = ink; ctx.fillRect(x + 6, y + 1, w - 12, 5)
                ctx.fillStyle = "#e8e8e8"; ctx.fillRect(x + 5, y + h - 7, w - 10, 6)
                break
            case "mouse":
                box(ctx, x + 4, y, w - 8, h, 7); ctx.fill()
                ctx.strokeStyle = ink; ctx.lineWidth = 1.2
                ctx.beginPath(); ctx.moveTo(cx, y + 1); ctx.lineTo(cx, y + 7); ctx.moveTo(x + 4, y + 7); ctx.lineTo(x + w - 4, y + 7); ctx.stroke()
                break
            case "gamepad":
                box(ctx, x, y + 3, w, h - 5, 6); ctx.fill()
                ctx.fillStyle = ink
                ctx.fillRect(x + 4, cy - 1, 6, 2); ctx.fillRect(x + 6, cy - 3, 2, 6)
                dot(ctx, x + w - 6, cy - 2, 1.4); dot(ctx, x + w - 4, cy + 1, 1.4)
                break
            case "coffee":
                box(ctx, x + 3, y + 3, w - 9, h - 3, 2); ctx.fill()
                ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x + w - 6, cy + 2, 3, -Math.PI / 2, Math.PI / 2); ctx.stroke()
                ctx.strokeStyle = "rgba(255,255,255,0.6)"; ctx.lineWidth = 1
                ctx.beginPath(); ctx.moveTo(x + 7, y + 2); ctx.lineTo(x + 8, y - 2); ctx.moveTo(x + 11, y + 2); ctx.lineTo(x + 12, y - 2); ctx.stroke()
                break
            case "paperclip":
                ctx.lineWidth = 2
                box(ctx, x + 6, y, 10, h, 5); ctx.stroke()
                box(ctx, x + 8, y + 3, 6, h - 6, 3); ctx.stroke()
                ctx.fillStyle = "#ffffff"; dot(ctx, x + 9, y + 5, 1.8); dot(ctx, x + 14, y + 5, 1.8)
                ctx.fillStyle = ink; dot(ctx, x + 9, y + 5, 0.8); dot(ctx, x + 14, y + 5, 0.8)
                break
            case "harddrive":
                box(ctx, x, y, w, h, 2); ctx.fill()
                ctx.fillStyle = "#cfd6dd"; dot(ctx, cx - 2, cy, 5.5)
                ctx.fillStyle = ink; dot(ctx, cx - 2, cy, 1.2)
                ctx.strokeStyle = ink; ctx.lineWidth = 1.4
                ctx.beginPath(); ctx.moveTo(x + w - 3, y + 3); ctx.lineTo(cx, cy + 1); ctx.stroke()
                break
            case "cdrom":
                dot(ctx, cx, cy, h * 0.55)
                ctx.fillStyle = "rgba(255,255,255,0.35)"; dot(ctx, cx - 2, cy - 2, h * 0.3)
                ctx.fillStyle = ink; dot(ctx, cx, cy, 2)
                break
            case "ram":
                ctx.fillRect(x, y + 3, w, h - 7)
                ctx.fillStyle = ink
                for (var k = 0; k < 4; k++) ctx.fillRect(x + 2 + k * 5, y + 5, 3, h - 11)
                ctx.fillStyle = "#ffd76b"
                for (var p2 = 0; p2 < 6; p2++) ctx.fillRect(x + 1 + p2 * 3.6, y + h - 4, 2, 3)
                break
            case "cpu":
                ctx.fillRect(x + 4, y + 2, w - 8, h - 4)
                for (var q = 0; q < 4; q++) {
                    ctx.fillRect(x + 6 + q * 3.5, y, 1.5, 2); ctx.fillRect(x + 6 + q * 3.5, y + h - 2, 1.5, 2)
                    ctx.fillRect(x + 1, y + 4 + q * 2.5, 3, 1.2); ctx.fillRect(x + w - 4, y + 4 + q * 2.5, 3, 1.2)
                }
                ctx.fillStyle = ink; ctx.fillRect(cx - 3, cy - 3, 6, 6)
                break
            case "pacman":
                ctx.fillStyle = "#ffd400"
                ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, h * 0.55, 0.25 * Math.PI, 1.75 * Math.PI); ctx.closePath(); ctx.fill()
                ctx.fillStyle = ink; dot(ctx, cx + 1, cy - 4, 1.3)
                break
            case "ghost":
                ctx.beginPath(); ctx.arc(cx, cy, w * 0.36, Math.PI, 0)
                ctx.lineTo(cx + w * 0.36, y + h)
                for (var gx = 3; gx >= 0; gx--) ctx.lineTo(cx - w * 0.36 + gx * w * 0.24, y + h - (gx % 2 === 0 ? 0 : 3))
                ctx.closePath(); ctx.fill()
                ctx.fillStyle = "#ffffff"; dot(ctx, cx - 3, cy - 1, 2.2); dot(ctx, cx + 3, cy - 1, 2.2)
                ctx.fillStyle = "#1d4ed8"; dot(ctx, cx - 2.4, cy - 1, 1); dot(ctx, cx + 3.6, cy - 1, 1)
                break
            case "bug":
                dot(ctx, cx, cy + 1, h * 0.38); dot(ctx, cx, y + 3, 3)
                ctx.lineWidth = 1.2; ctx.beginPath()
                for (var l = -1; l <= 1; l++) {
                    ctx.moveTo(cx - 5, cy + 1 + l * 4); ctx.lineTo(x + 1, cy + l * 5)
                    ctx.moveTo(cx + 5, cy + 1 + l * 4); ctx.lineTo(x + w - 1, cy + l * 5)
                }
                ctx.moveTo(cx - 1, y + 1); ctx.lineTo(cx - 4, y - 2); ctx.moveTo(cx + 1, y + 1); ctx.lineTo(cx + 4, y - 2)
                ctx.stroke()
                ctx.strokeStyle = ink; ctx.beginPath(); ctx.moveTo(cx, cy - 4); ctx.lineTo(cx, cy + 6); ctx.stroke()
                break
            case "usb":
                ctx.fillRect(x + 2, y + 4, w - 9, h - 8)
                ctx.fillStyle = "#cfd6dd"; ctx.fillRect(x + w - 7, y + 5, 6, h - 10)
                ctx.fillStyle = ink; ctx.fillRect(x + w - 5, y + 6, 1.5, 1.5); ctx.fillRect(x + w - 5, y + h - 8, 1.5, 1.5)
                break
            case "wifi":
                ctx.lineWidth = 2
                for (var arcN = 1; arcN <= 3; arcN++) {
                    ctx.beginPath(); ctx.arc(cx, y + h, arcN * 4.5, 1.25 * Math.PI, 1.75 * Math.PI); ctx.stroke()
                }
                dot(ctx, cx, y + h - 1, 1.8)
                break
            case "database":
                ctx.fillRect(x + 4, y + 3, w - 8, h - 6)
                ctx.fillStyle = "rgba(255,255,255,0.45)"
                for (var dbk = 0; dbk < 3; dbk++) ctx.fillRect(x + 4, y + 3 + dbk * 4, w - 8, 1)
                ctx.fillStyle = c; ctx.beginPath(); ctx.arc(cx, y + 3, (w - 8) / 2, Math.PI, 0); ctx.fill()
                break
            case "bsod":
                ctx.fillStyle = "#1e40ff"; box(ctx, x, y, w, h, 2); ctx.fill()
                ctx.fillStyle = "#ffffff"; ctx.font = "700 10px monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle"
                ctx.fillText(":(", cx, cy + 1)
                break
            }
        }

        function drawMenace(ctx, a, menace) {
            var x = a.x, y = a.y, w = a.w, h = a.h, cx = x + w / 2
            if (menace >= 4) {
                ctx.fillStyle = "#ff2030"
                for (var s = 0; s < 3; s++) {
                    var sy = y + 2 + s * 5
                    ctx.beginPath(); ctx.moveTo(x, sy); ctx.lineTo(x - 4, sy + 2); ctx.lineTo(x, sy + 4); ctx.fill()
                    ctx.beginPath(); ctx.moveTo(x + w, sy); ctx.lineTo(x + w + 4, sy + 2); ctx.lineTo(x + w, sy + 4); ctx.fill()
                }
            }
            if (menace >= 1) {
                ctx.fillStyle = "#ff1a1a"; dot(ctx, cx - 4, y + 4, 1.8); dot(ctx, cx + 4, y + 4, 1.8)
                ctx.strokeStyle = "#05070a"; ctx.lineWidth = 1.6
                ctx.beginPath(); ctx.moveTo(cx - 7, y + 1); ctx.lineTo(cx - 2, y + 3); ctx.moveTo(cx + 7, y + 1); ctx.lineTo(cx + 2, y + 3); ctx.stroke()
            }
            if (menace >= 2) {
                ctx.fillStyle = "#ffffff"
                for (var f = -1; f <= 1; f += 2) {
                    ctx.beginPath(); ctx.moveTo(cx + f * 4, y + h - 1); ctx.lineTo(cx + f * 2, y + h - 1); ctx.lineTo(cx + f * 3, y + h + 3); ctx.closePath(); ctx.fill()
                }
            }
        }

        function drawAlien(ctx, a) {
            var menace = root.menaceFor(root.level)
            ctx.save()
            if (menace >= 3) { ctx.shadowColor = "#ff2030"; ctx.shadowBlur = 4 + menace * 2 }
            drawShape(ctx, a, tint(a.color, menace))
            ctx.restore()
            drawMenace(ctx, a, menace)
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
