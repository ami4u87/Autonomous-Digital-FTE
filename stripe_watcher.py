"""StripeWatcher — monitors Stripe for successful payments and creates action files.

Creates task markdown files in the Obsidian vault Needs_Action/ folder.
Inherits from BaseWatcher.

REQUIREMENTS
------------
    pip install stripe

ENVIRONMENT
-----------
    Set STRIPE_SECRET_KEY before running.  NEVER hardcode the key.

        # Linux / macOS
        export STRIPE_SECRET_KEY="sk_live_..."

        # Windows (PowerShell)
        $env:STRIPE_SECRET_KEY = "sk_live_..."

        # Windows (cmd)
        set STRIPE_SECRET_KEY=sk_live_...
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import stripe

from base_watcher import BaseWatcher

logger = logging.getLogger(__name__)

# High-value threshold in cents ($500.00)
HIGH_VALUE_THRESHOLD_CENTS = 50_000


class StripeWatcher(BaseWatcher):
    """Watch Stripe for successful charges and payment intents."""

    def __init__(
        self,
        vault_path: str,
        check_interval: int = 300,
    ) -> None:
        super().__init__(vault_path, check_interval)

        # ---- API key (from environment only, never hardcoded) ----
        api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not api_key:
            self.logger.critical(
                "STRIPE_SECRET_KEY is not set.  "
                "Export it as an environment variable before running."
            )
            sys.exit(1)

        stripe.api_key = api_key

        # Warn loudly if someone accidentally uses a live key in dev
        if api_key.startswith("sk_live_"):
            self.logger.warning(
                "Running with a LIVE Stripe key — real money is involved"
            )

        # ---- State ----
        self.last_checked: float = time.time() - 3600  # start 1 hour back
        self.processed_ids_path = self.vault_path / "processed_stripe_ids.txt"
        self.processed_ids: set[str] = self._load_processed_ids()

        self.logger.info(
            "StripeWatcher initialised — polling every %ds, "
            "%d previously processed events loaded",
            self.check_interval,
            len(self.processed_ids),
        )

    # ------------------------------------------------------------------
    # Processed-ID persistence
    # ------------------------------------------------------------------

    def _load_processed_ids(self) -> set[str]:
        """Load previously processed event/charge IDs from disk."""
        if self.processed_ids_path.exists():
            ids = {
                line.strip()
                for line in self.processed_ids_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            self.logger.info("Loaded %d processed Stripe IDs", len(ids))
            return ids
        return set()

    def _save_processed_id(self, event_id: str) -> None:
        """Append a single ID to the processed-IDs file and in-memory set."""
        self.processed_ids.add(event_id)
        with self.processed_ids_path.open("a", encoding="utf-8") as f:
            f.write(event_id + "\n")

    # ------------------------------------------------------------------
    # BaseWatcher interface
    # ------------------------------------------------------------------

    def check_for_updates(self) -> list:
        """Fetch recent successful charge/payment events from Stripe."""
        cutoff = int(self.last_checked - 60)  # 60s overlap to avoid missed events

        self.logger.debug(
            "Checking Stripe events since %s",
            datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),
        )

        try:
            events = stripe.Event.list(
                types=[
                    "charge.succeeded",
                    "payment_intent.succeeded",
                ],
                created={"gt": cutoff},
                limit=20,
            )
        except stripe.error.AuthenticationError:
            self.logger.error(
                "Stripe authentication failed — check STRIPE_SECRET_KEY"
            )
            return []
        except stripe.error.RateLimitError:
            self.logger.warning(
                "Stripe rate limit hit — backing off until next cycle"
            )
            return []
        except stripe.error.APIConnectionError:
            self.logger.error(
                "Could not connect to Stripe — check network"
            )
            return []
        except stripe.error.StripeError:
            self.logger.exception("Stripe API error")
            return []

        new_events = [e for e in events.auto_paging_iter() if e.id not in self.processed_ids]

        if new_events:
            self.logger.info(
                "Found %d new Stripe event(s)",
                len(new_events),
            )

        self.last_checked = time.time()
        return new_events

    def create_action_file(self, item) -> Path:
        """Extract charge details from a Stripe Event and write an action file."""
        event = item
        event_id: str = event.id
        event_type: str = event.type

        # Extract the charge / payment_intent object from the event data
        obj = event.data.object
        charge_data = self._extract_charge_data(obj, event_type)

        amount_cents: int = charge_data["amount_cents"]
        amount_display: str = charge_data["amount_display"]
        currency: str = charge_data["currency"]
        customer_label: str = charge_data["customer_label"]
        description: str = charge_data["description"]
        created_ts: int = charge_data["created_ts"]
        charge_id: str = charge_data["charge_id"]

        received = datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()
        priority = "high" if amount_cents > HIGH_VALUE_THRESHOLD_CENTS else "medium"
        suggestions = self._suggest_actions(amount_cents, customer_label)

        content = (
            f"---\n"
            f"type: stripe_payment\n"
            f"amount: {amount_display} {currency}\n"
            f"customer: \"{customer_label}\"\n"
            f"received: {received}\n"
            f"priority: {priority}\n"
            f"status: succeeded\n"
            f"charge_id: {charge_id}\n"
            f"event_id: {event_id}\n"
            f"---\n"
            f"\n"
            f"## Payment Details\n"
            f"\n"
            f"**Amount:** {amount_display} {currency}\n"
            f"**Customer:** {customer_label}\n"
            f"**Description:** {description}\n"
            f"**Charge ID:** `{charge_id}`\n"
            f"**Received:** {received}\n"
            f"\n"
            f"## Suggested Actions\n"
            f"\n"
            f"{suggestions}"
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"STRIPE_{charge_id}_{timestamp}.md"
        filepath = self.needs_action / filename
        filepath.write_text(content, encoding="utf-8")

        self._save_processed_id(event_id)
        self.logger.info(
            "Action file created: %s (%s %s from %s)",
            filename,
            amount_display,
            currency,
            customer_label,
        )
        return filepath

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_charge_data(obj, event_type: str) -> dict:
        """Normalise fields from either a Charge or PaymentIntent object."""
        if event_type == "charge.succeeded":
            amount_cents = obj.get("amount", 0)
            currency = obj.get("currency", "usd").upper()
            description = obj.get("description") or "New payment received"
            created_ts = obj.get("created", 0)
            charge_id = obj.get("id", "unknown")

            # Customer info: try receipt_email → customer name → billing email
            customer_label = (
                obj.get("receipt_email")
                or obj.get("billing_details", {}).get("email")
                or obj.get("billing_details", {}).get("name")
                or "Unknown"
            )
        elif event_type == "payment_intent.succeeded":
            amount_cents = obj.get("amount", 0)
            currency = obj.get("currency", "usd").upper()
            description = obj.get("description") or "New payment received"
            created_ts = obj.get("created", 0)
            charge_id = obj.get("latest_charge") or obj.get("id", "unknown")

            customer_label = (
                obj.get("receipt_email")
                or obj.get("shipping", {}).get("name") if obj.get("shipping") else None
            ) or "Unknown"
        else:
            amount_cents = obj.get("amount", 0)
            currency = obj.get("currency", "usd").upper()
            description = "Payment event"
            created_ts = obj.get("created", 0)
            charge_id = obj.get("id", "unknown")
            customer_label = "Unknown"

        amount_display = f"${amount_cents / 100:,.2f}"

        return {
            "amount_cents": amount_cents,
            "amount_display": amount_display,
            "currency": currency,
            "customer_label": customer_label,
            "description": description,
            "created_ts": created_ts,
            "charge_id": charge_id,
        }

    @staticmethod
    def _suggest_actions(amount_cents: int, customer_label: str) -> str:
        """Build a markdown checklist based on the payment amount."""
        actions: list[str] = []

        if amount_cents > HIGH_VALUE_THRESHOLD_CENTS:
            actions.append(
                "- [ ] **HIGH VALUE** — Create `Pending_Approval/` file for human review"
            )

        actions.append(f"- [ ] Send thank-you email to {customer_label}")
        actions.append("- [ ] Update `Accounting/` revenue tracking")
        actions.append("- [ ] Add to weekly CEO briefing in `Briefings/`")
        actions.append("- [ ] Reconcile with bank records")
        actions.append("- [ ] Archive after processing → move to `Done/`")

        return "\n".join(actions) + "\n"


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Stripe payment watcher")
    parser.add_argument("vault", nargs="?", default=".", help="Path to Obsidian vault")
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Poll interval in seconds (default: 300)",
    )
    args = parser.parse_args()

    watcher = StripeWatcher(vault_path=args.vault, check_interval=args.interval)
    watcher.run()


# ======================================================================
# STRIPE SETUP — Step-by-step
# ======================================================================
#
# 1. CREATE A STRIPE ACCOUNT
#    https://dashboard.stripe.com/register
#    Complete the onboarding steps to activate your account.
#
# 2. GET YOUR API KEY
#    - Go to: https://dashboard.stripe.com/apikeys
#    - Copy the "Secret key" (starts with sk_test_ or sk_live_).
#    - NEVER commit this key to git.  NEVER paste it in source code.
#
# 3. SET THE ENVIRONMENT VARIABLE
#
#    # For testing (uses Stripe test mode — no real charges):
#    export STRIPE_SECRET_KEY="sk_test_..."
#
#    # For production (real money):
#    export STRIPE_SECRET_KEY="sk_live_..."
#
#    On Windows PowerShell:
#    $env:STRIPE_SECRET_KEY = "sk_test_..."
#
#    On Windows CMD:
#    set STRIPE_SECRET_KEY=sk_test_...
#
# 4. RUN THE WATCHER
#
#    python stripe_watcher.py /path/to/vault
#    python stripe_watcher.py /path/to/vault --interval 120
#
# 5. TEST MODE
#    Use sk_test_ keys to work with Stripe's test data.
#    Create test charges in the Stripe Dashboard:
#      Dashboard → Payments → + Create → use card 4242 4242 4242 4242
#    The watcher will pick them up on the next poll cycle.
#
# 6. WEBHOOK ALTERNATIVE (Advanced)
#    For real-time notifications instead of polling, set up a
#    Stripe webhook endpoint pointing to your server.  The watcher
#    approach is simpler and works without a public server.
#
# 7. SECURITY CHECKLIST
#    - [ ] API key is in an environment variable, NOT in source code
#    - [ ] .env / secrets files are in .gitignore
#    - [ ] Using sk_test_ keys during development
#    - [ ] processed_stripe_ids.txt is in .gitignore (contains event IDs)
#    - [ ] High-value payments (>$500) require Pending_Approval workflow
#
