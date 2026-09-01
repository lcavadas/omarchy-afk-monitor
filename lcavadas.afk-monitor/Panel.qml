import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "lcavadas.afk-monitor"
  ipcTarget: "lcavadas.afk-monitor"

  // Parsed collector record.
  property bool hasData: false
  property bool hasKey: true
  property string errorText: ""
  property string generatedAt: ""
  property var subscriptions: []

  // Poll cadence. Quota windows move slowly; the collector is one HTTP fan-out
  // per provider so 5 minutes is plenty and keeps the hub load negligible.
  property int refreshSeconds: 300

  // Defensive color/typography resolvers (same pattern as system-monitor).
  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color fgDim: Qt.darker(root.fg, 1.4)
  readonly property string fontFam: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property color barFg: root.bar ? root.bar.barForeground : Color.foreground
  readonly property color barFgDim: Qt.darker(root.barFg, 1.4)

  implicitWidth: barRow.implicitWidth
  implicitHeight: root.bar ? root.bar.barSize : Style.bar.sizeHorizontal

  function refresh() {
    if (!collectProc.running) collectProc.running = true
  }

  function updateRecord(raw) {
    var next = Model.parseRecord(raw)
    hasKey = next.ok
    errorText = next.error || ""
    generatedAt = next.generatedAt || ""
    subscriptions = next.subscriptions
    hasData = true
  }

  Process {
    id: collectProc
    command: ["python3", decodeURIComponent(Qt.resolvedUrl("collector.py").toString()).replace("file://", "")]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.updateRecord(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text && text.trim().length > 0) console.warn("[afk-monitor] collector stderr:", text.trim())
      }
    }
    onExited: function (code) {
      if (code !== 0) {
        root.hasData = true
        root.hasKey = false
        root.errorText = "collector exit " + code
      }
    }
  }

  // Immediate fetch, then a steady cadence. Also refresh on popup open so the
  // cards are fresh when the user actually looks at them.
  Timer {
    interval: 1500
    running: true
    repeat: false
    triggeredOnStart: false
    onTriggered: root.refresh()
  }

  Timer {
    interval: root.refreshSeconds * 1000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  onOpenedChanged: if (root.opened) root.refresh()

  // ---- Bar button: one metric per subscription. Quota subscriptions show a
  //      worst-window percentage; balance subscriptions show the amount. With
  //      nothing configured the module renders nothing but stays installed.
  Item {
    id: button
    anchors.fill: parent

    Row {
      id: barRow
      anchors.centerIn: parent
      spacing: Style.space(10)

      Repeater {
        model: root.hasKey ? root.subscriptions : []

        Row {
          id: subRow
          required property var modelData
          property var sub: modelData
          spacing: Style.space(3)

          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: Model.shortLabel(subRow.sub)
            color: root.barFgDim
            font.family: root.fontFam
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 0.8
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: {
              var s = subRow.sub
              if (s.windows.length > 0) {
                var worst = 0
                for (var i = 0; i < s.windows.length; i++) worst = Math.max(worst, s.windows[i].percent)
                return worst + "%"
              }
              if (s.balance) return "$" + s.balance.split(" ")[0]
              return ""
            }
            color: Model.statusColor(subRow.sub.status, root.barFg, Color.accent, Color.urgent)
            font.family: root.fontFam
            font.pixelSize: Style.font.caption
          }
        }
      }

      Text {
        visible: !root.hasKey
        text: "AFK"
        color: root.barFgDim
        font.family: root.fontFam
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 0.8
      }

      Text {
        visible: root.hasKey && root.subscriptions.length === 0
        text: ""
        font.pixelSize: Style.font.caption
      }
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      cursorShape: Qt.PointingHandCursor
      onClicked: function (mouse) {
        if (mouse.button === Qt.RightButton) root.refresh()
        else root.toggle()
      }
    }
  }

  // ---- Popup panel: one card per subscription, metered windows with reset
  //      countdowns, or a credit gauge for balance-style subscriptions.
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) { if (t === "r" || t === "R") root.refresh() }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        // Header: title + last-updated + manual refresh.
        Item {
          width: parent.width
          height: headerRow.implicitHeight

          Row {
            id: headerRow
            spacing: Style.space(8)

            Text {
              text: "AFK Subscriptions"
              color: root.fg
              font.family: root.fontFam
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: root.generatedAt ? "· updated " + Qt.formatTime(new Date(root.generatedAt), "HH:mm") : ""
              color: root.fgDim
              font.family: root.fontFam
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            anchors.right: parent.right
            text: "R to refresh"
            color: root.fgDim
            font.family: root.fontFam
            font.pixelSize: Style.font.caption
          }
        }

        Text {
          visible: !root.hasKey
          width: parent.width
          wrapMode: Text.WordWrap
          text: root.errorText || "No AFK API key found.\nRun `afk daemon` once or set AFK_USAGE_API_KEY."
          color: root.fgDim
          font.family: root.fontFam
          font.pixelSize: Style.font.caption
        }

        Text {
          visible: root.hasKey && root.hasData && root.subscriptions.length === 0
          width: parent.width
          wrapMode: Text.WordWrap
          text: "No AFK subscriptions connected.\nAdd one in AFK → Account → LLM."
          color: root.fgDim
          font.family: root.fontFam
          font.pixelSize: Style.font.caption
        }

        Repeater {
          model: root.subscriptions

          Column {
            id: subCard
            required property var modelData
            property var sub: modelData
            width: parent.width
            spacing: Style.space(8)

            PanelSeparator { foreground: root.fg }

            // Card header: label + overall status dot.
            Item {
              width: parent.width
              height: subHeader.implicitHeight

              Text {
                id: subHeader
                text: subCard.sub.label
                color: root.fg
                font.family: root.fontFam
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 0.5
              }

              Rectangle {
                anchors.verticalCenter: subHeader.verticalCenter
                anchors.right: parent.right
                width: Style.space(6)
                height: width
                radius: width / 2
                color: Model.statusColor(subCard.sub.status, root.fgDim, Color.accent, Color.urgent)
              }
            }

            // Quota windows: label, meter, percent, reset countdown.
            Repeater {
              model: subCard.sub.windows

              Item {
                id: windowRow
                required property var modelData
                property var w: modelData
                width: parent.width
                height: Style.space(16)

                Text {
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: Model.windowLabel(windowRow.w.name)
                  color: root.fgDim
                  font.family: root.fontFam
                  font.pixelSize: Style.font.caption
                  font.letterSpacing: 0.5
                }

                // Meter track + fill.
                Rectangle {
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  height: Style.space(4)
                  radius: height / 2
                  color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12)

                  Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: parent.width * Math.min(1, windowRow.w.percent / 100)
                    radius: height / 2
                    color: Model.statusColor(
                             windowRow.w.percent >= 100 ? "exhausted" : windowRow.w.percent >= 70 ? "warning" : "ok",
                             root.fg, Color.accent, Color.urgent)
                  }
                }

                Text {
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: {
                    var pct = windowRow.w.percent + "%"
                    var reset = Model.formatReset(windowRow.w.resetsAt)
                    return reset ? pct + " · " + reset : pct
                  }
                  color: Model.statusColor(
                           windowRow.w.percent >= 100 ? "exhausted" : windowRow.w.percent >= 70 ? "warning" : "ok",
                           root.fg, Color.accent, Color.urgent)
                  font.family: root.fontFam
                  font.pixelSize: Style.font.caption
                }
              }
            }

            // Balance-style subscription: credit gauge draining toward empty.
            Repeater {
              model: subCard.sub.balance ? [subCard.sub.balance] : []

              Item {
                id: balanceRow
                required property string modelData
                width: parent.width
                height: Style.space(16)

                Text {
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "BALANCE"
                  color: root.fgDim
                  font.family: root.fontFam
                  font.pixelSize: Style.font.caption
                  font.letterSpacing: 0.5
                }

                Text {
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: balanceRow.modelData
                  color: Model.statusColor(subCard.sub.status, root.fg, Color.accent, Color.urgent)
                  font.family: root.fontFam
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }
      }
    }
  }
}
