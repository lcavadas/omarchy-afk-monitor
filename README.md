# AFK Monitor (omarchy bar plugin)

Bar widget showing AFK subscription usage per connection: quota windows
(percent used, time to reset) and prepaid credit balances.

## How it works

- `collector.py` polls `https://afk-server.mooglest.com/api/auth/<provider>/usage`
  for every provider AFK supports (ChatGPT/Codex, xAI, OpenCode Go, Kimi,
  OpenRouter, DeepSeek, Moonshot). Providers that are not connected return 404
  and are skipped.
- Every watched account must be configured explicitly — nothing is assumed
  from AFK's own config. Keys live in `~/.config/omarchy/afk-monitor.json`
  (0600); array order is display order:

  ```json
  { "keys": [ { "label": "Work org", "key": "afk-..." } ] }
  ```

  Manage them from the popup (add / reorder / remove / show-on-bar) or the
  collector CLI: `add-key <label> <key>`, `remove-key <label>`,
  `move-key <label> <up|down|position>`, `list-keys`.
- `Panel.qml` (Quickshell bar widget) runs the collector on a 5-minute timer
  and on popup open. With no keys configured the bar shows the AFK mark and
  the popup offers the add-key form. Each subscription card has a switch to
  include or exclude it from the bar (default: on), persisted per
  account/provider in the widget's shell.json entry.

### Claude subscriptions

Claude (anthropic-oauth) quota is not available over HTTP — AFK receives it as
a live websocket `subscription_quota` message inside the agent process while a
Claude session runs, and the hub treats it as transient (never persisted).

Optional mirror: run any Claude-backed AFK session with
`AFK_QUOTA_MIRROR=1` and an agent hook that writes the latest
`subscription_quota` payload to `~/.cache/afk/claude-quota.json`
(`{"provider":"anthropic-oauth","windows":{...},"status":"..."}`).
The collector picks it up and shows it first. Without the mirror, Claude
subscriptions are simply absent from the monitor.

## Install

```sh
omarchy plugin add ssh://git@git.mooglest.com/mooglest/omarchy-afk-monitor.git --enable
omarchy bar move lcavadas.afk-monitor --section center   # or any placement you like
```

`--enable` also asks where to place the widget on the bar. Enable later or
change placement any time with `omarchy plugin enable lcavadas.afk-monitor`
and `omarchy bar move lcavadas.afk-monitor --section right`.

### Manual install (alternative)

```sh
git clone ssh://git@git.mooglest.com/mooglest/omarchy-afk-monitor.git \
  ~/.config/omarchy/plugins/lcavadas.afk-monitor
omarchy plugin enable lcavadas.afk-monitor
```

The shell hot-reloads plugin code on save; `omarchy restart shell` forces a
full reload if needed.

## Files

| File | Role |
|---|---|
| `manifest.json` | Plugin manifest (bar-widget entry point, repo root) |
| `collector.py` | Polls AFK hub usage endpoints, emits one JSON record |
| `Model.js` | Record parsing + formatting helpers (window labels, reset countdown, icon mapping) |
| `Panel.qml` | Bar widget (provider mark + value per subscription) and popup cards |
| `assets/` | Monochrome provider marks: `<id>.svg` (white) + `<id>-light.svg` (dark) for light bars |

Provider marks follow the agents panel's convention: the widget picks the
white or dark twin based on the bar's text colour, so marks stay readable on
dark, light, and transparent bars. Sources: simpleicons.org (anthropic,
openrouter, deepseek, kimi, moonshotai, opencode), the omarchy agents plugin
(claude, codex), worldvectorlogo (xai); the AFK mark is AFK's own brand glyph.
