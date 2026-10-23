/*
 * OnScreenKeyboard - Calamares-style clickable on-screen keyboard with a
 * physical-keypress test highlight.
 *
 * Restyled/restructured port of Calamares' keyboardq/data/Keyboard.qml,
 * using the same per-language-GROUP XML data files (keyboard_data/*.xml,
 * adapted from Calamares, GPL-3.0-or-later - see that directory), but:
 *   - grouped by real XKB layout code (this app's own keymapsModel
 *     values), not Calamares' fuzzy label-prefix match
 *   - restyled via OnScreenKey instead of Calamares' pixmap-backed Key
 *   - the XML is parsed in Python (engine.keyboardLayoutRows(), see
 *     xen.utils.keyboard_data) and handed to QML as JSON, not parsed in
 *     QML - this Qt6 install blocks QML's XMLHttpRequest from reading
 *     local files by default, and separately, Qt6's QtQml.XmlListModel
 *     dropped the XPath `query`-based role extraction Calamares' Qt5-era
 *     approach relies on (XmlListModelRole now only exposes
 *     name/elementName/attributeName). Python's stdlib xml.etree sidesteps
 *     both and matches this app's established pattern of parsing on the
 *     Python side and exposing plain data to QML (e.g. btrfsSubvolumes).
 *   - adds flashKeyForChar(), so physically typing into the paired test
 *     field (see KeyboardView.qml) highlights the matching on-screen key,
 *     confirming the live layout actually produces the expected character
 *     - Calamares' own keyboard is click-to-type only and has no such
 *       physical-keypress feedback
 */

import QtQuick
import QtQuick.Layouts

Item {
    id: keyboard

    property string xkbLayout: "us"
    property string xkbVariant: ""
    property var target: null
    property real uiScale: 1.0

    property color primaryColor: "#5597e6"
    property color surfaceColor: "#32373c"
    property color backgroundColor: "#1a1a1a"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    // All currently-instantiated OnScreenKey items, flat - rebuilt every
    // time the row Repeater finishes (re)loading. Used by flashKeyForChar.
    property var allKeys: []

    implicitHeight: Math.max(1, _rows.length) * keyHeight
    readonly property real keyHeight: 38 * uiScale

    // Per-key width unit, sized from the WIDEST row's own ratio total (not
    // a fixed column count) - using a fixed "columns" constant here
    // previously made every key render far too wide (the physical rows
    // this component draws sum to ~14-15 ratio units each, not 10), which
    // is what "oversized, not fitting" was actually caused by. Shorter
    // rows (e.g. the bottom Ctrl/Alt/Space row) simply don't span the
    // full width - same as a real keyboard - and stay centered via the
    // Row's own anchors.horizontalCenter below.
    readonly property real _maxRowUnits: {
        var m = 1
        for (var i = 0; i < _rows.length; i++) {
            var sum = 0
            for (var j = 0; j < _rows[i].length; j++) sum += (_rows[i][j].ratio || 1)
            if (sum > m) m = sum
        }
        return m
    }
    readonly property real keyWidth: width / _maxRowUnits

    // Parsed layout: array of rows, each an array of
    // {labels, ratio, checkable} objects. Re-fetched from the bridge
    // whenever xkbLayout/xkbVariant changes (plain JS binding -
    // re-evaluates because both are read directly in the expression).
    property var _rows: {
        try {
            return JSON.parse(engine.keyboardLayoutRows(xkbLayout, xkbVariant))
        } catch (e) {
            console.warn("OnScreenKeyboard: failed to parse layout rows for", xkbLayout, xkbVariant, e)
            return []
        }
    }

    // Side effect (clearing the stale key registry) belongs in a change
    // handler, not folded into the _rows binding above.
    onXkbLayoutChanged: allKeys = []
    onXkbVariantChanged: allKeys = []

    function flashKeyForChar(ch) {
        if (!ch) return
        var lower = ch.toLowerCase()
        for (var i = 0; i < allKeys.length; i++) {
            var k = allKeys[i]
            if (k && k.mainLabel && k.mainLabel.toLowerCase() === lower) {
                k.flashed = true
                return
            }
        }
    }

    function _typeChar(text) {
        if (!target) return
        target.text = target.text + text
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter

        Repeater {
            model: keyboard._rows

            delegate: Row {
                id: keyRow
                property var rowData: modelData
                anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined

                Repeater {
                    model: keyRow.rowData

                    delegate: OnScreenKey {
                        id: key
                        width: keyboard.keyWidth * (modelData.ratio || 1)
                        height: keyboard.keyHeight
                        uiScale: keyboard.uiScale
                        isChekable: modelData.checkable
                        keyColor: keyboard.surfaceColor
                        keyBorderColor: keyboard.textMutedColor
                        keyPressedColor: keyboard.primaryColor
                        keyFlashColor: Qt.lighter(keyboard.primaryColor, 1.4)
                        textColor: keyboard.textColor
                        textMutedColor: keyboard.textMutedColor

                        property string command: ""

                        Component.onCompleted: {
                            var list = keyboard.allKeys.slice()
                            list.push(key)
                            keyboard.allKeys = list
                            _applyLabels(modelData.labels)
                        }

                        function _applyLabels(raw) {
                            if (!raw) return
                            var labelSplit
                            if (raw.charAt(0) === "|") {
                                mainLabel = "|"
                                labelSplit = [raw]
                            } else {
                                labelSplit = raw.split(/[|]+/)
                                mainLabel = labelSplit[0].split(/[!]+/)[0]
                            }
                            if (labelSplit[1]) secondaryLabels = [labelSplit[1]]
                            var bangSplit = raw.split(/[!]+/)
                            if (bangSplit.length > 1) command = bangSplit[1]
                        }

                        onClicked: {
                            if (command === "backspace") {
                                if (keyboard.target) keyboard.target.text = keyboard.target.text.slice(0, -1)
                            } else if (command === "tab") {
                                keyboard._typeChar("\t")
                            } else if (!command && mainLabel.length === 1) {
                                keyboard._typeChar(mainLabel)
                            }
                        }
                        onAlternatesClicked: function(symbol) { keyboard._typeChar(symbol) }
                    }
                }
            }
        }
    }
}
