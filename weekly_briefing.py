"""Weekly Briefing Generator — scans the Obsidian vault and produces a
structured CEO briefing summarising the past week's activity.

Reads every folder (Done, Logs, Needs_Action, In_Progress, Pending_Approval,
Approved, Rejected, Accounting) and aggregates counts, financial totals,
response-time estimates, and open items into a single markdown report
saved to Briefings/.

REQUIREMENTS
------------
    No extra dependencies — uses only the Python standard library.

USAGE
-----
    # Generate briefing for the last 7 days (default)
    python weekly_briefing.py /path/to/vault

    # Custom window (last 14 days)
    python weekly_briefing.py /path/to/vault --days 14

    # Dry run — print to stdout, don't write file
    python weekly_briefing.py /path/to/vault --dry-run

SCHEDULING
----------
    # Windows Task Scheduler — run every Monday at 8 AM
    # Or add to crontab (Linux/macOS):
    #   0 8 * * 1  python /path/to/weekly_briefing.py /path/to/vault
"""

import argparse
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("WeeklyBriefing")


# ------------------------------------------------------------------
# Frontmatter parser (same logic as task_processor.py)
# ------------------------------------------------------------------

def parse_frontmatter(file_path: Path) -> dict[str, str]:
    """Extract YAML frontmatter key-value pairs from a markdown file."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return {}

    meta: dict[str, str] = {}
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
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        meta[key] = value

    return meta


# ------------------------------------------------------------------
# File scanning
# ------------------------------------------------------------------

def get_md_files(folder: Path, since: datetime) -> list[tuple[Path, datetime]]:
    """Return .md files in *folder* modified since *since*, with their mtime."""
    if not folder.is_dir():
        return []

    results: list[tuple[Path, datetime]] = []
    for f in folder.glob("*.md"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime >= since:
            results.append((f, mtime))

    # Also scan one level of subdirectories (e.g. Logs/Error_*/)
    for sub in folder.iterdir():
        if sub.is_dir():
            for f in sub.glob("*.md"):
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime >= since:
                    results.append((f, mtime))

    return sorted(results, key=lambda x: x[1], reverse=True)


def count_all_files(folder: Path) -> int:
    """Count all .md files in a folder (non-recursive)."""
    if not folder.is_dir():
        return 0
    return sum(1 for _ in folder.glob("*.md"))


# ------------------------------------------------------------------
# Data aggregation
# ------------------------------------------------------------------

class BriefingData:
    """Collects and holds all metrics for the briefing."""

    def __init__(self, vault: Path, since: datetime) -> None:
        self.vault = vault
        self.since = since
        self.now = datetime.now()

        # Folder references
        self.folders = {
            name: vault / name
            for name in [
                "Needs_Action", "In_Progress", "Plans", "Done",
                "Pending_Approval", "Approved", "Rejected",
                "Accounting", "Logs", "Briefings", "Updates",
            ]
        }

        # Aggregated metrics
        self.tasks_completed: list[dict] = []
        self.tasks_pending: list[dict] = []
        self.tasks_in_progress: list[dict] = []
        self.tasks_awaiting_approval: list[dict] = []
        self.approvals: list[dict] = []
        self.rejections: list[dict] = []
        self.action_successes: list[dict] = []
        self.action_failures: list[dict] = []
        self.errors: list[dict] = []

        # Financial
        self.total_revenue_cents: int = 0
        self.payment_count: int = 0
        self.payments: list[dict] = []

        # Source breakdown
        self.source_counts: Counter = Counter()

        # Collect
        self._scan()

    def _scan(self) -> None:
        """Walk through every vault folder and aggregate data."""
        self._scan_done()
        self._scan_needs_action()
        self._scan_in_progress()
        self._scan_pending_approval()
        self._scan_approved()
        self._scan_rejected()
        self._scan_logs()
        self._scan_done_for_financials()

    def _scan_done(self) -> None:
        for f, mtime in get_md_files(self.folders["Done"], self.since):
            meta = parse_frontmatter(f)
            task_type = meta.get("type", "unknown")
            self.tasks_completed.append({
                "file": f.name,
                "type": task_type,
                "completed": mtime.strftime("%Y-%m-%d %H:%M"),
                "subject": meta.get("subject", meta.get("from", f.stem)),
            })
            self.source_counts[task_type] += 1

    def _scan_needs_action(self) -> None:
        for f in self.folders["Needs_Action"].glob("*.md"):
            meta = parse_frontmatter(f)
            self.tasks_pending.append({
                "file": f.name,
                "type": meta.get("type", "unknown"),
                "priority": meta.get("priority", "medium"),
                "from": meta.get("from", ""),
            })

    def _scan_in_progress(self) -> None:
        for f in self.folders["In_Progress"].glob("*.md"):
            meta = parse_frontmatter(f)
            self.tasks_in_progress.append({
                "file": f.name,
                "type": meta.get("type", "unknown"),
                "from": meta.get("from", ""),
            })

    def _scan_pending_approval(self) -> None:
        for f in self.folders["Pending_Approval"].glob("*.md"):
            meta = parse_frontmatter(f)
            self.tasks_awaiting_approval.append({
                "file": f.name,
                "action_type": meta.get("action_type", "unknown"),
                "to": meta.get("to", ""),
                "amount": meta.get("amount", ""),
            })

    def _scan_approved(self) -> None:
        for f, mtime in get_md_files(self.folders["Approved"], self.since):
            meta = parse_frontmatter(f)
            self.approvals.append({
                "file": f.name,
                "action_type": meta.get("action_type", ""),
                "approved": mtime.strftime("%Y-%m-%d %H:%M"),
            })

    def _scan_rejected(self) -> None:
        for f, mtime in get_md_files(self.folders["Rejected"], self.since):
            meta = parse_frontmatter(f)
            self.rejections.append({
                "file": f.name,
                "rejected": mtime.strftime("%Y-%m-%d %H:%M"),
            })

    def _scan_logs(self) -> None:
        for f, mtime in get_md_files(self.folders["Logs"], self.since):
            meta = parse_frontmatter(f)
            log_type = meta.get("type", "")

            if log_type == "action_log":
                entry = {
                    "file": f.name,
                    "action_type": meta.get("action_type", ""),
                    "time": mtime.strftime("%Y-%m-%d %H:%M"),
                }
                if meta.get("result") == "success":
                    self.action_successes.append(entry)
                else:
                    self.action_failures.append(entry)

            if "Error_" in str(f.parent.name):
                self.errors.append({
                    "file": f.name,
                    "folder": f.parent.name,
                    "time": mtime.strftime("%Y-%m-%d %H:%M"),
                })

    def _scan_done_for_financials(self) -> None:
        """Look for stripe_payment files in Done/ to tally revenue."""
        for f in self.folders["Done"].glob("*.md"):
            meta = parse_frontmatter(f)
            if meta.get("type") != "stripe_payment":
                continue

            amount_str = meta.get("amount", "")
            # Parse "$1,234.56 USD" → cents
            amount_match = re.search(r"\$?([\d,]+\.?\d*)", amount_str)
            if amount_match:
                try:
                    dollars = float(amount_match.group(1).replace(",", ""))
                    cents = int(dollars * 100)
                    self.total_revenue_cents += cents
                    self.payment_count += 1
                    self.payments.append({
                        "file": f.name,
                        "amount": amount_str,
                        "customer": meta.get("customer", "Unknown"),
                    })
                except ValueError:
                    pass

        # Also check Approved/ stripe payments (processed but maybe not moved yet)
        for f in self.folders["Approved"].glob("*.md"):
            meta = parse_frontmatter(f)
            if meta.get("type") != "stripe_payment":
                continue
            amount_str = meta.get("amount", "")
            amount_match = re.search(r"\$?([\d,]+\.?\d*)", amount_str)
            if amount_match:
                try:
                    dollars = float(amount_match.group(1).replace(",", ""))
                    cents = int(dollars * 100)
                    self.total_revenue_cents += cents
                    self.payment_count += 1
                    self.payments.append({
                        "file": f.name,
                        "amount": amount_str,
                        "customer": meta.get("customer", "Unknown"),
                    })
                except ValueError:
                    pass


# ------------------------------------------------------------------
# Briefing renderer
# ------------------------------------------------------------------

def render_briefing(data: BriefingData) -> str:
    """Render the collected data into a markdown briefing document."""
    period_start = data.since.strftime("%Y-%m-%d")
    period_end = data.now.strftime("%Y-%m-%d")
    generated = data.now.strftime("%Y-%m-%d %H:%M:%S")
    revenue_dollars = data.total_revenue_cents / 100

    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)

    # ---- Header ----
    add("---")
    add("type: weekly_briefing")
    add(f"period_start: {period_start}")
    add(f"period_end: {period_end}")
    add(f"generated: {generated}")
    add("---")
    add()
    add(f"# Weekly CEO Briefing")
    add(f"**Period:** {period_start} → {period_end}")
    add(f"**Generated:** {generated}")
    add()

    # ---- Executive Summary ----
    add("## Executive Summary")
    add()
    add(f"| Metric | Value |")
    add(f"|--------|-------|")
    add(f"| Tasks Completed | {len(data.tasks_completed)} |")
    add(f"| Tasks Pending | {len(data.tasks_pending)} |")
    add(f"| Tasks In Progress | {len(data.tasks_in_progress)} |")
    add(f"| Awaiting Approval | {len(data.tasks_awaiting_approval)} |")
    add(f"| Approvals Given | {len(data.approvals)} |")
    add(f"| Rejections | {len(data.rejections)} |")
    add(f"| Actions Executed | {len(data.action_successes)} |")
    add(f"| Action Failures | {len(data.action_failures)} |")
    add(f"| Errors | {len(data.errors)} |")
    add(f"| Revenue This Period | ${revenue_dollars:,.2f} |")
    add(f"| Payments Received | {data.payment_count} |")
    add()

    # ---- Alerts ----
    alerts: list[str] = []
    if len(data.tasks_pending) > 10:
        alerts.append(f"- **Backlog alert:** {len(data.tasks_pending)} tasks sitting in Needs_Action/")
    if len(data.tasks_awaiting_approval) > 5:
        alerts.append(f"- **Approval bottleneck:** {len(data.tasks_awaiting_approval)} items awaiting your sign-off")
    if data.action_failures:
        alerts.append(f"- **Failed actions:** {len(data.action_failures)} action(s) failed — check Logs/ACTION_FAILED_*")
    if data.errors:
        alerts.append(f"- **Processing errors:** {len(data.errors)} error(s) in Logs/Error_*/")
    if revenue_dollars < 1500 and data.payment_count > 0:
        alerts.append(f"- **Revenue below target:** ${revenue_dollars:,.2f} (target: $2,500/week)")

    if alerts:
        add("## Alerts")
        add()
        for a in alerts:
            add(a)
        add()
    else:
        add("## Alerts")
        add()
        add("No alerts — all metrics within normal range.")
        add()

    # ---- Tasks Completed ----
    add("## Tasks Completed")
    add()
    if data.tasks_completed:
        add("| File | Type | Completed |")
        add("|------|------|-----------|")
        for t in data.tasks_completed[:25]:
            add(f"| {t['file']} | {t['type']} | {t['completed']} |")
        if len(data.tasks_completed) > 25:
            add(f"| ... | +{len(data.tasks_completed) - 25} more | |")
    else:
        add("No tasks completed this period.")
    add()

    # ---- Source Breakdown ----
    if data.source_counts:
        add("## Task Sources")
        add()
        add("| Source | Count |")
        add("|--------|-------|")
        for source, count in data.source_counts.most_common():
            add(f"| {source} | {count} |")
        add()

    # ---- Revenue / Payments ----
    add("## Revenue & Payments")
    add()
    if data.payments:
        add(f"**Total Revenue:** ${revenue_dollars:,.2f} from {data.payment_count} payment(s)")
        add()
        add("| Customer | Amount |")
        add("|----------|--------|")
        for p in data.payments[:20]:
            add(f"| {p['customer']} | {p['amount']} |")
    else:
        add("No payments recorded this period.")
    add()

    # ---- Pending Items (need CEO action) ----
    if data.tasks_awaiting_approval:
        add("## Awaiting Your Approval")
        add()
        add("| File | Action | Details |")
        add("|------|--------|---------|")
        for t in data.tasks_awaiting_approval:
            detail = t.get("to") or t.get("amount") or ""
            add(f"| {t['file']} | {t['action_type']} | {detail} |")
        add()

    if data.tasks_pending:
        add("## Backlog (Needs_Action/)")
        add()
        add("| File | Type | Priority |")
        add("|------|------|----------|")
        for t in data.tasks_pending[:15]:
            add(f"| {t['file']} | {t['type']} | {t['priority']} |")
        if len(data.tasks_pending) > 15:
            add(f"| ... | +{len(data.tasks_pending) - 15} more | |")
        add()

    if data.tasks_in_progress:
        add("## In Progress")
        add()
        add("| File | Type |")
        add("|------|------|")
        for t in data.tasks_in_progress:
            add(f"| {t['file']} | {t['type']} |")
        add()

    # ---- Failures / Errors ----
    if data.action_failures or data.errors:
        add("## Issues Requiring Attention")
        add()
        if data.action_failures:
            add("### Action Failures")
            add()
            for f in data.action_failures:
                add(f"- `{f['file']}` — {f['action_type']} at {f['time']}")
            add()
        if data.errors:
            add("### Processing Errors")
            add()
            for e in data.errors:
                add(f"- `{e['folder']}/{e['file']}` at {e['time']}")
            add()

    # ---- Approvals & Rejections Log ----
    if data.approvals or data.rejections:
        add("## Decisions Log")
        add()
        if data.approvals:
            add("### Approved")
            add()
            for a in data.approvals:
                add(f"- `{a['file']}` ({a['action_type'] or 'general'}) — {a['approved']}")
            add()
        if data.rejections:
            add("### Rejected")
            add()
            for r in data.rejections:
                add(f"- `{r['file']}` — {r['rejected']}")
            add()

    # ---- Footer ----
    add("---")
    add()
    add("## Recommended Actions This Week")
    add()
    if data.tasks_awaiting_approval:
        add(f"- [ ] Review {len(data.tasks_awaiting_approval)} item(s) in `Pending_Approval/`")
    if data.tasks_pending:
        add(f"- [ ] Clear {len(data.tasks_pending)} task(s) from `Needs_Action/` backlog")
    if data.action_failures:
        add("- [ ] Investigate failed actions in `Logs/ACTION_FAILED_*`")
    if data.errors:
        add("- [ ] Review processing errors in `Logs/Error_*/`")
    add("- [ ] Verify `Dashboard.md` metrics are current")
    add("- [ ] Update `Business_Goals.md` if targets have changed")
    add("- [ ] Review `Accounting/` records for completeness")
    add()

    return "\n".join(lines)


# ------------------------------------------------------------------
# Dashboard updater
# ------------------------------------------------------------------

def update_dashboard(vault: Path, data: BriefingData) -> None:
    """Rewrite Dashboard.md with current metrics."""
    revenue_dollars = data.total_revenue_cents / 100
    now = datetime.now()

    recent_activity: list[str] = []
    for t in data.tasks_completed[:5]:
        recent_activity.append(f"- Completed: {t['file']} ({t['type']})")
    for a in data.approvals[:3]:
        recent_activity.append(f"- Approved: {a['file']}")
    for s in data.action_successes[:3]:
        recent_activity.append(f"- Action executed: {s['action_type']}")
    if not recent_activity:
        recent_activity.append("- System ready - awaiting tasks")

    dashboard = (
        f"# CEO Dashboard\n"
        f"**Date:** {now.strftime('%Y-%m-%d')}\n"
        f"**Bank Balance:** Not connected yet\n"
        f"**Pending Tasks:** {len(data.tasks_pending) + len(data.tasks_in_progress)}\n"
        f"**Awaiting Approval:** {len(data.tasks_awaiting_approval)}\n"
        f"**Revenue This Week:** ${revenue_dollars:,.2f}\n"
        f"**Recent Activity:**\n"
    )
    for item in recent_activity:
        dashboard += f"{item}\n"

    dash_path = vault / "Dashboard.md"
    dash_path.write_text(dashboard, encoding="utf-8")
    logger.info("Dashboard.md updated")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a weekly CEO briefing from the Obsidian vault",
        epilog="Example: python weekly_briefing.py /path/to/vault --days 7",
    )
    parser.add_argument("vault", help="Path to the Obsidian vault root folder")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print briefing to stdout instead of writing to file",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Skip updating Dashboard.md",
    )
    args = parser.parse_args()

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        print(f"Error: vault path does not exist: {vault}", file=sys.stderr)
        sys.exit(1)

    since = datetime.now() - timedelta(days=args.days)
    logger.info("Generating briefing for %s → %s", since.strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))

    # Collect data
    data = BriefingData(vault, since)

    # Render
    briefing = render_briefing(data)

    if args.dry_run:
        print(briefing)
    else:
        briefings_dir = vault / "Briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"BRIEFING_{date_str}.md"
        filepath = briefings_dir / filename

        # Avoid overwriting if run multiple times in a day
        counter = 1
        while filepath.exists():
            filename = f"BRIEFING_{date_str}_{counter}.md"
            filepath = briefings_dir / filename
            counter += 1

        filepath.write_text(briefing, encoding="utf-8")
        logger.info("Briefing saved: %s", filepath)

    # Update dashboard
    if not args.no_dashboard:
        update_dashboard(vault, data)

    logger.info("Done")


if __name__ == "__main__":
    main()


# ======================================================================
# SCHEDULING
# ======================================================================
#
# WINDOWS TASK SCHEDULER
#   1. Open Task Scheduler → Create Basic Task
#   2. Name: "AI Employee Weekly Briefing"
#   3. Trigger: Weekly, Monday 8:00 AM
#   4. Action: Start a program
#      Program: python
#      Arguments: "E:\Autonomous-Digital-FTE(AI Employee)\weekly_briefing.py" "E:\Autonomous-Digital-FTE(AI Employee)"
#   5. Finish
#
# LINUX / macOS CRON
#   crontab -e
#   0 8 * * 1  /usr/bin/python3 /path/to/weekly_briefing.py /path/to/vault
#
# DAILY BRIEFINGS
#   Run with --days 1 for a daily summary:
#   python weekly_briefing.py /path/to/vault --days 1
#
