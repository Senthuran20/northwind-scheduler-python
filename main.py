"""Command-line entry point.

    python main.py "Find an hour for Priya and Sam on Tuesday afternoon"

Uses a real model if LLM_BASE_URL and LLM_MODEL are set, otherwise a
scripted demo model.
"""

import os
import sys

from scheduler.agent import SchedulingAgent
from scheduler.model import FakeModel, recommendation, tool_call
from scheduler.store import Store


def demo_model() -> FakeModel:
    return FakeModel([
        tool_call(
            "find_free_slots",
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            earliest="12:00",
        ),
        recommendation(
            attendees=["priya", "sam"],
            date="2026-11-03",
            duration_minutes=60,
            start="13:00",
            end="14:00",
        ),
    ])


def main() -> None:
    request = " ".join(sys.argv[1:]) or "Find an hour for Priya and Sam on Tuesday afternoon"
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_MODEL"):
        from scheduler.real_model import OpenAICompatibleModel
        model = OpenAICompatibleModel()
    else:
        model = demo_model()
    agent = SchedulingAgent(model, Store.load())
    print(agent.run(request))


if __name__ == "__main__":
    main()
