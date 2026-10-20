"""LLM access layer.

* One chokepoint: `structured_call(agent, user, schema)` -> validated Pydantic object.
  Output is parsed as JSON, validated against the schema, and retried once with the
  validation error fed back. Failure raises LLMOutputError (callers fall back / escalate).
* Every backend is configured from the environment, so no provider is hard-coded here:
  - Primary backend: LLM_API_KEY + LLM_API_URL (a messages-style endpoint: JSON body with
    model, max_tokens, system, messages; response with content[].text). Extra request headers
    the endpoint needs go in LLM_API_HEADERS as a JSON object. Model id: MSAFARA_MODEL.
  - Local backend: MSAFARA_LLM_PROVIDER=local, a model server on your own machine (LOCAL_LLM_HOST optional).
  - Hosted backend: MSAFARA_LLM_KEY + MSAFARA_HOSTED_URL (a generateContent-style endpoint; may
    contain {model} and {key}) + MSAFARA_HOSTED_MODEL. If the URL has no {key}, the key is sent in
    the header named by MSAFARA_HOSTED_KEY_HEADER (default x-api-key).
* No backend configured (or an incomplete one) -> `mock_mode()` is True and agents use their
  rule-based paths.
* `set_client()` lets tests inject a stub with the same `.messages.create` surface.
* Prompts are versioned files in msafara/prompts/<agent>_v<N>.md. `active.json` picks
  the version; env MSAFARA_PROMPT_<AGENT>=v2 overrides it per run (used by evals).
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

PROMPTS = Path(__file__).parent / "prompts"
# Placeholder model id meaning "whatever model the active backend is configured with".
DEFAULT_MODEL = "primary-default"
FREE_TIER_MODEL = "hosted-default"
T = TypeVar("T", bound=BaseModel)

_client = None
_forced_mock = False


class LLMOutputError(Exception):
    pass


def set_client(client):
    """Inject a client (tests). Pass None to reset to env-based behaviour."""
    global _client
    _client = client


def force_mock(flag: bool):
    global _forced_mock
    _forced_mock = flag


def _provider() -> str:
    return (os.environ.get("MSAFARA_LLM_PROVIDER") or os.environ.get("MSAFARA_BACKEND") or "").lower()


def _primary_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


def _primary_url() -> str:
    return os.environ.get("LLM_API_URL", "").strip()


def _primary_ready() -> bool:
    return bool(_primary_key() and _primary_url())


def _is_default_family(model: str) -> bool:
    """True when `model` is a placeholder id, to be swapped for the backend's configured model."""
    return model in (DEFAULT_MODEL, FREE_TIER_MODEL) or model.split("-")[0] == DEFAULT_MODEL.split("-")[0]


def _llm_key() -> str:
    return os.environ.get("MSAFARA_LLM_KEY", "")


def _hosted_model() -> str:
    m = os.environ.get("MSAFARA_HOSTED_MODEL") or os.environ.get("MSAFARA_MODEL") or ""
    return m if m and not _is_default_family(m) else ""


def _hosted_ready() -> bool:
    return bool(_llm_key() and os.environ.get("MSAFARA_HOSTED_URL") and _hosted_model())


def get_client(judge: bool = False):
    """judge=True ignores MSAFARA_RULES_ONLY, so the eval judge can grade a rules-only run with the same model."""
    if _forced_mock:
        return None
    if os.environ.get("MSAFARA_RULES_ONLY") == "1" and not judge:
        return None
    if _client is not None:
        return _client
    if _primary_ready():
        return ApiClient()
    if _provider() == "local":
        return LocalClient()
    if _hosted_ready():
        return HostedClient()
    return None


class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text, usage=None):
        self.content = [_Block(text)]
        self.usage = usage or {}


def _post_json(url: str, body: dict, headers: dict, timeout: int, attempts: int = 5) -> dict:
    """POST with backoff on rate limits / overload / transient server errors (429, 500, 502, 503, 529)."""
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode()
    for i in range(attempts):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 529) or i == attempts - 1:
                raise
            wait = float(e.headers.get("retry-after") or 0) or min(30.0, 2.0 * 2 ** i)
            time.sleep(wait)
    raise LLMOutputError("unreachable")


