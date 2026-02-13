"""End-to-end dry-run test for the AI Employee pipeline.

Tests every component directly (no watchdog, no sleep, no subprocesses).
Simulates the full lifecycle without any API keys or external services.

Run:  python test_e2e_dry_run.py
"""

import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(__file__).parent.resolve()
sys.path.insert(0, str(VAULT))

from task_processor import (
    ensure_folders,
    move_file,
    move_to_error,
    parse_frontmatter,
    execute_action,
    _action_send_email,
    _action_placeholder,
    _log_action_result,
)

PASS = 0
FAIL = 0


def safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    extra = f" - {detail}" if detail else ""
    safe_print(f"  [{tag}] {label}{extra}")


def section(title: str) -> None:
    safe_print(f"\n{'='*60}")
    safe_print(f"  {title}")
    safe_print(f"{'='*60}\n")


# ==================================================================
section("SETUP: Clean vault folders")
# ==================================================================

folders = ensure_folders(VAULT)

# Wipe all test-relevant folders
for name in ["Needs_Action", "In_Progress", "Done", "Pending_Approval", "Approved", "Rejected"]:
    for f in folders[name].glob("*.md"):
        f.unlink()

# Clean test artifacts from Plans/ and Logs/
for f in folders["Plans"].glob("PLAN_TEST*"):
    f.unlink()
for f in folders["Logs"].glob("*"):
    if f.is_file():
        try:
            f.unlink()
        except PermissionError:
            pass  # skip locked log files from other processes
for sub in folders["Logs"].iterdir():
    if sub.is_dir():
        try:
            shutil.rmtree(sub)
        except PermissionError:
            pass

safe_print("  Folders cleaned.")

# ==================================================================
section("TEST 1: Create test task files in Needs_Action/")
# ==================================================================

email_content = """\
---
type: email
from: "alice@example.com"
subject: "Q1 Report Request"
received: 2026-02-13T09:00:00
priority: high
status: pending
---

## Email Content

> Hi, could you send the Q1 revenue report ASAP? It's urgent.
"""

stripe_content = """\
---
type: stripe_payment
amount: $750.00 USD
customer: "bob@client.com"
received: 2026-02-13T10:30:00
priority: high
status: succeeded
charge_id: ch_test002
---

## Payment Details

**Amount:** $750.00 USD
**Customer:** bob@client.com
"""

whatsapp_content = """\
---
type: whatsapp
from: "John Doe"
received: 2026-02-13T11:00:00
priority: medium
keywords: help, quick
status: pending
---

## Message Content

Hey, can you help me with the invoice quickly?
"""

email_file = folders["Needs_Action"] / "EMAIL_test001.md"
stripe_file = folders["Needs_Action"] / "STRIPE_ch_test002.md"
whatsapp_file = folders["Needs_Action"] / "WHATSAPP_test_john.md"

email_file.write_text(email_content, encoding="utf-8")
stripe_file.write_text(stripe_content, encoding="utf-8")
whatsapp_file.write_text(whatsapp_content, encoding="utf-8")

check("EMAIL_test001.md created", email_file.exists())
check("STRIPE_ch_test002.md created", stripe_file.exists())
check("WHATSAPP_test_john.md created", whatsapp_file.exists())
na_count = len(list(folders["Needs_Action"].glob("*.md")))
check("Needs_Action/ has 3 files", na_count == 3, f"found {na_count}")

# ==================================================================
section("TEST 2: Move files Needs_Action -> In_Progress (simulating task_processor)")
# ==================================================================

moved_email = move_file(email_file, folders["In_Progress"])
moved_stripe = move_file(stripe_file, folders["In_Progress"])
moved_whatsapp = move_file(whatsapp_file, folders["In_Progress"])

check("EMAIL moved to In_Progress/", moved_email.exists(), moved_email.name)
check("STRIPE moved to In_Progress/", moved_stripe.exists(), moved_stripe.name)
check("WHATSAPP moved to In_Progress/", moved_whatsapp.exists(), moved_whatsapp.name)

na_after = len(list(folders["Needs_Action"].glob("*.md")))
ip_after = len(list(folders["In_Progress"].glob("*.md")))
check("Needs_Action/ is empty", na_after == 0, f"{na_after} remaining")
check("In_Progress/ has 3 files", ip_after == 3, f"{ip_after} found")

# ==================================================================
section("TEST 3: Simulate Claude output (plans + pending approval)")
# ==================================================================

plan_file = folders["Plans"] / "PLAN_TEST_EMAIL_test001.md"
plan_file.write_text("""\
---
type: plan
source: EMAIL_test001.md
created: 2026-02-13T09:05:00
---

## Plan: Respond to Q1 Report Request

- [x] Read the email
- [x] Check Company_Handbook.md rules
- [ ] Draft reply with Q1 data
- [ ] Send reply (requires approval)
""", encoding="utf-8")
check("Plan file created", plan_file.exists(), plan_file.name)

