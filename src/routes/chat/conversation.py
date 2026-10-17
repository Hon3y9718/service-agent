from uuid import UUID, uuid4
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.chat.conversations import Conversation
from src.models.auth.users import User
from src.db import get_session as get_db
from src.deps import get_current_user

router = APIRouter(prefix="/sessions", tags=["chat"])

class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime

class ConversationCreate(BaseModel):
    title: str

@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    data: ConversationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = Conversation(id=uuid4(), user_id=user.id, title=data.title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation

@router.get("/{session_id}", response_model=ConversationOut)
async def get_session_by_id(
    session_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Ownership check inside the query: one round trip, same 404 for both cases
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == session_id,
            Conversation.user_id == user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return conversation

@router.get("", response_model=list[ConversationOut])
async def list_sessions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    return result.all()