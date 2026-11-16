"""Короткий кеш ответов Пульса в памяти процесса.

Два одинаковых запроса подряд (подгрузка при открытии приложения + переход во вкладку)
идут в Пульс один раз: второй ждёт первый, а не запускает свой.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Hashable


class TTLCache:
    def __init__(self, max_items: int = 5000):
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Task] = {}
        self._max = max_items

    def get(self, key: Hashable, ttl: float) -> Any | None:
        hit = self._data.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        return None

    def put(self, key: Hashable, value: Any) -> None:
        if len(self._data) >= self._max:
            # самые старые записи — вон, чтобы память на дешёвом сервере не росла
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1][0])[: self._max // 5]:
                self._data.pop(k, None)
        self._data[key] = (time.time(), value)

    def drop(self, pred: Callable[[Hashable], bool]) -> None:
        for k in [k for k in self._data if pred(k)]:
            self._data.pop(k, None)

    async def fetch(self, key: Hashable, ttl: float, load: Callable[[], Awaitable[Any]],
                    keep: Callable[[Any], bool] = lambda v: True) -> Any:
        """Значение из кеша, если оно моложе ttl; иначе — один общий запрос load()."""
        hit = self.get(key, ttl)
        if hit is not None:
            return hit
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(load())
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        value = await asyncio.shield(task)
        if keep(value):
            self.put(key, value)
        return value
