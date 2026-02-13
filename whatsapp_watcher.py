"""WhatsAppWatcher — monitors WhatsApp Web for urgent unread messages.

Creates task markdown files in the Obsidian vault Needs_Action/ folder.
Inherits from BaseWatcher.

REQUIREMENTS
------------
    pip install playwright
    python -m playwright install chromium

First run must be headless=False so you can scan the QR code.
See setup instructions at the bottom of this file.
"""

import datetime
import json
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext, Page

from base_watcher import BaseWatcher

logger = logging.getLogger(__name__)


class WhatsAppWatcher(BaseWatcher):
    """Watch WhatsApp Web for unread messages containing urgent keywords."""

    KEYWORDS: list[str] = [
        "urgent",
        "asap",
        "invoice",
        "payment",
        "help",
        "quick",
        "now",
    ]

    def __init__(
        self,
        vault_path: str,
        session_path: str,
        check_interval: int = 30,
        headless: bool = True,
    ) -> None:
        super().__init__(vault_path, check_interval)

        self.session_path = Path(session_path)
        self.session_path.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.keywords = list(self.KEYWORDS)

    # ------------------------------------------------------------------
    # BaseWatcher interface
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list:
        """Launch a persistent browser context, scrape unread chats,
        and return those whose preview text contains at least one keyword.
        """
        urgent_messages: list[dict] = []

        try:
            with sync_playwright() as pw:
                context = self._get_browser_context(pw)
                try:
                    page = self._open_whatsapp(context)
                    if page is None:
                        return []

                    urgent_messages = self._scrape_unread_chats(page)
                finally:
                    context.close()

        except Exception:
            self.logger.exception("Error during WhatsApp check")

        return urgent_messages

    def create_action_file(self, item) -> Path:
        """Write an action markdown file for a single urgent WhatsApp message."""
        chat_name: str = item.get("chat_name", "Unknown")
        text: str = item.get("text", "")
        matched: list[str] = item.get("matched_keywords", [])
        received = datetime.datetime.now().isoformat()

        # Determine priority from keywords
        high_priority_words = {"urgent", "asap"}
        priority = "high" if high_priority_words & set(matched) else "medium"

        # Build suggested actions
        suggestions = self._suggest_actions(matched)

        # Slugify the chat name for the filename
        slug = self._slugify(chat_name)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"WHATSAPP_{timestamp}_{slug}.md"

        content = (
            f"---\n"
            f"type: whatsapp\n"
            f"from: \"{chat_name}\"\n"
            f"received: {received}\n"
            f"priority: {priority}\n"
            f"status: pending\n"
            f"keywords: {', '.join(matched)}\n"
            f"---\n"
            f"\n"
            f"## Message Content\n"
            f"\n"
            f"{text}\n"
            f"\n"
            f"## Suggested Actions\n"
            f"\n"
            f"{suggestions}"
        )

        filepath = self.needs_action / filename
        filepath.write_text(content, encoding="utf-8")
        self.logger.info(
            "Action file created: %s (from: %s, priority: %s)",
            filename,
            chat_name,
            priority,
        )
        return filepath

    # ------------------------------------------------------------------
    # Browser helpers
    # ------------------------------------------------------------------

    def _get_browser_context(self, pw) -> BrowserContext:
        """Return a persistent Chromium context that preserves the QR login."""
        self.logger.debug(
            "Launching persistent context from %s (headless=%s)",
            self.session_path,
            self.headless,
        )
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(self.session_path),
            headless=self.headless,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        return context

    def _open_whatsapp(self, context: BrowserContext) -> Page | None:
        """Navigate to WhatsApp Web and wait for the chat list to load.

        Returns the Page on success, or None if the user is not logged in.
        """
        page = context.new_page()

        self.logger.info("Navigating to WhatsApp Web")
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

        # Wait for either the chat list (logged in) or the QR canvas (not logged in)
        try:
            page.wait_for_selector(
                '[data-testid="chat-list"], [data-testid="qrcode"], canvas[aria-label="Scan me!"]',
                timeout=30_000,
            )
        except Exception:
            self.logger.error(
                "WhatsApp Web did not load within 30 seconds — "
                "check your network connection"
            )
            return None

        # Check if we landed on the QR code screen instead of the chat list
        qr_element = page.query_selector(
            '[data-testid="qrcode"], canvas[aria-label="Scan me!"]'
        )
        chat_list = page.query_selector('[data-testid="chat-list"]')

        if qr_element and not chat_list:
            self.logger.error(
                "Not logged in — scan QR code manually first. "
                "Run once with headless=False:  "
                "WhatsAppWatcher(vault, session, headless=False).run()"
            )
            return None

        # Give dynamic content a moment to populate
        page.wait_for_timeout(3000)
        self.logger.info("WhatsApp Web loaded — scanning unread chats")
        return page

    def _scrape_unread_chats(self, page: Page) -> list[dict]:
        """Find all unread chat rows and check their preview text for keywords."""
        urgent: list[dict] = []

        # WhatsApp marks unread chats with a span containing the unread count.
        # Multiple selectors for resilience across minor UI changes.
        unread_selectors = [
            'span[data-testid="icon-unread-count"]',
            'span[aria-label*="unread message"]',
            'span[aria-label*="unread"]',
        ]

        unread_badges = []
        for selector in unread_selectors:
            unread_badges = page.query_selector_all(selector)
            if unread_badges:
                self.logger.debug(
                    "Found %d unread badge(s) with selector: %s",
                    len(unread_badges),
                    selector,
                )
                break

        if not unread_badges:
            self.logger.debug("No unread chat badges found")
            return []

        for badge in unread_badges:
            try:
                # Walk up from the badge to the chat row container
                chat_row = badge.evaluate_handle(
                    """el => {
                        let node = el;
                        for (let i = 0; i < 10; i++) {
                            node = node.parentElement;
                            if (!node) return null;
                            if (node.getAttribute('data-testid') === 'cell-frame-container'
                                || node.getAttribute('role') === 'listitem'
                                || node.getAttribute('tabindex') === '-1') {
                                return node;
                            }
                        }
                        return node;
                    }"""
                )

                if not chat_row:
                    continue

                # Extract chat name
                chat_name = chat_row.evaluate(
                    """el => {
                        const title = el.querySelector('[data-testid="cell-frame-title"]');
                        if (title) return title.innerText.trim();
                        const span = el.querySelector('span[dir="auto"][title]');
                        if (span) return span.getAttribute('title') || span.innerText.trim();
                        return 'Unknown';
                    }"""
                )

                # Extract last message preview
                preview_text = chat_row.evaluate(
                    """el => {
                        const msg = el.querySelector('[data-testid="last-msg-status"]');
                        if (msg) return msg.innerText.trim();
                        const span2 = el.querySelector('span[data-testid="cell-frame-secondary"]');
                        if (span2) return span2.innerText.trim();
                        const spans = el.querySelectorAll('span[dir="ltr"], span[dir="auto"]');
                        for (const s of spans) {
                            if (s.innerText.length > 10) return s.innerText.trim();
                        }
                        return '';
                    }"""
                )

                if not preview_text:
                    self.logger.debug("No preview text for chat: %s", chat_name)
                    continue

                # Check for keyword matches
                text_lower = preview_text.lower()
                matched = [kw for kw in self.keywords if kw in text_lower]

                if matched:
                    self.logger.info(
                        "Keyword match in chat '%s': %s",
                        chat_name,
                        ", ".join(matched),
                    )
                    urgent.append(
                        {
                            "chat_name": chat_name,
                            "text": preview_text,
                            "matched_keywords": matched,
                        }
                    )

            except Exception:
                self.logger.exception("Error processing an unread chat badge")
                continue

        self.logger.info(
            "Scan complete — %d urgent message(s) found out of %d unread",
            len(urgent),
            len(unread_badges),
        )
        return urgent

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a safe filename slug."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_-]+", "_", text)
        return text[:50] or "unknown"

    @staticmethod
    def _suggest_actions(matched_keywords: list[str]) -> str:
        """Build a markdown checklist of suggested actions based on matched keywords."""
        actions: list[str] = []

        payment_words = {"invoice", "payment"}
        urgent_words = {"urgent", "asap"}
        help_words = {"help", "quick", "now"}

        matched_set = set(matched_keywords)

        if matched_set & urgent_words:
            actions.append("- [ ] **URGENT** — Escalate and respond immediately")

        if matched_set & payment_words:
            actions.append(
                "- [ ] Create `Pending_Approval/` file for payment review"
            )
            actions.append("- [ ] Verify invoice details and amount")

        if matched_set & help_words:
            actions.append("- [ ] Assess request and provide assistance")

        # Always-present actions
        actions.append("- [ ] Reply to sender")
        actions.append("- [ ] Log decision in `Logs/`")
        actions.append("- [ ] Archive after processing → move to `Done/`")

        return "\n".join(actions) + "\n"


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    import sys

    vault = sys.argv[1] if len(sys.argv) > 1 else "."
    session = sys.argv[2] if len(sys.argv) > 2 else "./whatsapp_session"

    # First run: set headless=False so you can scan the QR code
    headless_flag = "--headless" in sys.argv

    watcher = WhatsAppWatcher(
        vault_path=vault,
        session_path=session,
        headless=headless_flag,
    )
    watcher.run()


