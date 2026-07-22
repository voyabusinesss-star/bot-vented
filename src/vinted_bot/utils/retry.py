"""Retry / backoff simple."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from vinted_bot.utils.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.5,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    label: str = "operation",
) -> T:
    last_exc: BaseException | None = None
    attempts = max(1, max_retries)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except exceptions as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "retry_backoff",
                label=label,
                attempt=attempt,
                max_retries=attempts,
                delay=delay,
                error=str(exc),
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
