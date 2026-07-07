"""Tiny async pub/sub used to wire the Streamer and Worker agents together.

Topics that flow over the bus today:

  • ``worker_task``       — Streamer hands a chunk of work to the Worker
  • ``worker_done``       — Worker reports a finished task back
  • ``interrupt_worker``  — Streamer (or user) tells the Worker to stop
  • ``mood``              — Streamer announces an emotional state change
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    async def publish(self, topic: str, payload: Any) -> None:
        for q in list(self._subs.get(topic, [])):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    def subscribe(self, topic: str, maxsize: int = 256) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs[topic].append(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        if q in self._subs.get(topic, []):
            self._subs[topic].remove(q)

    async def stream(self, topic: str) -> AsyncIterator[Any]:
        q = self.subscribe(topic)
        try:
            while True:
                yield await q.get()
        finally:
            self.unsubscribe(topic, q)