approval_email = folders["Pending_Approval"] / "APPROVE_reply_alice.md"
approval_email.write_text("""\
---
action_type: send_email
to: alice@example.com
subject: "Re: Q1 Report Request"
body: Hi Alice, please find the Q1 revenue report. Total revenue was $28,500. Let me know if you need anything else.
threadId: thread_abc123
reason: Outbound email requires CEO approval
---

## Pending Approval: Reply to Alice

Sending Q1 report data to alice@example.com.
""", encoding="utf-8")
check("Pending approval (alice) created", approval_email.exists())

approval_stripe = folders["Pending_Approval"] / "APPROVE_thankyou_bob.md"
approval_stripe.write_text("""\
---
action_type: send_email
to: bob@client.com
subject: "Thank you for your payment"
body: Hi Bob, we received your payment of $750.00 for consulting services. Thank you!
reason: Payment >$500 requires CEO approval
---

## Pending Approval: Thank-You to Bob

Payment $750 received. Confirm thank-you email.
""", encoding="utf-8")
check("Pending approval (bob) created", approval_stripe.exists())

pa_count = len(list(folders["Pending_Approval"].glob("*.md")))
check("Pending_Approval/ has 2 files", pa_count == 2, f"found {pa_count}")

# ==================================================================
section("TEST 4: Frontmatter parsing")
# ==================================================================

meta = parse_frontmatter(approval_email)
check("action_type == 'send_email'", meta.get("action_type") == "send_email", meta.get("action_type", ""))
check("to == 'alice@example.com'", meta.get("to") == "alice@example.com", meta.get("to", ""))
check("subject parsed correctly", "Q1" in meta.get("subject", ""), meta.get("subject", ""))
check("body has content", len(meta.get("body", "")) > 20, f"len={len(meta.get('body', ''))}")
check("threadId parsed", meta.get("threadId") == "thread_abc123", meta.get("threadId", ""))

meta_stripe = parse_frontmatter(moved_stripe)
check("stripe type == 'stripe_payment'", meta_stripe.get("type") == "stripe_payment")
check("stripe amount parsed", "$750" in meta_stripe.get("amount", ""), meta_stripe.get("amount", ""))

meta_wa = parse_frontmatter(moved_whatsapp)
check("whatsapp from == 'John Doe'", meta_wa.get("from") == "John Doe")
check("whatsapp keywords parsed", "help" in meta_wa.get("keywords", ""))

# ==================================================================
section("TEST 5: CEO approves alice reply -> action executes (dry run)")
# ==================================================================

approved_alice = move_file(approval_email, folders["Approved"])
check("Alice approval moved to Approved/", approved_alice.exists())

meta_alice = parse_frontmatter(approved_alice)
success = execute_action(approved_alice, meta_alice, folders, dry_run=True)
check("send_email action returned success (dry run)", success)

# Simulate success -> move to Done/
if success and approved_alice.exists():
    done_alice = move_file(approved_alice, folders["Done"])
    check("Approved file moved to Done/", done_alice.exists(), done_alice.name)

# Write success log
_log_action_result(folders, approved_alice, "SUCCESS", "send_email", "DRY RUN: would send to alice@example.com")
success_logs = list(folders["Logs"].glob("ACTION_SUCCESS_*alice*"))
check("ACTION_SUCCESS log created", len(success_logs) > 0)

# ==================================================================
section("TEST 6: CEO rejects bob thank-you")
# ==================================================================

rejected_bob = move_file(approval_stripe, folders["Rejected"])
check("Bob rejection moved to Rejected/", rejected_bob.exists())

# Write rejection log (same as RejectionHandler would)
ts = datetime.now()
rej_log = folders["Logs"] / f"REJECTED_{ts.strftime('%Y%m%d_%H%M%S')}_bob.md"
rej_log.write_text(f"""\
---
type: rejection_log
rejected_file: "{rejected_bob.name}"
rejected_at: {ts.isoformat()}
---

## Rejection Recorded

**File:** {rejected_bob.name}
**Rejected at:** {ts.strftime('%Y-%m-%d %H:%M:%S')}
**Reason:** CEO decided to send a different message.
""", encoding="utf-8")
check("Rejection log created", rej_log.exists(), rej_log.name)

# ==================================================================
section("TEST 7: Placeholder action (post_linkedin)")
# ==================================================================

linkedin_file = folders["Approved"] / "APPROVE_linkedin_post.md"
linkedin_file.write_text("""\
---
action_type: post_linkedin
content: Excited to share our Q1 results!
---

## LinkedIn Post Draft

Sharing Q1 results on company page.
""", encoding="utf-8")

meta_li = parse_frontmatter(linkedin_file)
li_success = _action_placeholder(linkedin_file, meta_li, folders, dry_run=True)
check("Placeholder returned success (graceful)", li_success)

