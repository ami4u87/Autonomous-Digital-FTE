"""TaskProcessor (Orchestrator) — watches Needs_Action/ for new task files
and dispatches them to Claude Code for autonomous processing.

Monitors Approved/ for CEO sign-offs and automatically executes the
approved action (send email, etc.) via the local MCP servers.

REQUIREMENTS
------------
    pip install watchdog

USAGE
-----
    python task_processor.py /path/to/vault
    python task_processor.py /path/to/vault --log-to-file
    python task_processor.py /path/to/vault --dry-run
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

# MCP server base URL — localhost only, never remote
MCP_EMAIL_URL = "http://127.0.0.1:3000/send-email"

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logger = logging.getLogger("TaskProcessor")


def setup_logging(vault_path: Path, log_to_file: bool = False) -> None:
    """Configure console logging and optional file logging into vault/Logs/."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_to_file:
        logs_dir = vault_path / "Logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        date_stamp = datetime.now().strftime("%Y-%m-%d")
        log_file = logs_dir / f"processor_{date_stamp}.log"
        handlers.append(logging.FileHandler(str(log_file), encoding="utf-8"))

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


# ------------------------------------------------------------------
# Folder helper
# ------------------------------------------------------------------

def ensure_folders(vault: Path) -> dict[str, Path]:
    """Create all required vault folders and return a name→Path mapping."""
    names = [
        "Needs_Action",
        "In_Progress",
        "Plans",
        "Done",
        "Pending_Approval",
        "Approved",
        "Rejected",
        "Accounting",
        "Logs",
        "Briefings",
        "Updates",
    ]
    folders: dict[str, Path] = {}
    for name in names:
        p = vault / name
        p.mkdir(parents=True, exist_ok=True)
        folders[name] = p
    return folders


# ------------------------------------------------------------------
# Claude Code invocation
# ------------------------------------------------------------------

CLAUDE_PROMPT_TEMPLATE = """\
New task detected: {filename}

Analyze the task file at In_Progress/{filename}.
Determine the task type from its frontmatter (email, whatsapp, stripe_payment, etc.).

Follow every rule in Company_Handbook.md. In particular:
- NEVER take financial actions over $100 without approval.
- For payments or invoices, create a file in Pending_Approval/ with full details.
- Flag urgent keywords: urgent, asap, invoice, payment, help.

Steps to execute:
1. Read the task file and understand its content and priority.
2. Read Company_Handbook.md and Business_Goals.md for context.
3. Create a detailed plan file at Plans/PLAN_{stem}.md with checkboxed steps.
4. If the task involves a payment over $500 or any sensitive financial action,
   create a file in Pending_Approval/ explaining what needs approval and why.
5. Execute safe, non-financial actions from the plan (draft replies, update logs).
6. Log every decision to Logs/DECISION_{date}_{stem}.md.
7. Update Dashboard.md: increment Pending Tasks, add to Recent Activity.
8. If all steps are complete and no approval is pending, move the task to Done/.
   Otherwise leave it in In_Progress/ and note what is blocked.
"""


def invoke_claude(vault: Path, task_file: Path, dry_run: bool = False) -> bool:
    """Call Claude Code as a subprocess to process a single task file.

    Returns True on success, False on failure.
    """
    filename = task_file.name
    stem = task_file.stem
    prompt = CLAUDE_PROMPT_TEMPLATE.format(
        filename=filename,
        stem=stem,
        date=datetime.now().strftime("%Y%m%d"),
    )

    # On Windows, npm installs CLI tools as .cmd scripts
    claude_bin = "claude.cmd" if sys.platform == "win32" else "claude"

    command = [
        claude_bin,
        "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Glob,Grep",
    ]

    logger.info("Invoking Claude Code for: %s", filename)
    logger.debug("Command: %s", " ".join(command))

    if dry_run:
        logger.info("[DRY RUN] Would execute: %s", " ".join(command))
        return True

    try:
        # Unset CLAUDECODE env var to allow subprocess invocation
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(vault),
            env=env,
        )

        if result.stdout:
            logger.info("Claude output:\n%s", result.stdout[-2000:])

        if result.stderr:
            logger.warning("Claude stderr:\n%s", result.stderr[-1000:])

        if result.returncode == 0:
            logger.info("Claude completed successfully for: %s", filename)
            return True
        else:
            logger.error(
                "Claude exited with code %d for: %s",
                result.returncode,
                filename,
            )
            return False

    except subprocess.TimeoutExpired:
        logger.error("Claude timed out (300s) processing: %s", filename)
        return False
    except FileNotFoundError:
        logger.critical(
            "Claude CLI not found — make sure 'claude' is installed and on PATH. "
            "Install: npm install -g @anthropic-ai/claude-code"
        )
        return False
    except Exception:
        logger.exception("Unexpected error invoking Claude for: %s", filename)
        return False