class ApiClient:
    """Primary backend: a messages-style HTTP endpoint configured entirely from the environment."""

    def __init__(self):
        self.key = _primary_key()
        self.url = _primary_url()
        try:
            self.extra_headers = json.loads(os.environ.get("LLM_API_HEADERS") or "{}")
        except ValueError:
            self.extra_headers = {}
        self.messages = self

    def create(self, model, max_tokens, system, messages, **_):
        if _is_default_family(model):
            model = os.environ.get("MSAFARA_MODEL", "")
        if not model or _is_default_family(model):
            raise LLMOutputError("MSAFARA_MODEL is not set for the primary backend")
        headers = {"x-api-key": self.key, **{str(k): str(v) for k, v in self.extra_headers.items()}}
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": list(messages)}
        try:
            data = _post_json(self.url, body, headers, timeout=120)
        except LLMOutputError:
            raise
        except Exception as e:  # network or HTTP error: let callers fall back
            raise LLMOutputError(f"primary backend request failed: {str(e)[:200]}") from e
        text = "".join(b.get("text", "") for b in (data.get("content") or []) if isinstance(b, dict))
        u = data.get("usage") or {}
        return _Resp(text, dict(input=u.get("input_tokens", 0), output=u.get("output_tokens", 0)))


class LocalClient:
    """Fully free, no-signup backend: a model server running on your own machine (chat endpoint at /api/chat).

    MSAFARA_LLM_PROVIDER=local, MSAFARA_MODEL=<the local model's tag>, LOCAL_LLM_HOST optional.
    Small local models are weaker than hosted ones; label results with the model tag.
    """
    default_model = "local-model"

    def __init__(self):
        self.host = os.environ.get("LOCAL_LLM_HOST", "http://localhost:11434").rstrip("/")
        self.messages = self

    def create(self, model, max_tokens, system, messages, **_):
        import urllib.request
        if _is_default_family(model):
            model = self.default_model
        body = {"model": model, "stream": False, "format": "json",
                "options": {"temperature": 0, "num_predict": max_tokens},
                "messages": [{"role": "system", "content": system}] + list(messages)}
        req = urllib.request.Request(self.host + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
        return _Resp(data.get("message", {}).get("content", ""),
                     dict(input=data.get("prompt_eval_count", 0), output=data.get("eval_count", 0)))


class HostedClient:
    """Free-tier hosted backend (generateContent-style endpoint), configured from the environment.

    Results from this backend must be labelled with its own model id, never as the default model.
    """
    def __init__(self):
        self.key = _llm_key()
        self.url_template = os.environ.get("MSAFARA_HOSTED_URL", "")
        self.key_header = os.environ.get("MSAFARA_HOSTED_KEY_HEADER", "x-api-key")
        self.messages = self

    def create(self, model, max_tokens, system, messages, **_):
        import urllib.parse
        import urllib.request
        if _is_default_family(model):
            model = _hosted_model()
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0,
                                 "responseMimeType": "application/json"},
        }
        headers = {"Content-Type": "application/json"}
        url = self.url_template.replace("{model}", urllib.parse.quote(model, safe=""))
        if "{key}" in url:
            url = url.replace("{key}", urllib.parse.quote(self.key, safe=""))
        else:
            headers[self.key_header] = self.key
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        u = data.get("usageMetadata") or {}
        return _Resp("".join(p.get("text", "") for p in parts),
                     dict(input=u.get("promptTokenCount", 0), output=u.get("candidatesTokenCount", 0)))


def mock_mode() -> bool:
    return get_client() is None


def backend_label() -> str:
    if mock_mode():
        return "deterministic rules engine (no model connected)"
    if _client is None and not _primary_ready():
        if _provider() == "local":
            m = os.environ.get("MSAFARA_MODEL", LocalClient.default_model)
            return f"llm:local/{m if not _is_default_family(m) else LocalClient.default_model}"
        return f"llm:{_hosted_model() or FREE_TIER_MODEL}"
    return f"llm:{os.environ.get('MSAFARA_MODEL', DEFAULT_MODEL)}"


