/* KisanSathi front end. No framework, no build step.
   Security note: server/LLM text is always inserted with textContent, never innerHTML. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  const state = {
    lang: store.get("ks.lang") || ((navigator.language || "").toLowerCase().startsWith("en") ? "en" : "hi"),
    loc: null, crops: [], history: [], health: null, speaking: null, busy: false,
  };
  try { state.loc = JSON.parse(store.get("ks.loc") || "null"); } catch { state.loc = null; }

  // ---------------------------------------------------------------- helpers
  const t = (k, vars = {}) => {
    let s = (I18N[state.lang] && I18N[state.lang][k]) ?? I18N.en[k] ?? k;
    for (const [n, v] of Object.entries(vars)) s = s.replaceAll(`{${n}}`, v);
    return s;
  };
  const fmtNum = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  const fmtDec = (n, d = 1) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: d }).format(n);
  const inr = (n) => (n == null ? "—" : (n < 0 ? "-₹" : "₹") + fmtNum.format(Math.abs(n)));
  const pct = (x) => Math.round(x * 100) + "%";
  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) e.append(kid.nodeType ? kid : document.createTextNode(kid));
    return e;
  };
  const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
  const cropName = (c) => (state.lang === "hi" ? c.hi : c.en);
  const pickLang = (o) => (o && (o[state.lang] || o.en)) || "";

  async function api(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    let res;
    try { res = await fetch(path + (path.includes("?") ? "&" : "?") + "lang=" + state.lang, opt); }
    catch { throw { message: t("err_network"), network: true }; }
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) {
      const err = new Error();
      err.message = (data && data.message && pickLang(data.message)) || t("err_generic");
      err.details = data && data.details;
      err.code = data && data.error;
      throw err;
    }
    return data;
  }

  const errBox = (e) => {
    const box = el("div", { class: "err-box", role: "alert" }, el("div", { text: e.message || t("err_generic") }));
    if (e.details && e.details.length) {
      box.append(el("div", { text: t("err_fix") }), el("ul", {}, e.details.slice(0, 6).map(d => el("li", { text: `${(d.loc || []).slice(-1)[0] ?? ""}: ${d.msg}` }))));
      e.details.forEach(d => { const f = $(`[name="${(d.loc || []).slice(-1)[0]}"]`); if (f) f.classList.add("bad"); });
    }
    return box;
  };

  // ---------------------------------------------------------------- i18n apply
  function applyLang() {
    document.documentElement.lang = state.lang;
    $$("[data-i18n]").forEach(n => { n.textContent = t(n.dataset.i18n); });
    $$("[data-i18n-ph]").forEach(n => { n.placeholder = t(n.dataset.i18nPh); });
    $$("[data-lang]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.lang === state.lang)));
    $("#mic-label").textContent = t("mic_start");
    $("#mic").setAttribute("aria-label", t("mic_start"));
    $("#place-mic").setAttribute("aria-label", t("mic_start"));
    renderPlace(); renderExamples(); renderCropSelects(); renderRecHints(); renderMode(); renderDemo();
    document.title = (state.lang === "hi" ? "किसान साथी" : "KisanSathi");
    if (voice) voice.lang = speechLang();
  }
  const speechLang = () => (state.lang === "hi" ? "hi-IN" : "en-IN");

  // ---------------------------------------------------------------- location
  function renderPlace() {
    const n = $("#place-name");
    if (state.loc && state.loc.lat != null) {
      n.textContent = [state.loc.name, state.loc.state].filter(Boolean).join(", ");
      n.classList.remove("unset");
    } else { n.textContent = t("loc_none"); n.classList.add("unset"); }
  }
  async function searchPlace(q) {
    const list = clear($("#place-results"));
    list.append(el("li", {}, el("small", { text: t("loading") })));
    try {
      const r = await api("/api/geocode?q=" + encodeURIComponent(q));
      clear(list);
      if (!r.results.length) { list.append(el("li", {}, el("small", { text: t("loc_notfound") }))); return; }
      r.results.forEach(h => list.append(el("li", {}, el("button", {
        type: "button", onclick: () => setLocation({ name: h.name, state: h.admin1, district: h.admin2, lat: h.lat, lon: h.lon }),
      }, el("b", { text: h.name }), el("small", { text: [h.admin2, h.admin1].filter(Boolean).join(", ") })))));
    } catch (e) { clear(list).append(el("li", {}, errBox(e))); }
  }
  function setLocation(loc) {
    state.loc = loc; store.set("ks.loc", JSON.stringify(loc));
    $("#place-panel").hidden = true; clear($("#place-results")); renderPlace();
    if (!$("#tab-today").hidden) loadToday();
  }
  const needLoc = (out) => {
    if (state.loc && state.loc.lat != null) return false;
    clear(out).append(el("div", { class: "err-box", role: "alert", text: t("loc_need") }));
    $("#place-panel").hidden = false; $("#place-q").focus();
    return true;
  };

  // ---------------------------------------------------------------- data freshness
  const staleSources = new Map();
  function noteSources(sources = []) {
    sources.forEach(s => { if (s.stale) staleSources.set(s.source, s); else staleSources.delete(s.source); });
    const b = $("#stale-banner");
    if (staleSources.size) {
      b.textContent = t("stale_banner", { list: [...staleSources.keys()].map(k => t("src_" + k)).join(", ") });
      b.hidden = false;
    } else b.hidden = true;
  }
  function chips(sources = []) {
    if (!sources.length) return null;
    return el("div", { class: "chips", "aria-label": t("sources") }, sources.map(s => {
      const when = s.fetched_at ? new Date(s.fetched_at).toLocaleTimeString(state.lang === "hi" ? "hi-IN" : "en-IN", { hour: "2-digit", minute: "2-digit" }) : "";
      return el("span", { class: "chip" + (s.stale ? " stale" : "") }, `${t("src_" + s.source)}: ${s.stale ? t("src_stale") : t("src_live")}${when ? " " + when : ""}`);
    }));
  }

  // ---------------------------------------------------------------- crops
  async function loadCrops() {
    try { state.crops = (await api("/api/crops")).crops; } catch { state.crops = []; }
    renderCropSelects(); renderRecHints();
  }
  function renderCropSelects() {
    $$(".crop-select").forEach(sel => {
      const cur = sel.value;
      clear(sel);
      if (sel.id === "today-crop") sel.append(el("option", { value: "", text: "—" }));
      state.crops.forEach(c => sel.append(el("option", { value: c.id, text: cropName(c) })));
      if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
      else if (sel.id !== "today-crop" && state.crops.length) sel.value = (state.crops.find(c => c.id === "tomato") || state.crops[0]).id;
    });
  }
  function renderRecHints() {
    const form = $("#profit-form"); const c = state.crops.find(x => x.id === form.crop.value);
    if (!c) return;
    $$("[data-rec]", form).forEach(inp => {
      const v = c.rec[inp.dataset.rec];
      inp.placeholder = String(v);
      inp.parentElement.querySelector(".rec").textContent = t("hint_rec", { n: v + " kg" });
    });
    $("#seed-cost").placeholder = String(c.seed_cost);
    $("#seed-hint").textContent = t("hint_seedcost", { n: inr(c.seed_cost) });
  }

  // ---------------------------------------------------------------- tabs
  function showTab(name) {
    $$(".tab").forEach(s => { s.hidden = s.id !== "tab-" + name; });
    $$(".tabs [role=tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    window.scrollTo({ top: 0 });
    if (name === "today") loadToday();
    store.set("ks.tab", name);
  }

  // ---------------------------------------------------------------- voice (Web Speech API)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let voice = null, voiceTarget = null;
  function setMicUI(on) {
    $("#mic").classList.toggle("live", on); $("#mic").setAttribute("aria-pressed", String(on));
    $("#mic-label").textContent = on ? t("mic_stop") : t("mic_start");
    $("#place-mic").classList.toggle("on", on);
  }
  function micStatus(msg, isErr = false) { const s = $("#mic-status"); s.textContent = msg || ""; s.classList.toggle("err", isErr); }
  function initVoice() {
    $("#place-mic").innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-6a3.5 3.5 0 0 0-7 0v6A3.5 3.5 0 0 0 12 15Zm6-3.5a1 1 0 1 0-2 0 4 4 0 0 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.91V20H9.5a1 1 0 1 0 0 2h5a1 1 0 1 0 0-2H13v-2.59A6 6 0 0 0 18 11.5Z"/></svg>';
    if (!SR) { $("#place-mic").hidden = true; return; }
    voice = new SR(); voice.lang = speechLang(); voice.interimResults = true; voice.continuous = false; voice.maxAlternatives = 1;
    let finalText = "", heard = false;
    voice.onstart = () => { finalText = ""; heard = false; setMicUI(true); micStatus(t("mic_listening")); };
    voice.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i]; if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript;
      }
      heard = true; const text = (finalText + interim).trim();
      if (voiceTarget) voiceTarget.value = text;
    };
    voice.onerror = (ev) => {
      const map = { "not-allowed": "mic_denied", "service-not-allowed": "mic_denied", "no-speech": "mic_nospeech", "network": "mic_network" };
      micStatus(t(map[ev.error] || "mic_nospeech"), true); heard = false; finalText = "";
    };
    voice.onend = () => {
      setMicUI(false);
      const target = voiceTarget, text = (finalText || (target && target.value) || "").trim();
      if (heard && text && !$("#mic-status").classList.contains("err")) {
        micStatus("");
        if (target === $("#ask-q")) ask(text);
        else if (target === $("#place-q")) searchPlace(text);
      } else if (!$("#mic-status").classList.contains("err")) micStatus("");
    };
  }
  let listening = false;
  function toggleVoice(target) {
    if (!SR || !voice) { micStatus(t("mic_unsupported"), true); return; }
    if (listening) { voice.stop(); listening = false; return; }
    voiceTarget = target; voice.lang = speechLang(); micStatus("");
    try { voice.start(); listening = true; } catch { /* already started */ }
    voice.addEventListener("end", () => { listening = false; }, { once: true });
  }
  function speak(text, btn) {
    if (!("speechSynthesis" in window)) return;
    const synth = window.speechSynthesis;
    if (state.speaking === btn) { synth.cancel(); return; }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text); u.lang = speechLang();
    const v = synth.getVoices().find(x => x.lang && x.lang.toLowerCase().startsWith(state.lang === "hi" ? "hi" : "en"));
    if (v) u.voice = v;
    const reset = () => { if (state.speaking) state.speaking.textContent = t("listen"); state.speaking = null; };
    u.onend = u.onerror = reset;
    state.speaking = btn; btn.textContent = t("stop_listen"); synth.speak(u);
  }

  // ---------------------------------------------------------------- ask / agent
  function renderExamples() {
    const box = clear($("#examples"));
    ["ex1", "ex2", "ex3", "ex4"].forEach(k => box.append(el("button", { type: "button", text: t(k), onclick: () => ask(t(k)) })));
  }
  function renderMode() {
    const h = state.health; const n = $("#agent-mode");
    if (!h) { n.textContent = ""; return; }
    n.textContent = h.agent_mode === "rules" ? t("mode_rules") : t("mode_ai", { m: h.agent_mode });
  }
  async function loadHealth() { try { state.health = await api("/api/health"); } catch { state.health = null; } renderMode(); renderDemo(); }

  const reveal = (n) => n.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });

  async function ask(text) {
    text = (text || "").trim(); if (!text || state.busy) return;
    state.busy = true; $("#ask-q").value = "";
    const a = el("div", { class: "a" }, el("span", { class: "dots", text: t("thinking") }));
    const block = el("article", { class: "qa pending" }, el("div", { class: "q", text: text }), a);
    $("#thread").prepend(block);
    reveal(block);
    try {
      const loc = state.loc ? { name: state.loc.name, lat: state.loc.lat, lon: state.loc.lon, state: state.loc.state, district: state.loc.district } : null;
      const r = await api("/api/chat", { message: text, history: state.history.slice(-8), lang: state.lang, location: loc });
      state.history.push({ role: "user", content: text }, { role: "assistant", content: r.answer });
      noteSources(r.sources); block.classList.remove("pending"); renderAnswer(a, r); reveal(block);
    } catch (e) { clear(a).append(errBox(e)); block.classList.remove("pending"); }
    finally { state.busy = false; }
  }
  function renderAnswer(a, r) {
    clear(a);
    (r.answer || "").split(/\n{2,}/).forEach(p => a.append(el("p", { text: p })));
    const actions = el("div", { class: "qa-actions" });
    if ("speechSynthesis" in window) { const b = el("button", { type: "button", class: "pill", text: t("listen") }); b.addEventListener("click", () => speak(r.answer, b)); actions.append(b); }
    a.append(actions);
    if (r.citations && r.citations.length) a.append(el("p", { class: "cites", text: t("advisory_from") + ": " + r.citations.map(c => c.source).filter((v, i, s) => s.indexOf(v) === i).join("; ") }));
    const sc = chips(r.sources); if (sc) a.append(sc);
    if (r.tool_trace && r.tool_trace.length) {
      a.append(el("details", { class: "how" }, el("summary", { text: t("how") }),
        el("ul", { class: "trace" }, r.tool_trace.map(x => {
          const cls = !x.ok ? "fail" : x.stale ? "st" : "ok";
          const label = !x.ok ? t("trace_fail") + (x.error ? ` (${x.error})` : "") : x.stale ? t("trace_stale") : t("trace_ok");
          return el("li", {}, el("span", { text: t("tool_" + x.tool) }), el("span", { class: cls, text: label }));
        }))));
    }
  }

  // ---------------------------------------------------------------- sell tab
  function numOrUndef(v) { if (v === "" || v == null) return undefined; const n = Number(v); return Number.isFinite(n) ? n : undefined; }
  function formPayload(form, numeric) {
    const out = {};
    for (const [k, v] of new FormData(form).entries()) {
      if (numeric.includes(k)) { const n = numOrUndef(v); if (n !== undefined) out[k] = n; } else if (v !== "") out[k] = v;
    }
    return out;
  }
  const clearBad = (form) => $$(".bad", form).forEach(n => n.classList.remove("bad"));
  async function submitSell(ev) {
    ev.preventDefault(); const out = $("#sell-out"); clearBad(ev.target);
    if (needLoc(out)) return;
    const body = formPayload(ev.target, ["quantity_q", "cost_per_km", "commission_pct"]);
    Object.assign(body, { lat: state.loc.lat, lon: state.loc.lon, state: state.loc.state, district: state.loc.district, origin_place: state.loc.name });
    const btn = $("button[type=submit]", ev.target); btn.disabled = true;
    clear(out).append(el("div", { class: "loading", text: t("loading") + "…" }));
    try { const r = await api("/api/compare", body); noteSources(r.sources); renderSell(out, r); }
    catch (e) { clear(out).append(errBox(e)); } finally { btn.disabled = false; }
  }
  function renderSell(out, r) {
    clear(out);
    const best = r.options[0];
    out.append(el("div", { class: "verdict" },
      el("div", { class: "label", text: t("res_best") }),
      el("div", { class: "big", text: `${best.market}` }),
      el("div", { class: "sub num", text: `${t("th_net")}: ${inr(best.net)} (${inr(best.net_per_q)} ${t("per_q")})` }),
    ));
    out.append(el("p", { class: "bestline", style: "margin-top:14px", text: pickLang(r.explanation) }));
    const head = el("tr", {}, ["th_market", "th_quality", "th_net"].map((k, i) => el("th", { class: i === 2 ? "r" : "", text: t(k) })));
    const rows = r.options.map(o => {
      const q = o.best_departure.quality; const cls = q >= .85 ? "" : q >= .7 ? "mid" : "low";
      const hhmm = o.best_departure.depart.slice(11, 16);
      return el("tr", { class: o.market === r.best ? "best" : "" },
        el("td", {}, el("b", { text: o.market }), el("small", { class: "sub", text: `${inr(o.price.modal)}/q · ${fmtDec(o.distance_km, 0)} ${t("km")}${o.route_estimated ? " *" : ""}` }),
          el("small", { class: "sub", text: `${t("th_leave")} ${hhmm}` })),
        el("td", {}, el("span", { class: "qbar" }, el("i", { class: cls, style: `width:${Math.round(q * 100)}%` })), pct(q)),
        el("td", { class: "r" }, el("b", { text: inr(o.net) }), el("small", { class: "sub", text: `${inr(o.net_per_q)}/q` })));
    });
    out.append(el("div", { class: "tablewrap" }, el("table", { class: "ledger" }, el("thead", {}, head), el("tbody", {}, rows))));
    if (r.options.some(o => o.route_estimated)) out.append(el("small", { text: "* " + t("route_est") }));
    if (r.skipped && r.skipped.length) out.append(el("p", {}, el("small", { text: r.skipped.map(s => `${s.market}: ${s.reason}`).join("; ") })));
    out.append(el("details", { class: "more" }, el("summary", { text: t("assump_title") }), el("ul", {}, r.assumptions.map(a => el("li", { text: pickLang(a) })))));
    const sc = chips(r.sources); if (sc) out.append(sc);
  }

  // ---------------------------------------------------------------- profit tab
  const PROFIT_NUM = ["area_acres", "sowing_delay_days", "fertilizer_n_kg_acre", "fertilizer_p2o5_kg_acre", "fertilizer_k2o_kg_acre", "manure_t_acre",
    "fertilizer_cost_acre", "seed_cost_acre", "pesticide_cost_acre", "labour_days_acre", "wage_per_day", "machinery_cost_acre", "irrigation_cost_acre",
    "land_rent_acre", "other_cost_acre", "price_per_q", "transport_per_q", "commission_pct", "post_harvest_loss_pct", "msp_per_q"];
  async function submitProfit(ev) {
    ev.preventDefault(); const out = $("#profit-out"); clearBad(ev.target);
    const body = formPayload(ev.target, PROFIT_NUM);
    if (state.loc && state.loc.lat != null) Object.assign(body, { lat: state.loc.lat, lon: state.loc.lon, state: state.loc.state, district: state.loc.district });
    const btn = $("button[type=submit]", ev.target); btn.disabled = true;
    clear(out).append(el("div", { class: "loading", text: t("loading") + "…" }));
    try { const r = await api("/api/profit", body); noteSources(r.sources); renderProfit(out, r, body); }
    catch (e) { clear(out).append(errBox(e)); } finally { btn.disabled = false; }
  }
  const COST_COLORS = { seed: "#34457f", fertilizer: "#1f6f42", pesticide: "#8a5cc2", labour: "#e9a516", machinery: "#b23a2b", irrigation: "#2a8fb0", land_rent: "#7c6a58", other: "#98a0bd" };
  function renderProfit(out, r, body) {
    clear(out);
    if (r.needs_price) {
      out.append(el("div", { class: "err-box", role: "alert" }, el("div", { text: (r.price_error ? r.price_error + " — " : "") + pickLang(r.advice[0]) })));
    }
    const y = r.yield.per_acre;
    if (!r.needs_price) {
      const p = r.profit, neg = p.expected < 0;
      const lo = Math.min(p.cautious, p.expected, p.optimistic), hi = Math.max(p.cautious, p.expected, p.optimistic), span = (hi - lo) || 1;
      const at = (v) => ((v - lo) / span * 100).toFixed(1) + "%";
      out.append(el("div", { class: "verdict" },
        el("div", { class: "label", text: neg ? t("res_loss") : t("res_profit") }),
        el("div", { class: "big " + (neg ? "neg" : "pos"), text: inr(p.expected) }),
        el("div", { class: "sub num", text: t("res_range", { a: inr(p.cautious), b: inr(p.optimistic) }) }),
        el("div", { class: "rangebar", "aria-hidden": "true" }, el("span", { class: "span", style: `left:0;right:0` }), el("i", { style: `left:0` }), el("i", { class: "exp", style: `left:${at(p.expected)}` }), el("i", { style: `left:100%` })),
      ));
      const kv = el("dl", { class: "kv" });
      const add = (k, v) => kv.append(el("div", {}, el("dt", { text: k }), el("dd", { text: v })));
      add(t("res_be"), `${inr(r.breakeven_price)}`);
      add(t("res_yield"), `${fmtDec(y.p10)}–${fmtDec(y.p90)} q`);
      add(t("res_total_q"), `${fmtDec(r.marketable_q.expected)} q`);
      add(t("res_roi"), r.roi_pct == null ? "—" : `${fmtDec(r.roi_pct, 0)}%`);
      out.append(kv);
      const src = r.price.source === "farmer" ? t("price_farmer") : t("price_live", { n: r.price.n_markets, d: r.price.as_of });
      out.append(el("p", { style: "margin-top:12px" }, el("small", { text: `${t("price_used")}: ${inr(r.price.mid)} ${t("per_q")} — ${src}` })));
    } else {
      out.append(el("dl", { class: "kv" }, el("div", {}, el("dt", { text: t("res_yield") }), el("dd", { text: `${fmtDec(y.p10)}–${fmtDec(y.p90)} q` }))));
    }

    // costs
    const items = Object.entries(r.costs.per_acre).filter(([, v]) => v > 0);
    const totalAcre = items.reduce((s, [, v]) => s + v, 0) || 1;
    out.append(el("section", { class: "block" }, el("h3", { text: t("costs_title") }),
      el("div", { class: "stack", role: "img", "aria-label": t("costs_title") }, items.map(([k, v]) => el("i", { style: `width:${v / totalAcre * 100}%;background:${COST_COLORS[k]}` }))),
      el("ul", { class: "legend" }, items.map(([k, v]) => el("li", {}, el("span", { class: "sw", style: `background:${COST_COLORS[k]}` }), el("span", { class: "nm", text: t("c_" + k) }), el("span", { class: "num", text: inr(v) })))),
      el("p", { style: "margin-top:8px" }, el("b", { text: `${t("c_total")}: ${inr(r.costs.fixed_total)}` }), " ", el("small", { text: `(${inr(r.costs.fixed_per_acre)} ${t("per_acre")})` }))));

    // scenarios
    if (r.scenario_grid) {
      const cell = (v) => el("td", { class: v < 0 ? "neg" : "pos", text: inr(v) });
      out.append(el("section", { class: "block" }, el("h3", { text: t("scen_title") }),
        el("div", { class: "tablewrap" }, el("table", { class: "scen" },
          el("thead", {}, el("tr", {}, el("th"), ["scen_low", "scen_mid", "scen_high"].map(k => el("th", { text: t(k) })))),
          el("tbody", {}, r.scenario_grid.map(row => el("tr", {}, el("td", { text: t("scen_" + row.yield) }), cell(row.low), cell(row.mid), cell(row.high))))))));
    }

    // nutrients
    out.append(el("section", { class: "block" }, el("h3", { text: t("nutr_title") }),
      el("ul", { class: "nutr" }, r.nutrients.map(n => {
        const w = Math.min(100, (n.ratio || 0) / 1.5 * 100);
        return el("li", {}, el("b", { text: n.nutrient }),
          el("div", {}, el("div", { class: "meter" }, el("i", { class: n.status, style: `width:${w}%` }), el("b")),
            el("small", { text: `${fmtDec(n.applied, 0)} ${t("applied")} / ${fmtDec(n.recommended, 0)} ${t("recommended")}` })),
          el("span", { class: "tag " + n.status, text: t("n_" + n.status) }));
      }))));

    // advice
    out.append(el("section", { class: "block" }, el("h3", { text: t("advice_title") }),
      el("ul", { class: "advice" }, r.advice.map(a => el("li", { class: a.severity, text: pickLang(a) })))));
    out.append(el("details", { class: "more" }, el("summary", { text: t("assump_title") }), el("ul", {}, r.assumptions.map(a => el("li", { text: pickLang(a) })))));
    const sc = chips(r.sources); if (sc) out.append(sc);
  }

  // ---------------------------------------------------------------- today tab
  async function loadToday() {
    const wo = $("#weather-out"), mo = $("#mandi-out");
    if (!state.loc || state.loc.lat == null) { needLoc(wo); clear(mo); return; }
    const crop = $("#today-crop").value;
    clear(wo).append(el("div", { class: "loading", text: t("loading") + "…" }));
    clear(mo).append(el("div", { class: "loading", text: t("loading") + "…" }));
    api(`/api/weather?lat=${state.loc.lat}&lon=${state.loc.lon}` + (crop ? `&crop=${crop}` : ""))
      .then(w => { noteSources(w.sources); renderWeather(wo, w, !!crop); }).catch(e => clear(wo).append(errBox(e)));
    if (!crop) { clear(mo); return; }
    api(`/api/mandi?crop=${crop}&state=${encodeURIComponent(state.loc.state || "")}&district=${encodeURIComponent(state.loc.district || "")}`)
      .then(m => { noteSources(m.sources); renderMandi(mo, m); }).catch(e => clear(mo).append(errBox(e)));
  }
  function renderWeather(out, w, hasCrop) {
    clear(out);
    const alerts = el("div", { class: "alerts" }, w.alerts.map(a => el("div", { class: "alert " + a.type, text: pickLang(a) })));
    if (w.alerts.length) out.append(alerts); else if (hasCrop) out.append(el("p", { class: "ok-note", text: t("today_alerts_none") }));
    const maxRain = Math.max(20, ...w.days.map(d => d.rain_mm || 0));
    out.append(el("div", { class: "wx" }, w.days.map(d => {
      const dt = new Date(d.date + "T00:00:00");
      return el("div", { class: "wx-day" },
        el("span", { class: "d", text: dt.toLocaleDateString(state.lang === "hi" ? "hi-IN" : "en-IN", { weekday: "short", day: "numeric" }) }),
        el("div", {}, el("span", { class: "t num", text: `${Math.round(d.tmax)}° / ${Math.round(d.tmin)}°C` }), el("div", { class: "rainbar" }, el("i", { style: `width:${Math.min(100, (d.rain_mm || 0) / maxRain * 100)}%` }))),
        el("span", { class: "r", text: `${t("rain")} ${fmtDec(d.rain_mm || 0, 0)} mm` }));
    })));
    const sc = chips(w.sources); if (sc) out.append(sc);
  }
  function renderMandi(out, m) {
    clear(out);
    if (!m.markets.length) { out.append(el("p", { text: "—" })); return; }
    out.append(el("div", { class: "tablewrap" }, el("table", { class: "ledger" },
      el("thead", {}, el("tr", {}, [t("th_market"), t("modal"), t("range")].map((x, i) => el("th", { class: i ? "r" : "", text: x })))),
      el("tbody", {}, m.markets.slice(0, 12).map(x => el("tr", {}, el("td", {}, x.market, el("br"), el("small", { text: `${x.district} · ${x.date}` })),
        el("td", { class: "r", text: inr(x.modal) }), el("td", { class: "r", text: `${fmtNum.format(x.min)}–${fmtNum.format(x.max)}` })))))));
    const sc = chips(m.sources); if (sc) out.append(sc);
  }

  // ---------------------------------------------------------------- demo tools
  const DEMO_SOURCES = ["weather", "mandi", "route", "geocode"];
  function renderDemo() {
    const d = $("#demo"); if (!state.health || state.health.debug_endpoints === false) { d.hidden = true; return; }
    d.hidden = false; const box = clear($("#demo-toggles")); const down = new Set(state.health.simulated_outages || []);
    DEMO_SOURCES.forEach(s => box.append(el("button", {
      type: "button", "aria-pressed": String(down.has(s)), text: t("src_" + s),
      onclick: async () => {
        down.has(s) ? down.delete(s) : down.add(s);
        try { const r = await api("/api/debug/outage", { sources: [...down] }); state.health.simulated_outages = r.simulated_outages; } catch { /* disabled on server */ }
        renderDemo();
      },
    })));
  }

  // ---------------------------------------------------------------- wire up
  function init() {
    $$("[data-lang]").forEach(b => b.addEventListener("click", () => { state.lang = b.dataset.lang; store.set("ks.lang", state.lang); applyLang(); }));
    $$(".tabs [role=tab]").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
    $("#place-change").addEventListener("click", () => { const p = $("#place-panel"); p.hidden = !p.hidden; if (!p.hidden) $("#place-q").focus(); });
    $("#place-form").addEventListener("submit", ev => { ev.preventDefault(); searchPlace($("#place-q").value.trim()); });
    $("#place-mic").addEventListener("click", () => toggleVoice($("#place-q")));
    $("#mic").addEventListener("click", () => toggleVoice($("#ask-q")));
    $("#ask-form").addEventListener("submit", ev => { ev.preventDefault(); ask($("#ask-q").value); });
    $("#sell-form").addEventListener("submit", submitSell);
    $("#profit-form").addEventListener("submit", submitProfit);
    $("#profit-form").crop.addEventListener("change", renderRecHints);
    $("#today-crop").addEventListener("change", loadToday);
    $$("input,select").forEach(i => i.addEventListener("input", () => i.classList.remove("bad")));
    if (!state.loc) $("#place-panel").hidden = false;
    initVoice(); applyLang(); loadCrops(); loadHealth();
    showTab(["ask", "sell", "profit", "today"].includes(store.get("ks.tab")) ? store.get("ks.tab") : "ask");
    if ("speechSynthesis" in window) window.speechSynthesis.getVoices();
  }
  init();
})();
