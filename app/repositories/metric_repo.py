from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Metric


async def bulk_get_or_create_metrics(
    session: AsyncSession, metric_names_with_types: dict[str, str]
) -> dict[str, int]:
    """metric_names_with_types: {metric_name: data_type ('number'|'text')}.
    Returns {metric_name: metric_id}. Same batched get-or-create shape as stock_repo —
    auto-registers any metric name not seen before for this tenant.
    """
    names = list(metric_names_with_types.keys())
    if not names:
        return {}

    existing = await session.execute(select(Metric.id, Metric.name).where(Metric.name.in_(names)))
    name_to_id = {name: metric_id for metric_id, name in existing.all()}

    missing_names = [name for name in names if name not in name_to_id]
    if missing_names:
        stmt = insert(Metric).values(
            [{"name": name, "data_type": metric_names_with_types[name]} for name in missing_names]
        )
        await session.execute(stmt)
        refreshed = await session.execute(select(Metric.id, Metric.name).where(Metric.name.in_(missing_names)))
        name_to_id.update({name: metric_id for metric_id, name in refreshed.all()})

    return name_to_id


async def list_metrics(session: AsyncSession) -> list[Metric]:
    result = await session.execute(select(Metric).order_by(Metric.name))
    return list(result.scalars().all())


async def get_metric_by_name(session: AsyncSession, name: str) -> Metric | None:
    result = await session.execute(select(Metric).where(Metric.name == name))
    return result.scalar_one_or_none()