def _active() -> dict:
    return json.loads((PROMPTS / "active.json").read_text())


def active_versions() -> dict:
    out = {}
    for agent, v in _active().items():
        out[agent] = os.environ.get(f"MSAFARA_PROMPT_{agent.upper()}", v)
    return out


def available_versions(agent: str) -> list[str]:
    return sorted(p.stem.rsplit("_", 1)[1] for p in PROMPTS.glob(f"{agent}_v*.md"))


def load_prompt(agent: str) -> tuple[str, str]:
    version = active_versions()[agent]
    path = PROMPTS / f"{agent}_{version}.md"
    return path.read_text(), version


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object")


def structured_call(agent: str, user: str, schema: Type[T], max_tokens: int = 1024, retries: int = 1,
                    system: Optional[str] = None, judge: bool = False) -> T:
    client = get_client(judge=judge)
    if client is None:
        raise LLMOutputError("no LLM client available")
    sys_prompt = system if system is not None else load_prompt(agent)[0]
    sys_prompt += "\n\nRespond with a single JSON object only, matching this JSON schema:\n" + json.dumps(schema.model_json_schema())
    messages = [{"role": "user", "content": user}]
    last_err = ""
    for attempt in range(retries + 1):
        t0 = time.time()
        resp = client.messages.create(model=os.environ.get("MSAFARA_MODEL", DEFAULT_MODEL), max_tokens=max_tokens,
                                      system=sys_prompt, messages=messages)
        METER.add(agent, getattr(resp, "usage", None), time.time() - t0)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        try:
            return schema.model_validate(extract_json(text))
        except (ValueError, ValidationError) as e:
            last_err = str(e)[:400]
            messages = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": f"That output was invalid ({last_err}). Return only a corrected JSON object."}]
    raise LLMOutputError(f"{agent}: invalid output after {retries + 1} attempts: {last_err}")


