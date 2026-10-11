"""FastAPI router for the Msafara trip workflow. Included from app.py.

NOTE: like the rest of this prototype there is no authentication. Do not expose it publicly as is.
Vendor replies and operator edits are untrusted text; the page renders them with textContent only.
"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import agents, graph, kb, llm, rag, simulator, store
from .evals import run as eval_run
from .evals.dataset import CASES, HELDOUT

router = APIRouter()
ROOT = Path(__file__).resolve().parent
_graph = None

PRESETS = {
    "smooth": dict(label="Simulated vendors - smooth (quotes at/below anchor)", profile=dict(hotel="clean", transport="clean", activity="clean")),
    "negotiation": dict(label="Simulated vendors - negotiation (counters, 2 rounds)", profile=dict(hotel="negotiable", transport="clean", activity="two_round")),
    "firm_hotel": dict(label="Simulated vendors - firm hotel 22% high (escalates)", profile=dict(hotel="firm_high", transport="clean", activity="clean")),
    "ambiguous": dict(label="Simulated vendors - ambiguous activity replies (escalates)", profile=dict(hotel="clean", transport="clean", activity="ambiguous_all")),
    "silent_transport": dict(label="Simulated vendors - transport never replies (escalates)", profile=dict(hotel="clean", transport="silent_all", activity="clean")),
    "manual": dict(label="Manual - I paste each vendor's reply", profile=None),
}


def get_graph():
    global _graph
    if _graph is None:
        _graph = graph.build_graph(graph.sqlite_checkpointer(store.DB_PATH))
    return _graph


def _view(snap: dict) -> dict:
    t = snap["trip"]
    for s in t["services"]:
        s["label"] = agents.conf_label(s["confidence"])
        s["vendor_name"] = kb.vendor(s["selected_vendor_id"])["name"] if s["selected_vendor_id"] else None
        for c in s["candidates"]:
            c["anchor"] = (s["anchors"].get(c["vendor_id"]) or {}).get("anchor_rate")
    return dict(trip=t, gate=snap["gate"], done=snap["done"])


class NewTrip(BaseModel):
    request_text: str
    preset: str = "smooth"


class Resume(BaseModel):
    decision: dict


@router.get("/trips")
def trips_page():
    return FileResponse(ROOT.parent / "static" / "trips.html")


@router.get("/api/msafara/status")
def status():
    return dict(llm=llm.backend_label(), mock=llm.mock_mode(), rag_backend=rag.get_index().backend, rag_chunks=len(rag.get_index()),
                embedder="hashing TF-IDF (lexical; not a neural embedding model)", prompt_versions=llm.active_versions(),
                live_dispatch=os.environ.get("MSAFARA_LIVE_DISPATCH") == "1", vendors=len(kb.vendors()),
                kb_note="Synthetic, fictional vendors and prices.", policy=agents.get_policy().model_dump())


@router.get("/api/msafara/presets")
def presets():
    return {k: v["label"] for k, v in PRESETS.items()}


@router.post("/api/trips")
def create_trip(body: NewTrip):
    if not body.request_text.strip():
        raise HTTPException(400, "request_text is required")
    if body.preset not in PRESETS:
        raise HTTPException(400, "unknown preset")
    snap = graph.start_trip(get_graph(), body.request_text.strip(), simulate_profile=PRESETS[body.preset]["profile"])
    store.upsert(snap["trip"], snap["gate"])
    return _view(snap)


@router.get("/api/trips")
def list_trips():
    return store.list_trips()


@router.get("/api/trips/{trip_id}")
def get_trip(trip_id: str):
    snap = graph.snapshot(get_graph(), trip_id)
    if not snap:
        raise HTTPException(404, "trip not found")
    return _view(snap)


@router.post("/api/trips/{trip_id}/resume")
def resume(trip_id: str, body: Resume):
    try:
        snap = graph.resume_trip(get_graph(), trip_id, body.decision)
    except KeyError:
        raise HTTPException(404, "trip not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.upsert(snap["trip"], snap["gate"])
    return _view(snap)


class RagQuery(BaseModel):
    query: str
    vendor_id: str | None = None
    doc_type: str | None = None
    city: str | None = None
    k: int = 5


@router.post("/api/msafara/rag")
def rag_search(q: RagQuery):
    where = {k: v for k, v in (("vendor_id", q.vendor_id), ("doc_type", q.doc_type), ("city", q.city)) if v}
    hits = rag.retrieve(q.query, where or None, k=min(max(q.k, 1), 10))
    return [dict(id=h.id, score=round(h.score, 3), text=h.text, doc_type=h.metadata.get("doc_type"), vendor_id=h.metadata.get("vendor_id")) for h in hits]


@router.get("/api/msafara/evals")
def evals_history():
    runs = eval_run.history()
    return dict(runs=runs, n_cases=len(CASES), n_heldout=len(HELDOUT), mock=llm.mock_mode(),
                versions=llm.active_versions(),
                note="mock-mode runs are a rule-based baseline, not LLM results" if llm.mock_mode() else "LLM mode")


class EvalReq(BaseModel):
    label: str = ""
    intake_only: bool = False
    confirm_cost: bool = False


@router.post("/api/msafara/evals/run")
def evals_run(body: EvalReq):
    if not llm.mock_mode() and not body.confirm_cost:
        raise HTTPException(400, "LLM mode: a full run makes hundreds of model calls. Resend with confirm_cost=true.")
    return eval_run.evaluate(label=body.label, intake_only=body.intake_only, log=True)


@router.get("/api/msafara/prompts")
def prompts():
    out = {}
    act = llm.active_versions()
    for agent in act:
        out[agent] = dict(active=act[agent], versions={v: (llm.PROMPTS / f"{agent}_{v}.md").read_text() for v in llm.available_versions(agent)})
    return dict(prompts=out, changelog=(llm.PROMPTS / "CHANGELOG.md").read_text())
