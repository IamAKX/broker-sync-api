"""Automated daily "Fetch from Equal Solution" — see screens.
inception_settings._start_vendor_sync in the client repo for the manual
button this replicates server-side. Run by the brokersync-vendor-fetch
systemd timer (5:30pm IST / 12:00pm UTC daily) — see docs/AWS_DEPLOYMENT.md
for the timer/service setup.

Calls services.inception_vendor_sync_service.sync_nfofut_from_vendor
directly (no HTTP, no JWT — same "standalone script owns its own session"
pattern as scripts/backfill_lmv_wide.py), with no explicit email/password/
exchange, so it always uses this server's own EQLDATA_EMAIL/EQLDATA_PASSWORD
(.env) rather than whatever the desktop's own UI fields happen to hold —
this script has no UI, nothing to read those from.

Global/central dataset (Instrument/EodBar/TradingCalendar) — no tenant
schema involved, same as the vendor-sync HTTP endpoint's own scope.

Note: this bypasses the HTTP router's own cache.invalidate_tag() call, so
gunicorn's per-worker response cache can serve stale Inception reads for up
to its existing TTL (900s) after this runs — the same accepted per-worker-
cache tradeoff app/core/cache.py already documents for the HTTP endpoint,
just across all 3 workers here instead of up to 2.

Run manually:  .venv/bin/python scripts/vendor_fetch_cron.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, ".")

from app.db.central_session import CentralSessionLocal  # noqa: E402
from app.exceptions import VendorInitialSyncRequiredError, VendorNotConfiguredError  # noqa: E402
from app.services.inception_vendor_sync_service import sync_nfofut_from_vendor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vendor_fetch_cron")


async def main() -> int:
    async with CentralSessionLocal() as session:
        try:
            result = await sync_nfofut_from_vendor(session)
        except VendorNotConfiguredError as exc:
            log.error("Vendor credentials not configured: %s", exc)
            return 1
        except VendorInitialSyncRequiredError as exc:
            # First-time-only precondition — nothing loaded yet at all, so
            # there's no "since last sync" delta to fetch. Not an error:
            # the initial historical load is a separate, one-time,
            # manually-run step (see docs/INCEPTION_DATA.md), and this
            # timer will start succeeding on its own the day after that
            # runs, with no change needed here.
            log.warning("Skipping — no initial Inception load yet: %s", exc)
            return 0
        except Exception:
            log.exception("Vendor fetch failed")
            return 1

    log.info(
        "Vendor fetch %s — exchange=%s date_from=%s date_to=%s "
        "instruments_added=%d bars_written=%d (last_available %s -> %s)",
        result.status, result.exchange, result.date_from, result.date_to,
        result.instruments_added, result.bars_written,
        result.last_available_before, result.last_available_after,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