# =========================================================== usage meter (cost + latency metrics)
class _Meter:
    """Counts model calls, tokens and seconds. The eval harness resets it per trip."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls, self.input, self.output, self.seconds = 0, 0, 0, 0.0
        self.by_agent: dict[str, int] = {}

    def add(self, agent: str, usage: Optional[dict], seconds: float):
        self.calls += 1
        self.seconds += seconds
        self.by_agent[agent] = self.by_agent.get(agent, 0) + 1
        if usage:
            self.input += int(usage.get("input") or 0)
            self.output += int(usage.get("output") or 0)

    def snapshot(self) -> dict:
        return dict(calls=self.calls, input_tokens=self.input, output_tokens=self.output,
                    llm_seconds=round(self.seconds, 2), by_agent=dict(self.by_agent))


METER = _Meter()


def cost_usd(input_tokens: int, output_tokens: int) -> Optional[float]:
    """Cost from MSAFARA_PRICE_IN / MSAFARA_PRICE_OUT (USD per million tokens). None when prices are not set."""
    pin, pout = os.environ.get("MSAFARA_PRICE_IN"), os.environ.get("MSAFARA_PRICE_OUT")
    if not pin or not pout:
        return None
    return round(input_tokens / 1e6 * float(pin) + output_tokens / 1e6 * float(pout), 6)


def model_class() -> str:
    """Generic, public description of the model in use (no vendor or model names on public pages)."""
    if mock_mode():
        return "none (rules engine)"
    return os.environ.get("MSAFARA_MODEL_CLASS", "hosted LLM")


# =========================================================== native tool calling
class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict          # JSON-schema subset: object / properties{type, description, enum} / required


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict = {}


class ChatResult(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    usage: dict = {}
    raw: Optional[dict] = None   # the backend's own assistant message, echoed back verbatim next turn


# Normalised history entries:
#   {"role": "user", "content": str}
#   {"role": "assistant", "content": str, "tool_calls": [ToolCall...], "raw": dict | None}
#   {"role": "tool", "results": [{"id", "name", "content": str}]}


def _messages_api_chat(c: "ApiClient", system, history, tools, max_tokens) -> ChatResult:
    model = os.environ.get("MSAFARA_MODEL", "")
    if not model or _is_default_family(model):
        raise LLMOutputError("MSAFARA_MODEL is not set for the primary backend")
    msgs = []
    for h in history:
        if h["role"] == "user":
            msgs.append({"role": "user", "content": h["content"]})
        elif h["role"] == "assistant":
            if h.get("raw"):
                msgs.append(h["raw"])
            else:
                blocks = ([{"type": "text", "text": h["content"]}] if h.get("content") else []) + \
                         [{"type": "tool_use", "id": t.id, "name": t.name, "input": t.args} for t in h.get("tool_calls", [])]
                msgs.append({"role": "assistant", "content": blocks})
        else:
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                                                     for r in h["results"]]})
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": msgs,
            "tools": [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]}
    headers = {"x-api-key": c.key, **{str(k): str(v) for k, v in c.extra_headers.items()}}
    try:
        data = _post_json(c.url, body, headers, timeout=120)
    except Exception as e:
        raise LLMOutputError(f"primary backend request failed: {str(e)[:200]}") from e
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [ToolCall(id=b["id"], name=b["name"], args=b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"]
    u = data.get("usage") or {}
    return ChatResult(text=text, tool_calls=calls, usage=dict(input=u.get("input_tokens", 0), output=u.get("output_tokens", 0)),
                      raw={"role": "assistant", "content": blocks})


def _local_chat(c: "LocalClient", system, history, tools, max_tokens) -> ChatResult:
    import urllib.request
    model = os.environ.get("MSAFARA_MODEL") or c.default_model
    if _is_default_family(model):
        model = c.default_model
    msgs = [{"role": "system", "content": system}]
    for h in history:
        if h["role"] == "user":
            msgs.append({"role": "user", "content": h["content"]})
        elif h["role"] == "assistant":
            msgs.append(h.get("raw") or {"role": "assistant", "content": h.get("content", ""),
                                          "tool_calls": [{"function": {"name": t.name, "arguments": t.args}} for t in h.get("tool_calls", [])]})
        else:
            msgs += [{"role": "tool", "content": r["content"], "tool_name": r["name"]} for r in h["results"]]
    body = {"model": model, "stream": False, "messages": msgs, "options": {"temperature": 0, "num_predict": max_tokens},
            "tools": [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                      for t in tools]}
    req = urllib.request.Request(c.host + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise LLMOutputError(f"local backend request failed: {str(e)[:200]}") from e
    m = data.get("message") or {}
    calls = []
    for i, tc in enumerate(m.get("tool_calls") or []):
        f = tc.get("function") or {}
        args = f.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append(ToolCall(id=f"call_{len(history)}_{i}", name=f.get("name", ""), args=args))
    return ChatResult(text=m.get("content", ""), tool_calls=calls,
                      usage=dict(input=data.get("prompt_eval_count", 0), output=data.get("eval_count", 0)),
                      raw={"role": "assistant", "content": m.get("content", ""), "tool_calls": m.get("tool_calls") or []})


def _hosted_chat(c: "HostedClient", system, history, tools, max_tokens) -> ChatResult:
    import urllib.parse
    import urllib.request
    model = _hosted_model()
    contents = []
    for h in history:
        if h["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": h["content"]}]})
        elif h["role"] == "assistant":
            contents.append(h.get("raw") or {"role": "model", "parts": (
                ([{"text": h["content"]}] if h.get("content") else []) +
                [{"functionCall": {"name": t.name, "args": t.args}} for t in h.get("tool_calls", [])])})
        else:
            contents.append({"role": "user", "parts": [{"functionResponse": {"name": r["name"], "response": {"result": r["content"]}}}
                                                       for r in h["results"]]})
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
            "tools": [{"functionDeclarations": [{"name": t.name, "description": t.description, "parameters": t.parameters}
                                                for t in tools]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0}}
    headers = {"Content-Type": "application/json"}
    url = c.url_template.replace("{model}", urllib.parse.quote(model, safe=""))
    if "{key}" in url:
        url = url.replace("{key}", urllib.parse.quote(c.key, safe=""))
    else:
        headers[c.key_header] = c.key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise LLMOutputError(f"hosted backend request failed: {str(e)[:200]}") from e
    content = (data.get("candidates") or [{}])[0].get("content") or {"role": "model", "parts": []}
    parts = content.get("parts", [])
    calls = [ToolCall(id=f"call_{len(history)}_{i}", name=p["functionCall"]["name"], args=p["functionCall"].get("args") or {})
             for i, p in enumerate(parts) if "functionCall" in p]
    u = data.get("usageMetadata") or {}
    return ChatResult(text="".join(p.get("text", "") for p in parts if "text" in p), tool_calls=calls,
                      usage=dict(input=u.get("promptTokenCount", 0), output=u.get("candidatesTokenCount", 0)),
                      raw={"role": "model", "parts": parts})


def chat_with_tools(agent: str, system: str, history: list, tools: list[ToolSpec], max_tokens: int = 1024) -> ChatResult:
    client = get_client()
    if client is None:
        raise LLMOutputError("no LLM client available")
    t0 = time.time()
    if hasattr(client, "chat"):                       # test stubs
        res = client.chat(system=system, history=history, tools=tools)
    elif isinstance(client, ApiClient):
        res = _messages_api_chat(client, system, history, tools, max_tokens)
    elif isinstance(client, LocalClient):
        res = _local_chat(client, system, history, tools, max_tokens)
    elif isinstance(client, HostedClient):
        res = _hosted_chat(client, system, history, tools, max_tokens)
    else:
        raise LLMOutputError(f"client {type(client).__name__} does not support tool calling")
    METER.add(agent, res.usage, time.time() - t0)
    return res


class ToolLoopResult(BaseModel):
    terminal: Optional[str] = None       # name of the decision tool that ended the loop
    result: dict = {}
    steps: int = 0
    trace: list[dict] = []               # every tool call with its args and the executor's answer
    refused: int = 0                     # tool calls the executor refused (guardrail said no)


def run_tool_loop(agent: str, user: str, tools: list[ToolSpec], execute, max_steps: int = 8,
                  system: Optional[str] = None) -> ToolLoopResult:
    """Agentic loop: the model calls tools; `execute(name, args) -> (result: dict, terminal: bool)` runs them in code.

    The loop ends when an executed call is terminal (a decision tool accepted by its guardrail). A refused
    decision comes back to the model as an error it can correct. No terminal decision within max_steps
    raises LLMOutputError, and the caller falls back to the rules engine.
    """
    sys_prompt = system if system is not None else load_prompt(agent)[0]
    history: list = [{"role": "user", "content": user}]
    out = ToolLoopResult()
    nudged = False
    for step in range(max_steps):
        res = chat_with_tools(agent, sys_prompt, history, tools)
        out.steps = step + 1
        history.append({"role": "assistant", "content": res.text, "tool_calls": res.tool_calls, "raw": res.raw})
        if not res.tool_calls:
            if nudged:
                err = LLMOutputError(f"{agent}: model stopped without calling a decision tool")
                err.partial = out
                raise err
            nudged = True
            history.append({"role": "user", "content": "You must finish by calling exactly one decision tool."})
            continue
        results = []
        for tc in res.tool_calls:
            if tc.name not in {t.name for t in tools}:
                r, term = {"ok": False, "error": f"unknown tool {tc.name}"}, False
            else:
                try:
                    r, term = execute(tc.name, tc.args)
                except Exception as e:     # a bad argument must not crash the trip
                    r, term = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}, False
            if r.get("ok") is False:
                out.refused += 1
            out.trace.append(dict(tool=tc.name, args=tc.args, result=r))
            results.append({"id": tc.id, "name": tc.name, "content": json.dumps(r, default=str)})
            if term:
                out.terminal, out.result = tc.name, r
                return out
        history.append({"role": "tool", "results": results})
    err = LLMOutputError(f"{agent}: no decision after {max_steps} steps")
    err.partial = out
    raise err
