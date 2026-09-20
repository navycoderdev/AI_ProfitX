from collections.abc import Callable
from time import sleep
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def retry(attempts: int = 3, delay_seconds: float = 0.25,
          retry_on: tuple[type[Exception], ...] = (ConnectionError, TimeoutError)):
    """Small deterministic retry wrapper for transient I/O boundaries."""
    if attempts < 1:
        raise ValueError("attempts must be at least one")

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except retry_on:
                    if attempt == attempts - 1:
                        raise
                    sleep(delay_seconds * (2 ** attempt))
            raise RuntimeError("unreachable")
        return wrapped
    return decorator
