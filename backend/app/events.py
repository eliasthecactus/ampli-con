"""In-process pub/sub for Server-Sent Events."""
import asyncio
import json
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _put(self, msg: str) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client; drop

    def publish(self, event: str, data: Any) -> None:
        """Safe to call from the loop thread or from worker threads."""
        msg = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        if self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._put(msg)
        else:
            self._loop.call_soon_threadsafe(self._put, msg)


bus = EventBus()
