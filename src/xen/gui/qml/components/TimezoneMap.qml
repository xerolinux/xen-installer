/*
 * TimezoneMap - real interactive world map (tiled, via QtLocation's "osm"
 * plugin, the same mechanism as Calamares' locale/localeq Map.qml).
 *
 * Needs the qt6-location package (QtLocation + QtPositioning QML modules,
 * plus the "osm" geoservices plugin) - see packaging/xen-installer/
 * PKGBUILD. Tiles come from the bundled provider definition (see
 * gui/bridge.py's osmProvidersDirUrl); the offline nearest-timezone lookup
 * (engine.timezoneForCoordinate(), xen.utils.timezone_lookup) still
 * determines which IANA zone a click/coordinate resolves to, so picking
 * a timezone keeps working even when tile imagery can't load.
 *
 * Sizing: the map fills whatever space it is given at a 2:1 aspect (the
 * whole world's width at an exact tile zoom). It is rendered at a "virtual"
 * size of exactly one world width at a whole zoom level (512px at zoom 1,
 * 1024px at zoom 2) and that single rendered layer is scaled to fit -
 * scaling individual tiles (fractional zoom) left visible 1px seams under
 * the software renderer. Labels, pin and controls are drawn on top at
 * screen scale so they stay crisp.
 */

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtLocation
import QtPositioning
import "map_data/country_labels.js" as CountryLabels

