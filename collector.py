#!/usr/bin/env python3
"""Collect AFK subscription usage and emit one display-ready JSON record.

The record is consumed by the AFK Monitor bar widget. Every subscription
connection configured in AFK is polled and normalised to a common shape,
grouped per account (API key):

    {
      "generatedAt": iso8601,
      "ok": bool,
      "accounts": [
        {
          "label": "primary",
          "ok": bool,
          "error": null,
          "subscriptions": [
            {
              "provider":  "opencode-go",        # AFK provider id
              "label":     "OpenCode Go",
              "status":    "ok" | "warning" | "exhausted",
              "windows":   [ {"name", "percent", "resetsAt"} ],
              "balance":   "12.34 USD" | null    # credit-style subscriptions
            }
          ]
        }
      ]
    }

Accounts: every AFK key to watch must be configured explicitly in
~/.config/omarchy/afk-monitor.json (0600); nothing is assumed from
AFK's own config:

    { "keys": [ {"label": "Work org", "key": "afk-..."}, ... ] }

Array order is display order. Manage them with the collector CLI:

    collector.py add-key <label> <key>
    collector.py remove-key <label>
    collector.py move-key <label> <up|down|position>
    collector.py list-keys

Data sources (per account key):

    GET https://afk-server.mooglest.com/api/auth/<provider>/usage
        codex (ChatGPT), copilot (GitHub Copilot), xai, opencode-go, kimi,
        openrouter, deepseek, moonshot — 401/403/404 means the provider is
        not connected (or GitHub refused the unofficial Copilot quota call)
        and is skipped. Copilot needs AFK 0.13.7+.

    Claude (anthropic-oauth) has no HTTP route: its quota arrives as a live
    websocket `subscription_quota` message while an agent is running. The
    agent mirrors the last snapshot to ~/.cache/afk/claude-quota.json
    (primary account only — the mirror is written by this machine's agent).

Percentages are normalised to 0-100 here; window names are normalised to
lowercase short labels. The base URL can be overridden with AFK_USAGE_BASE
(for development against a local hub).
"""

import json
import os
import stat
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = "https://afk-server.mooglest.com"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_ITEMS = 64
MAX_JSON_STRING_BYTES = 8 * 1024
CONFIG_PATH = Path.home() / ".afk" / "config"
KEYS_PATH = Path(os.environ.get("AFK_MONITOR_KEYS") or
                 (Path.home() / ".config" / "omarchy" / "afk-monitor.json"))

# provider id -> display label
PROVIDERS = [
    ("codex", "ChatGPT"),
    ("copilot", "GitHub Copilot"),
    ("xai", "xAI"),
    ("opencode-go", "OpenCode Go"),
    ("kimi", "Kimi"),
    ("openrouter", "OpenRouter"),
    ("deepseek", "DeepSeek"),
    ("moonshot", "Moonshot"),
]


# ── extra AFK keys (other orgs / personal accounts) ────────────────────────
# Stored in ~/.config/omarchy/afk-monitor.json with 0600 perms:
#     { "keys": [ {"label": "Work org", "key": "afk-..."} ] }
# The daemon key from ~/.afk/config is always collected first as the
# "primary" account; extra keys each become their own account.

def load_keys():
    try:
        data = json.loads(KEYS_PATH.read_text())
        keys = data.get("keys") if isinstance(data, dict) else None
        if isinstance(keys, list):
            return [k for k in keys
                    if isinstance(k, dict) and str(k.get("key") or "").strip()]
    except (OSError, ValueError):
        pass
    return []


def _verify_keys_path():
    """Reject unsafe config locations before replacing the keys file."""
    parent = KEYS_PATH.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent_stat = os.stat(parent)
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise OSError("AFK Monitor config directory is not owned by this user")
    try:
        existing = os.lstat(KEYS_PATH)
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.getuid()
            or existing.st_mode & 0o077):
        raise OSError("AFK Monitor keys file is not a private regular file")


