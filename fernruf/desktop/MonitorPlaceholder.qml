import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: chatOpen ? 1080 : 400
    height: 720
    visible: false
    title: qsTr("Fernruf")
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.Window | Qt.WindowStaysOnTopHint

    readonly property color cyan: "#9BE7FF"
    readonly property int unitRadius: 24
    property bool chatOpen: true
    property int phonePage: 1
    property string tracked: ""

    function statusLine() {
        var elapsed = monitor.elapsed
        if (monitor.state === "dialing")
            return qsTr("Dialing…  %1").arg(elapsed)
        if (monitor.state === "ringing")
            return qsTr("Ringing…  %1").arg(elapsed)
        if (monitor.state === "answered")
            return qsTr("Connected  ·  %1").arg(elapsed)
        if (monitor.state === "ended" || monitor.state === "finalized")
            return qsTr("Ended  ·  %1").arg(elapsed)
        return qsTr("Ready")
    }

    function returnToCall() {
        phonePage = 1
    }

    function toggleTranscript() {
        chatOpen = !chatOpen
    }

    function chatStatus() {
        if (monitor.errorMessage.length)
            return qsTr("Connection lost")
        if (monitor.state === "ended")
            return qsTr("Summary is being written…")
        if (monitor.state === "finalized")
            return qsTr("Finished")
        if (monitor.state === "answered" && monitor.listening)
            return qsTr("Listening on · local playback")
        if (monitor.state === "answered")
            return qsTr("Listening off")
        if (monitor.state === "dialing" || monitor.state === "ringing")
            return qsTr("Waiting for the call")
        return ""
    }

    readonly property bool callOpen: monitor.state === "dialing"
        || monitor.state === "ringing"
        || monitor.state === "answered"
    readonly property bool audioReady: callOpen && monitor.listening && !monitor.errorMessage.length
    readonly property bool showAsk: monitor.state === "answered"
        && !monitor.listening
        && !monitor.askDismissed
        && !monitor.errorMessage.length

    Connections {
        target: monitor
        function onCallStarted() {
            if (!raiseOnCall)
                return
            window.show()
            if (canRaise) {
                window.raise()
                window.requestActivate()
            }
        }
        function onChanged() {
            if (monitor.state === "dialing" && window.tracked !== "dialing")
                window.phonePage = 1
            window.tracked = monitor.state
        }
    }

    onClosing: function (close) {
        close.accepted = false
        window.hide()
    }

    Shortcut {
        objectName: "backShortcut"
        sequence: "Escape"
        enabled: window.phonePage === 0
        onActivated: window.returnToCall()
    }

    Shortcut {
        objectName: "hideShortcut"
        sequence: "Escape"
        enabled: window.phonePage !== 0
        onActivated: window.hide()
    }

    component Tint: Image {
        property string iconName: ""
        property bool active: false
        property int side: 28
        width: side
        height: side
        source: Qt.resolvedUrl("icons/" + iconName + (active ? "-cyan.svg" : ".svg"))
        sourceSize.width: side * 2
        sourceSize.height: side * 2
        fillMode: Image.PreserveAspectFit
        smooth: true
    }

    component RoundButton: AbstractButton {
        id: control
        property string iconName: ""
        property string caption: ""
        property int diameter: 56
        property color iconTint: checked ? window.cyan : "white"
        property color fill: checked ? Qt.rgba(1, 1, 1, 0.32) : Qt.rgba(1, 1, 1, 0.18)
        property bool solidWhenOff: false
        checkable: false
        implicitWidth: Math.max(diameter, captionLabel.implicitWidth + 8)
        implicitHeight: diameter + (caption.length ? 24 : 0)
        opacity: enabled || solidWhenOff ? 1 : 0.38

        background: Rectangle {
            width: control.diameter
            height: control.diameter
            radius: width / 2
            color: control.fill
            x: (control.width - width) / 2
        }

        contentItem: Item {
            Tint {
                iconName: control.iconName
                active: control.checked
                side: Math.round(control.diameter * 0.46)
                x: (parent.width - width) / 2
                y: (control.diameter - height) / 2
            }
            Text {
                id: captionLabel
                text: control.caption
                color: Qt.rgba(1, 1, 1, 0.8)
                font.pixelSize: 12
                font.family: "Noto Sans"
                anchors.horizontalCenter: parent.horizontalCenter
                y: control.diameter + 6
            }
        }
    }

    component PillButton: AbstractButton {
        id: pill
        property string label: ""
        implicitHeight: 30
        implicitWidth: pillText.implicitWidth + 24
        background: Rectangle {
            radius: 15
            color: Qt.rgba(1, 1, 1, 0.18)
        }
        contentItem: Text {
            id: pillText
            text: pill.label
            color: "white"
            font.pixelSize: 12
            font.family: "Noto Sans"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component WindowButton: AbstractButton {
        id: control
        property string iconName: ""
        implicitWidth: 28
        implicitHeight: 28
        background: Rectangle {
            radius: 14
            color: control.hovered ? Qt.rgba(1, 1, 1, 0.24) : Qt.rgba(1, 1, 1, 0.12)
        }
        contentItem: Item {
            Tint {
                iconName: control.iconName
                side: 16
                anchors.centerIn: parent
            }
        }
    }

    Item {
        id: unit
        anchors.fill: parent

        Rectangle {
            id: plate
            objectName: "unitPlate"
            anchors.fill: parent
            radius: window.unitRadius
            color: frame.opaque ? "#1a2240" : "transparent"
            border.color: Qt.rgba(1, 1, 1, 0.08)
            border.width: 1
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            onPressed: function (mouse) {
                if (mouse.button === Qt.LeftButton)
                    window.startSystemMove()
            }
        }

        Item {
            id: phone
            width: 400
            height: parent.height
            visible: width > 0
            clip: true

            Rectangle {
                anchors.fill: parent
                color: Qt.rgba(1, 1, 1, 0.14)
                topLeftRadius: window.unitRadius
                bottomLeftRadius: window.unitRadius
                topRightRadius: window.chatOpen ? 0 : window.unitRadius
                bottomRightRadius: window.chatOpen ? 0 : window.unitRadius
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 26
                anchors.rightMargin: 26
                anchors.topMargin: window.chatOpen ? 22 : 48
                anchors.bottomMargin: 16
                spacing: 0

                Label {
                    text: monitor.number.length ? monitor.number : "+•• ••••"
                    color: "white"
                    font.pixelSize: 28
                    font.weight: 600
                    font.family: "Noto Sans"
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: window.statusLine()
                    color: Qt.rgba(1, 1, 1, 0.8)
                    font.pixelSize: 14
                    font.family: "Noto Sans"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 4
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.topMargin: 12
                    currentIndex: window.phonePage

                    ColumnLayout {
                        spacing: 14
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            AbstractButton {
                                objectName: "backToCall"
                                implicitHeight: 34
                                implicitWidth: backLabel.implicitWidth + 40
                                Accessible.name: qsTr("Back")
                                Accessible.role: Accessible.Button
                                onClicked: window.returnToCall()
                                background: Rectangle {
                                    radius: 17
                                    color: Qt.rgba(1, 1, 1, 0.22)
                                }
                                contentItem: Row {
                                    spacing: 6
                                    leftPadding: 10
                                    rightPadding: 12
                                    Tint {
                                        iconName: "go-previous"
                                        side: 16
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        id: backLabel
                                        text: qsTr("Back")
                                        color: "white"
                                        font.pixelSize: 13
                                        font.family: "Noto Sans"
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                            }
                            Item { Layout.fillWidth: true }
                            AbstractButton {
                                objectName: "transcriptOnDialer"
                                implicitHeight: 34
                                implicitWidth: dialerTranscriptLabel.implicitWidth + 40
                                Accessible.name: qsTr("Transcript")
                                Accessible.role: Accessible.Button
                                onClicked: window.toggleTranscript()
                                background: Rectangle {
                                    radius: 17
                                    color: window.chatOpen ? Qt.rgba(1, 1, 1, 0.32) : Qt.rgba(1, 1, 1, 0.18)
                                }
                                contentItem: Row {
                                    spacing: 6
                                    leftPadding: 10
                                    rightPadding: 12
                                    Tint {
                                        iconName: "list"
                                        side: 16
                                        active: window.chatOpen
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        id: dialerTranscriptLabel
                                        text: qsTr("Transcript")
                                        color: "white"
                                        font.pixelSize: 13
                                        font.family: "Noto Sans"
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                            }
                        }
                        Row {
                            id: phoneTabs
                            property int index: 1
                            Layout.alignment: Qt.AlignHCenter
                            spacing: 6
                            Repeater {
                                model: [
                                    { "name": qsTr("History"), "icon": "history" },
                                    { "name": qsTr("Keypad"), "icon": "dialpad" },
                                    { "name": qsTr("Contacts"), "icon": "contact" }
                                ]
                                AbstractButton {
                                    required property var modelData
                                    required property int index
                                    implicitHeight: 34
                                    implicitWidth: tabLabel.implicitWidth + 40
                                    onClicked: phoneTabs.index = index
                                    background: Rectangle {
                                        radius: 17
                                        color: phoneTabs.index === index ? Qt.rgba(1, 1, 1, 0.32) : "transparent"
                                    }
                                    contentItem: Row {
                                        spacing: 6
                                        leftPadding: 10
                                        Tint {
                                            iconName: modelData.icon
                                            side: 16
                                            active: phoneTabs.index === index
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        Text {
                                            id: tabLabel
                                            text: modelData.name
                                            color: "white"
                                            font.pixelSize: 12
                                            font.family: "Noto Sans"
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                    }
                                }
                            }
                        }

                        StackLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            currentIndex: phoneTabs.index
                            Item {
                                Label {
                                    anchors.centerIn: parent
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.Wrap
                                    color: Qt.rgba(1, 1, 1, 0.65)
                                    text: qsTr("History is not in this version yet.")
                                    font.family: "Noto Sans"
                                }
                            }
                            ColumnLayout {
                                spacing: 16
                                GridLayout {
                                    columns: 3
                                    columnSpacing: 22
                                    rowSpacing: 14
                                    Layout.alignment: Qt.AlignHCenter
                                    Repeater {
                                        model: [
                                            { "d": "1", "s": "" },
                                            { "d": "2", "s": "ABC" },
                                            { "d": "3", "s": "DEF" },
                                            { "d": "4", "s": "GHI" },
                                            { "d": "5", "s": "JKL" },
                                            { "d": "6", "s": "MNO" },
                                            { "d": "7", "s": "PQRS" },
                                            { "d": "8", "s": "TUV" },
                                            { "d": "9", "s": "WXYZ" },
                                            { "d": "*", "s": "" },
                                            { "d": "0", "s": "+" },
                                            { "d": "#", "s": "" }
                                        ]
                                        AbstractButton {
                                            required property var modelData
                                            implicitWidth: 72
                                            implicitHeight: 72
                                            enabled: sendDtmf
                                            opacity: 1
                                            background: Rectangle {
                                                radius: width / 2
                                                color: Qt.rgba(1, 1, 1, 0.18)
                                            }
                                            contentItem: Column {
                                                anchors.centerIn: parent
                                                spacing: 0
                                                Label {
                                                    text: modelData.d
                                                    color: "white"
                                                    font.pixelSize: 22
                                                    font.family: "Noto Sans"
                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                }
                                                Label {
                                                    text: modelData.s
                                                    color: Qt.rgba(1, 1, 1, 0.65)
                                                    font.pixelSize: 10
                                                    font.family: "Noto Sans"
                                                    anchors.horizontalCenter: parent.horizontalCenter
                                                    visible: text.length > 0
                                                }
                                            }
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.alignment: Qt.AlignHCenter
                                    spacing: 28
                                    RoundButton {
                                        diameter: 48
                                        iconName: "contact"
                                        enabled: false
                                    }
                                    RoundButton {
                                        diameter: 72
                                        iconName: "call-start"
                                        fill: "#2FDB6A"
                                        iconTint: "white"
                                        solidWhenOff: true
                                        enabled: false
                                    }
                                    RoundButton {
                                        diameter: 48
                                        iconName: "backspace"
                                        enabled: false
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    horizontalAlignment: Text.AlignHCenter
                                    color: Qt.rgba(1, 1, 1, 0.65)
                                    font.pixelSize: 11
                                    font.family: "Noto Sans"
                                    text: qsTr("Keypad is display only. The agent places the call.")
                                }
                            }
                            Item {
                                Label {
                                    anchors.centerIn: parent
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.Wrap
                                    color: Qt.rgba(1, 1, 1, 0.65)
                                    text: qsTr("Contacts are not in this version yet.")
                                    font.family: "Noto Sans"
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        spacing: 8
                        Item {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.topMargin: 8
                            width: 122
                            height: 122
                            Canvas {
                                id: ring
                                anchors.fill: parent
                                visible: monitor.state === "dialing" || monitor.state === "ringing"
                                onPaint: {
                                    var ctx = getContext("2d")
                                    ctx.reset()
                                    ctx.strokeStyle = "rgba(255,255,255,0.55)"
                                    ctx.lineWidth = 2
                                    ctx.beginPath()
                                    ctx.arc(width / 2, height / 2, width / 2 - 3, -0.4, Math.PI * 1.15)
                                    ctx.stroke()
                                }
                                Component.onCompleted: requestPaint()
                                RotationAnimation on rotation {
                                    from: 0
                                    to: 360
                                    duration: 1400
                                    loops: Animation.Infinite
                                    running: ring.visible
                                }
                            }
                            Rectangle {
                                width: 110
                                height: 110
                                radius: 55
                                anchors.centerIn: parent
                                color: Qt.rgba(1, 1, 1, 0.18)
                                Tint {
                                    iconName: "person"
                                    side: 48
                                    anchors.centerIn: parent
                                }
                            }
                        }
                        Label {
                            text: monitor.calleeName
                            visible: text.length > 0
                            color: "white"
                            font.pixelSize: 18
                            font.weight: 600
                            font.family: "Noto Sans"
                            Layout.alignment: Qt.AlignHCenter
                            Layout.topMargin: 6
                        }
                        Label {
                            text: monitor.campaign
                            visible: text.length > 0
                            color: Qt.rgba(1, 1, 1, 0.65)
                            font.pixelSize: 13
                            font.family: "Noto Sans"
                            Layout.alignment: Qt.AlignHCenter
                        }
                        Column {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.topMargin: 12
                            spacing: 14
                            Row {
                                spacing: 26
                                anchors.horizontalCenter: parent.horizontalCenter
                                RoundButton {
                                    iconName: monitor.muted ? "volume-muted" : "volume-high"
                                    caption: qsTr("Mute")
                                    checked: monitor.muted
                                    enabled: window.audioReady
                                    onClicked: monitor.setMuted(!monitor.muted)
                                }
                                RoundButton {
                                    iconName: "headphones"
                                    caption: qsTr("Live listen")
                                    checked: monitor.listening
                                    enabled: window.callOpen && !monitor.errorMessage.length
                                    onClicked: monitor.setListening(!monitor.listening)
                                }
                                RoundButton {
                                    iconName: "volume-high"
                                    caption: qsTr("Volume")
                                    enabled: window.audioReady
                                }
                            }
                            Row {
                                spacing: 26
                                anchors.horizontalCenter: parent.horizontalCenter
                                RoundButton {
                                    objectName: "openKeypad"
                                    iconName: "dialpad"
                                    caption: qsTr("Keypad")
                                    enabled: !monitor.errorMessage.length
                                    onClicked: window.phonePage = 0
                                }
                                RoundButton {
                                    objectName: "transcriptToggle"
                                    iconName: "list"
                                    caption: qsTr("Transcript")
                                    onClicked: window.toggleTranscript()
                                }
                                RoundButton {
                                    iconName: "record"
                                    caption: qsTr("Record")
                                    visible: showRecordButton
                                    iconTint: "#FF8A9B"
                                    enabled: false
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: 8
                            Layout.leftMargin: 8
                            Layout.rightMargin: 8
                            enabled: window.audioReady
                            opacity: enabled ? 1 : 0.38
                            Tint { iconName: "volume-high"; side: 16; Layout.alignment: Qt.AlignVCenter }
                            Slider {
                                id: volume
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: monitor.volume
                                onMoved: monitor.setVolume(value)
                                background: Rectangle {
                                    x: volume.leftPadding
                                    y: volume.topPadding + volume.availableHeight / 2 - height / 2
                                    width: volume.availableWidth
                                    height: 4
                                    radius: 2
                                    color: Qt.rgba(1, 1, 1, 0.18)
                                    Rectangle {
                                        width: volume.visualPosition * parent.width
                                        height: parent.height
                                        color: window.cyan
                                        radius: 2
                                    }
                                }
                                handle: Rectangle {
                                    x: volume.leftPadding + volume.visualPosition * (volume.availableWidth - width)
                                    y: volume.topPadding + volume.availableHeight / 2 - height / 2
                                    width: 16
                                    height: 16
                                    radius: 8
                                    color: "white"
                                }
                            }
                            Label {
                                text: Math.round(monitor.volume * 100) + " %"
                                color: Qt.rgba(1, 1, 1, 0.8)
                                font.pixelSize: 12
                                font.family: "Noto Sans"
                            }
                        }
                        Item { Layout.fillHeight: true }
                        RoundButton {
                            Layout.alignment: Qt.AlignHCenter
                            diameter: 72
                            iconName: "call-stop"
                            fill: "#E24B4B"
                            iconTint: "white"
                            enabled: hangupEnabled && window.callOpen && !monitor.errorMessage.length
                        }
                        Label {
                            text: qsTr("Hang up. Available after v1")
                            color: Qt.rgba(1, 1, 1, 0.65)
                            font.pixelSize: 12
                            font.family: "Noto Sans"
                            Layout.alignment: Qt.AlignHCenter
                            Layout.bottomMargin: 4
                        }
                    }
                }
            }
        }

        Item {
            id: chat
            objectName: "transcriptPanel"
            visible: window.chatOpen
            x: 400
            width: parent.width - 400
            height: parent.height
            z: 1

            Rectangle {
                anchors.fill: parent
                color: Qt.rgba(1, 1, 1, 0.08)
                topRightRadius: window.unitRadius
                bottomRightRadius: window.unitRadius
                topLeftRadius: 0
                bottomLeftRadius: 0
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    height: 52
                    color: "transparent"
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 18
                        anchors.rightMargin: 86
                        Tint { iconName: "chat"; side: 18 }
                        Label {
                            text: qsTr("Live transcript")
                            color: "white"
                            font.pixelSize: 15
                            font.family: "Noto Sans"
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: window.chatStatus()
                            color: Qt.rgba(1, 1, 1, 0.65)
                            font.pixelSize: 12
                            font.family: "Noto Sans"
                        }
                    }
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: 1
                        color: Qt.rgba(1, 1, 1, 0.14)
                    }
                }

                Rectangle {
                    visible: monitor.errorMessage.length > 0
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Layout.topMargin: 12
                    implicitHeight: errorCol.implicitHeight + 24
                    radius: 16
                    color: Qt.rgba(1, 0.45, 0.55, 0.2)
                    border.color: Qt.rgba(1, 0.54, 0.61, 0.45)
                    RowLayout {
                        id: errorCol
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10
                        Tint { iconName: "error"; side: 22; Layout.alignment: Qt.AlignTop }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: qsTr("Connection to the call was lost.")
                                color: "white"
                                font.pixelSize: 14
                                font.family: "Noto Sans"
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Label {
                                text: qsTr("The transcript so far is kept.")
                                color: Qt.rgba(1, 1, 1, 0.8)
                                font.pixelSize: 13
                                font.family: "Noto Sans"
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Label {
                                visible: monitor.detailsOpen
                                text: monitor.errorMessage
                                color: Qt.rgba(1, 1, 1, 0.8)
                                font.pixelSize: 12
                                font.family: "Noto Sans"
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Row {
                                spacing: 8
                                PillButton { label: qsTr("Connect again"); onClicked: monitor.retryConnection() }
                                PillButton { label: qsTr("Details"); onClicked: monitor.toggleDetails() }
                            }
                        }
                    }
                }

                Rectangle {
                    visible: window.showAsk
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Layout.topMargin: 12
                    implicitHeight: askCol.implicitHeight + 24
                    radius: 16
                    color: Qt.rgba(0.61, 0.91, 1, 0.16)
                    border.color: Qt.rgba(1, 1, 1, 0.18)
                    RowLayout {
                        id: askCol
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10
                        Tint { iconName: "info"; side: 22; Layout.alignment: Qt.AlignTop }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: qsTr("Live listen is off. The transcript continues. Tap Live listen to hear this call on this computer. This choice is only for this call.")
                                color: "white"
                                wrapMode: Text.Wrap
                                font.pixelSize: 13
                                font.family: "Noto Sans"
                                Layout.fillWidth: true
                            }
                            Row {
                                spacing: 8
                                PillButton {
                                    label: qsTr("Listen now")
                                    onClicked: monitor.setListening(true)
                                }
                                PillButton {
                                    label: qsTr("Keep asking")
                                    onClicked: monitor.dismissAsk()
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    visible: monitor.state === "finalized"
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Layout.topMargin: 12
                    implicitHeight: doneCol.implicitHeight + 20
                    radius: 16
                    color: Qt.rgba(0.55, 0.85, 0.7, 0.16)
                    border.color: Qt.rgba(1, 1, 1, 0.18)
                    RowLayout {
                        id: doneCol
                        anchors.fill: parent
                        anchors.margins: 12
                        Tint { iconName: "ok"; side: 20; Layout.alignment: Qt.AlignTop }
                        Label {
                            text: qsTr("Result: %1").arg(monitor.summaryLine.length ? monitor.summaryLine : qsTr("Call finished"))
                            color: "white"
                            wrapMode: Text.Wrap
                            font.pixelSize: 14
                            font.family: "Noto Sans"
                            Layout.fillWidth: true
                        }
                    }
                }

                Rectangle {
                    visible: monitor.state === "finalized" && monitor.hasSummaryCard
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Layout.topMargin: 10
                    implicitHeight: cardCol.implicitHeight + 24
                    radius: 20
                    color: Qt.rgba(1, 1, 1, 0.14)
                    border.color: Qt.rgba(1, 1, 1, 0.14)
                    ColumnLayout {
                        id: cardCol
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 4
                        Label {
                            visible: monitor.hasFacts
                            text: qsTr("Important facts")
                            color: Qt.rgba(1, 1, 1, 0.65)
                            font.pixelSize: 11
                            font.family: "Noto Sans"
                        }
                        Repeater {
                            model: factModel
                            Label {
                                required property string display
                                text: "•  " + display
                                color: "white"
                                wrapMode: Text.Wrap
                                font.pixelSize: 13
                                font.family: "Noto Sans"
                                Layout.fillWidth: true
                            }
                        }
                        Label {
                            visible: monitor.hasSteps
                            text: qsTr("Next steps")
                            color: Qt.rgba(1, 1, 1, 0.65)
                            font.pixelSize: 11
                            font.family: "Noto Sans"
                            Layout.topMargin: 6
                        }
                        Repeater {
                            model: stepModel
                            Label {
                                required property string display
                                text: "•  " + display
                                color: "white"
                                wrapMode: Text.Wrap
                                font.pixelSize: 13
                                font.family: "Noto Sans"
                                Layout.fillWidth: true
                            }
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Column {
                        id: emptyState
                        visible: transcript.count === 0 && monitor.state !== "finalized"
                        anchors.centerIn: parent
                        spacing: 10
                        width: 280
                        Item {
                            width: 78
                            height: 78
                            anchors.horizontalCenter: parent.horizontalCenter
                            Canvas {
                                anchors.fill: parent
                                onPaint: {
                                    var ctx = getContext("2d")
                                    ctx.strokeStyle = "rgba(255,255,255,0.55)"
                                    ctx.lineWidth = 2
                                    ctx.beginPath()
                                    ctx.arc(width / 2, height / 2, width / 2 - 3, 0.2, Math.PI * 1.5)
                                    ctx.stroke()
                                }
                                Component.onCompleted: requestPaint()
                                RotationAnimation on rotation {
                                    from: 0
                                    to: 360
                                    duration: 1600
                                    loops: Animation.Infinite
                                    running: emptyState.visible
                                }
                            }
                            Rectangle {
                                width: 64
                                height: 64
                                radius: 32
                                anchors.centerIn: parent
                                color: Qt.rgba(1, 1, 1, 0.12)
                                Tint {
                                    iconName: "chat"
                                    side: 28
                                    anchors.centerIn: parent
                                }
                            }
                        }
                        Label {
                            text: qsTr("No conversation yet")
                            color: "white"
                            font.pixelSize: 16
                            font.family: "Noto Sans"
                            anchors.horizontalCenter: parent.horizontalCenter
                        }
                        Label {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            color: Qt.rgba(1, 1, 1, 0.65)
                            font.pixelSize: 13
                            font.family: "Noto Sans"
                            text: qsTr("When the other person answers, the live transcript appears here, starting with the AI notice.")
                        }
                    }

                    ListView {
                        id: transcript
                        anchors.fill: parent
                        anchors.margins: 8
                        visible: count > 0
                        clip: true
                        spacing: 10
                        model: lineModel
                        property bool stick: true
                        onMovementStarted: stick = false
                        onAtYEndChanged: if (atYEnd) stick = true
                        onCountChanged: if (stick) Qt.callLater(positionViewAtEnd)

                        header: Item {
                            width: transcript.width
                            height: notice.implicitHeight + 12
                            Label {
                                id: notice
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: Math.min(implicitWidth + 28, parent.width - 24)
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.Wrap
                                topPadding: 6
                                bottomPadding: 6
                                leftPadding: 14
                                rightPadding: 14
                                text: qsTr("Notice: You are speaking with an AI assistant from Fernruf. This call is being transcribed.")
                                color: Qt.rgba(1, 1, 1, 0.8)
                                font.pixelSize: 12
                                font.family: "Noto Sans"
                                background: Rectangle {
                                    radius: 14
                                    color: Qt.rgba(1, 1, 1, 0.12)
                                    border.color: Qt.rgba(1, 1, 1, 0.18)
                                    border.width: 1
                                }
                            }
                        }

                        delegate: Item {
                            id: row
                            required property string speaker
                            required property string text
                            required property string clock
                            required property bool final
                            width: transcript.width
                            height: bubbleCol.implicitHeight + 6
                            opacity: final ? 1 : 0.62

                            Column {
                                id: bubbleCol
                                width: Math.min(520, row.width * 0.72)
                                x: row.speaker === "agent" ? 12 : row.width - width - 12
                                spacing: 3
                                Label {
                                    text: row.speaker === "agent" ? qsTr("Agent (AI)") : qsTr("Other person")
                                    color: Qt.rgba(1, 1, 1, 0.65)
                                    font.pixelSize: 11
                                    font.family: "Noto Sans"
                                    anchors.right: row.speaker === "callee" ? parent.right : undefined
                                }
                                Rectangle {
                                    width: Math.min(parent.width, line.implicitWidth + 26)
                                    height: line.implicitHeight + (row.final ? 28 : 36)
                                    anchors.right: row.speaker === "callee" ? parent.right : undefined
                                    radius: 18
                                    topLeftRadius: row.speaker === "agent" ? 6 : 18
                                    topRightRadius: row.speaker === "callee" ? 6 : 18
                                    color: row.speaker === "agent" ? Qt.rgba(0.608, 0.906, 1, 0.22) : Qt.rgba(1, 1, 1, 0.16)
                                    border.width: 1
                                    border.color: row.speaker === "agent" ? Qt.rgba(0.608, 0.906, 1, 0.35) : Qt.rgba(1, 1, 1, 0.16)
                                    Text {
                                        id: line
                                        x: 13
                                        y: 8
                                        width: Math.min(bubbleCol.width - 26, implicitWidth)
                                        text: row.text
                                        wrapMode: Text.Wrap
                                        color: "white"
                                        font.pixelSize: 15
                                        font.italic: !row.final
                                        font.family: "Noto Sans"
                                    }
                                    Row {
                                        visible: !row.final
                                        spacing: 4
                                        x: 13
                                        y: line.y + line.height + 4
                                        Repeater {
                                            model: 3
                                            Rectangle {
                                                width: 5
                                                height: 5
                                                radius: 2
                                                color: "white"
                                                SequentialAnimation on opacity {
                                                    loops: Animation.Infinite
                                                    running: !row.final
                                                    PauseAnimation { duration: index * 160 }
                                                    NumberAnimation { to: 0.25; duration: 280 }
                                                    NumberAnimation { to: 1; duration: 280 }
                                                    PauseAnimation { duration: 400 }
                                                }
                                            }
                                        }
                                    }
                                    Label {
                                        text: row.final ? row.clock : qsTr("provisional")
                                        color: Qt.rgba(1, 1, 1, 0.92)
                                        font.pixelSize: 11
                                        font.italic: !row.final
                                        font.family: "Noto Sans"
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        anchors.rightMargin: 10
                                        anchors.bottomMargin: 6
                                    }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 40
                    color: "transparent"
                    Rectangle {
                        anchors.top: parent.top
                        width: parent.width
                        height: 1
                        color: Qt.rgba(1, 1, 1, 0.14)
                    }
                    Label {
                        anchors.left: parent.left
                        anchors.leftMargin: 16
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - (sampleTag.visible ? 140 : 24)
                        elide: Text.ElideRight
                        color: Qt.rgba(1, 1, 1, 0.65)
                        font.pixelSize: 11
                        font.family: "Noto Sans"
                        text: monitor.state === "ended"
                              ? qsTr("Summary is being written…")
                              : qsTr("Provisional lines are dimmed and are replaced once they are final.")
                    }
                    Label {
                        id: sampleTag
                        visible: monitor.exampleData
                        anchors.right: parent.right
                        anchors.rightMargin: 16
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Example data")
                        color: "#FF8A9B"
                        font.pixelSize: 12
                        font.family: "Noto Sans"
                    }
                }
            }
        }

        Rectangle {
            visible: window.chatOpen
            x: 399
            width: 1
            height: parent.height
            color: Qt.rgba(1, 1, 1, 0.14)
        }

        Row {
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.topMargin: 12
            anchors.rightMargin: 12
            spacing: 6
            z: 20

            WindowButton {
                objectName: "minimizeWindow"
                iconName: "window-minimize"
                Accessible.name: qsTr("Minimize")
                onClicked: window.showMinimized()
            }
            WindowButton {
                objectName: "closeWindow"
                iconName: "window-close"
                Accessible.name: qsTr("Close")
                onClicked: window.hide()
            }
        }
    }
}
