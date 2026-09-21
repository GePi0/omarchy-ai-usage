#!/usr/bin/env python3
# omarchy:args=[--force] [--browser <name>] [--profile <dir>]
"""Print or write the Ollama Cloud usage record as JSON.

The only source of Ollama Cloud usage is the signed-in settings page on
ollama.com (server-rendered htmx HTML; no JSON API). This collector lets a
headless Chromium-based browser render that page with the desktop browser's
session cookies, parses the plan limits, balance, and per-model request
counts, and emits one display-ready JSON record for the stock AI toolbar
widget.

Auth relies on the browser's own cookie decryption, which needs the session
keyring; nothing is logged or stored beyond the usage numbers themselves.
The render keeps Chromium's normal sandbox: the copied cookie database is
stripped to ollama.com rows, Local State keeps only the os_crypt key
material, and output is capped before parsing. Set
OLLAMA_USAGE_ALLOW_NO_SANDBOX=1 to trade the sandbox away explicitly on
systems where it cannot run; this is an insecure escape hatch, not a default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT_ID = "ollama"
AGENT_NAME = "Ollama Cloud"
SETTINGS_URL = "https://ollama.com/settings"

# Hard cap on --dump-dom output. The DOM is parsed for a handful of
# attributes and lines; anything beyond this is not a settings page.
MAX_DOM_BYTES = 10 * 1024 * 1024
RENDER_TIMEOUT_SEC = 90

# (binary candidates in preference order, per-browser config dir name)
BROWSERS = [
    (["google-chrome-stable", "google-chrome", "chrome"], "google-chrome"),
    (["chromium", "chromium-browser"], "chromium"),
    (["brave-browser", "brave"], "BraveSoftware/Brave-Browser"),
]

UNIT_RE = {"minute": 60, "minutes": 60, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}

STATS_KEYS = (
    "todayPrompts",
    "todaySessions",
    "todayTotalTokens",
    "todayTokensByModel",
    "recentDays",
    "totalPrompts",
    "totalSessions",
    "activeDays",
)

TOKEN_KEYS = ("todayTokensByModel", "modelUsage")


def state_dir() -> Path:
  root = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
  return Path(root) / "omarchy" / "agents" / "usage"


def usage_record_path() -> Path:
  return state_dir() / f"{AGENT_ID}.json"


def empty_result(**overrides) -> dict:
  out: dict = {
    "schemaVersion": 1,
    "id": AGENT_ID,
    "name": AGENT_NAME,
    "updatedAt": datetime.now(timezone.utc).isoformat(),
    "ready": False,
    "hasLocalStats": False,
    "hasPromptStats": True,
    # ollama.com numbers are account-global, so synced machines must not sum them.
    "scope": "account",
    "tierLabel": "",
    "usageStatusText": "",
    "authHelpText": "",
    "limits": [],
    "balance": {},
    "modelRequests": {},
  }
  for key in STATS_KEYS:
    out[key] = 0
  for key in TOKEN_KEYS:
    out[key] = {} if "ByModel" in key else []
  out["recentDays"] = []
  out["activeDates"] = []
  out.update(overrides)
  return out


def expand_path(value: str | None) -> Path | None:
  text = str(value or "").strip()
  if not text:
    return None
  return Path(os.path.expandvars(os.path.expanduser(text))).expanduser()


def resolve_browser(name: str | None) -> list[tuple[str, Path]] | None:
  """Find (binary, config-dir) pairs. Explicit --browser / env wins."""
  if name:
    norm = name.lower()
    for bins, dirname in BROWSERS:
      if norm in (b.lower() for b in bins) or norm == dirname.lower().replace("-", ""):
        out = []
        for b in bins:
          path = shutil.which(b)
          if path:
            out.append((path, Path.home() / ".config" / dirname))
        return out or None
    path = shutil.which(name)
    if path:
      return [(path, Path.home() / ".config" / name)]
    return None
  out = []
  for bins, dirname in BROWSERS:
    for b in bins:
      path = shutil.which(b)
      if path:
        out.append((path, Path.home() / ".config" / dirname))
        break
  return out or None


def profile_candidates(config_dir: Path) -> list[Path]:
  if not config_dir.is_dir():
    return []
  out = []
  default = config_dir / "Default"
  if (default / "Cookies").exists():
    out.append(default)
  for p in sorted(config_dir.glob("Profile */Cookies")) + sorted(config_dir.glob("profile*/Cookies")):
    out.append(p.parent)
  return out


def has_ollama_session(profile_dir: Path) -> bool:
  """True if the (still encrypted) cookie DB mentions ollama.com."""
  db = profile_dir / "Cookies"
  if not db.exists():
    return False
  try:
    with sqlite3.connect(f"file:{db}?immutable=1", uri=True) as conn:
      row = conn.execute(
        "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%ollama.com'"
      ).fetchone()
      return bool(row and row[0])
  except Exception:
    return False


def pick_profiles(config_dir: Path) -> list[Path]:
  """Profiles with an ollama.com session, newest first."""
  candidates = [p for p in profile_candidates(config_dir) if has_ollama_session(p)]
  return sorted(candidates, key=lambda p: (p / "Cookies").stat().st_mtime, reverse=True)


def filter_cookie_db(src: Path, dst: Path) -> int:
  """Copy the cookie DB keeping only ollama.com rows.

  The desktop profile's cookie store covers every site the user is signed
  in to; a least-privilege render must not carry those. Returns the row
  count kept.
  """
  with sqlite3.connect(f"file:{src}?immutable=1", uri=True) as conn:
    rows = conn.execute(
      "SELECT * FROM cookies WHERE host_key LIKE '%ollama.com'"
    ).fetchall()
    create_sql = conn.execute(
      "SELECT sql FROM sqlite_master WHERE type='table' AND name='cookies'"
    ).fetchone()
  if not create_sql or not create_sql[0]:
    return 0
  if dst.exists():
    dst.unlink()
  with sqlite3.connect(dst) as out:
    out.execute(create_sql[0])
    if rows:
      placeholders = ",".join("?" * len(rows[0]))
      out.executemany(f"INSERT INTO cookies VALUES ({placeholders})", rows)
    out.commit()
  return len(rows)


def scrub_local_state(src: Path, dst: Path) -> None:
  """Keep only the os_crypt key material from Local State."""
  try:
    data = json.loads(src.read_text("utf-8"))
  except (OSError, ValueError):
    return
  keep: dict = {}
  os_crypt = data.get("os_crypt")
  if isinstance(os_crypt, dict):
    keep["os_crypt"] = os_crypt
  user_data_dir = data.get("user_data_dir")
  if isinstance(user_data_dir, str) and user_data_dir:
    keep["user_data_dir"] = user_data_dir
  dst.write_text(json.dumps(keep), "utf-8")


def read_capped(stream, cap: int) -> tuple[bytes, bool]:
  """Read up to cap bytes; returns (data, truncated)."""
  chunks: list[bytes] = []
  total = 0
  truncated = False
  while True:
    chunk = stream.read(65536)
    if not chunk:
      break
    room = cap - total
    if room <= 0:
      truncated = True
      break
    if len(chunk) > room:
      chunks.append(chunk[:room])
      truncated = True
      total += room
      break
    chunks.append(chunk)
    total += len(chunk)
  return b"".join(chunks), truncated


def render_page(binary: str, config_dir: Path, profile_dir: Path, url: str) -> str:
  """Render a page with headless Chromium using a least-privilege profile copy.

  The sandbox stays on: no --no-sandbox. The copied cookie database holds
  only ollama.com rows and Local State is reduced to the decryption key
  material, so a renderer compromise sees one site's session, not the
  desktop browser's. Output is hard-capped before it reaches the parser.
  """
  no_sandbox_env = os.environ.get("OLLAMA_USAGE_ALLOW_NO_SANDBOX") == "1"
  with tempfile.TemporaryDirectory(prefix="ollama-usage-") as tmp:
    profile = Path(tmp) / "profile" / "Default"
    profile.mkdir(parents=True)
    cookies_src = profile_dir / "Cookies"
    if cookies_src.exists():
      kept = filter_cookie_db(cookies_src, profile / "Cookies")
      if kept == 0:
        raise RuntimeError("no ollama.com cookies in the selected profile")
    for name in ("Preferences",):
      src = profile_dir / name
      if src.exists():
        shutil.copy2(src, profile / name)
    local_state = config_dir / "Local State"
    if local_state.exists():
      scrub_local_state(local_state, Path(tmp) / "profile" / "Local State")
    flags = [
      binary, "--headless=new", "--disable-gpu",
      "--no-first-run", "--disable-sync", "--disable-extensions",
      f"--user-data-dir={tmp}/profile",
      "--virtual-time-budget=15000", "--dump-dom", url,
    ]
    if no_sandbox_env:
      flags.insert(1, "--no-sandbox")
    try:
      proc = subprocess.Popen(
        flags, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
      )
    except OSError as exc:
      raise RuntimeError(f"headless browser failed to start: {exc}")
    try:
      dom_bytes, truncated = read_capped(proc.stdout, MAX_DOM_BYTES)
      proc.wait(timeout=RENDER_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
      proc.kill()
      proc.wait(timeout=10)
      raise RuntimeError("headless browser timed out")
    finally:
      if proc.stdout:
        proc.stdout.close()
    if truncated:
      raise RuntimeError("headless browser output exceeded the DOM cap")
    dom = dom_bytes.decode("utf-8", "replace")
    if proc.returncode != 0 or len(dom) < 2000:
      raise RuntimeError(f"headless browser failed (rc={proc.returncode}, bytes={len(dom)})")
  return dom


def is_signed_out(dom: str) -> bool:
  return "signin.ollama.com" in dom or "Sign in" in dom[:20000]


def text_nodes(dom: str) -> list[str]:
  """Visible text blocks in document order."""
  dom = re.sub(r"<(script|style)\b.*?</\1>", " ", dom, flags=re.S)
  dom = re.sub(r"<[^>]+>", "\n", dom)
  out = []
  for raw in dom.split("\n"):
    line = unicodedata.normalize("NFKD", raw).strip()
    if line:
      out.append(line)
  return out


def parse_resets_at(text: str, now: datetime) -> str:
  """'Resets in 1 day, 2 hours' / 'Resets in less than a minute' -> ISO."""
  seconds = 0.0
  for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s+(hours?|minutes?|days?)", text, re.I):
    seconds += float(num) * UNIT_RE[unit.lower()]
  if not seconds:
    if re.search(r"less than a minute", text, re.I):
      seconds = 30
    else:
      m = re.search(r"\ban?\s+(minute|hour|day)\b", text, re.I)
      if m:
        seconds = UNIT_RE[m.group(1).lower()]
  if not seconds:
    return ""
  return (now + timedelta(seconds=seconds)).astimezone().isoformat(timespec="seconds")


def parse_percent_runs(text: str) -> float:
  m = re.search(r"([\d.]+)\s*%\s*used", text, re.I)
  return float(m.group(1)) if m else 0.0


def parse_models(dom: str, start_marker: str) -> list[dict]:
  """Usage segments whose nearest preceding section marker is start_marker."""
  marks = [m.start() for m in re.finditer(r"Session usage|Weekly usage", dom)]
  models: list[dict] = []
  for seg in re.finditer(r'data-model="([^"]+)" data-requests="(\d+)"', dom):
    section = None
    for pos in marks:
      if pos < seg.start():
        section = dom[pos:pos + 20]
    if section and section.startswith(start_marker):
      models.append({"model": seg.group(1), "requests": int(seg.group(2))})
  return models


def parse_balance(text_lines: list[str]) -> dict:
  balance = {"remaining": 0, "funded": 0, "spent": 0, "currency": "USD", "estimated": False}
  for i, line in enumerate(text_lines):
    if line.strip().lower() == "balance remaining" and i + 1 < len(text_lines):
      m = re.search(r"\$([\d.,]+)", text_lines[i + 1])
      if m:
        balance["remaining"] = float(m.group(1).replace(",", ""))
      break
  return balance


def build_record(lines: list[str], dom: str) -> dict:
  # plan tier: the line right after "Cloud usage"
  tier = ""
  if "Cloud usage" in lines:
    i = lines.index("Cloud usage")
    if i + 1 < len(lines) and re.fullmatch(r"[a-z0-9 .-]{1,20}", lines[i + 1]):
      tier = lines[i + 1].strip()

  now = datetime.now().astimezone()
  limits = []

  def line_block(marker):
    """Text lines between this marker line and the next marker line. ollama.com
    renders each number on its own line, so line order beats DOM slicing."""
    try:
      i = lines.index(marker)
    except ValueError:
      return []
    out = []
    for line in lines[i + 1:]:
      if line in ("Session usage", "Weekly usage", "Models used this week"):
        break
      out.append(line)
    return out

  if "Session usage" in lines:
    window = " ".join(line_block("Session usage"))
    limits.append({
      "label": "Session (5-hour)",
      # Panel expects percent as a 0-1 fraction
      "percent": parse_percent_runs(window) / 100.0,
      "resetsAt": parse_resets_at(window, now),
      "title": "Session",
    })
  if "Weekly usage" in lines:
    window = " ".join(line_block("Weekly usage"))
    limits.append({
      "label": "Weekly",
      "percent": parse_percent_runs(window) / 100.0,
      "resetsAt": parse_resets_at(window, now),
      "title": "Weekly",
    })

  return empty_result(
    ready=True,
    tierLabel=tier,
    limits=[l for l in limits if l["resetsAt"]],
    balance=parse_balance(lines),
    modelRequests={m["model"]: m["requests"] for m in parse_models(dom, "Weekly usage")},
  )


def collect_record(browser_name: str | None, profile_override: Path | None) -> dict:
  """Render ollama.com/settings and parse it into a usage record."""
  browsers = resolve_browser(browser_name)
  if not browsers:
    raise RuntimeError("no Chromium-based browser found (google-chrome, chromium, or brave)")

  if profile_override:
    if not profile_override.is_dir():
      raise RuntimeError(f"profile directory not found: {profile_override}")
    dom = render_page(
      browsers[0][0], browsers[0][1], profile_override, f"{SETTINGS_URL}?nonce={uuid.uuid4().hex[:8]}"
    )
    if is_signed_out(dom):
      raise RuntimeError(f"{profile_override.name}: signed out of ollama.com")
    return build_record(text_nodes(dom), dom)

  errors = []
  for binary, config_dir in browsers:
    profiles = pick_profiles(config_dir)
    if not profiles:
      errors.append(f"no {Path(binary).name} profile has ollama.com cookies")
      continue
    for profile in profiles:
      try:
        dom = render_page(binary, config_dir, profile, f"{SETTINGS_URL}?nonce={uuid.uuid4().hex[:8]}")
        if not is_signed_out(dom):
          return build_record(text_nodes(dom), dom)
        errors.append(f"{Path(binary).name}/{profile.name}: signed out")
      except (RuntimeError, OSError) as exc:
        errors.append(f"{Path(binary).name}/{profile.name}: {exc}")
  raise RuntimeError("; ".join(errors) or "no usable browser profile")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Print the Ollama Cloud usage record as JSON")
  # --force / --limits-only exist so every collector accepts the same invocation.
  parser.add_argument("--force", action="store_true")
  parser.add_argument("--limits-only", action="store_true")
  parser.add_argument("--write", action="store_true", help="write ~/.local/state/omarchy/agents/usage/ollama.json")
  parser.add_argument("--clear", action="store_true", help="remove ollama.json so the agents panel drops the Ollama Cloud provider")
  parser.add_argument(
    "--browser",
    default=os.environ.get("OLLAMA_USAGE_BROWSER", ""),
    help="Browser binary to render with (default: auto-detect google-chrome/chromium/brave)",
  )
  parser.add_argument(
    "--profile",
    default=os.environ.get("OLLAMA_USAGE_PROFILE", ""),
    help="Browser profile directory holding the ollama.com session (default: auto-detect)",
  )
  args = parser.parse_args(argv)

  if args.clear:
    usage_record_path().unlink(missing_ok=True)
    return 0

  try:
    record = collect_record(args.browser, expand_path(args.profile))
    if not record["limits"] and not record["balance"].get("remaining"):
      raise RuntimeError("usage page rendered but no limits found; ollama.com markup may have changed")
  except (RuntimeError, OSError) as exc:
    print(f"ollama-cloud-usage: {exc}", file=sys.stderr)
    if usage_record_path().exists():
      # Keep the last good record; a transient failure (locked keyring, network,
      # markup change) should not blank the panel chip.
      return 0
    record = empty_result(
      usageStatusText="Ollama Cloud usage unavailable",
      authHelpText="Sign in to ollama.com in your desktop browser, then refresh.",
    )

  if args.write:
    state_dir().mkdir(parents=True, exist_ok=True)
    target = usage_record_path()
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(tmp, target)
    print(f"ollama-cloud-usage: wrote {target}")
  else:
    print(json.dumps(record, separators=(",", ":"), sort_keys=True))
  return 0


if __name__ == "__main__":
  sys.exit(main())