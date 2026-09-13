// AFK Monitor plugin data helpers.
// Parsing and formatting for the collector record. The collector.py script
// emits one JSON record with an "accounts" array (one per AFK API key); each
// account carries subscriptions with quota windows (percent 0-100, resetsAt
// unix seconds) or a credit balance.

var MAX_RECORD_BYTES = 256 * 1024
var MAX_ACCOUNTS = 16
var MAX_SUBSCRIPTIONS = 16
var MAX_WINDOWS = 8
var MAX_KEYS = 16
var MAX_TEXT_LENGTH = 128

function boundedText(value, fallback) {
  var text = String(value || fallback || "")
  return text.slice(0, MAX_TEXT_LENGTH)
}

function parseRecord(raw) {
  var record = {}
  var empty = function () {
    return {
      ok: false, error: "bad collector output",
      accounts: [], subscriptions: [], keys: []
    }
  }
  var text = String(raw || "{}")
  if (text.length > MAX_RECORD_BYTES) return empty()
  try {
    record = JSON.parse(text)
  } catch (e) {
    return empty()
  }
  if (!record || typeof record !== "object") return empty()

  var pctOf = function (sub) {
    var worst = 0
    var windows = Array.isArray(sub.windows) ? sub.windows : []
    for (var k = 0; k < windows.length; k++) worst = Math.max(worst, windows[k].percent)
    return worst
  }

  var statusOf = function (sub) {
    var s = sub.status
    if (["ok", "warning", "exhausted"].indexOf(s) >= 0) return s
    return pctOf(sub) >= 100 ? "exhausted" : pctOf(sub) >= 70 ? "warning" : "ok"
  }

  var parseSub = function (s, account) {
    var windows = []
    var ws = Array.isArray(s.windows) ? s.windows.slice(0, MAX_WINDOWS) : []
    for (var j = 0; j < ws.length; j++) {
      var w = ws[j]
      if (!w || typeof w !== "object") continue
      var pct = Number(w.percent)
      if (!isFinite(pct)) continue
      windows.push({
        name: boundedText(w.name, "usage"),
        percent: Math.max(0, Math.min(100, Math.round(pct))),
        resetsAt: Number(w.resetsAt) || 0
      })
    }
    return {
      provider: boundedText(s.provider),
      label: boundedText(s.label || s.provider, "Unknown"),
      status: statusOf(s),
      windows: windows,
      balance: boundedText(s.balance) || null,
      credits: boundedText(s.credits) || null,
      error: boundedText(s.error) || null,
      overageAvailable: !!s.overageAvailable,
      account: boundedText(account, "primary")
    }
  }

  // Accounts are the source of truth; visibleSubscriptions() applies the
  // per-subscription show-on-bar preferences for the bar list.
  var accounts = []
  var list = Array.isArray(record.accounts) ? record.accounts.slice(0, MAX_ACCOUNTS) : []
  for (var i = 0; i < list.length; i++) {
    var a = list[i]
    if (!a || typeof a !== "object") continue
    var accountLabel = boundedText(a.label, "account")
    var subs = []
    var inner = Array.isArray(a.subscriptions) ? a.subscriptions.slice(0, MAX_SUBSCRIPTIONS) : []
    for (var j = 0; j < inner.length; j++) {
      if (inner[j] && typeof inner[j] === "object") subs.push(parseSub(inner[j], accountLabel))
    }
    accounts.push({
      label: accountLabel,
      ok: a.ok !== false,
      error: boundedText(a.error) || null,
      subscriptions: subs
    })
  }

  return {
    ok: record.ok !== false,
    error: boundedText(record.error) || null,
    generatedAt: boundedText(record.generatedAt),
    accounts: accounts,
    keys: Array.isArray(record.keys)
      ? record.keys.filter(function (k) {
          return k && typeof k === "object" && k.label
        }).slice(0, MAX_KEYS).map(function (k) {
          return {label: boundedText(k.label, "account"), masked: boundedText(k.masked)}
        })
      : []
  }
}

// Flat list of every subscription across accounts, in account order —
// the popup renders from this so hidden-for-bar entries stay manageable.
function allSubscriptions(accounts) {
  var out = []
  var list = accounts || []
  for (var i = 0; i < list.length; i++) {
    var subs = (list[i].subscriptions || [])
    for (var j = 0; j < subs.length; j++) out.push(subs[j])
  }
  return out
}

// Flat list of subscriptions whose show-on-bar checkbox is on. Visibility
// is stored per "account/provider" key; anything unstated is visible.
function visibleSubscriptions(accounts, visibility) {
  var out = []
  var list = accounts || []
  for (var i = 0; i < list.length; i++) {
    var subs = (list[i].subscriptions || [])
    for (var j = 0; j < subs.length; j++) {
      var sub = subs[j]
      var key = String(sub.account || "") + "/" + String(sub.provider || "")
      if (visibility && visibility[key] === false) continue
      out.push(sub)
    }
  }
  return out
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
  premium: "PREMIUM",
  chat: "CHAT",
  completions: "COMPL",
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
  "copilot": "COP",
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
  "copilot": "copilot",
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
  var hasCredits = false
  for (var i = 0; i < subs.length; i++) {
    var s = subs[i]
    if (s.balance) hasBalance = true
    if (s.credits) hasCredits = true
    for (var j = 0; j < s.windows.length; j++) worst = Math.max(worst, s.windows[j].percent)
  }
  if (worst > 0) return worst + "%"
  if (hasCredits) return "credits"
  if (hasBalance) return "$"
  return ""
}

if (typeof module !== "undefined") {
  module.exports = {
    parseRecord: parseRecord,
    allSubscriptions: allSubscriptions,
    visibleSubscriptions: visibleSubscriptions,
    windowLabel: windowLabel,
    formatReset: formatReset,
    statusColor: statusColor,
    shortLabel: shortLabel,
    iconUrl: iconUrl,
    summaryLabel: summaryLabel
  }
}