# ======================================================================
# FIRST-RUN SETUP — Step-by-step
# ======================================================================
#
# 1. INSTALL DEPENDENCIES
#        pip install playwright
#        python -m playwright install chromium
#
# 2. CREATE A SESSION FOLDER
#    Create an empty folder that Playwright will use to persist your
#    browser profile (cookies, local storage → WhatsApp login).
#
#        mkdir whatsapp_session
#
#    This folder is passed as `session_path`.  Do NOT delete it after
#    login — it keeps you authenticated across runs.
#
# 3. FIRST RUN — SCAN THE QR CODE
#    You MUST run the first time with a visible browser so you can
#    scan the QR code with your phone:
#
#        python whatsapp_watcher.py /path/to/vault ./whatsapp_session
#
#    By default the first run opens a visible browser window.
#    - WhatsApp Web will display a QR code.
#    - On your phone: WhatsApp → Settings → Linked Devices → Link a Device.
#    - Scan the QR code on screen.
#    - Wait until the chat list loads (you'll see your conversations).
#    - Press Ctrl+C to stop the watcher.
#
# 4. SUBSEQUENT RUNS — HEADLESS
#    After the QR code is scanned and the session is saved, run in
#    headless mode for background monitoring:
#
#        python whatsapp_watcher.py /path/to/vault ./whatsapp_session --headless
#
#    The watcher will:
#    - Open WhatsApp Web in the background (no visible window).
#    - Scan unread chats every 30 seconds (configurable).
#    - Create action files in Needs_Action/ for any message containing
#      urgent keywords.
#
# 5. KEYWORD CUSTOMIZATION
#    Default keywords: urgent, asap, invoice, payment, help, quick, now
#    To customize, modify the KEYWORDS class attribute or pass a new list:
#
#        watcher = WhatsAppWatcher(vault, session)
#        watcher.keywords = ["urgent", "asap", "invoice", "payment", "help"]
#
# 6. SESSION EXPIRY
#    WhatsApp Web sessions can expire if:
#    - You log out from your phone.
#    - You haven't used the linked device for 14+ days.
#    - You re-link on another computer (max 4 linked devices).
#    If the watcher logs "Not logged in — scan QR code manually first",
#    re-run step 3.
#
# 7. SECURITY NOTES
#    - The session folder contains your WhatsApp login.
#      Treat it like a password — do NOT commit it to git.
#    - Add to .gitignore:  whatsapp_session/
#    - The watcher is READ-ONLY: it never sends messages or
#      modifies your WhatsApp data.
#
