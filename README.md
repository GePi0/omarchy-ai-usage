# AI Usage for Omarchy

One bar icon, one panel, one plugin: Claude Code, Codex, Fireworks, **and Ollama Cloud** usage, limits, and balance in a native Omarchy panel.

![preview](preview.png)

- **Plan tier, session/weekly limit meters** with reset countdowns and pace
- **Extra-usage balance** for prepaid accounts
- **Per-model request counts** (Ollama Cloud) and **token breakdowns** (Claude/Codex)
- **Today / last 7 days** token history
- Optional **multi-machine sync** (Syncthing/Dropbox) with automatic merging

## Install

```sh
omarchy plugin add https://github.com/GePi0/omarchy-ai-usage.git --enable
```

After install, click the AI button in the bar. You get one panel with every provider next to each other: Claude, Codex, Fireworks, and Ollama Cloud — your plan tier, the 5-hour session and weekly limit meters, extra-usage balance, and per-model request counts as recorded from ollama.com.

## Requirements

- Omarchy with the AI widget enabled (`omarchy.agents` is on by default; this plugin replaces it)
- For the Ollama Cloud provider: a Chromium-based browser (Google Chrome, Chromium, or Brave) **signed in to [ollama.com](https://ollama.com)** — open ollama.com once and make sure you are logged in
- Claude Code / Codex CLI / Fireworks for their respective providers (the panel collapses providers with no data)

## How it works

The stock `omarchy.agents` widget reads one JSON file per agent from `~/.local/state/omarchy/agents/usage/`, written by collectors behind `omarchy-agent-usage-update`. Claude Code, Codex, and Fireworks collectors ship with Omarchy itself.

**This plugin adds a fourth collector, built in.** ollama.com has no usage API, so the collector renders `https://ollama.com/settings` with a **headless copy of your desktop browser profile** and reads the numbers straight off the page: tier name, "Session usage", "Weekly usage" (percent used and reset countdowns), the extra-usage balance, and per-model request segments. Manual override if auto-detection picks the wrong target:

```sh
~/.config/omarchy/plugins/ollama-cloud.ai-usage/collect.py            # print record as JSON
OLLAMA_USAGE_BROWSER=chromium omarchy bar set ollama-cloud.ai-usage ...  # force a browser
```

The collector runs on every panel refresh and on a slow 15-minute cadence. Providers can be switched off individually:

```sh
omarchy bar set ollama-cloud.ai-usage providers '{"claude":{"enabled":false}}' --json
```

## Uninstall

```sh
omarchy plugin remove ollama-cloud.ai-usage
```

Removal deletes the plugin checkout and drops `~/.local/state/omarchy/agents/usage/ollama.json`, so the Ollama Cloud provider leaves the panel. It does not change browser profiles, cookies, or sessions.

## Privacy

The collector renders one local page view with your existing browser session. No cookies, tokens, or page content are written anywhere — only the usage numbers land in the state file. Expired sessions are not refreshed; open ollama.com in your browser and sign in again to renew.