import QtQuick
import QtQuick.Controls

ComboBox {
    id: optCombo

    property real uiScale: 1.0
    property color primaryColor: "#5597e6"
    property color backgroundColor: "#1a1a1a"
    property color surfaceColor: "#32373c"
    property color textColor: "#fffded"
    property color textMutedColor: "#9CA3AF"

    font.pixelSize: 13 * uiScale
    background: Rectangle {
        implicitHeight: 36 * optCombo.uiScale
        radius: 8
        color: optCombo.backgroundColor
        border.color: optCombo.textMutedColor
        border.width: 2
    }
    indicator: Text {
        x: optCombo.width - width - 12 * optCombo.uiScale
        y: (optCombo.height - height) / 2
        text: "▾"
        color: optCombo.textMutedColor
        font.pixelSize: 14 * optCombo.uiScale
    }
    contentItem: Text {
        text: optCombo.displayText
        color: optCombo.textColor
        font.pixelSize: 13 * optCombo.uiScale
        verticalAlignment: Text.AlignVCenter
        leftPadding: 12 * optCombo.uiScale
        rightPadding: 28 * optCombo.uiScale
        elide: Text.ElideRight
    }
    popup: Popup {
        y: optCombo.height
        width: optCombo.width
        implicitHeight: Math.min(contentItem.implicitHeight, 160 * optCombo.uiScale)
        padding: 1
        background: Rectangle { radius: 8; color: optCombo.surfaceColor; border.color: optCombo.primaryColor; border.width: 1 }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: optCombo.popup.visible ? optCombo.delegateModel : null
            currentIndex: optCombo.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
    }
    delegate: ItemDelegate {
        width: optCombo.width
        highlighted: optCombo.highlightedIndex === index
        contentItem: Text { text: modelData; color: optCombo.textColor; font.pixelSize: 13 * optCombo.uiScale; verticalAlignment: Text.AlignVCenter; leftPadding: 10 }
        background: Rectangle { color: highlighted ? Qt.rgba(optCombo.primaryColor.r, optCombo.primaryColor.g, optCombo.primaryColor.b, 0.25) : "transparent" }
    }
}
