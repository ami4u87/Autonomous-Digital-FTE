import time
import logging
from pathlib import Path
from abc import ABC, abstractmethod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


class BaseWatcher(ABC):
    """Base class for all vault watchers.

    Subclasses implement check_for_updates() to poll an external source
    and create_action_file() to write a task into Needs_Action/.
    """

    def __init__(self, vault_path: str, check_interval: int = 60) -> None:
        self.vault_path = Path(vault_path)
        self.needs_action = self.vault_path / "Needs_Action"
        self.needs_action.mkdir(parents=True, exist_ok=True)
        self.check_interval = check_interval
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def check_for_updates(self) -> list:
        """Poll the external source and return a list of new items to act on."""

    @abstractmethod
    def create_action_file(self, item) -> Path:
        """Write a single item as a markdown task file in Needs_Action/.

        Returns the Path of the created file.
        """

    def run(self) -> None:
        """Infinite polling loop with error handling and graceful shutdown."""
        self.logger.info(
            "Starting %s — polling every %ds",
            self.__class__.__name__,
            self.check_interval,
        )
        try:
            while True:
                try:
                    updates = self.check_for_updates()
                    if updates:
                        self.logger.info("Found %d new item(s)", len(updates))
                        for item in updates:
                            path = self.create_action_file(item)
                            self.logger.info("Created action file: %s", path.name)
                    else:
                        self.logger.debug("No new updates")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.logger.exception("Error during update check")

                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.logger.info("Shutting down %s gracefully", self.__class__.__name__)
