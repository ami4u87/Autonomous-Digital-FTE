# Demo Video Script: Autonomous Digital FTE

**Total Duration:** ~8 minutes
**Format:** Screen recording with voiceover
**Tools visible:** Terminal, Obsidian, Browser (Gmail/Stripe)

---

## SCENE 1: Introduction (0:00 - 0:45)

**[Screen: Title slide or GitHub repo page]**

**Voiceover:**

> "What if you could hire a digital employee that monitors your email, watches your payments, and handles routine business tasks — all while you sleep?"
>
> "This is the Autonomous Digital FTE — a fully open-source AI employee built with Python, Claude Code, and Obsidian."
>
> "It watches Gmail, WhatsApp, and Stripe for incoming work, processes tasks autonomously, and only bothers you when it needs approval for something important — like sending an email or handling a large payment."
>
> "Let me show you how it works, end to end."

---

## SCENE 2: The Vault Tour (0:45 - 2:00)

**[Screen: Open Obsidian vault, show folder sidebar]**

**Voiceover:**

> "Everything runs through this Obsidian vault. Think of it as the AI employee's desk."

**[Click through each folder]**

> "Needs_Action is the inbox — new tasks land here automatically."
>
> "In_Progress is where the AI is currently working."
>
> "Pending_Approval is the stack on your desk — things the AI needs your sign-off on before acting."
>
> "Approved and Rejected — you drag files here to give your decision. The system detects the move and acts immediately."
>
> "Done is the archive. Nothing gets deleted — the AI only moves files here when work is complete."
>
> "And Logs keeps a full audit trail of every decision the AI makes."

**[Open Dashboard.md]**

> "The Dashboard gives you a quick snapshot — pending tasks, revenue this week, recent activity. Updated automatically."

**[Open Company_Handbook.md]**

> "And this is the Company Handbook — the rules the AI follows. Never spend over $100 without approval. Always flag urgent keywords. Log every decision. You can edit these rules anytime and the AI adapts."

---

## SCENE 3: Starting the System (2:00 - 2:45)

**[Screen: Terminal, split into panes]**

**Voiceover:**

> "Let's start the system. We need three things running."

**[Terminal 1 — type and run:]**
```
node mcp_email_server.js
```

> "First, the MCP Email Server. This is a local-only Express server that can send emails through Gmail. It only accepts requests from localhost — no external access."

**[Terminal 2 — type and run:]**
```
python task_processor.py . --log-to-file
```

> "Second, the Task Processor — this is the brain. It watches the vault folders and dispatches work to Claude Code."

**[Terminal 3 — type and run:]**
```
python gmail_watcher.py . credentials.json
```

> "Third, the Gmail Watcher. It polls for unread important emails every two minutes."

> "All three are running. Now let's trigger a real workflow."

---

## SCENE 4: Live Demo — Email Arrives (2:45 - 4:30)

**[Screen: Browser — Gmail compose window]**

**Voiceover:**

> "I'm going to send myself an email marked as important. Subject: 'URGENT — Invoice #4521 for $2,300'. This hits two of our trigger keywords — urgent and invoice."

**[Send the email, mark as important]**

**[Screen: Switch to Terminal 3 — Gmail Watcher]**

> "Watch the Gmail Watcher terminal..."

**[Wait for watcher to detect — logs appear:]**
```
Found 1 new item(s)
Created action file: EMAIL_18abc123.md
```

> "It detected the unread important email and created a task file in Needs_Action."

**[Screen: Switch to Obsidian — open Needs_Action folder]**

> "Here it is in Obsidian. Let's open it."

**[Open the EMAIL file, show frontmatter and content]**

> "The AI extracted the sender, subject, timestamp, and set priority to high. It flagged the urgent and invoice keywords. And it already suggested actions — escalate immediately, create a pending approval for the payment, reply to the sender."

**[Screen: Switch to Terminal 2 — Task Processor]**

> "Now watch the Task Processor. It detected the new file..."

**[Logs show:]**
```
New task detected: EMAIL_18abc123.md
Moved: EMAIL_18abc123.md -> In_Progress/
Invoking Claude Code for: EMAIL_18abc123.md
```

> "It moved the file to In_Progress and invoked Claude Code to analyse it."

**[Wait for Claude to finish — logs show completion]**

> "Claude read the task, checked the Company Handbook, and saw this involves a $2,300 invoice — way above the $100 approval threshold."

**[Screen: Switch to Obsidian]**

> "So Claude created two things."

**[Open Plans/ folder, show the plan file]**

> "A detailed plan with checkboxed steps — read the invoice, verify the amount, draft a reply, get CEO approval before responding."

**[Open Pending_Approval/ folder, show the approval file]**

> "And a pending approval file. This is Claude asking for my permission to send a reply email. Look at the frontmatter — action_type is send_email, the recipient, subject, and body are all pre-filled."

---

## SCENE 5: CEO Approval Flow (4:30 - 5:45)

**[Screen: Obsidian — Pending_Approval folder]**

**Voiceover:**

> "Now here's the human-in-the-loop moment. I'm the CEO. I review what Claude wants to do."

