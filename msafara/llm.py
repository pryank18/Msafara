"""LLM access layer.

* One chokepoint: `structured_call(agent, user, schema)` -> validated Pydantic object.
  Output is parsed as JSON, validated against the schema, and retried once with the
  validation error fed back. Failure raises LLMOutputError (callers fall back / escalate).
* Every backend is configured from the environment, so no provider is hard-coded here:
  - Primary backend: LLM_API_KEY + LLM_API_URL (a messages-style endpoint: JSON body with
    model, max_tokens, system, messages; response with content[].text). Extra request headers
    the endpoint needs go in LLM_API_HEADERS as a JSON object. Model id: MSAFARA_MODEL.
  - Local backend: MSAFARA_LLM_PROVIDER=ollama (OLLAMA_HOST optional).
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


def get_client():
    if _forced_mock:
        return None
    if _client is not None:
        return _client
    if _primary_ready():
        return ApiClient()
    if _provider() == "ollama":
        return OllamaClient()
    if _hosted_ready():
        return HostedClient()
    return None


class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


def _post_json(url: str, body: dict, headers: dict, timeout: int) -> dict:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


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
        return _Resp(text)


class OllamaClient:
    """Fully free, no-signup backend: a local Ollama server (ollama.com) on your own machine.

    MSAFARA_LLM_PROVIDER=ollama, MSAFARA_MODEL=<ollama tag, default qwen2.5:7b>, OLLAMA_HOST optional.
    Small local models are weaker than hosted ones; label results with the model tag.
    """
    default_model = "qwen2.5:7b"

    def __init__(self):
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
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
        return _Resp(data.get("message", {}).get("content", ""))


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
        return _Resp("".join(p.get("text", "") for p in parts))


def mock_mode() -> bool:
    return get_client() is None


def backend_label() -> str:
    if mock_mode():
        return "deterministic rules engine (no model connected)"
    if _client is None and not _primary_ready():
        if _provider() == "ollama":
            m = os.environ.get("MSAFARA_MODEL", OllamaClient.default_model)
            return f"llm:ollama/{m if not _is_default_family(m) else OllamaClient.default_model}"
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
                    system: Optional[str] = None) -> T:
    client = get_client()
    if client is None:
        raise LLMOutputError("no LLM client available")
    sys_prompt = system if system is not None else load_prompt(agent)[0]
    sys_prompt += "\n\nRespond with a single JSON object only, matching this JSON schema:\n" + json.dumps(schema.model_json_schema())
    messages = [{"role": "user", "content": user}]
    last_err = ""
    for attempt in range(retries + 1):
        resp = client.messages.create(model=os.environ.get("MSAFARA_MODEL", DEFAULT_MODEL), max_tokens=max_tokens,
                                      system=sys_prompt, messages=messages)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        try:
            return schema.model_validate(extract_json(text))
        except (ValueError, ValidationError) as e:
            last_err = str(e)[:400]
            messages = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": f"That output was invalid ({last_err}). Return only a corrected JSON object."}]
    raise LLMOutputError(f"{agent}: invalid output after {retries + 1} attempts: {last_err}")
