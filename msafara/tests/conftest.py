import json
import os
from types import SimpleNamespace

import pytest

os.environ["MSAFARA_DRY_RUN"] = "1"
os.environ.pop("MSAFARA_LIVE_DISPATCH", None)
os.environ.pop("LLM_API_KEY", None)

from msafara import agents, graph, kb, llm, simulator  # noqa: E402

TEXT = "Need a 4-star in Jaipur for 12 people, 14-17 March 2027, budget around INR 3.2 lakh"


@pytest.fixture(autouse=True)
def _clean():
    llm.set_client(None)
    llm.force_mock(False)
    graph.set_reply_provider(None)
    yield
    llm.set_client(None)
    llm.force_mock(False)
    graph.set_reply_provider(None)


@pytest.fixture
def g():
    return graph.build_graph()


@pytest.fixture
def sim():
    graph.set_reply_provider(simulator.make_provider())


def approve_all(gate):
    if gate["gate"] == "escalation":
        return {"resolutions": {i["service_id"]: {"action": "drop"} for i in gate["items"]}}
    return {"action": "approve"}


class Stub:
    """Scripted stand-in for ApiClient(): script(system, user) -> text."""
    def __init__(self, script):
        self.script, self.calls = script, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, model, max_tokens, system, messages):
        self.calls.append((system, messages[-1]["content"]))
        text = self.script(system, messages[-1]["content"], len(self.calls))
        return SimpleNamespace(content=[SimpleNamespace(text=text)])
