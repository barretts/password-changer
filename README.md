# Password Changer

Automated password rotation pipeline for bulk credential updates from a LastPass vault export. Uses LLM-powered browser agents to navigate websites, log in, find password change forms, and set new passwords — all without human intervention.

> **Security:** This pipeline processes real credentials. Run it only on your local machine. Keep vault exports and `passwords.db` out of version control and never share them. The `.gitignore` excludes sensitive files by default.

## Security Notes

- Why this approach was acceptable for this constrained use case: [`RATIONAL.md`](./RATIONAL.md)
- How to harden this pipeline while preserving product value: [`SECURITY-HARDENING.md`](./SECURITY-HARDENING.md)

## How It Works

```
LastPass CSV
    │
    ▼
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  init-db.py  │────▶│  passwords.db    │◀───▶│  update-db.py   │
│  (parse CSV) │     │  (SQLite state)  │     │  (parse results)│
└──────────────┘     └──────────────────┘     └─────────────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │ run-password-changer│
                │  (bash orchestrator)│
                │   concurrency=2     │
                └────────┬────────────┘
                         │  spawns N workers
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        ┌───────────┐ ┌───────────┐ ┌───────────┐
        │  Cursor   │ │  Cursor   │ │  Cursor   │
        │  Agent    │ │  Agent    │ │  Agent    │
        │  (LLM)    │ │  (LLM)    │ │  (LLM)    │
        └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
              │             │             │
              └──────────┬────────────────┘
                         │ MCP Protocol
                         ▼
              ┌─────────────────────┐
              │  CamoFox MCP Bridge │
              │  (camofox-mcp)      │
              └─────────┬───────────┘
                        │ REST API
                        ▼
              ┌─────────────────────┐
              │  CamoFox Browser    │
              │  (Docker :9377)     │
              │  Anti-detection     │
              │  Firefox (Camoufox) │
              └─────────────────────┘
```

### The LLM Layer

Each password change task is handled by an instance of Cursor's `agent` CLI — a full LLM agent (Composer-1.5) with tool-use capabilities. The agent receives a prompt containing the site URL, username, and current password, then autonomously:

1. Opens a browser tab via CamoFox MCP
2. Navigates to the site, finding login pages (including hidden ones like payment portals)
3. Logs in with the current credentials
4. Navigates to Settings/Security/Account to find the password change form
5. Generates a new password via `generate-password.sh`
6. Fills and submits the change form
7. Handles retries if the site rejects the password (too long, wrong character set, etc.)
8. Outputs structured JSON with the result, new password, and site metadata

The agent adapts to each site's unique layout — there's no site-specific scraping logic. The LLM reads the page via accessibility tree snapshots and decides what to click, type, and navigate to. This is what makes it work across thousands of different websites without per-site configuration.

### Anti-Detection Browser

