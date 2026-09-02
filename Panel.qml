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
  property string errorText: ""
  property string generatedAt: ""
  property var accounts: []
  // Unfiltered flat list — drives the popup cards (every subscription stays
  // visible and toggleable there).
  property var allSubscriptions: []
  // Visible-only flat list — drives the bar metrics.
  property var subscriptions: []

  function refreshFlat() {
    allSubscriptions = Model.allSubscriptions(accounts)
    subscriptions = Model.visibleSubscriptions(accounts, setting("accountVisibility", {}))
  }

  // ---- AFK keys (orgs / personal accounts) --------------------------------
  // Every watched account must be configured here; stored by collector.py in
  // a 0600 file. Raw keys never travel through QML — only masked previews.
  property var keys: []
  property bool addingKey: false
  property string newKeyLabel: ""
  property string newKeyValue: ""
  property string keyMessage: ""
  property bool keyMessageGood: false

  // A key-command run is in flight (the collector answers with a full fresh
  // record on stdout, so no second pass is ever needed).
  property bool collectorBusy: false

  // Poll cadence. Quota windows move slowly; the collector is one HTTP fan-out
  // per provider so 5 minutes is plenty and keeps the hub load negligible.
  property int refreshSeconds: 300

  // Defensive color/typography resolvers (same pattern as system-monitor).
  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color fgDim: Qt.darker(root.fg, 1.4)
  readonly property string fontFam: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property color barFg: root.bar ? root.bar.barForeground : Color.foreground
  readonly property color barFgDim: Qt.darker(root.barFg, 1.4)

  // Surface-aware provider marks: white SVGs on dark bars, dark twins on
  // light ones (same contract as the agents panel's assets/<id>-light.svg).
  // Classified from the bar's *text* colour, not the background — themed or
  // transparent bars report backgrounds that don't match the visual surface,
  // but the foreground always carries the real contrast the text needs.
  function isLightSurface() {
    var c = root.barFg
    var lum = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b
    return lum < 0.5 // dark text => light surface => want the dark mark
  }

  implicitWidth: barRow.implicitWidth
  implicitHeight: root.bar ? root.bar.barSize : Style.bar.sizeHorizontal

  function refresh() {
    if (collectorBusy) return
    if (!collectProc.running) {
      collectProc.command = ["python3", collectorPath()]
      collectProc.running = true
    }
  }

  function collectorPath() {
    return decodeURIComponent(Qt.resolvedUrl("collector.py").toString()).replace("file://", "")
  }

  function runCollector(args) {
    // Key command (add/remove). The collector prints the human result to
    // stderr and a full fresh record to stdout in the same run, so the
    // popup updates from that single invocation.
    if (collectorBusy || collectProc.running) return
    collectorBusy = true
    collectProc.command = ["python3", collectorPath()].concat(args)
    collectProc.running = true
  }

  function addKey() {
    var label = newKeyLabel.trim()
    var key = newKeyValue.trim()
    if (label.length === 0 || key.length === 0) {
      keyMessage = "Label and key are both required."
      keyMessageGood = false
      return
    }
    newKeyLabel = ""
    newKeyValue = ""
    addingKey = false // collapse immediately; result line reports below
    runCollector(["add-key", label, key])
  }

  function removeKey(target) {
    runCollector(["remove-key", target])
  }

  function moveKey(target, direction) {
    runCollector(["move-key", target, direction])
  }

  // Show-on-bar state lives in the widget's shell.json entry (via the bar's
  // settings persistence), keyed "account/provider", defaulting to true.
  function subKey(sub) {
    return String(sub ? (sub.account || "") + "/" + (sub.provider || "") : "")
  }

  function subVisible(sub) {
    var stored = setting("accountVisibility", {})
    return !(stored && stored[subKey(sub)] === false)
  }

  function setSubVisible(sub, visible) {
    var stored = setting("accountVisibility", {})
    var next = {}
    for (var k in stored) if (Object.prototype.hasOwnProperty.call(stored, k)) next[k] = stored[k]
    next[subKey(sub)] = !!visible
    persistSettings({"accountVisibility": next})
    refreshFlat()
  }

  function persistSettings(patch) {
    if (!root.bar || !root.bar.shell || typeof root.bar.shell.updateEntryInline !== "function") return
    var merged = {}
    var current = settings || {}
    for (var k in current) if (Object.prototype.hasOwnProperty.call(current, k)) merged[k] = current[k]
    for (var k2 in patch) if (Object.prototype.hasOwnProperty.call(patch, k2)) merged[k2] = patch[k2]
    root.settings = merged
    root.bar.shell.updateEntryInline(root.moduleName, merged)
  }

  function updateRecord(raw) {
    var next = Model.parseRecord(raw)
    errorText = next.error || ""
    generatedAt = next.generatedAt || ""
    accounts = next.accounts
    refreshFlat()
    if (next.keys) keys = next.keys
    hasData = true
    collectorBusy = false
  }

  Process {
    id: collectProc
    command: ["python3", collectorPath()]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.updateRecord(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var t = String(text || "").trim()
        if (t.length === 0) return
        if (t.indexOf("saved ") === 0 || t.indexOf("removed ") === 0 || t.indexOf("accounts now:") === 0) {
          root.keyMessage = t
          root.keyMessageGood = t.indexOf("no key") !== 0
        } else if (t.indexOf("no key matching") === 0) {
          root.keyMessage = t
          root.keyMessageGood = false
        } else {
          console.warn("[afk-monitor] collector stderr:", t)
        }
      }
    }
    onExited: function (code) {
      if (code !== 0 && !root.hasData) {
        root.hasData = true
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

  // ---- Bar button: one icon + value per subscription. Quota subscriptions
  //      show the worst-window percentage; balance subscriptions show the
  //      amount. Provider marks come from assets/<id>.svg resolved by the
  //      same convention as the agents panel; a missing asset falls back to
  //      the short text label. With nothing configured the module renders
  //      nothing but stays installed.
  Item {
    id: button
    anchors.fill: parent

    Row {
      id: barRow
      anchors.centerIn: parent
      spacing: Style.space(10)

      Repeater {
        model: root.hasData ? root.subscriptions : []

        Row {
          id: subRow
          required property var modelData
          property var sub: modelData
          property string markUrl: Model.iconUrl(subRow.sub, root.isLightSurface())
          spacing: Style.space(4)

          // Provider mark; falls back to the short text label when the
          // provider has no shipped asset.
          Item {
            width: Style.space(13)
            height: Style.space(13)
            anchors.verticalCenter: parent.verticalCenter

            Image {
              anchors.fill: parent
              visible: subRow.markUrl !== ""
              source: subRow.markUrl
              sourceSize.width: width * 2
              sourceSize.height: height * 2
              fillMode: Image.PreserveAspectFit
              asynchronous: true
            }

            Text {
              anchors.centerIn: parent
              visible: subRow.markUrl === ""
              text: Model.shortLabel(subRow.sub)
              color: root.barFgDim
              font.family: root.fontFam
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 0.6
            }
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

      // No keys configured: show the AFK mark so the entry point stays
      // discoverable (clicking it opens the popup to add the first key).
      Image {
        visible: root.hasData && root.keys.length === 0
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(13)
        height: Style.space(13)
        source: root.isLightSurface() ? "assets/afk-light.svg" : "assets/afk.svg"
        sourceSize.width: width * 2
        sourceSize.height: height * 2
        fillMode: Image.PreserveAspectFit
        asynchronous: true
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
      // While the add-key form is open the TextFields own the keyboard.
      blocked: root.addingKey
      onTextKey: function (t) {
        if (t === "r" || t === "R") root.refresh()
        if (t === "a" || t === "A") {
          root.addingKey = true
          root.keyMessage = ""
          Qt.callLater(keyLabelField.forceActiveFocus)
        }
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        // Header: title + last-updated + manual refresh button.
        Item {
          width: parent.width
          height: Math.max(headerRow.implicitHeight, refreshBtn.height)

          Row {
            id: headerRow
            spacing: Style.space(8)

            Text {
              text: "AFK"
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

          Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(6)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: root.generatedAt ? "" : "R to refresh"
              color: root.fgDim
              font.family: root.fontFam
              font.pixelSize: Style.font.caption
            }

            PanelActionButton {
              id: refreshBtn
              iconText: "󰑐"
              tooltipText: "Refresh now (R)"
              foreground: root.fgDim
              hoverColor: root.fg
              fontFamily: root.fontFam
              onClicked: root.refresh()
            }
          }
        }

        Text {
          visible: root.hasData && root.keys.length === 0
          width: parent.width
          wrapMode: Text.WordWrap
          text: "No keys configured.\nAdd an AFK API key below to start watching an account."
          color: root.fgDim
          font.family: root.fontFam
          font.pixelSize: Style.font.caption
        }

        Text {
          visible: root.hasData && root.keys.length > 0 && root.allSubscriptions.length === 0
          width: parent.width
          wrapMode: Text.WordWrap
          text: "No AFK subscriptions connected.\nAdd one in AFK → Account → LLM."
          color: root.fgDim
          font.family: root.fontFam
          font.pixelSize: Style.font.caption
        }

        Repeater {
          // All subscriptions across all accounts render here; the
          // show-on-bar switches only filter the bar metrics.
          model: root.allSubscriptions

          Column {
            id: subCard
            required property var modelData
            property var sub: modelData
            width: parent.width
            spacing: Style.space(8)

            PanelSeparator { foreground: root.fg }

            // Card header: mark + label + overall status dot.
            Item {
              width: parent.width
              height: Math.max(subMark.height, subHeader.implicitHeight)

              Image {
                id: subMark
                anchors.left: parent.left
                anchors.verticalCenter: subHeader.verticalCenter
                width: Style.space(15)
                height: Style.space(15)
                visible: Model.iconUrl(subCard.sub, root.isLightSurface()) !== ""
                source: Model.iconUrl(subCard.sub, root.isLightSurface())
                sourceSize.width: width * 2
                sourceSize.height: height * 2
                fillMode: Image.PreserveAspectFit
                asynchronous: true
              }

              Text {
                id: subHeader
                anchors.left: subMark.visible ? subMark.right : parent.left
                anchors.leftMargin: subMark.visible ? Style.space(7) : 0
                anchors.verticalCenter: parent.verticalCenter
                text: subCard.sub.account !== "primary" && subCard.sub.account !== ""
                      ? subCard.sub.label + " · " + subCard.sub.account
                      : subCard.sub.label
                color: root.fg
                font.family: root.fontFam
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 0.5
              }

              ToggleSwitch {
                id: subVisibleSwitch
                width: subVisibleSwitch.implicitWidth
                height: subVisibleSwitch.implicitHeight
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                checked: root.subVisible(subCard.sub)
                onToggled: root.setSubVisible(subCard.sub, !checked)
              }
            }

            // Quota windows: label on its own line, full-width meter, the
            // percent + reset countdown on the line below (overlapping a
            // single line was unreadable).
            Repeater {
              model: subCard.sub.windows

              Column {
                id: windowRow
                required property var modelData
                property var w: modelData
                width: parent.width
                spacing: Style.space(3)

                Text {
                  text: Model.windowLabel(windowRow.w.name)
                  color: root.fgDim
                  font.family: root.fontFam
                  font.pixelSize: Style.font.caption
                  font.letterSpacing: 0.5
                }

                // Meter track + fill.
                Rectangle {
                  width: parent.width
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

        // ---- Extra AFK keys ------------------------------------------------
        // Additional AFK API keys (other orgs, personal accounts). Each key
        // becomes its own account whose subscriptions join the bar. Raw keys
        // stay in a 0600 file managed by collector.py; only masked previews
        // are shown here.
        Column {
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { foreground: root.fg }

          Item {
            width: parent.width
            height: keysHeader.implicitHeight

            Text {
              id: keysHeader
              text: "AFK keys"
              color: root.fg
              font.family: root.fontFam
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 0.5
            }

            Text {
              anchors.right: parent.right
              anchors.verticalCenter: keysHeader.verticalCenter
              text: root.addingKey ? "esc to cancel" : "+ add"
              color: root.addingKey ? root.fgDim : Color.accent
              font.family: root.fontFam
              font.pixelSize: Style.font.caption

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  if (root.addingKey) {
                    root.addingKey = false
                    root.keyMessage = ""
                  } else {
                    root.addingKey = true
                    root.keyMessage = ""
                    Qt.callLater(keyLabelField.forceActiveFocus)
                  }
                }
              }
            }
          }

          Text {
            visible: root.keys.length === 0 && !root.addingKey
            width: parent.width
            wrapMode: Text.WordWrap
            text: "No keys configured. Add an AFK API key to watch an\norg or personal account from this machine."
            color: root.fgDim
            font.family: root.fontFam
            font.pixelSize: Style.font.caption
          }

          // Configured keys in display order: show-on-bar checkbox, label,
          // masked preview, reorder arrows, remove.
          Repeater {
            model: root.keys

            Item {
              id: keyRow
              required property var modelData
              property string label: String(modelData.label || "?")
              property string masked: String(modelData.masked || "")
              property int index: {
                for (var i = 0; i < root.keys.length; i++)
                  if (String(root.keys[i].label || "") === keyRow.label) return i
                return 0
              }
              width: parent.width
              height: Math.max(Style.space(20), Style.space(22))

              Text {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - actionsRow.width - Style.space(10)
                elide: Text.ElideRight
                text: keyRow.label + "   " + keyRow.masked
                color: root.fg
                font.family: root.fontFam
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              Row {
                id: actionsRow
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                PanelActionButton {
                  iconText: "\uF077"
                  tooltipText: "Move up"
                  foreground: root.fgDim
                  hoverColor: root.fg
                  fontFamily: root.fontFam
                  enabled: keyRow.index > 0
                  onClicked: root.moveKey(keyRow.label, "up")
                }

                PanelActionButton {
                  iconText: "\uF078"
                  tooltipText: "Move down"
                  foreground: root.fgDim
                  hoverColor: root.fg
                  fontFamily: root.fontFam
                  enabled: keyRow.index < root.keys.length - 1
                  onClicked: root.moveKey(keyRow.label, "down")
                }

                PanelActionButton {
                  iconText: "󰆴"
                  tooltipText: "Remove key"
                  foreground: root.fgDim
                  hoverColor: Color.urgent
                  fontFamily: root.fontFam
                  onClicked: root.removeKey(keyRow.label)
                }
              }
            }
          }

          // Inline add form; blocks the panel key catcher while editing.
          Column {
            visible: root.addingKey
            width: parent.width
            spacing: Style.space(6)

            TextField {
              id: keyLabelField
              width: parent.width
              placeholderText: "Label (e.g. Work org)"
              foreground: root.fg
              font.family: root.fontFam
              text: root.newKeyLabel
              onTextChanged: root.newKeyLabel = text
              Keys.onReturnPressed: keyKeyField.forceActiveFocus()
              Keys.onEscapePressed: {
                root.addingKey = false
                root.keyMessage = ""
              }
            }

            TextField {
              id: keyKeyField
              width: parent.width
              placeholderText: "AFK API key"
              foreground: root.fg
              font.family: root.fontFam
              password: true
              text: root.newKeyValue
              onTextChanged: root.newKeyValue = text
              onAccepted: root.addKey()
              Keys.onEscapePressed: {
                root.addingKey = false
                root.keyMessage = ""
              }
            }

            Row {
              spacing: Style.space(6)

              Button {
                text: "Save"
                foreground: root.fg
                accent: Color.accent
                fontFamily: root.fontFam
                onClicked: root.addKey()
              }

              Button {
                text: "Cancel"
                foreground: root.fgDim
                accent: Color.accent
                fontFamily: root.fontFam
                onClicked: {
                  root.addingKey = false
                  root.keyMessage = ""
                }
              }
            }
          }

          Text {
            visible: root.keyMessage !== ""
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.keyMessage
            color: root.keyMessageGood ? root.fgDim : Color.urgent
            font.family: root.fontFam
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
