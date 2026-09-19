# AI Usage for Omarchy

One bar icon, one panel, one plugin: **Claude Code, Codex, Fireworks, and Ollama Cloud** usage, limits, and balance in a native Omarchy panel.

![Panel preview: Ollama Cloud provider showing balance, session and weekly limits, and per-model requests](preview.png)

## What you get

| Feature | Claude Code | Codex CLI | Fireworks | Ollama Cloud |
|---|---|---|---|---|
| Plan tier (PRO, etc.) | ✅ | ✅ | ✅ | ✅ |
| Session (5-hour) + weekly limit meters with % used | ✅ | ✅ | ✅ | ✅ |
| Reset countdowns | ✅ | ✅ | ✅ | ✅ |
| Extra-usage balance (prepaid credits) | — | — | ✅ | ✅ |
| Per-model request counts | — | — | — | ✅ |
| Per-model token breakdown (input/output/cache) | ✅ | ✅ | ✅ | — |
| Today + last 7 days token history | ✅ | ✅ | ✅ | — |
| Multi-machine sync (optional) | ✅ | ✅ | ✅ | ✅ |

- **One panel for every provider.** Providers without data don't appear at all, so you only ever see what you actually use. ollama.com reports account-global numbers, so when sync is on they are merged, never summed across machines.
- **Limit meters.** Each window shows how much of your 5-hour session and weekly allowance is used and when it resets. The bar icon lights up when a window passes 90% or your prepaid balance runs low.
- **Per-model requests (Ollama Cloud).** See which models you actually used this week — e.g. `glm 5.3 Flash`, `gemma4:31b` — with request counts, straight from your ollama.com account.
- **Token history** (Claude, Codex, Fireworks). Today's prompts/sessions and tokens, plus a 7-day bar chart with a peak marker.
- **Controls.** Click the bar icon to open the panel. Switch providers with a middle-click on the bar icon, the named tabs inside the panel, or the ←/→ arrow keys; ↑/↓ scrolls. The refresh button in the panel header (or the `r` key) re-reads every provider; `Esc` closes. Right-click on the bar icon opens the agent picker.

## Install

```sh
omarchy plugin add https://github.com/GePi0/omarchy-ai-usage.git --enable
```

Click the AI button in the bar and you get one dashboard with every provider next to each other.

## Requirements

- **Omarchy** with the AI widget enabled (`omarchy.agents` is on by default; this plugin replaces it in the bar)
- **Ollama Cloud provider:** a Chromium-based browser — Google Chrome, Chromium, or Brave — **signed in to [ollama.com](https://ollama.com)**. Just open ollama.com once in your desktop browser and make sure you are logged in; the plugin reads the rest.
- **Claude Code / Codex CLI / Fireworks:** needed only for their own provider cards. Providers without data simply don't show up in the panel.

## How it works

The Omarchy shell reads one JSON file per agent from `~/.local/state/omarchy/agents/usage/`, written by collectors behind `omarchy-agent-usage-update`. Collectors for Claude Code, Codex, and Fireworks ship with Omarchy itself.

**This plugin adds the missing fourth collector, built in.** ollama.com has no usage API, so the collector renders `https://ollama.com/settings` with a headless copy of your desktop browser profile and reads the numbers straight off the page: plan tier, "Session usage" and "Weekly usage" (percent used and reset countdowns), the extra-usage balance, and per-model request segments. The collector runs on every panel refresh and on a slow 15-minute cadence, since Ollama Cloud usage changes slowly and the render is not cheap.

Providers can be toggled individually without uninstalling:

```sh
omarchy bar set ollama-cloud.ai-usage providers '{"claude":{"enabled":false}}' --json
```

If auto-detection picks the wrong browser, force the right one:

```sh
python3 ~/.config/omarchy/plugins/ollama-cloud.ai-usage/collect.py    # print the record as JSON
OLLAMA_USAGE_BROWSER=chromium python3 ~/.config/omarchy/plugins/ollama-cloud.ai-usage/collect.py --write
```

`OLLAMA_USAGE_BROWSER` accepts `google-chrome`, `chromium`, or `brave`; `OLLAMA_USAGE_PROFILE` points at a specific browser profile directory if you have several.

## Uninstall

```sh
omarchy plugin remove ollama-cloud.ai-usage
```

Removal deletes the plugin checkout and drops `~/.local/state/omarchy/agents/usage/ollama.json`, so the Ollama Cloud provider leaves the panel. It does not touch browser profiles, cookies, or sessions.

## Privacy

The collector renders one local page view with your existing browser session — no new logins, no shared data. No cookies, tokens, or page content are ever written anywhere; only the usage numbers land in the local state file. Expired sessions are not refreshed: open ollama.com in your browser and sign in again to renew.

## License

[MIT](LICENSE) — © 2026 Gerard Piella