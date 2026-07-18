from datetime import date

from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Holiday


async def list_holidays(session: AsyncSession, year: int | None) -> list[Holiday]:
    stmt = select(Holiday).order_by(Holiday.holiday_date)
    if year is not None:
        stmt = stmt.where(extract("year", Holiday.holiday_date) == year)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_holiday(session: AsyncSession, holiday_id: int) -> Holiday | None:
    result = await session.execute(select(Holiday).where(Holiday.id == holiday_id))
    return result.scalar_one_or_none()


async def create_holiday(session: AsyncSession, holiday_date: date, name: str) -> Holiday:
    holiday = Holiday(holiday_date=holiday_date, name=name)
    session.add(holiday)
    await session.flush()
    return holiday


async def update_holiday(
    session: AsyncSession, holiday_id: int, holiday_date: date, name: str
) -> Holiday | None:
    holiday = await get_holiday(session, holiday_id)
    if holiday is None:
        return None
    holiday.holiday_date = holiday_date
    holiday.name = name
    await session.flush()
    return holiday


async def delete_holiday(session: AsyncSession, holiday_id: int) -> bool:
    holiday = await get_holiday(session, holiday_id)
    if holiday is None:
        return False
    await session.delete(holiday)
    return True


async def is_holiday(session: AsyncSession, check_date: date) -> bool:
    result = await session.execute(select(Holiday.id).where(Holiday.holiday_date == check_date))
    return result.scalar_one_or_none() is not None
