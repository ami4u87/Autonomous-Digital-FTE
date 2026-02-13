"""GmailWatcher — polls Gmail for unread important emails and creates action files.

Inherits from BaseWatcher.  Requires a Google OAuth credentials file
(see setup instructions at the bottom of this file).
"""

import datetime
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from base_watcher import BaseWatcher

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailWatcher(BaseWatcher):
    """Watch a Gmail inbox for unread important messages."""

    def __init__(
        self,
        vault_path: str,
        credentials_path: str = "credentials.json",
        check_interval: int = 120,
    ) -> None:
        super().__init__(vault_path, check_interval)

        self.credentials_path = Path(credentials_path)
        self.token_path = self.vault_path / "token.json"
        self.processed_ids_path = self.vault_path / "processed_gmail_ids.txt"
        self.processed_ids: set[str] = self._load_processed_ids()
        self.service = self._build_service()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _build_service(self):
        """Authenticate with Google and return a Gmail API service object."""
        creds: Credentials | None = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                self.logger.info("Refreshing expired token")
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"OAuth client file not found: {self.credentials_path}\n"
                        "See setup instructions at the bottom of gmail_watcher.py"
                    )
                self.logger.info("Starting OAuth flow — a browser window will open")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            self.logger.info("Token saved to %s", self.token_path)

        service = build("gmail", "v1", credentials=creds)
        self.logger.info("Gmail service built successfully")
        return service

    # ------------------------------------------------------------------
    # Processed-ID persistence
    # ------------------------------------------------------------------

    def _load_processed_ids(self) -> set[str]:
        """Load previously processed message IDs from disk."""
        if self.processed_ids_path.exists():
            ids = {
                line.strip()
                for line in self.processed_ids_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            self.logger.info("Loaded %d processed message IDs", len(ids))
            return ids
        return set()

    def _save_processed_id(self, message_id: str) -> None:
        """Append a single ID to the processed-IDs file and the in-memory set."""
        self.processed_ids.add(message_id)
        with self.processed_ids_path.open("a", encoding="utf-8") as f:
            f.write(message_id + "\n")

    # ------------------------------------------------------------------
    # BaseWatcher interface
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list:
        """Query Gmail for unread important messages not yet processed."""
        self.logger.debug("Checking Gmail for unread important messages")

        try:
            results = (
                self.service.users()
                .messages()
                .list(userId="me", q="is:unread is:important", maxResults=20)
                .execute()
            )
        except Exception:
            self.logger.exception("Failed to list Gmail messages")
            return []

        messages = results.get("messages", [])
        if not messages:
            self.logger.debug("No unread important messages found")
            return []

        new_messages = [m for m in messages if m["id"] not in self.processed_ids]
        if new_messages:
            self.logger.info(
                "%d new message(s) out of %d unread important",
                len(new_messages),
                len(messages),
            )
        return new_messages

    def create_action_file(self, item) -> Path:
        """Fetch the full message, extract headers, and write an action file."""
        message_id: str = item["id"]

        try:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception:
            self.logger.exception("Failed to fetch message %s", message_id)
            raise

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        sender = headers.get("From", "Unknown")
        subject = headers.get("Subject", "(no subject)")
        snippet = msg.get("snippet", "")
        received = datetime.datetime.now().isoformat()

        # Determine suggested actions based on content
        suggestions = self._suggest_actions(subject, snippet)

        content = (
            f"---\n"
            f"type: email\n"
            f"from: \"{sender}\"\n"
            f"subject: \"{subject}\"\n"
            f"received: {received}\n"
            f"priority: high\n"
            f"status: pending\n"
            f"---\n"
            f"\n"
            f"## Email Content\n"
            f"\n"
            f"**From:** {sender}\n"
            f"**Subject:** {subject}\n"
            f"\n"
            f"> {snippet}\n"
            f"\n"
            f"## Suggested Actions\n"
            f"\n"
            f"{suggestions}"
        )

        filename = f"EMAIL_{message_id}.md"
        filepath = self.needs_action / filename
        filepath.write_text(content, encoding="utf-8")

        self._save_processed_id(message_id)
        self.logger.info("Action file created: %s (subject: %s)", filename, subject)
        return filepath

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _suggest_actions(subject: str, snippet: str) -> str:
        """Generate a checklist of suggested actions based on email content."""
        text = f"{subject} {snippet}".lower()
        actions: list[str] = []

        urgent_keywords = {"urgent", "asap", "immediately", "critical", "emergency"}
        payment_keywords = {"invoice", "payment", "pay", "billing", "receipt", "charge"}
        meeting_keywords = {"meeting", "calendar", "schedule", "call", "zoom", "teams"}
        reply_keywords = {"reply", "respond", "confirm", "rsvp", "feedback", "question"}

        if urgent_keywords & set(text.split()):
            actions.append("- [ ] **URGENT** — Escalate and respond immediately")

        if payment_keywords & set(text.split()):
            actions.append("- [ ] Review financial details and create Pending_Approval file")

        if meeting_keywords & set(text.split()):
            actions.append("- [ ] Check calendar and confirm availability")

        if reply_keywords & set(text.split()):
            actions.append("- [ ] Draft and send reply")

        # Always include these baseline actions
        actions.append("- [ ] Read full email")
        actions.append("- [ ] Decide on response or next step")
        actions.append("- [ ] Log decision in Logs/")

        return "\n".join(actions) + "\n"


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    import sys

    vault = sys.argv[1] if len(sys.argv) > 1 else "."
    creds = sys.argv[2] if len(sys.argv) > 2 else "credentials.json"

    watcher = GmailWatcher(vault_path=vault, credentials_path=creds)
    watcher.run()


# ======================================================================
# GOOGLE OAUTH SETUP — Step-by-step
# ======================================================================
#
# 1. GO TO GOOGLE CLOUD CONSOLE
#    https://console.cloud.google.com/
#    - Sign in with the Google account whose Gmail you want to monitor.
#    - Create a new project (or select an existing one).
#      Click the project dropdown at the top → "New Project" →
#      name it (e.g. "AI-Employee") → Create.
#
# 2. ENABLE THE GMAIL API
#    - In the left sidebar: APIs & Services → Library.
#    - Search for "Gmail API" → click it → "Enable".
#
# 3. CONFIGURE THE OAUTH CONSENT SCREEN
#    - APIs & Services → OAuth consent screen.
#    - Choose "External" (or "Internal" if using Google Workspace).
#    - Fill in the required fields:
#        App name:       AI Employee
#        User support email: <your email>
#        Developer email:    <your email>
#    - Click "Save and Continue" through Scopes and Test Users.
#    - Under "Test users", add the Gmail address you will monitor.
#
# 4. CREATE OAUTH CLIENT CREDENTIALS
#    - APIs & Services → Credentials → "Create Credentials" → "OAuth client ID".
#    - Application type: "Desktop app".
#    - Name: "AI Employee Desktop" (or anything you like).
#    - Click "Create".
#
# 5. DOWNLOAD THE CREDENTIALS FILE
#    - On the Credentials page, find your new OAuth 2.0 Client ID.
#    - Click the download icon (⬇) to download the JSON file.
#    - Rename it to "credentials.json".
#    - Place it in the project root (same directory as this file),
#      or pass the path via the credentials_path argument.
#
# 6. FIRST RUN
#    - Run:  python gmail_watcher.py /path/to/vault credentials.json
#    - A browser window will open asking you to authorize the app.
#    - Grant "Read your Gmail" permission.
#    - A token.json will be saved in your vault so you won't need
#      to re-authorize on subsequent runs (it auto-refreshes).
#
# 7. SCOPES
#    - This watcher uses gmail.readonly — it can never send, delete,
#      or modify any email. Read-only access for safety.
#
# TROUBLESHOOTING
#    - "Access blocked: This app's request is invalid"
#      → Make sure your email is listed under Test Users in the
#        OAuth consent screen.
#    - "File not found: credentials.json"
#      → Download from step 5 and place in the correct path.
#    - Token expired / revoked
#      → Delete token.json and re-run to re-authorize.
#
