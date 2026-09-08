"""Política async bootstrap: coroutines, async iteration and cancellation."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable


class PitonCancellation(asyncio.CancelledError):
    pass


class CancellationToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def check(self) -> None:
        if self.cancelled:
            raise PitonCancellation()


def run_coroutine(awaitable: Awaitable[Any]) -> Any:
    return asyncio.run(awaitable)


async def async_collect(iterable: AsyncIterator[Any]) -> list[Any]:
    result = []
    async for item in iterable:
        result.append(item)
    return result


async def async_with(manager: Any, body: Callable[[Any], Awaitable[Any]]) -> Any:
    resource = await manager.__aenter__()
    try:
        return await body(resource)
    except BaseException as error:
        if await manager.__aexit__(type(error), error, error.__traceback__):
            return None
        raise
    else:
        await manager.__aexit__(None, None, None)