**[Read the approval file on screen]**

> "It wants to reply to the sender acknowledging the invoice and saying we'll process payment within 48 hours. The body looks professional. I'm happy with this."

**[Drag the file from Pending_Approval to Approved in Obsidian sidebar]**

> "I drag the file to the Approved folder. That's it. That's my entire input."

**[Screen: Switch to Terminal 2 — Task Processor]**

> "The Task Processor detected the move instantly."

**[Logs show:]**
```
APPROVAL RECEIVED: APPROVE_reply_invoice.md
Parsed action_type='send_email' with fields: action_type, to, subject, body
Sending email -> to='sender@company.com' subject='Re: Invoice #4521'
Email sent successfully — messageId=18xyz789
Approved task completed -> moved to Done/
```

> "It parsed the frontmatter, called the MCP Email Server, which sent the email through Gmail, and moved everything to Done."

**[Screen: Switch to Obsidian — show Done/ folder with the file, then Logs/ folder]**

> "The task is archived in Done. And in Logs, there's a full audit trail — ACTION_SUCCESS with the timestamp, recipient, and message ID."

**[Screen: Browser — Gmail Sent folder, show the sent email]**

> "And there it is in my Gmail Sent folder. A professional reply, sent by my AI employee, with my approval."

---

## SCENE 6: Rejection Flow (5:45 - 6:15)

**[Screen: Obsidian — create or show another Pending_Approval file]**

**Voiceover:**

> "What if I don't agree with what the AI wants to do? Let's say it drafted a reply I don't like."

**[Drag file from Pending_Approval to Rejected]**

> "I drag it to Rejected instead. The system logs the rejection, the AI doesn't send anything, and I can handle it myself or tell the AI to try again with different wording."

**[Show Logs/REJECTED file]**

> "Full rejection log with timestamp. Nothing was sent. Complete control."

---

## SCENE 7: Stripe Payment Demo (6:15 - 7:00)

**[Screen: Browser — Stripe Dashboard]**

**Voiceover:**

> "Let's see the payment monitoring. I'll create a test charge in Stripe."

**[Create a test payment: $750, card 4242 4242 4242 4242]**

> "A $750 payment just came through."

**[Screen: Terminal — Stripe Watcher logs]**

> "The Stripe Watcher picks it up on the next poll cycle and creates a task file."

**[Screen: Obsidian — open STRIPE file in Needs_Action]**

> "Here's the payment task. Amount, customer, charge ID, all extracted. Priority is high because it's over $500. The suggested actions include sending a thank-you email, updating accounting, and flagging it for approval since it's a high-value transaction."

> "The same pipeline kicks in — Claude processes it, creates a plan, and if it wants to send a thank-you email, it asks for my approval first."

---

## SCENE 8: Weekly Briefing (7:00 - 7:30)

**[Screen: Terminal]**

**Voiceover:**

> "At the end of the week, I generate a briefing."

**[Type and run:]**
```
python weekly_briefing.py .
```

**[Screen: Obsidian — open the generated briefing in Briefings/]**

> "One command gives me a full executive summary. Tasks completed, revenue received, pending items, approvals I gave, rejections, any errors — all in a clean markdown report. It even flags alerts if my backlog is growing or revenue is below target."

> "And it automatically updates the Dashboard."

---

## SCENE 9: Closing (7:30 - 8:00)

**[Screen: GitHub repo page or architecture diagram]**

**Voiceover:**

> "To recap — this system gives you an AI employee that:"
>
> "Monitors your email, payments, and messages 24/7."
>
> "Processes incoming work autonomously using Claude Code."
>
> "Never takes financial action without your explicit approval."
>
> "Logs every single decision for full auditability."
>
> "And runs entirely on your local machine — your data never leaves your control."
>
> "The entire system is open source. Link in the description."
>
> "Thanks for watching."

**[Screen: End card with GitHub URL]**

```
github.com/ami4u87/Autonomous-Digital-FTE
```

---

## Recording Checklist

Before recording, prepare:

- [ ] Obsidian vault open with sidebar visible
- [ ] Three terminal panes ready (MCP server, task processor, gmail watcher)
- [ ] Gmail account with OAuth configured and logged in
- [ ] Stripe test mode dashboard open (optional for live demo)
- [ ] Send yourself a test email beforehand to verify watcher works
- [ ] Clean vault folders so demo starts fresh
- [ ] Screen recording software running (OBS, Loom, or similar)
- [ ] Microphone tested for voiceover

## Alternative: Dry-Run Demo (No API Keys)

If you don't have credentials set up, you can demo using the test suite:

```bash
python test_e2e_dry_run.py
```

Show the terminal output scrolling through all 49 tests passing, then open Obsidian to show the generated files in Done/, Logs/, Plans/, and Briefings/. This demonstrates every component without any external services.

**Voiceover adjustment for dry-run:**

> "I'm running the end-to-end test suite. This simulates the full pipeline — creating tasks, moving files, parsing approvals, validating actions, generating briefings — all without needing any API keys. 49 tests, all passing."
