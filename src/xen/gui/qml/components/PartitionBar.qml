import QtQuick
import QtQuick.Controls
import "Units.js" as Units
import "PartitionColors.js" as PartitionColors

Item {
    id: bar

    property var segments: []
    property real uiScale: 1.0
    property string segmentNamePrefix: ""
    property bool interactive: false
    property bool showLabels: true
    property int selectedNumber: 0
    // partition number -> reason it can't be picked; those segments are dimmed
    property var blockedReasons: ({})
    property string newLabel: ""
    property color textColor: "#fffded"
    property color outlineColor: "#9CA3AF"

    signal segmentClicked(var segment)

    readonly property var shownSegments: segments.filter(function(s) { return !s.pendingDelete })
    readonly property var geometry: layoutSegments(shownSegments, width)

    implicitHeight: 28 * uiScale

    function segmentLabel(seg) {
        if (seg.kind === "free") return qsTr("Free")
        if (seg.newRef) return newLabel
        return seg.osName || seg.label || seg.name || ""
    }

    // Proportional widths, but every partition stays visible and clickable
    // (a 16 MiB MSR next to 500 GiB would otherwise be 0 px wide).
    function layoutSegments(segs, total) {
        var minWidth = 8 * uiScale
        var bytes = 0
        for (var i = 0; i < segs.length; i++) bytes += Math.max(0, segs[i].sizeBytes)
        var widths = []
        var fixedWidth = 0
        var flexBytes = 0
        for (i = 0; i < segs.length; i++) {
            var raw = bytes > 0 ? total * segs[i].sizeBytes / bytes : 0
            if (segs[i].kind === "free" && segs[i].sizeBytes < bytes / 100) {
                widths.push(0)
            } else if (raw < minWidth) {
                widths.push(minWidth)
                fixedWidth += minWidth
            } else {
                widths.push(-1)
                flexBytes += segs[i].sizeBytes
            }
        }
        var x = 0
        var result = []
        for (i = 0; i < segs.length; i++) {
            var w = widths[i] >= 0 ? widths[i]
                : Math.max(0, total - fixedWidth) * segs[i].sizeBytes / flexBytes
            result.push({ x: x, w: w })
            x += w
        }
        return result
    }

    readonly property real cornerRadius: 6
    readonly property int firstShown: edgeIndex(geometry, false)
    readonly property int lastShown: edgeIndex(geometry, true)

    function edgeIndex(geo, fromEnd) {
        for (var k = 0; k < geo.length; k++) {
            var i = fromEnd ? geo.length - 1 - k : k
            if (geo[i].w > 0) return i
        }
        return -1
    }

    // clip is axis-aligned under the software backend, so the end segments
    // round their own outer corners instead of relying on the track.
    Rectangle {
        id: track
        anchors.fill: parent
        radius: bar.cornerRadius
        color: PartitionColors.free

        Repeater {
            model: bar.shownSegments

            Item {
                id: seg
                objectName: bar.segmentNamePrefix && modelData.number
                    ? bar.segmentNamePrefix + modelData.number : ""
                readonly property string reason: modelData.number ? (bar.blockedReasons[modelData.number] || "") : ""
                readonly property bool selected: modelData.kind === "partition" && !modelData.newRef
                    && modelData.number === bar.selectedNumber
                readonly property bool atStart: index === bar.firstShown
                readonly property bool atEnd: index === bar.lastShown
                readonly property color fill: PartitionColors.forSegment(modelData)
                x: bar.geometry[index] ? bar.geometry[index].x : 0
                width: bar.geometry[index] ? bar.geometry[index].w : 0
                height: track.height
                opacity: reason !== "" ? 0.35 : 1

                Rectangle {
                    anchors.fill: parent
                    radius: seg.atStart || seg.atEnd ? bar.cornerRadius : 0
                    color: seg.fill
                }

                Rectangle {
                    visible: seg.atStart !== seg.atEnd
                    x: seg.atStart ? bar.cornerRadius : 0
                    width: Math.max(0, parent.width - bar.cornerRadius)
                    height: parent.height
                    color: seg.fill
                }

                Rectangle {
                    visible: !seg.atEnd
                    anchors.right: parent.right
                    width: 1
                    height: parent.height
                    color: Qt.rgba(0, 0, 0, 0.35)
                }

                Rectangle {
                    visible: seg.selected
                    anchors.fill: parent
                    radius: seg.atStart || seg.atEnd ? bar.cornerRadius : 0
                    color: "transparent"
                    border.color: bar.textColor
                    border.width: 3
                }

                Text {
                    anchors.fill: parent
                    anchors.leftMargin: 6 * bar.uiScale
                    anchors.rightMargin: 6 * bar.uiScale
                    visible: bar.showLabels && seg.width > 48 * bar.uiScale
                    text: bar.segmentLabel(modelData) + "  " + Units.humanSize(modelData.sizeBytes)
                    color: bar.textColor
                    font.pixelSize: 11 * bar.uiScale
                    font.bold: seg.selected || !!modelData.newRef
                    style: Text.Raised
                    styleColor: Qt.rgba(0, 0, 0, 0.5)
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                ToolTip.visible: segHover.hovered && seg.width > 0
                ToolTip.text: {
                    var head = modelData.kind === "free"
                        ? qsTr("Free space")
                        : (modelData.newRef ? bar.newLabel : (modelData.name || "")) + (modelData.fstype ? " (" + modelData.fstype + ")" : "")
                    var line = head + "  " + Units.humanSize(modelData.sizeBytes)
                    return seg.reason !== "" ? line + "\n" + seg.reason : line
                }

                HoverHandler { id: segHover }

                MouseArea {
                    anchors.fill: parent
                    enabled: bar.interactive && modelData.kind === "partition"
                    cursorShape: seg.reason === "" ? Qt.PointingHandCursor : Qt.ForbiddenCursor
                    onClicked: bar.segmentClicked(modelData)
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: bar.cornerRadius
        color: "transparent"
        border.color: Qt.rgba(bar.outlineColor.r, bar.outlineColor.g, bar.outlineColor.b, 0.25)
        border.width: 1
    }
}
