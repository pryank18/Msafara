from msafara import kb, rag, seed_kb, agents


def test_chunking_short_and_long():
    assert rag.chunk_text("One sentence.") == ["One sentence."]
    long = " ".join(f"Sentence number {i} is here." for i in range(80))
    ch = rag.chunk_text(long, max_chars=200, overlap=30)
    assert len(ch) > 5 and all(len(c) <= 230 for c in ch)
    assert "Sentence number 79" in ch[-1]


def test_embedding_is_deterministic_and_normalised():
    e1 = rag.HashingEmbedder().fit(["a b c", "b c d"]); e2 = rag.HashingEmbedder().fit(["a b c", "b c d"])
    assert e1.embed("b c") == e2.embed("b c")
    import numpy as np
    assert abs(np.linalg.norm(e1.embed("a b c")) - 1) < 1e-5


def test_vendor_id_filter_returns_only_that_vendor():
    for v in kb.vendors()[:10]:
        hits = rag.retrieve("past quote rate", {"vendor_id": v["vendor_id"], "doc_type": "past_quote"}, k=10)
        assert len(hits) == 3 and all(h.metadata["vendor_id"] == v["vendor_id"] for h in hits)


def test_chroma_and_numpy_backends_agree():
    a = rag.get_index()
    b = rag.VectorIndex(prefer_chroma=False).build(kb.documents())
    assert b.backend == "numpy"
    for q, w in [("4-star hotel Jaipur group", {"doc_type": "vendor_profile", "city": "Jaipur", "star": 4}),
                 ("cancellation terms", {"doc_type": "policy"}),
                 ("transport coach Goa", {"doc_type": "vendor_profile", "city": "Goa", "category": "transport"})]:
        ha, hb = a.query(q, w, 4), b.query(q, w, 4)
        assert [h.id for h in ha if h.score > 1e-6] == [h.id for h in hb if h.score > 1e-6]   # zero-similarity ties may order differently
        assert all(abs(x.score - y.score) < 1e-3 for x, y in zip(ha, hb))


def test_anchor_from_retrieval_equals_ground_truth_for_every_vendor():
    for v in kb.vendors():
        a = agents.anchor_for(v["vendor_id"]); t = kb.truth_anchor(v["vendor_id"])
        assert (a.anchor_rate, a.rate_card_rate, a.floor_rate) == (t["anchor"], t["rate_card"], t["floor"])
        assert a.sources and a.n_quotes == 3


def test_policy_retrieval_finds_cancellation_terms():
    hit = rag.retrieve("free cancellation days before arrival", {"doc_type": "policy"}, k=1)[0]
    assert "Cancellation" in hit.text


def test_kb_generation_is_deterministic_and_has_enough_eligible_vendors():
    import json
    fresh = seed_kb.build()
    assert fresh["vendors"] == json.loads((seed_kb.OUT / "vendors.json").read_text())
    from collections import defaultdict
    groups = defaultdict(list)
    for v in kb.vendors():
        groups[(v["city"], v["category"], v["star"])].append(v)
    assert len(kb.vendors()) == 80 and len(groups) == 20
    for k, vs in groups.items():
        assert sum(1 for v in vs if not v["blacklisted"] and v["capacity"] >= 24) >= 3, k


def test_matching_excludes_blacklisted_and_small_vendors():
    from msafara.models import TripBrief
    from datetime import date
    b = agents.finalize_brief(TripBrief(city="Goa", check_in=date(2027, 1, 1), check_out=date(2027, 1, 3), pax=30, hotel_star=4))
    pol = agents.get_policy()
    for svc in agents.build_services(b):
        cands, _ = agents.match_vendors(svc, b, pol)
        assert 3 <= len(cands) <= 5
        for c in cands:
            v = kb.vendor(c.vendor_id)
            assert not v["blacklisted"] and v["capacity"] >= svc.min_capacity
