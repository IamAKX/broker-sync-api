from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import DuplicateHolidayDateError, HolidayNotFoundError
from app.repositories.holiday_repo import (
    create_holiday,
    delete_holiday,
    list_holidays,
    update_holiday,
)
from app.schemas.holidays import HolidayOut


def _to_out(holiday) -> HolidayOut:
    return HolidayOut(id=holiday.id, holiday_date=holiday.holiday_date, name=holiday.name)


async def get_holidays(session: AsyncSession, year: int | None) -> list[HolidayOut]:
    rows = await list_holidays(session, year)
    return [_to_out(h) for h in rows]


async def add_holiday(session: AsyncSession, holiday_date: date, name: str) -> HolidayOut:
    try:
        holiday = await create_holiday(session, holiday_date, name)
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateHolidayDateError(f"A holiday already exists on {holiday_date}") from exc
    await session.commit()
    return _to_out(holiday)


async def edit_holiday(
    session: AsyncSession, holiday_id: int, holiday_date: date, name: str
) -> HolidayOut:
    try:
        holiday = await update_holiday(session, holiday_id, holiday_date, name)
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateHolidayDateError(f"A holiday already exists on {holiday_date}") from exc
    if holiday is None:
        raise HolidayNotFoundError(f"Holiday {holiday_id} not found")
    await session.commit()
    return _to_out(holiday)


async def remove_holiday(session: AsyncSession, holiday_id: int) -> None:
    deleted = await delete_holiday(session, holiday_id)
    if not deleted:
        raise HolidayNotFoundError(f"Holiday {holiday_id} not found")
    await session.commit()
