"""Dedicated recurring-billing worker entry point."""
import logging
import time
from os import getenv

from .billing_cycles import refresh_overdue_invoices, run_due_billing
from .database import SessionLocal

logging.basicConfig(level=getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("billing-scheduler")


def main() -> None:
    interval = max(30, int(getenv("BSS_BILLING_INTERVAL_SECONDS", "300")))
    while True:
        session = SessionLocal()
        try:
            result = run_due_billing(session)
            overdue = refresh_overdue_invoices(session)
            logger.info("billing cycle result=%s overdue_updated=%s", result, overdue)
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("billing cycle failed")
        finally:
            session.close()
        time.sleep(interval)


if __name__ == "__main__":
    main()
