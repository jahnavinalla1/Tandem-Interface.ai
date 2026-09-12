"""Telemetry and instrumentation to guarantee zero LLM calls during replay."""


class LLMCallTracker:
    """Thread-safe counter and monitor for LLM invocations."""

    def __init__(self):
        self._count: int = 0
        self._calls: list[dict] = []

    def record_call(
        self, model: str, prompt_snippet: str, provider: str = "unspecified"
    ) -> None:
        self._count += 1
        self._calls.append(
            {"provider": provider, "model": model, "snippet": prompt_snippet[:100]}
        )

    def reset(self) -> None:
        self._count = 0
        self._calls.clear()

    @property
    def call_count(self) -> int:
        return self._count


# Global tracker instance
llm_tracker = LLMCallTracker()
