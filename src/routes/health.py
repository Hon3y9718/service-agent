from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session

router = APIRouter(tags=["health"])

@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
      await session.execute(text("SELECT 1"))
    except Exception:
      return {"status": "error", "db": "down"}
    return {"status": "ok", "db": "up"}