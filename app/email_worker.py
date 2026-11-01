"""Local always-on mailbox poller.

Run separately from Streamlit:
    python -m app.email_worker

This process keeps checking the coach mailbox while the computer is awake.
It is intentionally single-process; do not start multiple workers against the
same mailbox/database.
"""
import logging
import os
import signal
import time

from app.config import settings
from app.email_workflow import sync_and_process_emails

logging.basicConfig(
    level=os.getenv("EMAIL_WORKER_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("email_worker")
_running = True


def _stop(_signum, _frame):
    global _running
    _running = False


def main():
    if not settings.email_enabled:
        raise SystemExit("EMAIL_ENABLED must be true before starting the email worker.")

    interval = max(30, int(os.getenv("EMAIL_POLL_INTERVAL_SECONDS", "60")))
    limit = max(1, int(os.getenv("EMAIL_POLL_BATCH_SIZE", "50")))
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    logger.info("Email worker started; polling interval=%ss", interval)
    while _running:
        started = time.monotonic()
        try:
            result = sync_and_process_emails(limit=limit)
            sync = result.get("sync", {})
            if not sync.get("success", False):
                logger.error("Mailbox sync failed: %s", sync.get("error", "unknown error"))
            else:
                processed = result.get("processed", [])
                deliveries = result.get("deliveries", [])
                logger.info(
                    "Mailbox cycle complete: inserted=%s processed=%s sent=%s pending=%s",
                    sync.get("inserted", 0),
                    len(processed),
                    sum(1 for item in deliveries if item.get("status") == "sent"),
                    sum(1 for item in deliveries if item.get("status") != "sent"),
                )
        except Exception:
            # Do not log message bodies, credentials, or full settings.
            logger.exception("Mailbox cycle failed")
        remaining = max(1, interval - int(time.monotonic() - started))
        for _ in range(remaining):
            if not _running:
                break
            time.sleep(1)

    logger.info("Email worker stopped")


if __name__ == "__main__":
    main()
