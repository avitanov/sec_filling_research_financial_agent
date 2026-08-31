"""Provide small Strands plugins used by the research agents."""

from typing import TYPE_CHECKING

from botocore.exceptions import EventStreamError
from botocore.exceptions import ReadTimeoutError as BotocoreReadTimeoutError
from strands import ModelRetryStrategy
from strands.hooks import AfterModelCallEvent, HookOrder
from strands.plugins import Plugin
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

if TYPE_CHECKING:
    from strands import Agent


class ProviderRetryStrategy(ModelRetryStrategy):
    """Retry transient provider failures, including Bedrock stream errors."""

    def is_retryable(self, exception: Exception) -> bool:
        """Return whether a failed model call should be retried."""
        return super().is_retryable(exception) or isinstance(
            exception,
            (
                EventStreamError,
                BotocoreReadTimeoutError,
                Urllib3ReadTimeoutError,
            ),
        )


class RetryTrackingPlugin(Plugin):
    """Count model retries scheduled by the Strands retry strategy."""

    name = "retry-tracking"

    def __init__(self) -> None:
        super().__init__()
        self.count = 0
        self.events: list[dict[str, str | int | None]] = []
        self._attempt = 0

    def _record_retry(self, event: AfterModelCallEvent) -> None:
        self._attempt += 1
        if event.retry:
            self.count += 1
            self.events.append(
                {
                    "attempt": self._attempt,
                    "exception": str(event.exception) if event.exception else None,
                    "stop_reason": (
                        str(event.stop_response.stop_reason)
                        if event.stop_response
                        else None
                    ),
                }
            )

    def reset(self) -> None:
        """Reset the retry count before a new isolated question run."""
        self.count = 0
        self.events = []
        self._attempt = 0

    def init_agent(self, agent: "Agent") -> None:
        """Register retry tracking after the built-in retry strategy.

        Args:
            agent: Strands agent receiving the plugin.
        """
        agent.add_hook(
            self._record_retry,
            AfterModelCallEvent,
            order=HookOrder.SDK_LAST,
        )