Item {
    id: root

    property string selectedTimezone: ""
    property real uiScale: 1.0

    property color primaryColor: "#5597e6"
    property color accentColor: "#6b9ce8"
    property color surfaceColor: "#32373c"
    property color backgroundColor: "#1a1a1a"
    property color textColor: "#fffded"

    signal timezonePicked(string timezone)

    // Set just before a click's own round trip (timezonePicked ->
    // LocaleView -> Main.qml -> engine.setSelectedTimezone ->
    // selectedTimezone binding, all synchronous) reaches back here via
    // onSelectedTimezoneChanged - without this guard that round trip would
    // re-center the map on the zone's fixed zone1970.tab coordinate
    // (e.g. a representative city, possibly far from the actual click)
    // instead of leaving the pin where the user put it. Selection made via
    // the timezoneCombo (no preceding click) still re-centers, which is
    // correct there since no click position exists to preserve.
    property bool _suppressSync: false

    // Test-facing hooks (also a perfectly reasonable programmatic API):
    // pick a real lat/lon directly instead of simulating a pixel click
    // through the map's live tile projection.
    readonly property bool _havePin: pin.visible
    readonly property real pinLat: pin.visible ? pin.coordinate.latitude : 0
    readonly property real pinLon: pin.visible ? pin.coordinate.longitude : 0

    // Thickness of the opaque frame around the map viewport.
    readonly property int _frame: 5

    readonly property int _displayWidth: Math.max(256, Math.floor(Math.min(width - 2 * _frame, (height - 2 * _frame) * 2)))
    readonly property int _displayHeight: Math.floor(_displayWidth / 2)
    // Zoom 2 once the display is big enough that its tiles' own labels
    // stay readable after scaling down, zoom 1 otherwise.
    readonly property int _baseZoom: _displayWidth >= 680 ? 2 : 1
    readonly property int _virtualWidth: 256 * Math.pow(2, _baseZoom)
    readonly property real _scale: _displayWidth / _virtualWidth

    implicitWidth: 512 + 2 * _frame
    implicitHeight: 256 + 2 * _frame

    // Deferred: applied immediately, the map still has the previous virtual
    // width and clamps a smaller zoom back up to its old minimum.
    on_BaseZoomChanged: Qt.callLater(function() { map.zoomLevel = root._baseZoom })

    function _placePin(lat, lon) {
        pin.coordinate = QtPositioning.coordinate(lat, lon)
        pin.visible = true
        pinGraphic.scale = 0.4
        pinDropAnim.restart()
    }

    function _syncFromSelection() {
        if (root._suppressSync) return
        if (!root.selectedTimezone) return
        var coord = JSON.parse(engine.coordinateForTimezone(root.selectedTimezone))
        if (coord === null) return
        map.center = QtPositioning.coordinate(coord[0], coord[1])
        root._placePin(coord[0], coord[1])
    }

    // Map coordinate -> point in mapViewport (display) coordinates.
    function _toDisplay(coordinate) {
        var p = map.fromCoordinate(coordinate, false)
        return Qt.point(p.x * root._scale, p.y * root._scale)
    }

    function _mapType(types, style) {
        for (var i = 0; i < types.length; i++)
            if (types[i].style === style)
                return types[i]
        return types[0]
    }

    // Test hook: where (lat, lon) is drawn, in window coordinates.
    function _windowPointFor(lat, lon) {
        return mapViewport.mapToItem(null, _toDisplay(QtPositioning.coordinate(lat, lon)))
    }

    readonly property bool _lowZoom: map.zoomLevel < 3
    property var _labelLayout: []

    FontMetrics {
        id: labelMetrics
        font.pixelSize: 10
        font.bold: true
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.5
    }

    // Places country labels, most important first (the data is sorted by
    // Natural Earth's MIN_LABEL), skipping any that would overlap one
    // already placed, so dense regions stay readable.
    function _layoutLabels() {
        var labels = CountryLabels.labels
        var layout = new Array(labels.length)
        if (!map.mapReady || map.zoomLevel >= 3) {
            root._labelLayout = layout
            return
        }
        // Start with the on-map controls as obstacles so no name hides
        // under them.
        var placed = [
            { x: attributionPill.x - 4, y: attributionPill.y - 4, w: attributionPill.width + 8, h: attributionPill.height + 8 },
            { x: zoomButtons.x - 4, y: zoomButtons.y - 4, w: zoomButtons.width + 8, h: zoomButtons.height + 8 }
        ]
        var lineHeight = labelMetrics.height
        // More names as the world gets bigger on screen (its on-screen
        // width expressed as a tile zoom level, plus some headroom).
        var detail = 0.8 + Math.log2(root._displayWidth / 256) + map.zoomLevel - root._baseZoom
        for (var i = 0; i < labels.length; i++) {
            var l = labels[i]
            if (l[3] > detail)
                continue
            var p = root._toDisplay(QtPositioning.coordinate(l[1], l[2]))
            var w = labelMetrics.advanceWidth(l[0].toUpperCase()) + l[0].length * 0.5
            var r = { x: Math.round(p.x - w / 2), y: Math.round(p.y - lineHeight / 2), w: w, h: lineHeight }
            if (r.x < 2 || r.y < 2 || r.x + r.w > mapViewport.width - 2 || r.y + r.h > mapViewport.height - 2)
                continue
            var clear = true
            for (var j = 0; j < placed.length && clear; j++) {
                var q = placed[j]
                clear = r.x + r.w + 4 < q.x || q.x + q.w + 4 < r.x || r.y + r.h + 1 < q.y || q.y + q.h + 1 < r.y
            }
            if (!clear)
                continue
            placed.push(r)
            layout[i] = { x: r.x, y: r.y, show: true }
        }
        root._labelLayout = layout
    }

    // Coalesces the many geometry/camera change signals of one event loop
    // pass into a single layout, run once everything has settled (the
    // on-map controls' positions included).
    function _scheduleLayout() {
        Qt.callLater(root._layoutLabels)
    }

    function _zoomBy(steps) {
        map.zoomLevel = Math.max(map.minimumZoomLevel,
                                 Math.min(map.maximumZoomLevel, Math.round(map.zoomLevel) + steps))
    }

    function _pickAt(lat, lon) {
        var tz = engine.timezoneForCoordinate(lat, lon)
        if (!tz) return
        root._placePin(lat, lon)
        root._suppressSync = true
        root.timezonePicked(tz)
        root._suppressSync = false
    }

    Plugin {
        id: mapPlugin
        name: "osm"

        // Qt's own online providers repository (maps-redirect.qt.io)
        // routes every default OSM map style through Thunderforest, which
        // needs a paid API key and otherwise serves "API Key Required"
        // watermarked tiles. Our bundled provider definition points at a
        // keyless tile service instead - see gui/bridge.py's
        // osmProvidersDirUrl.
        PluginParameter {
            name: "osm.mapping.providersrepository.address"
            value: engine.osmProvidersDirUrl
        }
        PluginParameter {
            name: "osm.useragent"
            value: "XenInstaller/0.2 (+https://xerolinux.xyz)"
        }
    }

    // Outer drop shadow, same concentric plain-rectangles trick as the hero
    // cards (see WelcomeView.qml) - a shader-based DropShadow never renders
    // under the app's mandatory QT_QUICK_BACKEND=software. Grows evenly on
    // every side (nudged slightly down) so the corners stay smooth curves.
    Repeater {
        model: 6
        delegate: Rectangle {
            readonly property int step: index + 1
            anchors.centerIn: mapCard
            anchors.verticalCenterOffset: 3
            width: mapCard.width + step * 4
            height: mapCard.height + step * 4
            radius: mapCard.radius + step * 2
            color: Qt.rgba(0, 0, 0, 0.07)
        }
    }

    Rectangle {
        id: mapCard
        anchors.centerIn: parent
        width: root._displayWidth + 2 * root._frame
        height: root._displayHeight + 2 * root._frame
        radius: 14
        color: root.backgroundColor

        Item {
            id: mapViewport
            anchors.fill: parent
            anchors.margins: root._frame
            clip: true
            onWidthChanged: root._scheduleLayout()
            onHeightChanged: root._scheduleLayout()

            Item {
                width: root._virtualWidth
                height: root._virtualWidth / 2
                scale: root._scale
                transformOrigin: Item.TopLeft
                layer.enabled: true
                layer.smooth: true

                // Physical relief underneath (no labels): the zoomed-out
                // view, and what shows through the street map while its
                // tiles are still downloading after a zoom in. Follows the
                // street map's camera.
                Map {
                    anchors.fill: parent
                    plugin: mapPlugin
                    activeMapType: root._mapType(supportedMapTypes, MapType.TerrainMap)
                    center: map.center
                    zoomLevel: map.zoomLevel
                    // The relief service stops at zoom 8.
                    visible: map.zoomLevel <= 8
                    copyrightsVisible: false
                }

                // Street map, the one the user drives. Fully transparent
                // below zoom 3, where our own country names label the relief
                // instead; from zoom 3 its own country, city and road labels
                // take over. Two fixed-style maps rather than one switching
                // style, so tiles of the old style never linger next to the
                // other's labels (duplicate names) while new tiles download.
                Map {
                    id: map
                    anchors.fill: parent
                    plugin: mapPlugin
                    activeMapType: root._mapType(supportedMapTypes, MapType.StreetMap)
                    color: "transparent"
                    opacity: root._lowZoom ? 0 : 1
                    center: QtPositioning.coordinate(20, 0)
                    zoomLevel: root._baseZoom
                    // Replaced by the compact attribution below - Qt's
                    // built-in banner is a large white box that clashes
                    // with the theme.
                    copyrightsVisible: false

                    Component.onCompleted: root._syncFromSelection()
                    onMapReadyChanged: root._scheduleLayout()
                    onCenterChanged: root._scheduleLayout()
                    onZoomLevelChanged: root._scheduleLayout()
                    onWidthChanged: root._scheduleLayout()
                    onHeightChanged: root._scheduleLayout()
                }

                // Input on its own layer above both maps: pointer handlers
                // on the street map get nothing while it is transparent.
                Item {
                    anchors.fill: parent

                    MouseArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton
                        cursorShape: Qt.PointingHandCursor
                        property var clickCoordinate: map.toCoordinate(Qt.point(mouseX, mouseY))

                        onClicked: root._pickAt(clickCoordinate.latitude, clickCoordinate.longitude)
                    }

                    // Same pan/zoom gestures as Calamares' Map-qt6.qml. A
                    // plain MouseArea with no drag.target only fires
                    // onClicked for a real tap, so dragging never drops a
                    // pin. Wheel zoom moves in whole steps (tiles are never
                    // scaled, see the header) - a touchpad's many small
                    // deltas are accumulated into one notch at a time - and
                    // keeps the point under the cursor in place.
                    WheelHandler {
                        property real accumulated: 0
                        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                        onWheel: (event) => {
                            accumulated += event.angleDelta.y
                            if (Math.abs(accumulated) < 120)
                                return
                            var cursor = Qt.point(event.x, event.y)
                            var anchor = map.toCoordinate(cursor, false)
                            root._zoomBy(accumulated > 0 ? 1 : -1)
                            accumulated = 0
                            var moved = map.fromCoordinate(anchor, false)
                            map.pan(Math.round(moved.x - cursor.x), Math.round(moved.y - cursor.y))
                        }
                    }

                    DragHandler {
                        target: null
                        onTranslationChanged: (delta) => map.pan(-Math.round(delta.x), -Math.round(delta.y))
                    }
                }
            }

            // Country names for the zoomed-out view, where tiles carry no
            // country labels (see the two maps above). Plain items
            // placed by _layoutLabels() rather than map items: MapItemView
            // delegates never painted under the software backend.
            Repeater {
                model: CountryLabels.labels
                delegate: Text {
                    required property var modelData
                    required property int index
                    readonly property var slot: root._labelLayout[index]
                    visible: slot !== undefined && slot.show
                    x: slot ? slot.x : 0
                    y: slot ? slot.y : 0
                    text: modelData[0]
                    font: labelMetrics.font
                    color: "#4f4a3e"
                    style: Text.Outline
                    styleColor: Qt.rgba(1, 1, 1, 0.85)
                }
            }

            Item {
                id: pin
                property var coordinate: QtPositioning.coordinate()
                readonly property point pos: {
                    map.mapReady; map.center; map.zoomLevel; root._scale
                    return root._toDisplay(coordinate)
                }
                visible: false
                x: Math.round(pos.x - width / 2)
                y: Math.round(pos.y - height)
                width: pinGraphic.width
                height: pinGraphic.height

                Item {
                    id: pinGraphic
                    width: 22
                    height: 30
                    transformOrigin: Item.Bottom

                    Rectangle {
                        width: 20
                        height: 20
                        radius: 10
                        anchors.horizontalCenter: parent.horizontalCenter
                        color: root.accentColor
                        border.color: root.backgroundColor
                        border.width: 2
                    }
                    Rectangle {
                        width: 2
                        height: 12
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.top
                        anchors.topMargin: 18
                        color: root.accentColor
                    }
                }

                NumberAnimation {
                    id: pinDropAnim
                    target: pinGraphic; property: "scale"; from: 0.4; to: 1.0
                    duration: 220; easing.type: Easing.OutBack
                }
            }

            Rectangle {
                id: attributionPill
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.margins: 8
                // Leaves room for the zoom buttons in the opposite corner.
                width: Math.min(attribution.implicitWidth + 10, parent.width - zoomButtons.width - 24)
                height: attribution.implicitHeight + 4
                radius: height / 2
                color: Qt.rgba(1, 1, 1, 0.75)

                Text {
                    id: attribution
                    anchors.centerIn: parent
                    width: parent.width - 10
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                    text: "Powered by Esri · Esri, HERE, Garmin, USGS, © OpenStreetMap contributors · Natural Earth"
                    font.pixelSize: 8 * root.uiScale
                    color: "#4a4a4a"
                }
            }

            Column {
                id: zoomButtons
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 8
                spacing: 6

                Repeater {
                    model: [{ label: "+", steps: 1 }, { label: "−", steps: -1 }]
                    delegate: Rectangle {
                        width: 28 * root.uiScale
                        height: width
                        radius: width / 2
                        color: zoomMouse.pressed ? Qt.darker(root.surfaceColor, 1.2) : root.surfaceColor
                        border.color: root.primaryColor
                        border.width: 1

                        Text {
                            anchors.centerIn: parent
                            text: modelData.label
                            color: root.textColor
                            font.bold: true
                            font.pixelSize: 14 * root.uiScale
                        }

                        MouseArea {
                            id: zoomMouse
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root._zoomBy(modelData.steps)
                        }
                    }
                }
            }
        }

        // Rounds the map's corners. The map can't be clipped to a rounded
        // shape: `clip` is always axis-aligned, and per-corner radii and
        // ShapePath.fillItem render nothing useful under the software
        // backend (all confirmed empirically). Instead this ring - a
        // transparent rectangle whose border is exactly the frame - is drawn
        // over the map's edge: its inner edge is the viewport with a curved
        // corner (radius minus border), so it covers just the bits of map
        // outside that curve, in the frame's own colour.
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: root._frame
            border.color: mapCard.color
        }

        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: 1
            border.color: Qt.darker(root.surfaceColor, 1.2)
        }
    }

    onSelectedTimezoneChanged: root._syncFromSelection()
    on_ScaleChanged: root._scheduleLayout()
}
