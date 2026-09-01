// AFK Monitor plugin data helpers.
// Parsing and formatting for the collector record. The collector.py script
// emits one JSON record with an "accounts" array (one per AFK API key); each
// account carries subscriptions with quota windows (percent 0-100, resetsAt
// unix seconds) or a credit balance.

function parseRecord(raw) {
  var record = {}
  var empty = function () {
    return {
      ok: false, error: "bad collector output",
      accounts: [], subscriptions: [], extraKeys: []
    }
  }
  try {
    record = JSON.parse(String(raw || "{}"))
  } catch (e) {
    return empty()
  }
  if (!record || typeof record !== "object") return empty()

  var pctOf = function (sub) {
    var worst = 0
    for (var k = 0; k < sub.windows.length; k++) worst = Math.max(worst, sub.windows[k].percent)
    return worst
  }

  var statusOf = function (sub) {
    var s = sub.status
    if (["ok", "warning", "exhausted"].indexOf(s) >= 0) return s
    return pctOf(sub) >= 100 ? "exhausted" : pctOf(sub) >= 70 ? "warning" : "ok"
  }

  var parseSub = function (s, account) {
    var windows = []
    var ws = s.windows || []
    for (var j = 0; j < ws.length; j++) {
      var w = ws[j]
      var pct = Number(w.percent)
      if (!isFinite(pct)) continue
      windows.push({
        name: String(w.name || "usage"),
        percent: Math.max(0, Math.min(100, Math.round(pct))),
        resetsAt: Number(w.resetsAt) || 0
      })
    }
    return {
      provider: String(s.provider || ""),
      label: String(s.label || s.provider || "Unknown"),
      status: statusOf(s),
      windows: windows,
      balance: s.balance || null,
      account: String(account || "primary")
    }
  }

  // Accounts are the source of truth; a flattened subscription list drives
  // the bar (one metric per subscription, any account).
  var accounts = []
  var flat = []
  var list = record.accounts || []
  for (var i = 0; i < list.length; i++) {
    var a = list[i]
    var accountLabel = String(a.label || "primary")
    var subs = []
    var inner = a.subscriptions || []
    for (var j = 0; j < inner.length; j++) {
      var sub = parseSub(inner[j], accountLabel)
      subs.push(sub)
      flat.push(sub)
    }
    accounts.push({
      label: accountLabel,
      ok: a.ok !== false,
      error: a.error || null,
      subscriptions: subs
    })
  }

  return {
    ok: record.ok !== false,
    error: record.error || null,
    generatedAt: record.generatedAt || "",
    accounts: accounts,
    subscriptions: flat,
    extraKeys: Array.isArray(record.extraKeys)
      ? record.extraKeys.filter(function (k) {
          return k && typeof k === "object" && k.label
        })
      : []
  }
}

// Short uppercase window labels for the bar/popup rows.
var WINDOW_LABELS = {
  primary: "5H",
  secondary: "WEEK",
  rolling: "ROLL",
  session: "5H",
  weekly: "WEEK",
  monthly: "MONTH",
  daily: "DAY",
  credits: "CREDITS",
  total: "TOTAL",
  extra: "EXTRA"
}

function windowLabel(name) {
  var key = String(name || "").toLowerCase()
  if (WINDOW_LABELS[key]) return WINDOW_LABELS[key]
  return String(name || "usage").toUpperCase()
}

// Human time-to-reset: "3h 12m", "2d", "45m". Empty when unknown/past.
function formatReset(unixSeconds) {
  var ts = Number(unixSeconds) || 0
  if (ts <= 0) return ""
  var delta = ts - Date.now() / 1000
  if (delta <= 0) return ""
  var mins = Math.round(delta / 60)
  if (mins < 60) return mins + "m"
  var hours = Math.floor(mins / 60)
  var rem = mins % 60
  if (hours < 48) return hours + "h" + (rem ? " " + rem + "m" : "")
  var days = Math.floor(hours / 24)
  return days + "d"
}

function statusColor(status, normal, warn, urgent) {
  if (status === "exhausted") return urgent
  if (status === "warning") return warn
  return normal
}

// Short bar labels per provider so the row stays compact.
var SHORT_LABELS = {
  "codex": "GPT",
  "chatgpt": "GPT",
  "xai": "XAI",
  "xai-oauth": "XAI",
  "opencode-go": "OCG",
  "kimi": "KIMI",
  "moonshot": "MS",
  "openrouter": "OR",
  "deepseek": "DS",
  "anthropic-oauth": "CLAUDE"
}

function shortLabel(sub) {
  var key = String(sub && sub.provider ? sub.provider : "")
  if (SHORT_LABELS[key]) return SHORT_LABELS[key]
  var label = String(sub && sub.label ? sub.label : "")
  return label ? label.split(" ")[0].toUpperCase() : "AFK"
}

// Bar asset per provider. Marks live in assets/<id>.svg (white, for dark
// bars) with an assets/<id>-light.svg twin (dark) for light bars. Keys are
// collector provider ids plus the aliases the mirror may report.
var PROVIDER_ICONS = {
  "codex": "codex",
  "chatgpt": "codex",
  "xai": "xai",
  "xai-oauth": "xai",
  "opencode-go": "opencode",
  "kimi": "kimi",
  "moonshot": "moonshot",
  "openrouter": "openrouter",
  "deepseek": "deepseek",
  "anthropic-oauth": "anthropic",
  "claude": "claude"
}

function iconKey(sub) {
  var key = String(sub && sub.provider ? sub.provider : "")
  if (PROVIDER_ICONS[key]) return PROVIDER_ICONS[key]
  return ""
}

// Surface-aware mark URL: white mark on dark bars, dark twin on light ones.
// Falls back to "" (caller shows the text glyph) when no asset exists.
function iconUrl(sub, lightSurface) {
  var key = iconKey(sub)
  if (!key) return ""
  return lightSurface ? "assets/" + key + "-light.svg" : "assets/" + key + ".svg"
}

function summaryLabel(subs) {
  // Bar text: worst window percent across subscriptions, or "$" for balance-only.
  var worst = 0
  var hasBalance = false
  for (var i = 0; i < subs.length; i++) {
    var s = subs[i]
    if (s.balance) hasBalance = true
    for (var j = 0; j < s.windows.length; j++) worst = Math.max(worst, s.windows[j].percent)
  }
  if (worst > 0) return worst + "%"
  if (hasBalance) return "$"
  return ""
}

if (typeof module !== "undefined") {
  module.exports = {
    parseRecord: parseRecord,
    windowLabel: windowLabel,
    formatReset: formatReset,
    statusColor: statusColor,
    shortLabel: shortLabel,
    iconUrl: iconUrl,
    summaryLabel: summaryLabel
  }
}
