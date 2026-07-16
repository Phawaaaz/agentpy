"""Shared retry helper for transient provider failures.

Rate limits and dropped connections are normal weather for a model API, not
programming errors -- a call that fails with one should be retried with
exponential backoff before the failure is allowed to surface. This lives in
providers/ (not the orchestrator) so the loop stays provider-blind: each
provider wraps its own SDK call with the SDK's own exception types, and
nothing upstream knows retrying happens at all.

Both vendor SDKs also retry internally (their clients default to a couple of
quick attempts); this layer catches what still escapes that, with longer
backoff, so a brief rate-limit burst doesn't kill a whole agent run.
"""

import time
from typing import Callable, TypeVar

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY_S = 1.0


def call_with_retries(
    call: Callable[[], T],
    retryable: tuple[type[BaseException], ...],
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay_s: float = DEFAULT_BASE_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `call`, retrying on the given exception types with exponential
    backoff (base, 2*base, 4*base, ...). The final attempt's exception is
    re-raised unchanged so callers still see the SDK's real error. `sleep`
    is injectable so tests never actually wait."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except retryable:
            if attempt == attempts:
                raise
            sleep(base_delay_s * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")  # loop always returns or raises
