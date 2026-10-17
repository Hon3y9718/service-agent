import asyncio
import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.chat.conversations import Conversation
from src.models.chat.conversations import Message, Sender
from src.models.auth.users import User
from src.schemas.chat import ConversationDetail, MessageIn
from src.agent.agent import stream_model
from src.db import SessionLocal, get_session as get_db
from src.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

# ---------- helpers ----------
async def get_owned_conversation(
    db: AsyncSession, conversation_id: UUID, user_id: UUID
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

async def save_agent_message(conversation_id: UUID, content: str) -> UUID:
    # Own session: do not rely on the request-scoped one while streaming
    async with SessionLocal() as db:
        msg = Message(
            id=uuid4(),
            conversation_id=conversation_id,
            sender=Sender.AGENT.value,
            content=content.strip(),
        )
        msg_id = msg.id  # read before commit
        db.add(msg)
        await db.commit()
        return msg_id


async def agent_stream(conversation_id: UUID, history: list[dict]):
    parts: list[str] = []
    saved = False
    try:
        async for token in stream_model(history):
            parts.append(token)
            yield sse("token", {"text": token})

        msg_id = await save_agent_message(conversation_id, "".join(parts))
        saved = True
        yield sse("done", {"message_id": str(msg_id)})
    except Exception:
        logger.exception("Agent stream failed for conversation %s", conversation_id)
        yield sse("error", {"detail": "The agent failed to respond."})
    finally:
        # Client disconnected or error mid-way: keep what was generated
        if not saved and parts:
            await asyncio.shield(
                save_agent_message(conversation_id, "".join(parts))
            )

# ---------- routes ----------
@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await get_owned_conversation(db, conversation_id, user.id)
    messages = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return {"conversation": conversation, "messages": messages.all()}

@router.post("/{conversation_id}")
async def send_message(
    conversation_id: UUID,
    payload: MessageIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_conversation(db, conversation_id, user.id)

    # 1. Everything coming in through the API is "user". Save it first.
    db.add(
        Message(
            conversation_id=conversation_id,
            sender=Sender.USER.value,
            content=payload.content,
        )
    )
    await db.commit()

    # 2. Build history as plain dicts so no ORM objects are touched while streaming
    rows = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    history = [
        {
            "role": "assistant" if m.sender == Sender.AGENT.value else "user",
            "content": m.content,
        }
        for m in rows.all()
    ]

    # 3. Everything going out is "agent"
    return StreamingResponse(
        agent_stream(conversation_id, history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )