"""Powers Inception > Data & Settings' "Fetch from Equal Solution" button
(screens/inception_settings.py in the desktop client) — a single click
that:
  1. Finds the last date data is available (MAX(EodBar.trade_date)).
  2. Fetches everything NFOFUT has published since then, through today,
     from Equal Solution (the vendor), using server-side-only credentials
     (app.core.config.settings.eqldata_*) — the desktop client never sends
     or receives them, see app/routers/inception.py's endpoint and that
     repo's screens/inception_settings.py docstring for why (a distributed
     desktop app is the wrong place to hold a shared vendor account's real
     password).
  3. Upserts it straight into the central Instrument/EodBar/TradingCalendar
     tables — the same shared dataset every tenant's Inception feature
     reads from (see docs/INCEPTION_DATA.md).

Deliberately NOT the full from-2000 historical backfill the sibling
inception-stock-data repo's eod_backfill.py script does (that stays a
manual, offline, resumable-over-hours operation run directly against the
vendor's 500-symbol/366-day-per-request caps for a dataset this size) —
this is the small "top up what's missing since last time" incremental
case, expected to usually be a handful of days and comfortably fit in one
or two vendor requests. Still chunks by both documented caps defensively
(_chunk_dates/_chunk_list) in case nobody's clicked this in a while.
Raises VendorInitialSyncRequiredError instead of attempting a decades-long
fetch if EodBar is completely empty — see that exception's docstring.

No background-job infrastructure exists in this backend (no Celery/
APScheduler) — this runs synchronously inside the request handler,
offloading every blocking `requests` call (app.services.eqldata_client) to
a thread via asyncio.to_thread so the event loop isn't blocked meanwhile.
The desktop client uses a generous timeout and its own background QThread
so this doesn't freeze its UI either — see that repo's
api/inception_api.sync_vendor_data / screens/inception_settings.py.
"""

import asyncio
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions import VendorInitialSyncRequiredError, VendorNotConfiguredError
from app.repositories import instrument_repo
from app.schemas.inception import VendorSyncResponse
from app.services import eqldata_client

EXCHANGE = "NFOFUT"
_MAX_RANGE_DAYS = 366  # vendor's documented per-request cap
_MAX_SYMBOLS_PER_REQUEST = 500  # vendor's documented per-request cap
_ROLL_SUFFIXES = ("_I", "_II")


async def sync_nfofut_from_vendor(session: AsyncSession) -> VendorSyncResponse:
    if not settings.eqldata_email or not settings.eqldata_password:
        raise VendorNotConfiguredError(
            "Equal Solution credentials aren't configured on this server "
            "(EQLDATA_EMAIL/EQLDATA_PASSWORD) — see .env.example."
        )

    last_available = await instrument_repo.get_latest_trade_date(session)
    if last_available is None:
        raise VendorInitialSyncRequiredError(
            "No Inception data loaded yet — this button only fetches what's "
            "missing since the last sync. Run the initial historical load "
            "first (see docs/INCEPTION_DATA.md)."
        )

    today = date.today()
    date_from = last_available + timedelta(days=1)
    if date_from > today:
        return VendorSyncResponse(
            status="already_up_to_date", exchange=EXCHANGE, date_from=date_from, date_to=today,
            last_available_before=last_available, last_available_after=last_available,
            instruments_added=0, bars_written=0,
        )

    token = await asyncio.to_thread(
        eqldata_client.generate_auth_token,
        settings.eqldata_email, settings.eqldata_password, settings.eqldata_base_url,
    )
    all_instruments = await asyncio.to_thread(
        eqldata_client.get_instrument_list, token, settings.eqldata_base_url,
    )
    symbols = _continuous_futures(all_instruments.get(EXCHANGE, []))

    instrument_by_symbol = await instrument_repo.get_or_create_instruments(session, symbols)
    # A brand-new instrument this sync just created has no bounds set yet
    # (update_instrument_date_bounds below is what sets them) — a
    # previously-loaded one already has real ones from the historical load.
    instruments_added = sum(
        1 for i in instrument_by_symbol.values()
        if i.first_traded_date is None and i.last_traded_date is None
    )

    bar_rows: list[dict] = []
    seen_dates: set[date] = set()
    dates_by_instrument: dict[int, list[date]] = {}

    for symbol_batch in _chunk_list(symbols, _MAX_SYMBOLS_PER_REQUEST):
        prefixed = [f"{EXCHANGE}:{sym}" for sym in symbol_batch]
        for chunk_start, chunk_end in _chunk_dates(date_from, today, _MAX_RANGE_DAYS):
            csv_rows = await asyncio.to_thread(
                eqldata_client.fetch_eod_range_rows, token, prefixed,
                chunk_start.isoformat(), chunk_end.isoformat(), settings.eqldata_base_url,
            )
            for raw in csv_rows:
                parsed = _parse_row(raw, instrument_by_symbol)
                if parsed is None:
                    continue
                bar_rows.append(parsed["bar"])
                seen_dates.add(parsed["trade_date"])
                dates_by_instrument.setdefault(parsed["instrument_id"], []).append(parsed["trade_date"])

    bars_written = await instrument_repo.bulk_upsert_eod_bars(session, bar_rows)
    await instrument_repo.bulk_upsert_trading_calendar(session, seen_dates)
    await instrument_repo.update_instrument_date_bounds(
        session, _resolve_date_bounds(instrument_by_symbol, dates_by_instrument),
    )
    await session.commit()

    last_available_after = max(seen_dates) if seen_dates else last_available

    return VendorSyncResponse(
        status="ok", exchange=EXCHANGE, date_from=date_from, date_to=today,
        last_available_before=last_available, last_available_after=last_available_after,
        instruments_added=instruments_added, bars_written=bars_written,
    )


