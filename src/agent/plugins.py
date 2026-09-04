"""Provide small Strands plugins used by the research agents."""

from typing import TYPE_CHECKING, Any, Callable

from botocore.exceptions import EventStreamError
from botocore.exceptions import ReadTimeoutError as BotocoreReadTimeoutError
from strands import ModelRetryStrategy
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookOrder,
)
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


class ProgressPlugin(Plugin):
    """Report tool activity to a callback while an agent stage is running."""

    name = "progress"

    def __init__(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        super().__init__()
        self._on_event = on_event
        self._stage: str | None = None

    def _report_tool_call(self, event: BeforeToolCallEvent) -> None:
        self._on_event(
            {
                "type": "tool_call",
                "stage": self._stage,
                "tool": event.tool_use.get("name"),
                "arguments": event.tool_use.get("input") or {},
            }
        )

    def _report_tool_result(self, event: AfterToolCallEvent) -> None:
        self._on_event(
            {
                "type": "tool_result",
                "stage": self._stage,
                "tool": event.tool_use.get("name"),
                "status": (event.result or {}).get("status", "unknown"),
                "content": (event.result or {}).get("content", []),
                "duration": event.duration,
            }
        )

    def set_stage(self, stage: str | None) -> None:
        """Label subsequent tool events with the stage that produced them."""
        self._stage = stage

    def init_agent(self, agent: "Agent") -> None:
        """Register tool progress reporting on one agent.

        Args:
            agent: Strands agent receiving the plugin.
        """
        agent.add_hook(self._report_tool_call, BeforeToolCallEvent)
        agent.add_hook(self._report_tool_result, AfterToolCallEvent)
