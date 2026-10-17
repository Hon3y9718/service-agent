import asyncio
from collections.abc import AsyncIterator


async def stream_model(history: list[dict]) -> AsyncIterator[str]:
    last_user = next(
        (m["content"] for m in reversed(history) if m["role"] == "user"), ""
    )
    reply = f"This is a dummy agent reply. You said: {last_user}"

    for i, word in enumerate(reply.split(" ")):
        # test hook for the error path, remove before shipping
        if "__fail__" in last_user and i == 3:
            raise RuntimeError("dummy failure")
        await asyncio.sleep(0.05)  # simulate token latency
        yield word + " "