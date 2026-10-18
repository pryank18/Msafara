/* Msafara demo backend.
   On GitHub Pages there is no FastAPI server, so this script answers the
   dashboard's /api/* calls in the browser with a seeded 12-person
   Kilimanjaro & Zanzibar trip. When the page is served by the real backend
   (any host other than *.github.io), it does nothing. */
(function () {
  if (!/github\.io$/.test(location.hostname) && !/[?&]demo=1/.test(location.search)) return;

  const KEY = "msafara_demo_v2";
  const realFetch = window.fetch.bind(window);
  const H = 3600 * 1000;
  const iso = ms => new Date(ms).toISOString().slice(0, 19);
  const now = () => Date.now();

  function msg(direction, content, agoHours) {
    return { direction, content, timestamp: iso(now() - agoHours * H) };
  }

  function seed() {
    const t = now();
    const reqs = [
      {
        request_id: "REQ-1001",
        vendor: { name: "Stone Town Heritage Hotel", contact_channel: "email", contact_address: "reservations@stonetown-heritage.example" },
        need: { category: "accommodation", description: "6 twin rooms, 3-star, Stone Town", dates: "2026-11-14 to 2026-11-17", quantity: 6, budget_min: 120, budget_max: 160 },
        status: "confirmed",
        message_history: [
          msg("outbound", "Hello, we'd like to request 6 twin rooms (3-star) in Stone Town for 14–17 Nov 2026 for a 12-person group. Could you confirm availability and your best nightly rate?", 50),
          msg("inbound", "Dear team, we can confirm 6 twin rooms for 14–17 Nov at $145 per room per night, breakfast included. Held until 20 Nov.", 30)
        ],
        latest_parsed_result: { rate: 145, available: true, alternative_offer: "Breakfast included" },
        escalation_reason: null, follow_up_count: 0, negotiation_rounds: 0,
        hold_expires_at: iso(t + 60 * H), updated_at: iso(t - 30 * H)
      },
      {
        request_id: "REQ-1002",
        vendor: { name: "Kibo Summit Treks", contact_channel: "email", contact_address: "ops@kibo-summit.example" },
        need: { category: "activity", description: "Machame route, 7 days, 12 climbers incl. guides & park fees", dates: "2026-11-06 to 2026-11-12", quantity: 12, budget_min: 1800, budget_max: 1950 },
        status: "negotiating",
        message_history: [
          msg("outbound", "We're planning a 7-day Machame climb for 12 guests, 6–12 Nov 2026, all-inclusive of guides and park fees. Please share availability and per-person pricing.", 70),
          msg("inbound", "Available for those dates. Our all-inclusive price is $2,150 per person.", 46),
          msg("outbound", "Thank you. Our budget for this group is $1,950 per person. With 12 climbers booked together, could you meet us at that rate?", 45)
        ],
        latest_parsed_result: { rate: 2150, available: true, alternative_offer: null },
        escalation_reason: null, follow_up_count: 0, negotiation_rounds: 1, last_counter: 1950,
        hold_expires_at: null, updated_at: iso(t - 45 * H)
      },
      {
        request_id: "REQ-1003",
        vendor: { name: "Arusha Safari Wheels", contact_channel: "whatsapp", contact_address: "+255700000101" },
        need: { category: "transport", description: "2 Land Cruisers with drivers, Arusha ↔ Moshi ↔ JRO", dates: "2026-11-05 to 2026-11-13", quantity: 2, budget_min: 180, budget_max: 240 },
        status: "awaiting_response",
        message_history: [
          msg("outbound", "Hi, we need 2 Land Cruisers with drivers for transfers between Arusha, Moshi and JRO airport, 5–13 Nov 2026. What is your daily rate per vehicle?", 40),
          msg("outbound", "Following up on our request for 2 Land Cruisers, 5–13 Nov. Could you confirm availability when you get a moment?", 16)
        ],
        latest_parsed_result: null,
        escalation_reason: null, follow_up_count: 1, negotiation_rounds: 0,
        hold_expires_at: null, updated_at: iso(t - 16 * H)
      },
      {
        request_id: "REQ-1004",
        vendor: { name: "Nungwi Beach Lodge", contact_channel: "email", contact_address: "bookings@nungwi-lodge.example" },
        need: { category: "accommodation", description: "6 sea-view rooms, Nungwi", dates: "2026-11-17 to 2026-11-20", quantity: 6, budget_min: 150, budget_max: 210 },
        status: "escalated",
        message_history: [
          msg("outbound", "We'd like 6 sea-view rooms in Nungwi for 17–20 Nov 2026 (12 guests). Please confirm availability and rate.", 36),
          msg("inbound", "We have 4 sea-view rooms at $195. The other 2 could be garden rooms, or sea-view from the 18th only.", 12)
        ],
        latest_parsed_result: { rate: 195, available: "partial", alternative_offer: "4 sea-view + 2 garden rooms, or all sea-view from 18 Nov" },
        escalation_reason: "Partial availability with two alternative offers — needs a coordinator decision.",
        follow_up_count: 0, negotiation_rounds: 0,
        hold_expires_at: null, updated_at: iso(t - 12 * H)
      },
      {
        request_id: "REQ-1005",
        vendor: { name: "Spice Island Dhow Co.", contact_channel: "whatsapp", contact_address: "+255700000202" },
        need: { category: "activity", description: "Private sunset dhow cruise, 12 guests", dates: "2026-11-16", quantity: 12, budget_min: 35, budget_max: 55 },
        status: "confirmed",
        message_history: [
          msg("outbound", "Hello! Could you do a private sunset dhow cruise for 12 guests on 16 Nov 2026? Please share your per-person price.", 28),
          msg("inbound", "Yes, confirmed for 16 Nov. $45 per person including soft drinks and snacks. We hold the boat for 24 hours.", 4)
        ],
        latest_parsed_result: { rate: 45, available: true, alternative_offer: "Soft drinks and snacks included" },
        escalation_reason: null, follow_up_count: 0, negotiation_rounds: 0,
        hold_expires_at: iso(t + 20 * H), updated_at: iso(t - 4 * H)
      },
      {
        request_id: "REQ-1006",
        vendor: { name: "Moshi Mountain Inn", contact_channel: "email", contact_address: "stay@moshi-inn.example" },
        need: { category: "accommodation", description: "Pre- and post-climb nights, 6 twin rooms, Moshi", dates: "2026-11-05 & 2026-11-12", quantity: 6, budget_min: 70, budget_max: 100 },
        status: "sent",
        message_history: [
          msg("outbound", "Hi, we need 6 twin rooms in Moshi on 5 Nov and 12 Nov 2026 (before and after a Kilimanjaro climb). Could you confirm availability and rates?", 1)
        ],
        latest_parsed_result: null,
        escalation_reason: null, follow_up_count: 0, negotiation_rounds: 0,
        hold_expires_at: null, updated_at: iso(t - 1 * H)
      }
    ];
    const sourcing = [{
      sourcing_id: "SRC-201",
      need: { category: "transport", description: "JRO airport → Moshi arrival transfer, 12 pax + luggage", dates: "2026-11-05" },
      vendors: [
        { vendor: "Kili Express Shuttles", status: "confirmed", rate: 180, available: true, alternative_offer: "1 coaster bus, luggage trailer" },
        { vendor: "Moshi Airport Transfers", status: "confirmed", rate: 210, available: true, alternative_offer: "2 vans" },
        { vendor: "Uhuru Transport", status: "awaiting_response", rate: null, available: null, alternative_offer: "" }
      ]
    }];
    return { reqs, sourcing, seq: 1007, srcSeq: 202, polled: false };
  }

  let db;
  try {
    if (/[?&]reset=1/.test(location.search)) localStorage.removeItem(KEY);
    db = JSON.parse(localStorage.getItem(KEY) || "null");
  } catch (e) { db = null; }
  if (!db || !db.reqs) db = seed();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (e) {} };

  const json = (data, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const find = id => db.reqs.find(r => r.request_id === id);
  const touch = r => { r.updated_at = iso(now()); };

  // Negotiation policy for the inbox. budget_max is the walk-away price: the most we pay without a human.
  const POLICY = { autoAcceptPct: 3, maxCounters: 3, stuckLimit: 2, minStepPct: 1 };
  const FIRM = /best (?:we|i) can (?:do|offer|go)|(?:our|my|the) (?:very )?(?:best|final|lowest|last)(?: possible)? (?:price|rate|offer|figure)|(?:can'?t|cannot|can not|unable to|won'?t) (?:go|come) (?:any )?(?:lower|down|further|below)|no (?:further|more) (?:discount|reduction|room|flexibility)|no flexibility|\bfinal\b|rock[- ]bottom|non[- ]negotiable|take it or leave it|that'?s (?:the )?(?:best|lowest)/i;
  const DECLINE = /fully booked|sold out|not available|unavailable|no availability|cannot (?:accommodate|take|do (?:it|this|those))|can'?t (?:accommodate|take|do (?:it|this|those))|unable to (?:accommodate|take|help)|regret/i;
  const PARTIAL = /\bonly (?:have )?\d+\b|\bpartial\b|\binstead\b|\balternative\b/i;
  const money = n => "$" + Math.round(n).toLocaleString("en-US");

  function priceIn(text) {
    const t = text.replace(/(\d),(\d{3})/g, "$1$2");
    const m = t.match(/\$\s?(\d+(?:\.\d+)?)/) || t.match(/(\d{2,6}(?:\.\d+)?)\s*(?:usd|dollars|per |pp\b|\/)/i) || t.match(/\b(\d{3,6}(?:\.\d+)?)\b/);
    return m ? parseFloat(m[1]) : null;
  }

  function lastCounter(r) {
    if (r.last_counter != null) return r.last_counter;
    // older saved data: recover our last ask from the thread
    for (let i = r.message_history.length - 1; i >= 0; i--) {
      const h = r.message_history[i];
      if (h.direction === "outbound" && /budget|rate of|meet us|propose/i.test(h.content)) { const p = priceIn(h.content); if (p) return p; }
    }
    return null;
  }

  // Our next ask, or null when there is no honest move left. Never repeats or lowers our own ask.
  function nextCounter(r, rate, firm) {
    const walk = r.need.budget_max, last = lastCounter(r);
    const ceiling = Math.min(walk, rate - 1);
    let next;
    if (last == null) {                                                         // opening counter: mid-budget,
      const mid = r.need.budget_min ? (r.need.budget_min + walk) / 2 : walk * 0.92; // leaving room to step up,
      next = Math.max(mid, rate * 0.75);                                        // but never a >25% lowball
    }
    else if (firm) next = ceiling;                                              // their final word: our best answer
    else next = (last + ceiling) / 2;                                           // step halfway up
    next = Math.round(Math.min(next, ceiling));
    if (last != null && next - last < walk * POLICY.minStepPct / 100) next = Math.round(ceiling);
    if (last != null && next <= last) return null;
    if (next >= rate) return null;
    return next;
  }

  function escalate(r, reason, suggestion) {
    r.status = "escalated";
    r.escalation_reason = reason;
    r.suggested_next_move = suggestion || null;
  }

  function parseReply(r, text) {
    const s = text.toLowerCase();
    const rate = priceIn(text);
    const firm = FIRM.test(text);
    const prevRate = r.latest_parsed_result && r.latest_parsed_result.rate;
    r.message_history.push({ direction: "inbound", content: text, timestamp: iso(now()) });
    r.suggested_next_move = null;
    if (DECLINE.test(s) && rate === null) {
      r.status = "declined";
      r.latest_parsed_result = { rate: null, available: false, alternative_offer: null };
      return;
    }
    if (PARTIAL.test(s) && !/confirm/.test(s)) {
      escalate(r, "Reply offers partial availability or an alternative — needs a coordinator decision.",
        "Ask the client whether the alternative works, then lock it or source the rest elsewhere.");
      r.latest_parsed_result = { rate, available: "partial", alternative_offer: text.slice(0, 120) };
      return;
    }
    if (rate === null) {
      escalate(r, "Couldn't find a clear rate or yes/no in the reply.", "Reply asking the vendor to confirm availability and a per-unit rate.");
      return;
    }
    r.latest_parsed_result = { rate, available: true, alternative_offer: firm ? "Vendor says this is their best price" : null };
    const walk = r.need.budget_max;
    const confirm = note => {
      r.status = "confirmed";
      r.escalation_reason = null;
      r.hold_expires_at = iso(now() + 48 * H);
      r.agreed_note = note || null;
    };
    if (!walk || rate <= walk) return confirm();
    const overPct = (rate - walk) / walk * 100;
    if (overPct <= POLICY.autoAcceptPct)
      return confirm(`Accepted at ${money(rate)}: ${overPct.toFixed(1)}% over budget, inside the ${POLICY.autoAcceptPct}% auto-accept tolerance.`);

    // A reply to our counter where the vendor said "final" or barely moved counts as a stuck round.
    if (r.negotiation_rounds > 0 && (firm || (prevRate && rate > prevRate * 0.99))) r.stuck_rounds = (r.stuck_rounds || 0) + 1;
    const last = lastCounter(r);
    const qty = r.need.quantity || 1;
    const suggestion = () => {
      const extra = (rate - walk) * qty;
      return `Accept at ${money(rate)} (+${money(extra)} over budget across ${qty} units), offer ${money(walk)} with a value-add ask ` +
        `(upgrade, meals, a free place), or source another vendor.`;
    };
    if ((r.stuck_rounds || 0) >= POLICY.stuckLimit)
      return escalate(r, `Vendor hasn't moved in ${r.stuck_rounds} rounds: ${money(rate)} vs budget ${money(walk)} (${overPct.toFixed(0)}% over).`, suggestion());
    if (r.negotiation_rounds >= POLICY.maxCounters)
      return escalate(r, `Still ${money(rate)} after ${r.negotiation_rounds} counter-offers (budget ${money(walk)}).`, suggestion());
    const next = nextCounter(r, rate, firm);
    if (next === null)
      return escalate(r, `${firm ? "Vendor says " + money(rate) + " is their best price" : "Vendor is at " + money(rate)}` +
        ` and we've already offered our walk-away ${money(last)} (budget ${money(walk)}, ${overPct.toFixed(0)}% over).`, suggestion());
    r.negotiation_rounds += 1;
    r.last_counter = next;
    r.status = "negotiating";
    const body = last == null
      ? `Thank you for the quote. For this booking we can offer ${money(next)}. Could you meet us at that rate?`
      : firm
        ? `Thank you for coming back to us. We understand ${money(rate)} is your best price. The most we can stretch to is ${money(next)}${qty > 1 ? " for the full booking" : ""} — if that works, we can confirm today.`
        : `Thank you for the revised rate. We can move to ${money(next)}. Could you meet us there so we can confirm?`;
    r.message_history.push({ direction: "outbound", content: body, timestamp: iso(now()) });
  }

  function vendorsList() {
    const map = {};
    db.reqs.forEach(r => {
      const k = r.vendor.contact_address;
      const v = map[k] || (map[k] = { name: r.vendor.name, contact_channel: r.vendor.contact_channel, category: r.need.category, total_requests: 0, confirmed: 0, last_used_at: "" });
      v.total_requests++;
      if (r.status === "confirmed") v.confirmed++;
      if (r.updated_at > v.last_used_at) v.last_used_at = r.updated_at;
    });
    return Object.values(map).map(v => ({ ...v, last_used_at: v.last_used_at.replace("T", " "), confirm_rate: v.total_requests ? v.confirmed / v.total_requests : null }));
  }

  function proposalDoc(body) {
    const rows = body.request_ids.map(find).filter(Boolean);
    const esc = s => String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    const lines = rows.map(r => `<tr><td>${esc(r.need.category)}</td><td>${esc(r.vendor.name)}</td><td>${esc(r.need.description)}</td><td>${esc(r.need.dates)}</td><td>$${esc(r.latest_parsed_result && r.latest_parsed_result.rate)}</td></tr>`).join("");
    return `<html><head><meta charset="utf-8"><title>${esc(body.trip_title)}</title></head><body style="font-family:Calibri,Arial,sans-serif">
<h1>${esc(body.trip_title)}</h1>${body.client_name ? `<p>Prepared for ${esc(body.client_name)}</p>` : ""}
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse"><tr><th>Category</th><th>Supplier</th><th>Service</th><th>Dates</th><th>Agreed rate</th></tr>${lines}</table>
<p style="color:#666">Generated by the Msafara demo. Rates are the confirmed supplier quotes.</p></body></html>`;
  }

  async function handle(path, opts) {
    const method = (opts && opts.method) || "GET";
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    let m;

    if (path === "/api/requests" && method === "GET")
      return json(db.reqs.slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map(r => ({ request_id: r.request_id, updated_at: r.updated_at.replace("T", " ") })));

    if (path === "/api/requests" && method === "POST") {
      const r = {
        request_id: "REQ-" + db.seq++, vendor: body.vendor, need: body.need, status: "sent",
        message_history: [{ direction: "outbound", content: `Hello ${body.vendor.name}, we'd like to request: ${body.need.description} for ${body.need.dates}${body.need.quantity ? ` (quantity ${body.need.quantity})` : ""}. Could you confirm availability and your best rate?`, timestamp: iso(now()) }],
        latest_parsed_result: null, escalation_reason: null, follow_up_count: 0, negotiation_rounds: 0, hold_expires_at: null
      };
      touch(r); db.reqs.push(r); save();
      return json(r);
    }

    if ((m = path.match(/^\/api\/requests\/([^/]+)$/))) {
      const r = find(m[1]);
      return r ? json(r) : json({ detail: "Request not found" }, 404);
    }

    if ((m = path.match(/^\/api\/requests\/([^/]+)\/(reply|lock|revise)$/))) {
      const r = find(m[1]);
      if (!r) return json({ detail: "Request not found" }, 404);
      if (m[2] === "reply") parseReply(r, body.reply_text || "");
      if (m[2] === "lock") { r.status = "confirmed"; r.escalation_reason = null; r.suggested_next_move = null; r.hold_expires_at = null; }
      if (m[2] === "revise") {
        if (body.new_quantity) r.need.quantity = body.new_quantity;
        r.status = "sent"; r.escalation_reason = null; r.suggested_next_move = null; r.stuck_rounds = 0;
        r.message_history.push({ direction: "outbound", content: `Update to our booking: ${body.change_description}. Could you re-confirm availability and rate?`, timestamp: iso(now()) });
      }
      touch(r); save();
      return json(r);
    }

    if (path === "/api/sourcing" && method === "GET")
      return json(db.sourcing.map(s => ({ sourcing_id: s.sourcing_id })).reverse());

    if (path === "/api/sourcing" && method === "POST") {
      const s = { sourcing_id: "SRC-" + db.srcSeq++, need: body.need, vendors: body.vendors.map(v => ({ vendor: v.name, status: "sent", rate: null, available: null, alternative_offer: "" })) };
      db.sourcing.push(s); save();
      return json(s);
    }

    if ((m = path.match(/^\/api\/sourcing\/([^/]+)\/compare$/))) {
      const s = db.sourcing.find(x => x.sourcing_id === m[1]);
      if (!s) return json({ detail: "Not found" }, 404);
      return json({
        need: s.need, all_vendors: s.vendors,
        confirmed_ranked_by_price: s.vendors.filter(v => v.status === "confirmed").sort((a, b) => a.rate - b.rate),
        still_pending: s.vendors.filter(v => v.status !== "confirmed" && v.status !== "declined").map(v => v.vendor)
      });
    }

    if (path === "/api/check") {
      const timeout_checks = [];
      db.reqs.forEach(r => {
        const quietHours = (now() - Date.parse(r.updated_at + "Z")) / H;
        if ((r.status === "sent" || r.status === "awaiting_response") && quietHours > 12) {
          const before = r.status;
          if (r.follow_up_count >= 2) { r.status = "escalated"; r.escalation_reason = "No reply after 2 follow-ups."; }
          else {
            r.follow_up_count++; r.status = "awaiting_response";
            r.message_history.push({ direction: "outbound", content: `Friendly follow-up on our request (${r.need.description}, ${r.need.dates}). Could you confirm availability?`, timestamp: iso(now()) });
          }
          touch(r); timeout_checks.push({ request_id: r.request_id, before, after: r.status });
        }
      });
      const expiring_holds = db.reqs.filter(r => r.hold_expires_at).map(r => {
        const left = (Date.parse(r.hold_expires_at + "Z") - now()) / H;
        return { request_id: r.request_id, vendor: r.vendor.name, hours_left: Math.max(left, 0), already_expired: left <= 0 };
      }).filter(h => h.hours_left < 36);
      save();
      return json({ timeout_checks, expiring_holds });
    }

    if (path === "/api/poll") {
      const processed = [];
      if (!db.polled) {
        const r = find("REQ-1003");
        if (r && r.status === "awaiting_response") {
          parseReply(r, "Hello, both Land Cruisers are available 5–13 Nov with drivers. $220 per vehicle per day, fuel included.");
          touch(r); processed.push({ request_id: r.request_id, status: r.status });
        }
        db.polled = true; save();
      }
      return json({ configured: true, processed });
    }

    if (path === "/api/status")
      return json({ mock_llm_mode: true, dispatch_configured: false, inbox_poll_configured: false, whatsapp_configured: false });

    if (path === "/api/vendors") return json(vendorsList());

    if (path === "/api/proposal") {
      return new Response(new Blob([proposalDoc(body)], { type: "application/msword" }), { status: 200, headers: { "Content-Type": "application/msword", "X-Ext": ".doc" } });
    }

    return json({ detail: "Not available in the demo" }, 404);
  }

  window.fetch = function (input, opts) {
    const url = typeof input === "string" ? input : input.url;
    const path = url.replace(location.origin, "").split("?")[0];
    if (path.startsWith("/api/")) return handle(path, opts);
    return realFetch(input, opts);
  };

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.createElement("div");
    bar.style.cssText = "position:fixed;top:12px;right:12px;z-index:50;background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:8px;padding:8px 12px;font:12px/1.4 system-ui,sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.25)";
    bar.innerHTML = 'Live demo · sample trip data runs in your browser · <a href="?reset=1" style="color:#f59e0b">reset</a>';
    document.body.appendChild(bar);
  });
})();
