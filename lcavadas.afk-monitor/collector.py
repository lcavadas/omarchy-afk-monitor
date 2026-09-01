#!/usr/bin/env python3
"""Collect AFK subscription usage and emit one display-ready JSON record.

The record is consumed by the AFK Monitor bar widget. Every subscription
connection configured in AFK is polled and normalised to a common shape:

    {
      "generatedAt": iso8601,
      "ok": bool,
      "subscriptions": [
        {
          "provider":  "opencode-go",          # AFK provider id
          "label":     "OpenCode Go",
          "status":    "ok" | "warning" | "exhausted",
          "windows":   [ {"name", "percent", "resetsAt"} ],
          "balance":   "12.34 USD" | null      # credit-style subscriptions
        }
      ]
    }

Data sources (all authenticated with the daemon API key from ~/.afk/config,
overridable with AFK_USAGE_API_KEY):

    GET https://afk-server.mooglest.com/api/auth/<provider>/usage
        codex (ChatGPT), xai, opencode-go, kimi, openrouter, deepseek,
        moonshot — 404 means the provider is not connected and is skipped.

    Claude (anthropic-oauth) has no HTTP route: its quota arrives as a live
    websocket `subscription_quota` message while an agent is running. The
    agent mirrors the last snapshot to ~/.cache/afk/claude-quota.json when
    AFK_QUOTA_MIRROR=1 is set (optional companion hook, never required).

Percentages are normalised to 0-100 here; window names are normalised to
lowercase short labels. The base URL can be overridden with AFK_USAGE_BASE
(for development against a local hub).
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = "https://afk-server.mooglest.com"
CONFIG_PATH = Path.home() / ".afk" / "config"

# provider id -> display label
PROVIDERS = [
    ("codex", "ChatGPT"),
    ("xai", "xAI"),
    ("opencode-go", "OpenCode Go"),
    ("kimi", "Kimi"),
    ("openrouter", "OpenRouter"),
    ("deepseek", "DeepSeek"),
    ("moonshot", "Moonshot"),
]


def resolve_api_key():
    """AFK_USAGE_API_KEY beats AFK_API_KEY beats ~/.afk/config api_key."""
    key = os.environ.get("AFK_USAGE_API_KEY") or os.environ.get("AFK_API_KEY")
    if key:
        return key.strip()
    try:
        for line in CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("api_key") and "=" in line:
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def fetch_json(url, api_key):
    """Return parsed JSON or None. 404 = not connected = skip silently."""
    req = urllib.request.Request(url, headers={"X-AFK-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return None  # 401/403/404: provider not configured or auth expired
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ── normalisers (mirror AFK web/src/lib/quotaMappers.ts) ──────────────────

def clamp(value, lo=0, hi=100):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return max(lo, min(hi, round(v)))


def parse_reset(value):
    """Accept unix seconds, unix millis, or an ISO timestamp; return unix seconds."""
    if isinstance(value, (int, float)) and value > 0:
        return int(value / 1000) if value > 1_000_000_000_000 else int(value)
    if isinstance(value, str) and value.strip():
        try:
            from datetime import datetime as dt
            parsed = dt.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp())
        except ValueError:
            return 0
    return 0


def status_from_percent(pct):
    if pct is None:
        return "ok"
    if pct >= 100:
        return "exhausted"
    if pct >= 70:
        return "warning"
    return "ok"


def fmt_money(value):
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return None


def map_wham(raw):
    """ChatGPT/Codex: rate_limit.primary_window/secondary_window used_percent 0-100."""
    rl = raw.get("rate_limit") or {}
    windows = []
    for name in ("primary", "secondary"):
        w = rl.get(f"{name}_window")
        if not w:
            continue
        pct = clamp(w.get("used_percent"))
        if pct is None:
            continue
        windows.append({
            "name": name,
            "percent": pct,
            "resetsAt": parse_reset(w.get("reset_at")),
        })
    for extra in raw.get("additional_rate_limits") or []:
        w = (extra.get("rate_limit") or {}).get("primary_window")
        if not w:
            continue
        pct = clamp(w.get("used_percent"))
        if pct is None:
            continue
        name = extra.get("metered_feature") or extra.get("limit_name") or "extra"
        windows.append({"name": name, "percent": pct, "resetsAt": parse_reset(w.get("reset_at"))})
    primary_pct = windows[0]["percent"] if windows else 0
    balance = (raw.get("credits") or {}).get("balance")
    return {
        "windows": windows,
        "status": "exhausted" if rl.get("limit_reached") else status_from_percent(primary_pct),
        "balance": fmt_money(balance),
    }


def map_xai(raw):
    """xAI: config.creditUsagePercent or used/monthlyLimit, prepaid balance in cents."""
    config = raw.get("config") or {}
    pct = clamp(config.get("creditUsagePercent"))
    if pct is None:
        used = config.get("used", {}).get("val")
        limit = config.get("monthlyLimit", {}).get("val")
        if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
            pct = clamp(used / limit * 100)
    pct = pct or 0
    period = config.get("currentPeriod") or {}
    ptype = period.get("type") or ""
    name = "monthly" if "MONTHLY" in ptype else "weekly" if "WEEKLY" in ptype else "usage"
    prepaid = config.get("prepaidBalance", {}).get("val") or 0
    balance = fmt_money(prepaid / 100) if prepaid > 0 else None
    return {
        "windows": [{"name": name, "percent": pct, "resetsAt": parse_reset(period.get("end"))}],
        "status": status_from_percent(pct),
        "balance": balance,
    }


def map_opencode_go(raw):
    """OpenCode Go: usage.rolling/weekly/monthly {percent 0-100, resetsAt ISO}."""
    usage = raw.get("usage") or {}
    windows = []
    worst = 0
    for name in ("rolling", "weekly", "monthly"):
        w = usage.get(name)
        if not w:
            continue
        pct = clamp(w.get("percent"))
        if pct is None:
            continue
        worst = max(worst, pct)
        windows.append({"name": name, "percent": pct, "resetsAt": parse_reset(w.get("resetsAt"))})
    return {"windows": windows, "status": status_from_percent(worst), "balance": None}


def map_kimi(raw):
    """Kimi: limits[] with window duration/timeUnit and detail used/remaining/limit."""
    windows = []
    worst = 0

    def add(name, detail):
        nonlocal worst
        if not detail:
            return
        used, remaining, limit = detail.get("used"), detail.get("remaining"), detail.get("limit")
        nums = [n for n in (used, remaining, limit) if isinstance(n, (int, float))]
        if not nums:
            return
        pct = None
        if isinstance(limit, (int, float)) and limit > 0:
            if isinstance(used, (int, float)):
                pct = clamp(used / limit * 100)
            elif isinstance(remaining, (int, float)):
                pct = clamp((limit - remaining) / limit * 100)
        if pct is None:
            return
        worst = max(worst, pct)
        windows.append({"name": name, "percent": pct, "resetsAt": parse_reset(detail.get("resetTime"))})

    for entry in raw.get("limits") or []:
        window = entry.get("window") or {}
        duration, unit = window.get("duration"), (window.get("timeUnit") or "").upper()
        if duration == 300 and "MINUTE" in unit:
            name = "rolling"
        elif duration:
            name = f"{duration}-{unit.lower()}"
        else:
            name = "limit"
        add(name, entry.get("detail"))
    add("weekly", raw.get("usage"))

    total = raw.get("totalQuota") or {}
    remaining, limit = total.get("remaining"), total.get("limit")
    if isinstance(remaining, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        pct = clamp((limit - remaining) / limit * 100)
        worst = max(worst, pct)
        windows.append({"name": "total", "percent": pct, "resetsAt": 0})
    elif isinstance(remaining, (int, float)):
        windows.append({"name": "total", "percent": 0, "resetsAt": 0})
    return {"windows": windows, "status": status_from_percent(worst), "balance": None}


def map_openrouter(raw):
    """OpenRouter: data.limit / limit_remaining in dollars."""
    data = raw.get("data") or {}
    limit, remaining = data.get("limit"), data.get("limit_remaining")
    windows = []
    pct = 0
    if isinstance(limit, (int, float)) and limit > 0:
        used = None
        if isinstance(remaining, (int, float)):
            used = limit - remaining
        elif isinstance(data.get("usage"), (int, float)):
            used = data["usage"]
        if used is not None:
            pct = clamp(used / limit * 100)
            windows.append({"name": "credits", "percent": pct, "resetsAt": 0})
    if isinstance(remaining, (int, float)) and remaining <= 0:
        status = "exhausted"
    else:
        status = status_from_percent(pct)
    return {
        "windows": windows,
        "status": status,
        "balance": fmt_money(remaining) if isinstance(remaining, (int, float)) else None,
    }


def map_deepseek(raw):
    """DeepSeek: balance_infos[] with USD total_balance."""
    infos = raw.get("balance_infos") or []
    chosen = next((i for i in infos if (i.get("currency") or "").upper() == "USD"), infos[0] if infos else None)
    total = chosen.get("total_balance") if chosen else None
    try:
        total = float(total)
    except (TypeError, ValueError):
        total = None
    currency = (chosen.get("currency") or "USD").upper() if chosen else "USD"
    balance = None
    if total is not None:
        balance = f"{total:.2f}" if currency == "USD" else f"{total:.2f} {currency}"
    status = "exhausted" if (raw.get("is_available") is False or (total is not None and total <= 0)) else "ok"
    if total is not None and 0 < total < 1:
        status = "warning"
    return {"windows": [], "status": status, "balance": balance}


def map_moonshot(raw):
    """Moonshot: data.available_balance."""
    data = raw.get("data") or raw
    available = data.get("available_balance") if isinstance(data, dict) else None
    try:
        available = float(available)
    except (TypeError, ValueError):
        available = None
    balance = f"{available:.2f}" if available is not None else None
    status = "ok"
    if available is not None:
        status = "exhausted" if available <= 0 else "warning" if available < 1 else "ok"
    return {"windows": [], "status": status, "balance": balance}


MAPPERS = {
    "codex": map_wham,
    "xai": map_xai,
    "opencode-go": map_opencode_go,
    "kimi": map_kimi,
    "openrouter": map_openrouter,
    "deepseek": map_deepseek,
    "moonshot": map_moonshot,
}

# Claude windows from the mirrored websocket snapshot (provider: anthropic-oauth)
CLAUDE_MIRROR_PATH = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "afk" / "claude-quota.json"


def map_claude_mirror(mirror):
    """Normalise a mirrored subscription_quota payload for anthropic-oauth."""
    windows = []
    worst = 0
    for name, w in (mirror.get("windows") or {}).items():
        pct = clamp(w.get("used_percent"))
        if pct is None:
            continue
        worst = max(worst, pct)
        windows.append({"name": name, "percent": pct, "resetsAt": int(w.get("resets_at") or 0)})
    if not windows and mirror.get("status"):
        # status-only snapshot (e.g. exhausted flag without utilisation headers)
        status = mirror.get("status")
        return {"windows": [], "status": status if status in ("ok", "warning", "exhausted") else "ok", "balance": None}
    return {"windows": windows, "status": status_from_percent(worst), "balance": None}


def collect():
    api_key = resolve_api_key()
    base = (os.environ.get("AFK_USAGE_BASE") or DEFAULT_BASE).rstrip("/")
    record = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "ok": api_key is not None,
        "error": None if api_key else "no API key configured",
        "subscriptions": [],
    }
    if not api_key:
        return record

    seen = set()
    for provider, label in PROVIDERS:
        payload = fetch_json(f"{base}/api/auth/{provider}/usage", api_key)
        if payload is None:
            continue
        mapped = MAPPERS[provider](payload)
        if not mapped["windows"] and not mapped["balance"]:
            continue
        seen.add(provider)
        record["subscriptions"].append({"provider": provider, "label": label, **mapped})

    # Claude subscription — only via mirrored websocket snapshot when present.
    try:
        mirror = json.loads(CLAUDE_MIRROR_PATH.read_text())
        if mirror.get("provider") == "anthropic-oauth":
            mapped = map_claude_mirror(mirror)
            if mapped["windows"] or mapped["status"] != "ok":
                record["subscriptions"].insert(0, {
                    "provider": "anthropic-oauth", "label": "Claude", **mapped
                })
    except (OSError, ValueError):
        pass

    return record


def main():
    record = collect()
    json.dump(record, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
