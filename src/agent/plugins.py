"""Provide small Strands plugins used by the research agents."""

from typing import TYPE_CHECKING

from strands.hooks import AfterModelCallEvent, HookOrder
from strands.plugins import Plugin

if TYPE_CHECKING:
    from strands import Agent


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