# ------------------------------------------------------------------
# File movement helpers
# ------------------------------------------------------------------

def move_file(src: Path, dest_dir: Path, prefix: str = "") -> Path:
    """Move a file into dest_dir, optionally prepending a prefix to the name."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    new_name = f"{prefix}{src.name}" if prefix else src.name
    dest = dest_dir / new_name

    # Avoid overwriting — append a counter if the destination exists
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
        counter += 1

    shutil.move(str(src), str(dest))
    logger.info("Moved: %s → %s", src.name, dest)
    return dest


def move_to_error(task_file: Path, logs_dir: Path) -> Path:
    """Move a failed task file into a timestamped error folder inside Logs/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    error_dir = logs_dir / f"Error_{ts}"
    error_dir.mkdir(parents=True, exist_ok=True)
    dest = error_dir / task_file.name
    shutil.move(str(task_file), str(dest))
    logger.info("Moved failed task to: %s", dest)
    return dest


# ------------------------------------------------------------------
# Frontmatter parser
# ------------------------------------------------------------------

def parse_frontmatter(file_path: Path) -> dict[str, str]:
    """Read a markdown file and extract YAML frontmatter key-value pairs.

    Returns a flat dict of string→string.  Handles quoted and unquoted values.
    Non-frontmatter content is stored under the key '__body__'.
    """
    text = file_path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}

    # Split on the --- delimiters
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        meta["__body__"] = text
        return meta

    yaml_block, body = match.group(1), match.group(2)
    meta["__body__"] = body.strip()

    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        meta[key] = value

    return meta


# ------------------------------------------------------------------
# Action executors
# ------------------------------------------------------------------

def execute_action(
    approved_file: Path,
    meta: dict[str, str],
    folders: dict[str, Path],
    dry_run: bool = False,
) -> bool:
    """Dispatch an approved action based on its action_type.

    Returns True on success, False on failure.
    """
    action_type = meta.get("action_type", "").strip().lower()

    if not action_type:
        logger.warning(
            "No action_type in frontmatter of %s — logging only, no action taken",
            approved_file.name,
        )
        return True  # not a failure, just nothing to execute

    logger.info("Executing action_type='%s' from %s", action_type, approved_file.name)

    dispatch: dict[str, callable] = {
        "send_email": _action_send_email,
        "post_linkedin": _action_placeholder,
        "post_twitter": _action_placeholder,
        "create_invoice": _action_placeholder,
        "schedule_meeting": _action_placeholder,
    }

    handler = dispatch.get(action_type)
    if handler is None:
        logger.error("Unknown action_type '%s' in %s", action_type, approved_file.name)
        return False

    return handler(approved_file, meta, folders, dry_run)