Standard Playwright/Chromium automation gets blocked by bot detection on most sites. This pipeline uses [CamoFox](https://github.com/redf0x1/camofox-mcp), which wraps [Camoufox](https://github.com/daijro/camoufox) — a Firefox fork with C++ level fingerprint spoofing:

- Unique browser fingerprint per tab (user agent, WebGL, canvas, fonts, screen size)
- Human-like navigator properties
- No `navigator.webdriver` flag
- Timezone/locale consistency

This reduced the "blocked by bot detection" rate from ~60% (Playwright) to ~15% (CamoFox).

### Vision LLM for CAPTCHA Solving

When the agent encounters a visual CAPTCHA (reCAPTCHA image grid, hCaptcha, text CAPTCHA), it doesn't give up. Instead:

1. Takes a screenshot of the page via CamoFox REST API (`captcha-screenshot.sh`)
2. Sends the image to a local vision LLM ([Qwen 2.5 VL](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct)) running on [LM Studio](https://lmstudio.ai/) (default: `http://127.0.0.1:1234`, override with `VISION_LLM_URL`)
3. The vision model analyzes the CAPTCHA and returns a JSON response (which grid cells to click, what text to type, etc.)
4. The agent acts on the response — clicking tiles, typing text, submitting

This runs entirely locally with no external CAPTCHA-solving service. The vision model handles ~70% of image-based CAPTCHAs. Behavioral CAPTCHAs (Cloudflare Turnstile, PerimeterX) can't be solved this way since they require browser-level signals, but CamoFox's anti-detection sometimes bypasses those automatically.

## Setup

### Prerequisites

- **Docker** — for the CamoFox browser server
- **Node.js 18+** — for the CamoFox MCP bridge (`npx`)
- **Cursor IDE** with `agent` CLI — the LLM orchestrator
- **Python 3.10+** — for database scripts
- **SQLite 3** — ships with macOS/Python
- **LM Studio** (optional) — for local CAPTCHA solving with a vision model. Set `VISION_LLM_URL` (default: `http://127.0.0.1:1234/v1/chat/completions`) and optionally `VISION_LLM_MODEL` if running on another host or using a different model.

### 1. Start CamoFox Browser

```bash
docker run -d -p 9377:9377 --name camofox-browser ghcr.io/redf0x1/camofox-browser:latest
```

### 2. Configure MCP

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "camofox": {
      "command": "npx",
      "args": ["-y", "camofox-mcp@latest"],
      "env": {
        "CAMOFOX_URL": "http://127.0.0.1:9377"
      }
    }
  }
}
```

### 3. Initialize Database

```bash
pip install -r requirements.txt
python3 init-db.py lastpass_vault_export.csv
python3 apply-skip-list.py
```

### 4. Run

```bash
./run-password-changer.sh 2    # concurrency of 2 (max stable)
```

Resume after interruption:

```bash
RESUME=1 ./run-password-changer.sh 2
```

Kill gracefully:

```bash
./kill-password-changer.sh
```

### 5. Export Results

```bash
python3 consolidate.py    # generates REPORT.md and CSV exports
```

## File Reference

| File                       | Purpose                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `init-db.py`               | Parses LastPass CSV, creates SQLite schema, populates `passwords.db`                        |
| `run-password-changer.sh`  | Orchestrates parallel agent workers with FIFO semaphore                                     |
| `prompt-template.md`       | The prompt each agent receives — instructions for navigating, logging in, changing password |
| `update-db.py`             | Extracts JSON results from agent logs and updates the database                              |
| `generate-password.sh`     | Generates cryptographically random passwords meeting common site requirements               |
| `kill-password-changer.sh` | Graceful shutdown — kills workers, resets in-progress tasks                                 |
| `consolidate.py`           | Generates reports and CSV exports (Bitwarden, 1Password compatible)                         |
| `solve-captcha.py`         | Sends screenshots to the local vision LLM for CAPTCHA solving                               |
| `captcha-screenshot.sh`    | Saves a screenshot from CamoFox browser via REST API                                        |
| `skip-domains.conf`        | Domains to auto-skip (Google, Microsoft, banks with mandatory MFA, etc.)                    |
| `apply-skip-list.py`       | Marks matching entries as 'skipped' in the database                                         |
| `camoufox-recheck.py`      | Re-checks blocked/dead sites using Camoufox to identify recoverable entries                 |
| `test-camoufox.py`         | Standalone test script for Camoufox reachability testing                                    |

## Results

From a vault of ~1,385 actionable entries:

| Status         | Count | Description                                              |
| -------------- | ----: | -------------------------------------------------------- |
| success        |   148 | Password changed and new credential stored               |
| mfa_required   |   103 | Logged in but MFA blocked automated change               |
| blocked        |   181 | Bot detection prevented access                           |
| failed         |   221 | Login worked but change form not found, site error, etc. |
| stale_password |   249 | LastPass password was already wrong/expired              |
| site_dead      |   231 | Domain no longer exists or is unreachable                |
| skipped        |   251 | Auto-skipped (Google, Microsoft, banks, etc.)            |

## Time Savings

**Manual effort for 148 password changes:** 3-5 minutes each (navigate, login, find settings, change, verify, record new password) = 7-12 hours of focused clicking. Realistically spread over 3-5 days with context-switching and fatigue.

**Actual time spent building and running this pipeline:**

| Activity | Time |
|----------|------|
| Designing pipeline, writing scripts, setting up database | ~2 hours |
| First Playwright MCP run (117 successes) | ~1 hour |
| Debugging (file deletion incident, database locks, counter bugs) | ~1 hour |
| Switching to CamoFox, investigating blocks, fixing tab leak | ~2 hours |
| Unattended agent runtime across all runs | ~3 hours |
| **Total interactive time** | **~6 hours** |

Built over 2 days. Beyond the 148 automated password changes, the pipeline also produced a categorized, prioritized queue for the remaining 1,200+ entries — tagged by site category, MFA type, failure reason, and manual action needed — which would have taken days to triage by hand.

## Limitations

- **Concurrency capped at 2** — the `agent` CLI has a race condition writing `cli-config.json` when running 3+ instances
- **Cross-origin CAPTCHA iframes** — reCAPTCHA/hCaptcha grids inside iframes can't be clicked by any automation framework (browser security)
- **Behavioral CAPTCHAs** — Cloudflare Turnstile and PerimeterX press-and-hold challenges require real human interaction
- **Stale credentials** — ~18% of the LastPass vault had passwords that were already changed/expired
- **MFA** — the pipeline correctly detects and skips MFA-protected accounts rather than attempting bypass