manual_tasks = list(folders["Needs_Action"].glob("MANUAL_*linkedin*"))
check("Manual task created in Needs_Action/", len(manual_tasks) > 0)

if linkedin_file.exists():
    move_file(linkedin_file, folders["Done"])

# ==================================================================
section("TEST 8: Validation - missing 'to' field rejects action")
# ==================================================================

bad_file = folders["Approved"] / "APPROVE_bad_email.md"
bad_file.write_text("""\
---
action_type: send_email
subject: "Missing recipient"
body: This should fail validation.
---
""", encoding="utf-8")

meta_bad = parse_frontmatter(bad_file)
bad_result = execute_action(bad_file, meta_bad, folders, dry_run=True)
check("Action correctly rejected (missing 'to')", not bad_result)

bad_file2 = folders["Approved"] / "APPROVE_bad_email2.md"
bad_file2.write_text("""\
---
action_type: send_email
to: not-an-email
subject: "Bad address"
body: This should also fail.
---
""", encoding="utf-8")

meta_bad2 = parse_frontmatter(bad_file2)
bad_result2 = execute_action(bad_file2, meta_bad2, folders, dry_run=True)
check("Action correctly rejected (invalid email)", not bad_result2)

# Unknown action type
meta_unknown = {"action_type": "fax_machine"}
unk_result = execute_action(bad_file, meta_unknown, folders, dry_run=True)
check("Unknown action_type rejected", not unk_result)

# Clean up
for f in [bad_file, bad_file2]:
    if f.exists():
        f.unlink()

# ==================================================================
section("TEST 9: Error handling - move_to_error")
# ==================================================================

error_test = folders["In_Progress"] / "ERROR_test_task.md"
error_test.write_text("---\ntype: test\n---\nThis task failed.\n", encoding="utf-8")

error_dest = move_to_error(error_test, folders["Logs"])
check("Failed task moved to Logs/Error_*/", error_dest.exists())
check("Error folder created", error_dest.parent.name.startswith("Error_"))

# ==================================================================
section("TEST 10: Move completed tasks to Done/ for briefing")
# ==================================================================

for f in folders["In_Progress"].glob("*.md"):
    move_file(f, folders["Done"])

done_count = len(list(folders["Done"].glob("*.md")))
check("All tasks in Done/", done_count >= 3, f"{done_count} files")

# ==================================================================
section("TEST 11: Weekly Briefing generation")
# ==================================================================

from weekly_briefing import BriefingData, render_briefing, update_dashboard

since = datetime.now() - timedelta(days=7)
data = BriefingData(VAULT, since)

check("Completed tasks detected", len(data.tasks_completed) > 0, f"{len(data.tasks_completed)}")
check("Rejections detected", len(data.rejections) > 0, f"{len(data.rejections)}")

briefing = render_briefing(data)
check("Briefing rendered", len(briefing) > 200, f"{len(briefing)} chars")
check("Has Executive Summary", "Executive Summary" in briefing)
check("Has Tasks Completed table", "Tasks Completed" in briefing)
check("Has Revenue section", "Revenue" in briefing)
check("Has Recommended Actions", "Recommended Actions" in briefing)

briefing_file = folders["Briefings"] / f"BRIEFING_TEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
briefing_file.write_text(briefing, encoding="utf-8")
check("Briefing file saved", briefing_file.exists(), briefing_file.name)

# ==================================================================
section("TEST 12: Dashboard update")
# ==================================================================

update_dashboard(VAULT, data)
dash = (VAULT / "Dashboard.md").read_text(encoding="utf-8")

check("Dashboard has title", "CEO Dashboard" in dash)
check("Dashboard has today's date", datetime.now().strftime("%Y-%m-%d") in dash)
check("Dashboard has Pending Tasks", "Pending Tasks" in dash)
check("Dashboard has Revenue", "Revenue This Week" in dash)
check("Dashboard has Recent Activity", "Recent Activity" in dash)

# ==================================================================
section("FINAL VAULT STATE")
# ==================================================================

for name in ["Needs_Action", "In_Progress", "Done", "Pending_Approval",
             "Approved", "Rejected", "Plans", "Logs", "Briefings"]:
    folder = folders[name]
    count = sum(1 for _ in folder.glob("*.md"))
    # Also count in subdirs (Logs/Error_*)
    for sub in folder.iterdir():
        if sub.is_dir():
            count += sum(1 for _ in sub.glob("*.md"))
    safe_print(f"  {name + '/':.<25} {count} file(s)")

# ==================================================================
section("TEST RESULTS")
# ==================================================================

total = PASS + FAIL
safe_print(f"  Passed: {PASS}/{total}")
safe_print(f"  Failed: {FAIL}/{total}")
safe_print("")

if FAIL == 0:
    safe_print("  ALL TESTS PASSED")
else:
    safe_print(f"  {FAIL} TEST(S) FAILED -- see above for details")

safe_print("")
sys.exit(0 if FAIL == 0 else 1)