def _action_send_email(
    approved_file: Path,
    meta: dict[str, str],
    folders: dict[str, Path],
    dry_run: bool,
) -> bool:
    """Send an email via the local MCP Email Server (POST /send-email)."""
    to = meta.get("to", "").strip()
    subject = meta.get("subject", "").strip()
    body = meta.get("body", "").strip() or meta.get("__body__", "").strip()

    # ---- Validate required fields ----
    missing = []
    if not to:
        missing.append("to")
    if not subject:
        missing.append("subject")
    if not body:
        missing.append("body")

    if missing:
        logger.error(
            "Cannot send email — missing fields: %s (file: %s)",
            ", ".join(missing),
            approved_file.name,
        )
        return False

    if "@" not in to:
        logger.error("Invalid email address '%s' in %s", to, approved_file.name)
        return False

    # ---- Safety: only call localhost MCP ----
    if "127.0.0.1" not in MCP_EMAIL_URL and "localhost" not in MCP_EMAIL_URL:
        logger.critical("MCP_EMAIL_URL is not localhost — refusing to send")
        return False

    payload = {"to": to, "subject": subject, "body": body}
    thread_id = meta.get("threadId", "").strip()
    if thread_id:
        payload["threadId"] = thread_id

    logger.info(
        "Sending email → to='%s' subject='%s' threadId=%s",
        to,
        subject,
        thread_id or "none",
    )

    if dry_run:
        logger.info("[DRY RUN] Would POST to %s with payload: %s", MCP_EMAIL_URL, json.dumps(payload))
        return True

    # ---- Call MCP server via urllib (no extra dependency) ----
    try:
        req = urllib.request.Request(
            MCP_EMAIL_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = json.loads(resp.read().decode("utf-8"))

        if resp_body.get("success"):
            message_id = resp_body.get("messageId", "unknown")
            logger.info("Email sent successfully — messageId=%s", message_id)
            _log_action_result(
                folders, approved_file, "SUCCESS", "send_email",
                f"Email sent to {to} (messageId={message_id})",
            )
            return True
        else:
            error_msg = resp_body.get("error", "Unknown error")
            logger.error("MCP server returned failure: %s", error_msg)
            _log_action_result(
                folders, approved_file, "FAILED", "send_email",
                f"MCP error: {error_msg}",
            )
            return False

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        logger.error("MCP HTTP error %d: %s", e.code, error_body or e.reason)
        _log_action_result(
            folders, approved_file, "FAILED", "send_email",
            f"HTTP {e.code}: {error_body or e.reason}",
        )
        return False

    except urllib.error.URLError as e:
        logger.error(
            "Cannot reach MCP Email Server at %s — is it running?  Error: %s",
            MCP_EMAIL_URL,
            e.reason,
        )
        _log_action_result(
            folders, approved_file, "FAILED", "send_email",
            f"Connection failed: {e.reason}",
        )
        return False

    except Exception:
        logger.exception("Unexpected error calling MCP Email Server")
        _log_action_result(
            folders, approved_file, "FAILED", "send_email",
            "Unexpected error — see processor logs",
        )
        return False


def _action_placeholder(
    approved_file: Path,
    meta: dict[str, str],
    folders: dict[str, Path],
    dry_run: bool,
) -> bool:
    """Placeholder for future action types (LinkedIn, Twitter, invoicing, etc.)."""
    action_type = meta.get("action_type", "unknown")
    logger.warning(
        "Action '%s' is not yet implemented — file: %s.  "
        "Creating Needs_Action/ notification for manual handling.",
        action_type,
        approved_file.name,
    )

    ts = datetime.now()
    notification = (
        f"---\n"
        f"type: action_not_implemented\n"
        f"original_file: \"{approved_file.name}\"\n"
        f"action_type: {action_type}\n"
        f"created: {ts.isoformat()}\n"
        f"priority: medium\n"
        f"status: pending\n"
        f"---\n"
        f"\n"
        f"## Action Not Yet Implemented\n"
        f"\n"
        f"The action **{action_type}** was approved but has no automated handler yet.\n"
        f"Please execute manually.\n"
        f"\n"
        f"**Original file:** {approved_file.name}\n"
        f"\n"
        f"## Manual Steps\n"
        f"\n"
        f"- [ ] Perform the '{action_type}' action manually\n"
        f"- [ ] Move original to `Done/` when complete\n"
        f"- [ ] Log outcome in `Logs/`\n"
    )

    notif_file = folders["Needs_Action"] / f"MANUAL_{ts.strftime('%Y%m%d_%H%M%S')}_{approved_file.stem}.md"
    notif_file.write_text(notification, encoding="utf-8")
    return True  # not a failure — it's a graceful fallback


def _log_action_result(
    folders: dict[str, Path],
    source_file: Path,
    result: str,
    action_type: str,
    details: str,
) -> Path:
    """Write a structured log file for an action execution attempt."""
    ts = datetime.now()
    tag = "ACTION_SUCCESS" if result == "SUCCESS" else "ACTION_FAILED"

    content = (
        f"---\n"
        f"type: action_log\n"
        f"result: {result.lower()}\n"
        f"action_type: {action_type}\n"
        f"source_file: \"{source_file.name}\"\n"
        f"executed_at: {ts.isoformat()}\n"
        f"---\n"
        f"\n"
        f"## {tag.replace('_', ' ').title()}\n"
        f"\n"
        f"**Action:** {action_type}\n"
        f"**Source:** {source_file.name}\n"
        f"**Time:** {ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Details:** {details}\n"
    )

    log_file = folders["Logs"] / f"{tag}_{ts.strftime('%Y%m%d_%H%M%S')}_{source_file.stem}.md"
    log_file.write_text(content, encoding="utf-8")
    logger.info("Action log written: %s", log_file.name)
    return log_file


# ------------------------------------------------------------------
# Event handlers
# ------------------------------------------------------------------

class NeedsActionHandler(FileSystemEventHandler):
    """Watch Needs_Action/ for new .md files and process them."""

    def __init__(self, vault: Path, folders: dict[str, Path], dry_run: bool = False):
        super().__init__()
        self.vault = vault
        self.folders = folders
        self.dry_run = dry_run
        self.processing: set[str] = set()  # guard against duplicate events

    def on_created(self, event):
        if event.is_directory:
            return
        src = Path(event.src_path)
        if src.suffix != ".md":
            return
        if src.name in self.processing:
            return

        self.processing.add(src.name)
        try:
            self._handle_new_task(src)
        finally:
            self.processing.discard(src.name)

    def _handle_new_task(self, src: Path) -> None:
        """Move to In_Progress, invoke Claude, then route based on result."""
        logger.info("=" * 60)
        logger.info("New task detected: %s", src.name)
        logger.info("=" * 60)

        # Brief pause — let the watcher finish writing the file
        time.sleep(1)

        if not src.exists():
            logger.warning("File vanished before processing: %s", src.name)
            return

        # 1. Move to In_Progress
        in_progress_file = move_file(src, self.folders["In_Progress"])

        # 2. Invoke Claude Code
        success = invoke_claude(self.vault, in_progress_file, dry_run=self.dry_run)

        if success:
            # Check if Claude already moved it (to Done/ or Pending_Approval/)
            if in_progress_file.exists():
                logger.info(
                    "Task remains in In_Progress/ for review: %s",
                    in_progress_file.name,
                )
        else:
            # Move to Logs/Error_*/
            if in_progress_file.exists():
                move_to_error(in_progress_file, self.folders["Logs"])


class ApprovalHandler(FileSystemEventHandler):
    """Watch Approved/ for files that the CEO has moved from Pending_Approval/.

    When a file lands in Approved/:
      1. Parse its frontmatter for action_type and parameters.
      2. Execute the action (e.g. send_email via MCP server).
      3. On success → move to Done/, write ACTION_SUCCESS log.
      4. On failure → write ACTION_FAILED log, create Needs_Action notification.
    """

    def __init__(self, vault: Path, folders: dict[str, Path], dry_run: bool = False):
        super().__init__()
        self.vault = vault
        self.folders = folders
        self.dry_run = dry_run
        self.processing: set[str] = set()

    def on_created(self, event):
        if event.is_directory:
            return
        src = Path(event.src_path)
        if src.suffix != ".md":
            return
        if src.name in self.processing:
            return

        self.processing.add(src.name)
        try:
            self._handle_approval(src)
        finally:
            self.processing.discard(src.name)

    def _handle_approval(self, approved_file: Path) -> None:
        """Full approval pipeline: log → parse → execute → route."""
        logger.info("=" * 60)
        logger.info("APPROVAL RECEIVED: %s", approved_file.name)
        logger.info("=" * 60)

        # Brief pause — let the file system finish writing
        time.sleep(1)

        if not approved_file.exists():
            logger.warning("Approved file vanished: %s", approved_file.name)
            return

        # 1. Log the approval event
        self._log_approval(approved_file)

        # 2. Parse frontmatter
        try:
            meta = parse_frontmatter(approved_file)
        except Exception:
            logger.exception("Failed to parse frontmatter: %s", approved_file.name)
            meta = {}

        action_type = meta.get("action_type", "").strip().lower()
        logger.info(
            "Parsed action_type='%s' with fields: %s",
            action_type or "(none)",
            ", ".join(k for k in meta if k != "__body__"),
        )

        # 3. Execute the action
        if action_type:
            success = execute_action(
                approved_file, meta, self.folders, dry_run=self.dry_run
            )
        else:
            logger.info(
                "No action_type — approval logged, no automated action to execute"
            )
            success = True

        # 4. Route the file based on outcome
        if success and approved_file.exists():
            move_file(approved_file, self.folders["Done"])
            logger.info("Approved task completed → moved to Done/")
        elif not success and approved_file.exists():
            logger.warning("Action failed — file stays in Approved/ for retry")
            # Create a notification so the failure is visible
            self._create_failure_notification(approved_file, meta)

    def _log_approval(self, approved_file: Path) -> None:
        """Write an approval log entry."""
        ts = datetime.now()
        log_entry = (
            f"---\n"
            f"type: approval_log\n"
            f"approved_file: \"{approved_file.name}\"\n"
            f"approved_at: {ts.isoformat()}\n"
            f"---\n"
            f"\n"
            f"## Approval Received\n"
            f"\n"
            f"**File:** {approved_file.name}\n"
            f"**Approved at:** {ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        log_file = self.folders["Logs"] / f"APPROVED_{ts.strftime('%Y%m%d_%H%M%S')}_{approved_file.stem}.md"
        log_file.write_text(log_entry, encoding="utf-8")
        logger.info("Approval logged: %s", log_file.name)

    def _create_failure_notification(self, approved_file: Path, meta: dict) -> None:
        """Create a Needs_Action file alerting that an approved action failed."""
        ts = datetime.now()
        action_type = meta.get("action_type", "unknown")

        notification = (
            f"---\n"
            f"type: action_failure_alert\n"
            f"original_file: \"{approved_file.name}\"\n"
            f"action_type: {action_type}\n"
            f"failed_at: {ts.isoformat()}\n"
            f"priority: high\n"
            f"status: pending\n"
            f"---\n"
            f"\n"
            f"## Action Execution Failed\n"
            f"\n"
            f"The approved action **{action_type}** could not be executed.\n"
            f"\n"
            f"**Original file:** `Approved/{approved_file.name}`\n"
            f"**Failed at:** {ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"\n"
            f"## Suggested Actions\n"
            f"\n"
            f"- [ ] Check `Logs/ACTION_FAILED_*.md` for error details\n"
            f"- [ ] Verify the MCP server is running (`GET http://127.0.0.1:3000/health`)\n"
            f"- [ ] Fix the issue and move the file back to `Approved/` to retry\n"
            f"- [ ] Or execute the action manually and move to `Done/`\n"
        )

        notif_file = self.folders["Needs_Action"] / f"ALERT_FAILED_{ts.strftime('%Y%m%d_%H%M%S')}_{approved_file.stem}.md"
        notif_file.write_text(notification, encoding="utf-8")
        logger.info("Failure notification created: %s", notif_file.name)


class RejectionHandler(FileSystemEventHandler):
    """Watch Rejected/ for files the CEO has declined."""

    def __init__(self, vault: Path, folders: dict[str, Path]):
        super().__init__()
        self.vault = vault
        self.folders = folders

    def on_created(self, event):
        if event.is_directory:
            return
        src = Path(event.src_path)
        if src.suffix != ".md":
            return

        logger.info("REJECTION NOTED: %s", src.name)

        ts = datetime.now()
        log_entry = (
            f"---\n"
            f"type: rejection_log\n"
            f"rejected_file: \"{src.name}\"\n"
            f"rejected_at: {ts.isoformat()}\n"
            f"---\n"
            f"\n"
            f"## Rejection Recorded\n"
            f"\n"
            f"**File:** {src.name}\n"
            f"**Rejected at:** {ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**Reason:** (CEO to fill in)\n"
        )

        log_file = self.folders["Logs"] / f"REJECTED_{ts.strftime('%Y%m%d_%H%M%S')}_{src.stem}.md"
        log_file.write_text(log_entry, encoding="utf-8")
        logger.info("Rejection logged: %s", log_file.name)


# ------------------------------------------------------------------
# Startup: process files already sitting in Needs_Action/
# ------------------------------------------------------------------

def process_backlog(vault: Path, folders: dict[str, Path], dry_run: bool) -> None:
    """Pick up any .md files already in Needs_Action/ from before startup."""
    needs_action = folders["Needs_Action"]
    backlog = sorted(needs_action.glob("*.md"))

    if not backlog:
        logger.info("No backlog in Needs_Action/")
        return

    logger.info("Processing %d backlog file(s) from Needs_Action/", len(backlog))
    handler = NeedsActionHandler(vault, folders, dry_run)

    for task_file in backlog:
        handler._handle_new_task(task_file)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Task Processor — watches Needs_Action/ and dispatches to Claude Code",
        epilog="Example: python task_processor.py /path/to/vault --log-to-file",
    )
    parser.add_argument(
        "vault",
        help="Path to the Obsidian vault root folder",
    )
    parser.add_argument(
        "--log-to-file",
        action="store_true",
        help="Also write logs to vault/Logs/processor_DATE.log",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect tasks but do not invoke Claude (for testing)",
    )
    parser.add_argument(
        "--skip-backlog",
        action="store_true",
        help="Ignore existing files in Needs_Action/ on startup",
    )
    args = parser.parse_args()

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        print(f"Error: vault path does not exist: {vault}", file=sys.stderr)
        sys.exit(1)

    setup_logging(vault, log_to_file=args.log_to_file)
    folders = ensure_folders(vault)

    logger.info("=" * 60)
    logger.info("Task Processor starting")
    logger.info("Vault: %s", vault)
    logger.info("Dry run: %s", args.dry_run)
    logger.info("=" * 60)

    # ---- Process any existing backlog ----
    if not args.skip_backlog:
        process_backlog(vault, folders, args.dry_run)

    # ---- Set up filesystem watchers ----
    observer = Observer()

    needs_handler = NeedsActionHandler(vault, folders, dry_run=args.dry_run)
    observer.schedule(needs_handler, str(folders["Needs_Action"]), recursive=False)
    logger.info("Watching: %s", folders["Needs_Action"])

    approval_handler = ApprovalHandler(vault, folders, dry_run=args.dry_run)
    observer.schedule(approval_handler, str(folders["Approved"]), recursive=False)
    logger.info("Watching: %s", folders["Approved"])

    rejection_handler = RejectionHandler(vault, folders)
    observer.schedule(rejection_handler, str(folders["Rejected"]), recursive=False)
    logger.info("Watching: %s", folders["Rejected"])

    observer.start()
    logger.info("All watchers active — waiting for tasks (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested — stopping watchers")
        observer.stop()

    observer.join()
    logger.info("Task Processor stopped gracefully")


if __name__ == "__main__":
    main()


# ======================================================================
# HOW IT WORKS — Architecture overview
# ======================================================================
#
#  Watchers (gmail, whatsapp, stripe)
#       │
#       ▼
#  Needs_Action/         ← new .md task files land here
#       │
#       ▼
#  TaskProcessor detects file (watchdog)
#       │
#       ├─ 1. Move file → In_Progress/
#       ├─ 2. Invoke Claude Code (subprocess)
#       │       Claude reads:
#       │         - The task file
#       │         - Company_Handbook.md (rules)
#       │         - Business_Goals.md (context)
#       │       Claude creates:
#       │         - Plans/PLAN_*.md (action plan)
#       │         - Pending_Approval/*.md (if needed)
#       │         - Logs/DECISION_*.md (audit trail)
#       │         - Updates Dashboard.md
#       │       Claude moves:
#       │         - Task → Done/ (if fully resolved)
#       ├─ 3. On failure → Logs/Error_*/
#       │
#       ▼
#  CEO reviews Pending_Approval/
#       │
#       ├─ Moves to Approved/  → ApprovalHandler:
#       │     1. Logs approval event
#       │     2. Parses frontmatter for action_type + params
#       │     3. Dispatches to action executor:
#       │         send_email → POST http://127.0.0.1:3000/send-email
#       │         post_linkedin → (placeholder — creates manual task)
#       │         post_twitter  → (placeholder — creates manual task)
#       │     4. On success → move to Done/ + ACTION_SUCCESS log
#       │     5. On failure → ACTION_FAILED log + ALERT in Needs_Action/
#       │
#       └─ Moves to Rejected/  → RejectionHandler logs it
#
# APPROVED FILE FORMAT (frontmatter example):
#   ---
#   action_type: send_email
#   to: client@example.com
#   subject: Re: Your inquiry
#   body: Thank you for reaching out...
#   threadId: 18abc123def         (optional, for Gmail thread replies)
#   ---
#
# ======================================================================
# USAGE EXAMPLES
# ======================================================================
#
# Basic usage:
#   python task_processor.py "E:\My Vault"
#
# With file logging:
#   python task_processor.py "E:\My Vault" --log-to-file
#
# Test without invoking Claude:
#   python task_processor.py "E:\My Vault" --dry-run
#
# Skip existing backlog on startup:
#   python task_processor.py "E:\My Vault" --skip-backlog
#
# Combined:
#   python task_processor.py "E:\My Vault" --log-to-file --dry-run
#
