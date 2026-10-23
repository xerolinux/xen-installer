import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Units.js" as Units

ColumnLayout {
    id: panel
    objectName: "alongsidePanel"

    property real uiScale: 1.0
    property string distroName: ""
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property color errorColor: "#EF4444"

    readonly property var disk: {
        var disks = engine.disksModel
        for (var i = 0; i < disks.length; i++)
            if (disks[i].name === engine.selectedDisk) return disks[i]
        return null
    }
    readonly property var blockedReasons: {
        var reasons = {}
        var candidates = engine.alongsideCandidates
        for (var i = 0; i < candidates.length; i++)
            if (!candidates[i].eligible) reasons[candidates[i].number] = candidates[i].reason
        return reasons
    }
    readonly property var chosen: {
        var candidates = engine.alongsideCandidates
        for (var i = 0; i < candidates.length; i++)
            if (candidates[i].eligible && candidates[i].number === engine.alongsidePartition) return candidates[i]
        return null
    }
    readonly property int keptIndex: indexOf(afterBar.shownSegments, function(s) {
        return s.kind === "partition" && !s.newRef && s.number === engine.alongsidePartition })
    readonly property int newIndex: indexOf(afterBar.shownSegments, function(s) { return !!s.newRef })
    readonly property bool splitShown: keptIndex >= 0 && newIndex >= 0
    property string reasonMessage: ""
    default property alias options: optionsFlow.data

    spacing: 6 * uiScale

    readonly property string diskName: engine.selectedDisk
    onDiskNameChanged: reasonMessage = ""

    function indexOf(list, test) {
        for (var i = 0; i < list.length; i++)
            if (test(list[i])) return i
        return -1
    }

    function escaped(text) {
        return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    }

    function describe(part) {
        var name = part.osName || part.label
        return name ? "%1 (%2, %3)".arg(name).arg(part.path).arg(part.fstype)
                    : "%1 (%2)".arg(part.path).arg(part.fstype)
    }

    function setNewSize(mb) {
        engine.setAlongsideNewSizeMb(Math.round(mb))
    }

    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 10 * uiScale
        rowSpacing: 6 * uiScale

        Text {
            text: qsTr("Current:")
            color: textMutedColor
            font.pixelSize: 12 * uiScale
        }

        PartitionBar {
            objectName: "alongsideCurrentBar"
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            uiScale: panel.uiScale
            textColor: panel.textColor
            outlineColor: panel.textMutedColor
            segments: panel.disk ? panel.disk.segments : []
            segmentNamePrefix: "currentSegment_"
            interactive: true
            selectedNumber: engine.alongsidePartition
            blockedReasons: panel.blockedReasons
            onSegmentClicked: function(segment) {
                var reason = panel.blockedReasons[segment.number]
                if (reason) {
                    panel.reasonMessage = qsTr("%1: %2").arg(segment.name).arg(reason)
                } else {
                    panel.reasonMessage = ""
                    engine.setAlongsidePartition(segment.number)
                }
            }
        }

        Text {
            text: qsTr("After:")
            color: textMutedColor
            font.pixelSize: 12 * uiScale
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: afterBar.implicitHeight

            PartitionBar {
                id: afterBar
                objectName: "alongsideAfterBar"
                anchors.fill: parent
                uiScale: panel.uiScale
                textColor: panel.textColor
                outlineColor: panel.textMutedColor
                segments: engine.planPreview
                newLabel: panel.distroName
                selectedNumber: engine.alongsidePartition
            }

            Rectangle {
                id: handle
                objectName: "alongsideHandle"
                visible: panel.splitShown
                width: 8 * uiScale
                height: parent.height + 8 * uiScale
                y: -4 * uiScale
                x: afterBar.geometry[panel.newIndex] ? afterBar.geometry[panel.newIndex].x - width / 2 : 0
                radius: 3
                color: textColor
                border.color: activeFocus ? primaryColor : Qt.rgba(0, 0, 0, 0.5)
                border.width: activeFocus ? 2 : 1
                activeFocusOnTab: true
                Accessible.role: Accessible.Slider
                Accessible.name: qsTr("Size of the new partition")

                property real startX: 0
                property real spanLeft: 0
                property real spanRight: 0
                property real spanMb: 0

                Keys.onLeftPressed: panel.setNewSize(engine.alongsideNewSizeMb + 1024)
                Keys.onRightPressed: panel.setNewSize(engine.alongsideNewSizeMb - 1024)

                HoverHandler { cursorShape: Qt.SizeHorCursor }

                DragHandler {
                    target: null
                    margin: 8 * uiScale
                    yAxis.enabled: false
                    cursorShape: Qt.SizeHorCursor
                    onActiveChanged: {
                        if (!active || !panel.splitShown) return
                        handle.forceActiveFocus()
                        var kept = afterBar.geometry[panel.keptIndex]
                        var created = afterBar.geometry[panel.newIndex]
                        var segs = afterBar.shownSegments
                        handle.startX = created.x
                        handle.spanLeft = kept.x
                        handle.spanRight = created.x + created.w
                        handle.spanMb = (segs[panel.keptIndex].sizeBytes + segs[panel.newIndex].sizeBytes) / 1048576
                    }
                    onTranslationChanged: {
                        if (!active || handle.spanRight <= handle.spanLeft) return
                        var x = handle.startX + translation.x
                        panel.setNewSize((handle.spanRight - x) / (handle.spanRight - handle.spanLeft) * handle.spanMb)
                    }
                }
            }
        }
    }

    Text {
        objectName: "alongsideReasonText"
        Layout.fillWidth: true
        visible: panel.reasonMessage !== ""
        text: panel.reasonMessage
        color: errorColor
        font.pixelSize: 12 * uiScale
        wrapMode: Text.WordWrap
    }

    Text {
        objectName: "alongsideSizeText"
        Layout.fillWidth: true
        visible: panel.chosen !== null && panel.splitShown
        textFormat: Text.StyledText
        readonly property var kept: afterBar.shownSegments[panel.keptIndex]
        readonly property var created: afterBar.shownSegments[panel.newIndex]
        text: kept && created && panel.chosen
            ? qsTr("%1 will be shrunk to %2 and a new %3 partition will be created for %4.")
                .arg("<b>" + panel.escaped(panel.describe(panel.chosen)) + "</b>")
                .arg("<b>" + Units.humanSize(kept.sizeBytes) + "</b>")
                .arg("<b>" + Units.humanSize(created.sizeBytes) + "</b>")
                .arg(panel.escaped(panel.distroName))
            : ""
        color: textColor
        font.pixelSize: 12 * uiScale
        wrapMode: Text.WordWrap
    }

    Flow {
        id: optionsFlow
        Layout.fillWidth: true
        spacing: 10 * uiScale

        Text {
            objectName: "alongsideEspText"
            width: Math.min(implicitWidth, optionsFlow.width)
            height: Math.max(implicitHeight, 36 * uiScale)
            visible: engine.espCandidates.length === 1 && !!engine.alongsideEsp.path
            text: qsTr("The EFI system partition at %1 will be reused (not formatted).").arg(engine.alongsideEsp.path || "")
            color: textMutedColor
            font.pixelSize: 12 * uiScale
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WordWrap
        }

        Row {
            visible: engine.espCandidates.length > 1
            spacing: 10 * uiScale

            Text {
                height: espCombo.height
                verticalAlignment: Text.AlignVCenter
                text: qsTr("EFI system partition:")
                color: textColor
                font.pixelSize: 12 * uiScale
            }

            OptionCombo {
                id: espCombo
                objectName: "alongsideEspCombo"
                width: 260 * uiScale
                uiScale: panel.uiScale
                primaryColor: panel.primaryColor
                backgroundColor: panel.backgroundColor
                surfaceColor: panel.surfaceColor
                textColor: panel.textColor
                textMutedColor: panel.textMutedColor
                model: engine.espCandidates.map(function(e) { return e.path + "  " + Units.humanSize(e.sizeBytes) })
                currentIndex: panel.indexOf(engine.espCandidates, function(e) {
                    return e.disk === engine.alongsideEsp.disk && e.number === engine.alongsideEsp.number })
                onActivated: function(index) {
                    var e = engine.espCandidates[index]
                    engine.setAlongsideEsp(JSON.stringify({ disk: e.disk, number: e.number }))
                }
            }
        }
    }
}