def _continuous_futures(items: list[str]) -> list[str]:
    """items: ["NFOFUT:ABB_I", "NFOFUT:ABB26AUGFUT", ...] — strips the
    "EXCHANGE:" prefix and keeps only continuous roll symbols (_I/_II).
    Matches inception-stock-data/eod_backfill.py's own
    filter_continuous_futures: NFOFUT's instrument list mixes short-lived
    dated expiry contracts (~3 months of history) with continuous roll
    symbols (full history) — per Equal Solution support, only _I/_II carry
    the full history a historical dataset needs."""
    out = []
    for item in items:
        symbol = item.split(":", 1)[1] if ":" in item else item
        if symbol.endswith(_ROLL_SUFFIXES):
            out.append(symbol)
    return out


def _parse_row(raw: dict, instrument_by_symbol: dict) -> dict | None:
    """One EOD_RANGE.csv row (INSTRUMENT,DTYYYYMMDD,OPEN,HIGH,LOW,CLOSE,VOL,
    OPENINT — see docs/INCEPTION_DATA.md §3 for the same shape the offline
    loader reads) into an EodBar-shaped dict, or None for a row this sync
    can't use (an instrument this batch didn't request — shouldn't happen,
    but never crash the whole sync over one row — or an unparseable date/
    number)."""
    instrument = instrument_by_symbol.get(str(raw.get("INSTRUMENT", "")).strip())
    if instrument is None:
        return None
    try:
        trade_date = datetime.strptime(str(raw.get("DTYYYYMMDD", "")), "%Y%m%d").date()
        open_interest_raw = raw.get("OPENINT")
        bar = {
            "instrument_id": instrument.id,
            "trade_date": trade_date,
            "open": float(raw["OPEN"]),
            "high": float(raw["HIGH"]),
            "low": float(raw["LOW"]),
            "close": float(raw["CLOSE"]),
            "volume": int(float(raw["VOL"])),
            "open_interest": int(float(open_interest_raw)) if open_interest_raw not in (None, "") else None,
            "source": "eqldata",
        }
    except (KeyError, ValueError, TypeError):
        return None
    return {"bar": bar, "trade_date": trade_date, "instrument_id": instrument.id}


def _resolve_date_bounds(instrument_by_symbol: dict, dates_by_instrument: dict[int, list[date]]) -> list[dict]:
    """[{"id", "first_traded_date", "last_traded_date"}, ...] for every
    instrument that got at least one new bar this sync — first_traded_date
    is only ever set the FIRST time (a brand-new instrument this sync
    discovered has none yet); an already-loaded instrument's is left
    exactly as the historical load set it, never moved. last_traded_date
    always advances to the max of what it already was and what this sync
    just wrote (in practice always the new value, since this is always an
    incremental fetch strictly after the previous last_traded_date — the
    max() is defensive, not load-bearing)."""
    bounds = []
    for instrument in instrument_by_symbol.values():
        dates = dates_by_instrument.get(instrument.id)
        if not dates:
            continue
        new_first = instrument.first_traded_date or min(dates)
        new_last = max(instrument.last_traded_date, max(dates)) if instrument.last_traded_date else max(dates)
        bounds.append({"id": instrument.id, "first_traded_date": new_first, "last_traded_date": new_last})
    return bounds


def _chunk_dates(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _chunk_list(seq: list, size: int) -> list[list]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]