def save_keys(entries):
    _verify_keys_path()
    tmp = KEYS_PATH.with_name(KEYS_PATH.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"keys": entries}, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, KEYS_PATH)
        os.chmod(KEYS_PATH, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mask_key(key):
    k = str(key)
    if len(k) <= 8:
        return k[:2] + "…"
    return k[:6] + "…" + k[-4:]


def keys_cli(argv):
    """add-key <label> --stdin | remove-key <label|mask> | list-keys

    Mutating commands print a human message to stderr and then fall through
    to a normal collection, so a single invocation always ends with the full
    JSON record on stdout — callers never need a second round-trip.
    """
    if not argv:
        return False
    cmd, rest = argv[0], argv[1:]
    if cmd == "add-key" and len(rest) == 2 and rest[1] == "--stdin":
        entries = load_keys()
        label, key = rest[0].strip(), sys.stdin.readline().strip()
        if not label or not key:
            print("add-key: label and key are both required", file=sys.stderr)
            return True
        entries = [e for e in entries if e.get("label") != label]
        entries.append({"label": label, "key": key})
        save_keys(entries)
        print(f"saved {label} ({mask_key(key)})", file=sys.stderr)
        return True
    if cmd == "remove-key" and len(rest) == 1:
        target = rest[0]
        entries = load_keys()
        kept = [e for e in entries
                if e.get("label") != target and mask_key(e.get("key", "")) != target]
        if len(kept) == len(entries):
            print(f"no key matching {target!r}", file=sys.stderr)
        else:
            save_keys(kept)
            print(f"removed {target}", file=sys.stderr)
        return True
    if cmd == "move-key" and len(rest) == 2:
        label, where = rest[0].strip(), rest[1].strip().lower()
        entries = load_keys()
        idx = next((i for i, e in enumerate(entries)
                    if e.get("label") == label), None)
        if idx is None:
            print(f"no key matching {label!r}", file=sys.stderr)
            return True
        if where in ("up", "down"):
            j = idx - 1 if where == "up" else idx + 1
            if j < 0 or j >= len(entries):
                print(f"{label} is already at the {'top' if where == 'up' else 'bottom'}",
                      file=sys.stderr)
                return True
            entries[idx], entries[j] = entries[j], entries[idx]
        elif where.isdigit():
            pos = max(0, min(len(entries) - 1, int(where) - 1))
            entries.insert(pos, entries.pop(idx))
        else:
            print("move-key: use up, down, or a 1-based position", file=sys.stderr)
            return True
        save_keys(entries)
        print(f"moved {label} {where}", file=sys.stderr)
        return True
    if cmd == "list-keys":
        entries = load_keys()
        if not entries:
            print("no keys configured")
        for i, e in enumerate(entries, 1):
            print(f"{i}\t{e.get('label', '?')}\t{mask_key(e.get('key', ''))}")
        return True
    return False


def fetch_usage(url, api_key):
    """Return (payload, status). status is 200, an HTTP code, or "error".

    401/403/404 = not connected (or GitHub refused Copilot's unofficial
    quota call) and should be skipped. Other failures are returned so
    Copilot can show a small error instead of vanishing.
    """
    req = urllib.request.Request(url, headers={"X-AFK-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return None, resp.status
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return None, "error"
            payload = json.loads(body.decode())
            if not _safe_json(payload):
                return None, "error"
            return payload, 200
    except urllib.error.HTTPError as e:
        return None, e.code
    except (urllib.error.URLError, OSError, ValueError):
        return None, "error"


def _safe_json(value, depth=0):
    """Accept only bounded JSON trees before mapper code consumes server input."""
    if depth > MAX_JSON_DEPTH:
        return False
    if isinstance(value, str):
        return len(value.encode()) <= MAX_JSON_STRING_BYTES
    if isinstance(value, list):
        return len(value) <= MAX_JSON_ITEMS and all(_safe_json(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return (len(value) <= MAX_JSON_ITEMS
                and all(isinstance(k, str) and len(k.encode()) <= MAX_JSON_STRING_BYTES
                        and _safe_json(v, depth + 1) for k, v in value.items()))
    return value is None or isinstance(value, (bool, int, float))


def fetch_json(url, api_key):
    """Return parsed JSON or None. 404 = not connected = skip silently."""
    payload, status = fetch_usage(url, api_key)
    return payload if status == 200 else None


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
    credits = raw.get("credits") or {}
    balance = None
    # Only report a balance when the plan actually carries credits; ChatGPT
    # Plus returns balance "0" with has_credits False, which would read as
    # an exhausted prepaid account.
    if credits.get("has_credits"):
        balance = fmt_money(credits.get("balance"))
    return {
        "windows": windows,
        "status": "exhausted" if rl.get("limit_reached") else status_from_percent(primary_pct),
        "balance": balance,
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


def _copilot_snapshot_usable(snap):
    """True when a Copilot quota snapshot can be shown as a credit card."""
    if not isinstance(snap, dict):
        return False
    if snap.get("unlimited") is True:
        return True
    if snap.get("has_quota") is True:
        return True
    entitlement = snap.get("entitlement")
    try:
        return float(entitlement) > 0
    except (TypeError, ValueError):
        return False


def _pick_copilot_snapshot(snapshots):
    """Prefer premium_interactions; else the first snapshot with has_quota."""
    if not isinstance(snapshots, dict):
        return None, None
    premium = snapshots.get("premium_interactions")
    if _copilot_snapshot_usable(premium):
        return "premium", premium
    for name, snap in snapshots.items():
        if name == "premium_interactions":
            continue
        if isinstance(snap, dict) and snap.get("has_quota") is True:
            label = "premium" if name == "premium_interactions" else str(name or "credits")
            return label, snap
    return None, None


def map_copilot(raw):
    """GitHub Copilot: quota_snapshots.premium_interactions remaining credits.

    used_percent = clamp(100 - percent_remaining) when present, else
    (entitlement - remaining) / entitlement. Unlimited snapshots have no bar.
    """
    data = raw if isinstance(raw, dict) else {}
    name, snap = _pick_copilot_snapshot(data.get("quota_snapshots") or {})
    if snap is None:
        return {"windows": [], "status": "ok", "balance": None, "credits": None}

    reset = parse_reset(data.get("quota_reset_date_utc")) or parse_reset(data.get("quota_reset_date"))
    if snap.get("unlimited") is True:
        return {
            "windows": [],
            "status": "ok",
            "balance": None,
            "credits": "Unlimited",
            "overageAvailable": bool(snap.get("overage_permitted")),
        }

    remaining_raw = snap.get("quota_remaining")
    if remaining_raw is None:
        remaining_raw = snap.get("remaining")
    remaining = None
    try:
        remaining = int(round(float(remaining_raw)))
    except (TypeError, ValueError):
        remaining = None
    entitlement = None
    try:
        entitlement = int(round(float(snap.get("entitlement"))))
    except (TypeError, ValueError):
        entitlement = None

    pct = None
    if snap.get("percent_remaining") is not None:
        try:
            pct = clamp(100 - float(snap.get("percent_remaining")))
        except (TypeError, ValueError):
            pct = None
    if pct is None and remaining is not None and isinstance(entitlement, int) and entitlement > 0:
        pct = clamp((entitlement - remaining) / entitlement * 100)
    pct = 0 if pct is None else pct

    credits = None
    if remaining is not None and entitlement is not None:
        credits = f"{remaining} / {entitlement} credits"
    elif remaining is not None:
        credits = f"{remaining} credits"

    return {
        "windows": [{"name": name or "credits", "percent": pct, "resetsAt": reset}],
        "status": status_from_percent(pct),
        "balance": None,
        "credits": credits,
        "overageAvailable": bool(snap.get("overage_permitted")),
    }


def map_moonshot(raw):
    """Moonshot: data.available_balance."""
    data = raw.get("data") or raw
    available = data.get("available_balance") if isinstance(data, dict) else None
    try:
        available = float(available)
    except (TypeError, ValueError):
        available = None
    if available is None:
        return {"windows": [], "status": "ok", "balance": None}
    # Match AFK web's remainingCreditStatus: the ledger exists, so a zero or
    # negative balance genuinely means drained (exhausted), not "hidden".
    balance = f"{available:.2f}"
    status = "exhausted" if available <= 0 else "warning" if available < 1 else "ok"
    return {"windows": [], "status": status, "balance": balance}


MAPPERS = {
    "codex": map_wham,
    "copilot": map_copilot,
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


def daemon_key():
    """This machine's daemon key, if ~/.afk/config is readable. Only used to
    attribute the Claude mirror to the account that actually runs agents."""
    try:
        for line in CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("api_key") and "=" in line:
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def collect_account(api_key, label, base, with_claude_mirror):
    """Fan out the provider usage endpoints for one AFK key."""
    subscriptions = []
    for provider, provider_label in PROVIDERS:
        payload, status = fetch_usage(f"{base}/api/auth/{provider}/usage", api_key)
        if payload is None:
            # Copilot: hide on 401/403/404; surface other failures as a
            # small error so a down hub or AFK < 0.13.7 does not crash.
            if provider == "copilot" and status not in (401, 403, 404):
                detail = f"HTTP {status}" if isinstance(status, int) else "request failed"
                subscriptions.append({
                    "provider": provider,
                    "label": provider_label,
                    "status": "ok",
                    "windows": [],
                    "balance": None,
                    "credits": None,
                    "error": f"Could not load Copilot quota ({detail})",
                })
            continue
        mapped = MAPPERS[provider](payload)
        if not mapped["windows"] and not mapped["balance"] and not mapped.get("credits"):
            continue
        subscriptions.append({
            "provider": provider, "label": provider_label, **mapped
        })

    # Claude subscription — only via mirrored websocket snapshot when present.
    # The mirror is written by this machine's agent, so it belongs to the
    # configured account whose key is the daemon key (if any).
    if with_claude_mirror:
        try:
            mirror = json.loads(CLAUDE_MIRROR_PATH.read_text())
            if mirror.get("provider") == "anthropic-oauth":
                mapped = map_claude_mirror(mirror)
                if mapped["windows"] or mapped["status"] != "ok":
                    subscriptions.insert(0, {
                        "provider": "anthropic-oauth", "label": "Claude", **mapped
                    })
        except (OSError, ValueError):
            pass
    return subscriptions


def collect():
    base = (os.environ.get("AFK_USAGE_BASE") or DEFAULT_BASE).rstrip("/")
    daemon = daemon_key()
    accounts = []
    for entry in load_keys():
        label = str(entry.get("label") or "account")
        key = str(entry["key"]).strip()
        accounts.append({
            "label": label,
            "ok": True,
            "error": None,
            "subscriptions": collect_account(
                key, label, base, with_claude_mirror=(key == daemon)),
        })

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "error": None,
        "accounts": accounts,
        # Masked previews only — the raw keys never leave this file's storage.
        "keys": [
            {"label": str(e.get("label") or "?"), "masked": mask_key(e.get("key", ""))}
            for e in load_keys()
        ],
    }


def main():
    argv = sys.argv[1:]
    mutating = keys_cli(argv)  # True → message on stderr, then collect below
    record = collect()
    if mutating:
        # Let the caller know the effect: which accounts now exist.
        print("accounts now: " + ", ".join(a["label"] for a in record["accounts"]),
              file=sys.stderr)
    json.dump(record, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
