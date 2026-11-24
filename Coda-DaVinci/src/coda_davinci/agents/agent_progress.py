"""Bound idle model turns while allowing background work to finish."""


class IdleTurnGuard:
    def __init__(self, limit=3):
        self.limit = limit
        self.count = 0

    def observe(self, *, has_calls, has_active_jobs, text):
        self.count = 0 if has_calls or has_active_jobs else self.count + 1
        if self.count < self.limit:
            return None
        return {
            "status": "no_progress",
            "message": f"Stopped after {self.count} consecutive responses without tool calls or active background jobs.",
            "idle_turns": self.count,
            "last_agent_text": (text or "")[-2000:],
        }
