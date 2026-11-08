// Placeholder window. Replace this file when the mockup arrives.
// The Python object "monitor" keeps these names: number, state, elapsed,
// listening, volume, muted, summaryLine. The list model is "lineModel".
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 420
    height: 640
    visible: false
    title: "Fernruf"
    flags: Qt.Window | Qt.WindowStaysOnTopHint

    Connections {
        target: monitor
        function onCallStarted() {
            window.show()
            window.raise()
            window.requestActivate()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        Label {
            text: monitor.number.length ? monitor.number : "No call"
            font.pixelSize: 22
        }
        Label { text: monitor.state }
        Label { text: monitor.elapsed }
        Label {
            text: monitor.summaryLine
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            visible: monitor.summaryLine.length > 0
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: lineModel
            delegate: Label {
                required property string display
                text: display
                width: ListView.view.width
                wrapMode: Text.Wrap
            }
        }

        CheckBox {
            text: "Listen to this call on this computer"
            checked: monitor.listening
            onToggled: monitor.setListening(checked)
        }
        RowLayout {
            Slider {
                Layout.fillWidth: true
                from: 0
                to: 1
                value: monitor.volume
                onMoved: monitor.setVolume(value)
            }
            Button {
                text: monitor.muted ? "Unmute" : "Mute"
                onClicked: monitor.setMuted(!monitor.muted)
            }
        }
    }
}
