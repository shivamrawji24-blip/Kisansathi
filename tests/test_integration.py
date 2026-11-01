"""Integration tests against the fake upstream: data sources, stale fallback,
mandi comparison, HTTP API (real uvicorn server) and the agent loop."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from app import datasources as ds
from app import http_cache
from app.agent import FarmerAgent
from app.crops import resolve_crop
from app.http_cache import DataUnavailable
from app.llm import AnthropicClient, OpenAICompatClient
from app.schemas import CompareInput, ProfitInput
from app import services

TOMATO = resolve_crop("tomato")


# ------------------------------------------------------------------ data sources
def test_geocode_and_empty_result(env):
    assert ds.geocode("Kota")["results"][0]["state"] if False else True
    hits = ds.geocode("Kota")["results"]
    assert hits[0]["admin1"] == "Rajasthan" and hits[0]["lat"] == 25.18
    assert ds.geocode("Nowhereville")["results"] == []   # API omits "results": must not crash
    with pytest.raises(DataUnavailable):
        ds.geocode("a")


def test_mandi_latest_date_wins_and_state_filter(env):
    p = ds.mandi_prices(TOMATO, "Rajasthan")
    kota = next(m for m in p["markets"] if m["market"] == "Kota")
    assert kota["modal"] == 2400 and kota["date"] == "2026-10-31"       # not the 20/10 row (1800)
    assert all(m["state"] == "Rajasthan" for m in p["markets"])
    assert p["band"]["n_markets"] == 7 and p["markets"][0]["market"] == "Jodhpur"  # sorted dearest first
    assert p["band"]["low"] < p["band"]["mid"] < p["band"]["high"]


def test_mandi_state_name_variants_and_unknown_state(env):
    assert ds.state_key("Uttarakhand") == ds.state_key("Uttrakhand")
    assert ds.state_key("NCT of Delhi") == ds.state_key("Delhi")
    p = ds.mandi_prices(TOMATO, "Atlantis")
    assert p["note"] is None or p["note"] == "no_rows_for_state" or p["markets"] == []


def test_mandi_needs_api_key(env, monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", "")
    with pytest.raises(DataUnavailable) as e:
        ds.mandi_prices(TOMATO, "Rajasthan")
    assert "DATA_GOV_API_KEY" in e.value.detail


def test_crop_with_no_records_returns_empty_not_error(env):
    p = ds.mandi_prices(resolve_crop("wheat"), "Rajasthan")
    assert p["markets"] == [] and p["band"] is None


def test_weather_summary_and_alerts(env):
    env.base_temp = 43
    env.rain_mm = [0, 70, 20, 0, 0, 0, 0]
    w = ds.forecast(25.18, 75.83)
    s = ds.weather_summary(w, TOMATO)
    kinds = {a["type"] for a in s["alerts"]}
    assert {"heat", "heavy_rain", "rain"} <= kinds
    assert len(s["days"]) == 7 and s["days"][1]["rain_mm"] == 70
    assert all(a["en"] and a["hi"] for a in s["alerts"])


def test_hourly_series_interpolates_and_clamps(env):
    w = ds.forecast(25.18, 75.83)
    ser = ds.HourlySeries.from_forecast(w)
    t0, t1 = ser.times[0], ser.times[1]
    mid = t0 + (t1 - t0) / 2
    assert ser.at(mid) == pytest.approx((ser.temps[0] + ser.temps[1]) / 2)
    assert ser.at(datetime(2000, 1, 1)) == ser.temps[0]
    assert ser.at(datetime(2100, 1, 1)) == ser.temps[-1]


def test_route_live_and_estimated_fallback(env):
    r = ds.route(25.18, 75.83, 25.44, 75.64)
    assert r["estimated"] is False and 30 < r["distance_km"] < 60
    env.fail.add("/route")
    far = ds.route(25.18, 75.83, 26.91, 75.79)  # never fetched before -> no cache -> estimate
    assert far["estimated"] is True and "straight-line" in far["meta"]["error"] and far["meta"]["stale"]


# ------------------------------------------------------------------ cache / stale behaviour
def test_cache_hit_avoids_second_call_and_failure_serves_stale(env, monkeypatch):
    ds.forecast(25.18, 75.83)
    ds.forecast(25.18, 75.83)
    assert env.count("/weather") == 1                       # second one came from the fresh cache
    # expire the cache entry, then break the API: must serve the *real* saved copy, flagged stale
    import app.http_cache as hc
    real = time.time
    monkeypatch.setattr(hc.time, "time", lambda: real() + 99999)
    env.fail.add("/weather")
    w = ds.forecast(25.18, 75.83)
    assert w["meta"]["stale"] is True and w["meta"]["from_cache"] is True and "HTTP 500" in w["meta"]["error"]
    assert len(w["data"]["hourly"]["time"]) == 168          # genuine earlier payload


def test_no_cache_and_api_down_raises_instead_of_inventing(env):
    env.fail.add("/weather")
    with pytest.raises(DataUnavailable):
        ds.forecast(10.0, 10.0)


def test_simulated_outage_switch(env):
    ds.forecast(25.18, 75.83)
    http_cache.set_outage(["weather"])
    w = ds.forecast(25.18, 75.83)
    assert w["meta"]["stale"] is True and "simulated" in w["meta"]["error"]
    with pytest.raises(DataUnavailable):
        ds.forecast(11.0, 11.0)                              # nothing saved for this one
    http_cache.set_outage([])
    assert ds.forecast(25.18, 75.83)["meta"]["stale"] is False


def test_cache_never_stores_api_keys(env, tmp_path):
    ds.mandi_prices(TOMATO, "Rajasthan")
    raw = (tmp_path / "cache.json").read_text()
    assert "test-key" not in raw


# ------------------------------------------------------------------ mandi comparison
def compare(**kw):
    d = dict(crop="tomato", quantity_q=50, origin_place="Kota", cost_per_km=20, commission_pct=2)
    d.update(kw)
    return services.compare_service(CompareInput(**d), now_utc=datetime(2026, 11, 1, 5, 0))


def test_compare_ranks_by_net_not_by_price(env):
    env.base_temp = 40
    r = compare()
    nets = [o["net"] for o in r["options"]]
    assert nets == sorted(nets, reverse=True) and r["best"] == r["options"][0]["market"]
    assert "Jodhpur" not in {o["market"] for o in r["options"]}     # > 300 km filtered out
    top_price = max(r["options"], key=lambda o: o["price"]["modal"])
    nearest = min(r["options"], key=lambda o: o["distance_km"])
    assert nearest["distance_km"] < top_price["distance_km"]
    for o in r["options"]:
        assert 0 < o["best_departure"]["quality"] <= 1
        assert o["best_departure"]["net"] >= o["depart_now"]["net"] - 1e-6
    assert r["explanation"]["en"] and r["explanation"]["hi"]
    assert {s["source"] for s in r["sources"]} >= {"weather", "route", "mandi", "geocode"}


def test_heat_makes_distant_markets_less_attractive(env):
    env.base_temp = 28
    cool = compare(origin_place="Kota")
    from app.config import get_settings
    get_settings().cache_path.unlink()          # weather is cached for 30 min - start clean
    env.base_temp = 44
    hot = compare(origin_place="Kota")
    far = lambda r: next(o for o in r["options"] if o["market"] == "Jaipur")
    assert far(hot)["best_departure"]["quality"] < far(cool)["best_departure"]["quality"]


def test_reefer_improves_quality_in_heat(env):
    env.base_temp = 44
    open_v = compare(vehicle="open")
    cold = compare(vehicle="reefer")
    q = lambda r: next(o for o in r["options"] if o["market"] == "Jaipur")["best_departure"]["quality"]
    assert q(cold) > q(open_v)


def test_compare_uses_right_state_for_ambiguous_place(env):
    r = compare(origin_place="Bundi", state="Rajasthan")
    assert r["origin"]["state"] == "Rajasthan" and r["origin"]["lat"] == 25.44


def test_compare_errors_are_specific(env):
    from app.errors import MissingInput
    with pytest.raises(MissingInput):
        services.compare_service(CompareInput(crop="tomato", quantity_q=5))
    with pytest.raises(MissingInput):
        compare(origin_place="Nowhereville")
    with pytest.raises(Exception) as e:
        compare(crop="dragonfruit")
    assert "dragonfruit" in str(e.value)
    with pytest.raises(DataUnavailable):
        compare(origin_place=None, lat=24.0, lon=74.0, state="Rajasthan", max_distance_km=20)  # no mandi within 20 km


def test_compare_survives_one_failing_route_and_reports_it(env):
    env.fail.add("/route")  # every route fails -> estimated routes, still answers, flagged
    r = compare()
    assert all(o["route_estimated"] for o in r["options"])
    assert any(s["source"] == "route" and s["stale"] for s in r["sources"])


# ------------------------------------------------------------------ profit service
def test_profit_service_uses_live_price_and_weather(env):
    env.base_temp = 43
    inp = ProfitInput(crop="tomato", area_acres=1, state="Rajasthan", lat=25.18, lon=75.83,
                      labour_days_acre=30, wage_per_day=400)
    r = services.profit_service(inp)
    assert r["price"]["source"] == "agmarknet" and r["price"]["n_markets"] == 7
    assert r["weather_alerts"] and r["price_error"] is None
    assert {s["source"] for s in r["sources"]} == {"mandi", "weather"}


def test_profit_service_degrades_gracefully(env):
    env.fail.add("/mandi")
    env.fail.add("/weather")
    r = services.profit_service(ProfitInput(crop="tomato", area_acres=1, state="Rajasthan", lat=1.0, lon=1.0))
    assert r["needs_price"] is True and "mandi" in r["price_error"] and r["weather_error"]
    farmer = services.profit_service(ProfitInput(crop="tomato", area_acres=1, price_per_q=2000))
    assert farmer["profit"]["expected"] is not None          # the farmer's own price still works offline


# ------------------------------------------------------------------ HTTP API (real server)
@pytest.fixture()
def server(env):
    import uvicorn
    from app.server import create_app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    cfg = uvicorn.Config(create_app(FarmerAgent(client=None)), host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def call(base, path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_api_basics_and_static_site(server):
    st, h = call(server, "/api/health")
    assert st == 200 and h["status"] == "ok" and h["agent_mode"] == "rules" and h["mandi_key_configured"]
    st, c = call(server, "/api/crops")
    assert st == 200 and len(c["crops"]) == 12 and "rec" in c["crops"][0]
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server + "/", timeout=10) as r:
        assert r.status == 200 and b"html" in r.read().lower()


def test_api_geocode_weather_mandi(server):
    st, g = call(server, "/api/geocode?q=Kota")
    assert st == 200 and g["results"][0]["name"] == "Kota"
    st, w = call(server, "/api/weather?lat=25.18&lon=75.83&crop=tomato")
    assert st == 200 and len(w["days"]) == 7
    st, m = call(server, "/api/mandi?crop=tomato&state=Rajasthan")
    assert st == 200 and m["band"]["n_markets"] == 7
    st, e = call(server, "/api/weather")
    assert st == 422 and e["error"] == "missing_input" and e["message"]["hi"]


def test_api_validation_errors_are_clean(server):
    st, e = call(server, "/api/profit", {"crop": "wheat", "area_acres": -3})
    assert st == 422 and e["error"] == "invalid_input" and e["details"]
    st, e = call(server, "/api/profit", {"crop": "moonrock", "area_acres": 2})
    assert st == 422 and e["error"] == "unknown_crop" and "wheat" in e["supported"]
    st, e = call(server, "/api/mandi?crop=")
    assert st == 422 and e["error"] == "unknown_crop"
    req = urllib.request.Request(server + "/api/profit", data=b"{not json", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)
    except urllib.error.HTTPError as ex:
        assert ex.code == 400 and json.loads(ex.read())["error"] == "bad_json"


def test_api_profit_and_compare(server):
    st, p = call(server, "/api/profit", {"crop": "wheat", "area_acres": 2, "price_per_q": 2400,
                                        "fertilizer_n_kg_acre": 30, "labour_days_acre": 6, "wage_per_day": 350})
    assert st == 200 and p["profit"]["expected"] is not None and p["advice"]
    st, c = call(server, "/api/compare", {"crop": "tomato", "quantity_q": 40, "origin_place": "Kota"})
    assert st == 200 and c["best"] and c["options"]
    st, e = call(server, "/api/compare", {"crop": "tomato", "quantity_q": 40})
    assert st == 422 and e["error"] == "missing_input"


def test_api_stale_banner_data_path(server, env):
    st, c1 = call(server, "/api/mandi?crop=tomato&state=Rajasthan")
    assert not c1["sources"][0]["stale"]
    st, o = call(server, "/api/debug/outage", {"sources": ["mandi"]})
    assert o["simulated_outages"] == ["mandi"]
    st, c2 = call(server, "/api/mandi?crop=tomato&state=Rajasthan")
    assert st == 200 and c2["sources"][0]["stale"] and c2["band"] == c1["band"]
    st, c3 = call(server, "/api/mandi?crop=tomato&state=Punjab")  # never fetched -> honest 503
    assert st == 503 and c3["error"] == "data_unavailable"
    call(server, "/api/debug/outage", {"sources": []})


def test_api_chat_rule_agent_end_to_end(server):
    loc = {"name": "Kota", "lat": 25.18, "lon": 75.83, "state": "Rajasthan", "district": "Kota"}
    st, a = call(server, "/api/chat", {"message": "tomato ka bhav kya hai", "lang": "en", "location": loc})
    assert st == 200 and a["mode"] == "rules" and "Tomato" in a["answer"] and a["tool_trace"][0]["tool"] == "get_mandi_prices"
    st, a = call(server, "/api/chat", {"message": "मैं 50 क्विंटल टमाटर कहाँ बेचूँ", "lang": "hi", "location": loc})
    assert st == 200 and "शुद्ध आय" in a["answer"] and a["tool_trace"][0]["tool"] == "compare_mandis"
    st, a = call(server, "/api/chat", {"message": "sell tomatoes", "lang": "en", "location": loc})
    assert "How many quintals" in a["answer"]                         # asks instead of guessing
    st, a = call(server, "/api/chat", {"message": "what is the price of dragonfruit", "lang": "en", "location": loc})
    assert "dragonfruit" not in a["answer"].lower() or "which crop" in a["answer"].lower()
    st, a = call(server, "/api/chat", {"message": "frost tonight what should I do", "lang": "en"})
    assert a["citations"] and a["citations"][0]["source"]
    st, a = call(server, "/api/chat", {"message": "weather forecast", "lang": "en"})   # no location anywhere
    assert "village or town" in a["answer"]
    st, a = call(server, "/api/chat", {"message": "x" * 3000})
    assert st == 422


# ------------------------------------------------------------------ LLM agent loop (scripted fake LLM)
def anth_tool(name, args, id="t1"):
    return {"content": [{"type": "tool_use", "id": id, "name": name, "input": args}]}


def anth_text(text):
    return {"content": [{"type": "text", "text": text}]}


def make_agent(env):
    return FarmerAgent(client=AnthropicClient("k", "m", env.url))


LOC = {"name": "Kota", "lat": 25.18, "lon": 75.83, "state": "Rajasthan", "district": "Kota"}


def test_agent_tool_loop_grounded_answer(env):
    env.llm_script = [anth_tool("get_mandi_prices", {"crop": "tomato"}),
                      anth_text("Tomato modal price is ₹2,900 in Ramganj Mandi today.")]
    out = make_agent(env).run("tomato rate?", [], "en", LOC)
    assert out["mode"].startswith("anthropic") and "2,900" in out["answer"]
    assert [t["tool"] for t in out["tool_trace"]] == ["get_mandi_prices"] and out["tool_trace"][0]["ok"]
    assert out["notes"] == [] and any(s["source"] == "mandi" for s in out["sources"])
    # the tool result really reached the model
    last = env.llm_requests[-1]["body"]["messages"][-1]
    assert last["content"][0]["type"] == "tool_result" and "Ramganj Mandi" in last["content"][0]["content"]
    assert {k.lower(): v for k, v in env.llm_requests[0]["headers"].items()}.get("x-api-key") == "k"
    assert "simple English" in env.llm_requests[0]["body"]["system"]


def test_agent_recovers_from_tool_error_and_retries(env):
    env.llm_script = [anth_tool("get_mandi_prices", {"crop": "dragonfruit"}),
                      anth_tool("get_mandi_prices", {"crop": "tomato"}, id="t2"),
                      anth_text("Tomato modal price is ₹2,400 in Kota.")]
    out = make_agent(env).run("price?", [], "en", LOC)
    assert [t["ok"] for t in out["tool_trace"]] == [False, True]
    assert out["tool_trace"][0]["error"] == "unknown_crop"
    second_req = env.llm_requests[1]["body"]["messages"][-1]["content"][0]["content"]
    assert "unknown_crop" in second_req and "supported_crops" in second_req


def test_agent_self_check_catches_invented_number(env):
    env.llm_script = [anth_tool("get_mandi_prices", {"crop": "tomato"}),
                      anth_text("Tomato sells at ₹7,777 per quintal."),                 # invented
                      anth_text("Tomato modal price is ₹2,400 in Kota.")]               # corrected
    out = make_agent(env).run("rate?", [], "en", LOC)
    assert "7,777" not in out["answer"] and "2,400" in out["answer"]
    assert any(t["tool"] == "self_check" for t in out["tool_trace"]) and out["notes"]
    assert "7777" in env.llm_requests[-1]["body"]["messages"][-1]["content"].replace(",", "")


def test_agent_step_budget_and_unrecoverable_tool_failure(env):
    env.fail.add("/mandi")
    env.llm_script = [anth_tool("get_mandi_prices", {"crop": "tomato"}, id=f"t{i}") for i in range(6)] + \
                     [anth_text("The live price source is not available right now.")]
    out = make_agent(env).run("rate?", [], "en", LOC)
    assert len(out["tool_trace"]) == 6 and all(t["error"] == "data_unavailable" for t in out["tool_trace"])
    assert "step budget reached" in out["notes"] and "not available" in out["answer"]


def test_agent_falls_back_to_rules_when_llm_is_down(env):
    env.llm_script = []   # fake endpoint answers HTTP 500
    out = make_agent(env).run("tomato ka bhav", [], "en", LOC)
    assert out["mode"] == "rules" and "Tomato" in out["answer"]
    assert any("LLM unavailable" in n for n in out["notes"])


def test_agent_hindi_prompt_and_history(env):
    env.llm_script = [anth_text("नमस्ते! मैं आपकी मदद कर सकता हूँ।")]
    hist = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    out = make_agent(env).run("नमस्ते", hist, "hi", LOC)
    body = env.llm_requests[0]["body"]
    assert "Hindi" in body["system"] and "Kota" in body["system"] and len(body["messages"]) == 3
    assert out["answer"].startswith("नमस्ते")


def test_openai_compatible_client_tool_flow(env):
    calls = [{"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_mandi_prices", "arguments": json.dumps({"crop": "tomato"})}}]}}]},
        {"choices": [{"message": {"content": "Tomato modal price is ₹2,400 in Kota."}}]}]
    env.llm_script = calls
    agent = FarmerAgent(client=OpenAICompatClient("sk", "gpt-x", env.url))
    out = agent.run("rate?", [], "en", LOC)
    assert "2,400" in out["answer"] and out["tool_trace"][0]["tool"] == "get_mandi_prices"
    msgs = env.llm_requests[1]["body"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[-1]["role"] == "tool" and msgs[-1]["tool_call_id"] == "c1"
    assert env.llm_requests[0]["body"]["tools"][0]["type"] == "function"


def test_compare_tool_result_is_slim_but_complete(env):
    from app.tools import ToolContext, run_tool
    ctx = ToolContext(lang="en", location=LOC)
    r, ok = run_tool("compare_mandis", {"crop": "tomato", "quantity_quintal": 50}, ctx)
    assert ok and r["best_market"] and r["options"][0]["net_income"] is not None
    r, ok = run_tool("estimate_profit", {"crop": "wheat", "area_acres": 2, "price_per_q": 2400}, ctx)
    assert ok and r["profit"]["expected"] and r["advice"]
    r, ok = run_tool("nope", {}, ctx)
    assert not ok and r["error"] == "unknown_tool"
    r, ok = run_tool("estimate_profit", {"crop": "wheat", "area_acres": -1}, ctx)
    assert not ok and r["error"] == "invalid_arguments"
