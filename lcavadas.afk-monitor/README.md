# AFK Monitor (omarchy bar plugin)

Bar widget showing AFK subscription usage per connection: quota windows
(percent used, time to reset) and prepaid credit balances.

## How it works

- `collector.py` polls `https://afk-server.mooglest.com/api/auth/<provider>/usage`
  for every provider AFK supports (ChatGPT/Codex, xAI, OpenCode Go, Kimi,
  OpenRouter, DeepSeek, Moonshot). Providers that are not connected return 404
  and are skipped. Auth uses the daemon API key from `~/.afk/config`
  (`AFK_USAGE_API_KEY` overrides, `AFK_API_KEY` is also honoured).
- `Panel.qml` (Quickshell bar widget) runs the collector on a 5-minute timer
  and on popup open, then renders one bar metric per subscription and one card
  per subscription in the popup.

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
ln -s "$PWD/lcavadas.afk-monitor" ~/.config/omarchy/plugins/lcavadas.afk-monitor
omarchy plugin validate ~/.config/omarchy/plugins/lcavadas.afk-monitor
omarchy bar move lcavadas.afk-monitor --section right   # or edit shell.json
```

The shell hot-reloads plugin code on save; `omarchy restart shell` forces a
full reload if needed.

## Files

| File | Role |
|---|---|
| `manifest.json` | Plugin manifest (bar-widget entry point) |
| `collector.py` | Polls AFK hub usage endpoints, emits one JSON record |
| `Model.js` | Record parsing + formatting helpers (window labels, reset countdown) |
| `Panel.qml` | Bar widget and popup cards |
