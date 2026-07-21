from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest, LmvSnapshotUploadResponse
from app.services import lmv_snapshot_service

router = APIRouter(prefix="/lmv-snapshot", tags=["lmv-snapshot"])


@router.post("/daily-upload", response_model=LmvSnapshotUploadResponse)
async def daily_upload(
    payload: LmvSnapshotUploadRequest, session: AsyncSession = Depends(get_tenant_db)
) -> LmvSnapshotUploadResponse:
    return await lmv_snapshot_service.upsert_lmv_snapshot(session, payload)
