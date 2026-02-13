# Autonomous Digital FTE (AI Employee)

An autonomous AI employee system that monitors Gmail, WhatsApp, and Stripe, processes incoming tasks through Claude Code, and executes approved actions — all orchestrated through an Obsidian vault.

## How It Works

```
Gmail / WhatsApp / Stripe
        |
        v
   Watchers poll sources
        |
        v
   Needs_Action/*.md        Task processor detects new file
        |
        v
   In_Progress/             Claude Code analyses the task
        |
        |--- Safe actions: executed immediately
        |--- Sensitive/financial: Pending_Approval/*.md
        v
   CEO reviews in Obsidian
        |
        |-- Approved/       Auto-executes (send email via MCP)
        |                   Moves to Done/ + logs success
        |
        |-- Rejected/       Logged with reason
```

## Components

| File | Language | Purpose |
|------|----------|---------|
| `base_watcher.py` | Python | Abstract base class for all watchers (polling loop, error handling) |
| `gmail_watcher.py` | Python | Polls Gmail for unread important emails, creates task files |
| `whatsapp_watcher.py` | Python | Monitors WhatsApp Web via Playwright for urgent messages |
| `stripe_watcher.py` | Python | Watches Stripe for successful payments via Events API |
| `task_processor.py` | Python | Orchestrator — detects tasks, invokes Claude, executes approved actions |
| `mcp_email_server.js` | Node.js | Local Express server for sending emails via Gmail API |
| `weekly_briefing.py` | Python | Generates CEO briefing reports with metrics and recommendations |
| `test_e2e_dry_run.py` | Python | End-to-end test suite (49 tests, no API keys needed) |

## Vault Structure

```
Obsidian Vault/
|-- Dashboard.md              CEO overview (balance, tasks, revenue)
|-- Company_Handbook.md       Operating rules and approval thresholds
|-- Business_Goals.md         Revenue targets and key metrics
|
|-- Needs_Action/             Incoming tasks from watchers
|-- In_Progress/              Tasks being processed by Claude
|-- Plans/                    Action plans with checkboxed steps
|-- Done/                     Completed tasks (archive)
|-- Pending_Approval/         Tasks awaiting CEO sign-off
|-- Approved/                 CEO-approved tasks (triggers execution)
|-- Rejected/                 Declined tasks with reasons
|-- Accounting/               Financial records
|-- Logs/                     Decision logs, action logs, error logs
|-- Briefings/                Weekly/daily CEO briefings
|-- Updates/                  Status reports
```

## Quick Start

### 1. Install Dependencies

```bash
# Python
pip install -r requirements.txt
python -m playwright install chromium

# Node.js
npm install
```

### 2. Run the E2E Test (no API keys needed)

```bash
python test_e2e_dry_run.py
```

This validates the entire pipeline: task creation, file movement, frontmatter parsing, approval workflow, action execution (dry run), validation/rejection, error handling, briefing generation, and dashboard updates.

### 3. Set Up Credentials

**Gmail (for GmailWatcher + MCP Email Server):**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project, enable Gmail API
3. Create OAuth 2.0 credentials (Desktop app)
4. Download `credentials.json` to the vault root
5. Run `gmail_watcher.py` once to complete OAuth flow (creates `token.json`)

See detailed instructions at the bottom of `gmail_watcher.py`.

**Stripe:**
```bash
# Use test key for development (no real charges)
export STRIPE_SECRET_KEY="sk_test_..."

# Windows PowerShell
$env:STRIPE_SECRET_KEY = "sk_test_..."
```

**WhatsApp:**
```bash
# First run - visible browser to scan QR code
python whatsapp_watcher.py . ./whatsapp_session

# Subsequent runs - headless
python whatsapp_watcher.py . ./whatsapp_session --headless
```

### 4. Start the System

Run each in a separate terminal:

```bash
# Terminal 1: MCP Email Server
node mcp_email_server.js

# Terminal 2: Task Processor (orchestrator)
python task_processor.py . --log-to-file

# Terminal 3: Gmail Watcher
python gmail_watcher.py . credentials.json

# Terminal 4: Stripe Watcher
python stripe_watcher.py .

# Terminal 5: WhatsApp Watcher (optional)
python whatsapp_watcher.py . ./whatsapp_session --headless
```

### 5. Generate Weekly Briefing

```bash
# Weekly summary
python weekly_briefing.py .

# Daily summary
python weekly_briefing.py . --days 1

# Preview without writing
python weekly_briefing.py . --dry-run
```

Schedule with Windows Task Scheduler or cron for automatic reports.

## Task Lifecycle

1. **Watcher** detects event (new email, payment, message)
2. Watcher creates a markdown file in `Needs_Action/` with YAML frontmatter
3. **Task Processor** detects the file, moves it to `In_Progress/`
4. **Claude Code** analyses the task, reads `Company_Handbook.md` for rules
5. Claude creates a plan in `Plans/`, logs decisions in `Logs/`
6. If approval needed (payment >$500, outbound email), Claude creates a file in `Pending_Approval/`
7. **CEO** reviews in Obsidian, drags file to `Approved/` or `Rejected/`
8. **Approval Handler** parses the frontmatter and auto-executes the action:
   - `send_email` -> POST to local MCP server -> Gmail API
   - `post_linkedin`, `post_twitter` -> placeholder (creates manual task)
9. On success -> `Done/` + success log. On failure -> alert in `Needs_Action/`

## Approved File Format

When Claude creates a file in `Pending_Approval/`, it uses this frontmatter:

```yaml
---
action_type: send_email
to: client@example.com
subject: "Re: Your inquiry"
body: Thank you for reaching out. Here is the information you requested.
threadId: 18abc123def    # optional, for Gmail thread replies
---
```

Moving this file to `Approved/` triggers automatic execution.

## Safety Rules

- Financial actions over $100 require CEO approval
- Payments over $500 are flagged as high priority
- Files are never deleted, only moved to `Done/` or `Rejected/`
- Every decision is logged in `Logs/`
- MCP Email Server only accepts requests from `127.0.0.1`
- Stripe API key is loaded from environment variables, never hardcoded
- WhatsApp and Gmail watchers are read-only (never send/modify)
- `credentials.json`, `token.json`, `.env`, and `whatsapp_session/` are in `.gitignore`

## Dry Run / Testing

```bash
# Task processor - detect tasks without invoking Claude
python task_processor.py . --dry-run

# Full E2E test - 49 assertions, no API keys needed
python test_e2e_dry_run.py

# Weekly briefing preview
python weekly_briefing.py . --dry-run
```

## Tech Stack

- **Python 3.12+** - Watchers, orchestrator, briefing generator
- **Node.js 24+** - MCP Email Server
- **Claude Code** - AI task analysis and planning
- **Obsidian** - Human interface for review and approval
- **watchdog** - Filesystem event monitoring
- **Playwright** - WhatsApp Web browser automation
- **googleapis** - Gmail API (Python + Node.js)
- **Stripe SDK** - Payment event monitoring

## License

MIT
