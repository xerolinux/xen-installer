import QtQuick
import QtQuick.Controls

Flickable {
    id: guide
    objectName: "guideAccordion"

    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"
    property int openIndex: -1

    contentWidth: width
    contentHeight: guideColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: guide.contentHeight > guide.height ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }

    function key(name) { return "<b><font color=\"" + primaryColor + "\">" + name + "</font></b>" }
    function fs(name) {
        var colors = { btrfs: "#f5b041", ext4: "#34d399", xfs: "#c4b5fd" }
        return "<b><font color=\"" + colors[name] + "\">" + name + "</font></b>"
    }

    readonly property string fsLine: qsTr("Mount <b>/</b> as %1, %2 or %3.").arg(fs("btrfs")).arg(fs("ext4")).arg(fs("xfs"))
    readonly property string efiLine: qsTr("Select the existing EFI partition, click %1, mount <b>/boot/efi</b>, choose <b>Keep</b>. Never format it if another OS uses it.").arg(key(qsTr("Edit")))
    readonly property string nextLine: qsTr("Click %1.").arg(key(qsTr("Next")))

    readonly property var sections: [
        {
            title: qsTr("Use the whole disk, my own layout"),
            steps: [
                qsTr("Pick the disk in <b>Storage device</b>."),
                qsTr("Click %1 (GPT). This removes every partition on the disk at once.").arg(key(qsTr("New Partition Table"))),
                qsTr("Select the free space, click %1: 2000 MiB, <b>fat32</b>, mount <b>/boot/efi</b>.").arg(key(qsTr("Create"))),
                qsTr("Select the remaining free space, %1.").arg(key(qsTr("Create"))) + " " + fsLine,
                qsTr("Optional: <b>/home</b> and <b>swap</b> partitions (give / less space first)."),
                qsTr("Click %1. Nothing is written until Install.").arg(key(qsTr("Next")))
            ],
            note: qsTr("Btrfs: subvolumes (@, @home, ...) are created for you; click <b>Edit…</b> beside Subvolumes to change them. A separate /home is usually not needed with Btrfs."),
            table: []
        },
        {
            title: qsTr("Install in free space next to another OS"),
            steps: [
                qsTr("Select the <b>free space</b>, click %1.").arg(key(qsTr("Create"))) + " " + fsLine,
                efiLine,
                nextLine
            ],
            note: qsTr("No free space? Use \"Shrink a partition\" below, or the simpler <b>Install alongside</b> option on the previous screen."),
            table: []
        },
        {
            title: qsTr("Shrink a partition to make room"),
            steps: [
                qsTr("Select the partition to shrink, click %1.").arg(key(qsTr("Edit"))),
                qsTr("Drag the size bar down (or type a size), keep <b>Content: Keep</b>, click OK."),
                qsTr("Select the new free space, %1.").arg(key(qsTr("Create"))) + " " + fsLine,
                efiLine,
                nextLine
            ],
            note: qsTr("%1 partitions can't be shrunk. %2, %3 and NTFS can. Shrinking Windows: turn off BitLocker and Fast Startup in Windows first.").arg(fs("xfs")).arg(fs("ext4")).arg(fs("btrfs")),
            table: []
        },
        {
            title: qsTr("Replace an old Linux install"),
            steps: [
                qsTr("Select the old Linux root partition, click %1, choose <b>Format</b>.").arg(key(qsTr("Edit"))) + " " + fsLine,
                qsTr("Or %1 it and %2 a new one in the space.").arg(key(qsTr("Delete"))).arg(key(qsTr("Create"))),
                efiLine,
                nextLine
            ],
            note: qsTr("Formatting erases everything on that partition."),
            table: []
        },
        {
            title: qsTr("Keep my existing /home"),
            steps: [
                qsTr("Select your old /home partition, click %1, mount <b>/home</b>, choose <b>Keep</b>.").arg(key(qsTr("Edit"))),
                qsTr("Format or create a separate partition for <b>/</b>.") + " " + fsLine,
                efiLine,
                nextLine
            ],
            note: qsTr("Your files stay. Use the same username as before so the folder matches."),
            table: []
        },
        {
            title: qsTr("Add swap"),
            steps: [
                qsTr("Easiest: on %1 nothing to do, a swap file goes in the @swap subvolume.").arg(fs("btrfs")),
                qsTr("Or %1 a partition with file system <b>linuxswap</b> (no mount point), about the size of your RAM for hibernation.").arg(key(qsTr("Create")))
            ],
            note: "",
            table: []
        },
        {
            title: qsTr("Which file system should I pick?"),
            steps: [],
            note: "",
            table: [
                { name: fs("btrfs"), text: qsTr("Default. Snapshots and rollback, compression. Subvolumes set up for you (Edit… to change). Can shrink.") },
                { name: fs("ext4"), text: qsTr("Simple and proven. No snapshots. Can shrink and grow.") },
                { name: fs("xfs"), text: qsTr("Fast with large files. <b>Can grow but never shrink</b>, so resizing it later is not possible here.") },
                { name: "<b>fat32</b>", text: qsTr("Only for the EFI partition (/boot/efi).") },
                { name: "<b>linuxswap</b>", text: qsTr("Swap partition, no mount point.") }
            ]
        }
    ]

    component BodyText: Text {
        textFormat: Text.StyledText
        wrapMode: Text.WordWrap
        color: guide.textColor
        font.pixelSize: 12 * guide.uiScale
        lineHeight: 1.1
    }

    Column {
        id: guideColumn
        width: guide.width - (guide.contentHeight > guide.height ? 10 * guide.uiScale : 0)
        spacing: 0

        Text {
            text: "\u{1F4A1} " + qsTr("Guide")
            color: guide.textColor
            font.pixelSize: 14 * guide.uiScale
            font.bold: true
        }

        Text {
            objectName: "guidePrompt"
            width: parent.width
            topPadding: 2 * guide.uiScale
            bottomPadding: 4 * guide.uiScale
            text: qsTr("Select what you want to do to see the guide.")
            color: guide.textMutedColor
            font.pixelSize: 12 * guide.uiScale
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: guide.sections

            Column {
                id: section
                required property int index
                required property var modelData
                readonly property bool open: guide.openIndex === index
                width: guideColumn.width

                Item {
                    objectName: "guideHeader_" + section.index
                    property string title: section.modelData.title
                    width: parent.width
                    height: headerText.implicitHeight + 8 * guide.uiScale

                    Text {
                        id: headerText
                        anchors.left: parent.left
                        anchors.right: arrow.left
                        anchors.rightMargin: 6 * guide.uiScale
                        anchors.verticalCenter: parent.verticalCenter
                        text: section.modelData.title
                        color: section.open ? guide.primaryColor : guide.textColor
                        font.pixelSize: 12.5 * guide.uiScale
                        font.bold: true
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        id: arrow
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: section.open ? "▴" : "▾"
                        color: guide.textMutedColor
                        font.pixelSize: 13 * guide.uiScale
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: guide.openIndex = section.open ? -1 : section.index
                    }
                }

                Column {
                    objectName: "guideBody_" + section.index
                    visible: section.open
                    width: parent.width
                    spacing: 3 * guide.uiScale
                    bottomPadding: 8 * guide.uiScale

                    Repeater {
                        model: section.modelData.steps
                        Row {
                            required property int index
                            required property string modelData
                            width: parent.width
                            spacing: 4 * guide.uiScale
                            BodyText {
                                id: number
                                text: (parent.index + 1) + "."
                                width: 16 * guide.uiScale
                            }
                            BodyText {
                                width: parent.width - number.width - parent.spacing
                                text: parent.modelData
                            }
                        }
                    }

                    Repeater {
                        model: section.modelData.table
                        Row {
                            required property var modelData
                            width: parent.width
                            spacing: 6 * guide.uiScale
                            BodyText {
                                id: fsName
                                width: 66 * guide.uiScale
                                text: parent.modelData.name
                                font.pixelSize: 11.5 * guide.uiScale
                            }
                            BodyText {
                                width: parent.width - fsName.width - parent.spacing
                                text: parent.modelData.text
                                font.pixelSize: 11.5 * guide.uiScale
                            }
                        }
                    }

                    BodyText {
                        visible: section.modelData.note !== ""
                        width: parent.width
                        text: section.modelData.note
                        color: guide.textMutedColor
                        font.pixelSize: 11.5 * guide.uiScale
                    }
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: Qt.rgba(guide.textMutedColor.r, guide.textMutedColor.g, guide.textMutedColor.b, 0.25)
                }
            }
        }
    }
}
