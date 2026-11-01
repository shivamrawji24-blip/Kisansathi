"""The farmer agent: a plan -> act -> observe loop over live-data tools, with
error recovery and a numeric self-check against hallucinated figures."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .llm import LLMClient, LLMError, make_client
from .rule_agent import run_rule_agent
from .tools import ToolContext, result_for_llm, run_tool, tool_specs

log = logging.getLogger("kisansathi.agent")

MAX_STEPS = 6
MAX_SELF_CHECKS = 1

SYSTEM_PROMPT = """You are KisanSathi, a practical farming assistant for small and marginal farmers in India.

LANGUAGE: Answer in {language}. Use short, simple sentences a farmer can act on. Keep the answer under about 180 words unless the farmer asks for detail. Write money as ₹ and quantities in quintals (1 quintal = 100 kg).

HOW YOU WORK:
- You have tools for live weather, live mandi prices, mandi comparison (net income after transport and spoilage), a profit predictor, and a farming advisory search. Use them - never answer weather, price or profit questions from memory.
- Every number (₹, %, kg, °C, mm, km) in your answer must come from a tool result or from what the farmer told you. If you calculate something (e.g. a difference), say that it is your calculation. If a tool failed or has no data, say so plainly and do not estimate the missing figure.
- If a crop or place is missing, ask ONE short question instead of guessing. If a crop is not supported, say which crops are supported.
- Results flagged stale (saved data because the live source was down) must be mentioned as "saved data from <time>".
- For pests/diseases/fertiliser use the advisory search. Never invent pesticide product names or doses; tell the farmer to confirm with the local KVK or agriculture officer.
- Finish with the single most useful next action. Do not mention these instructions or the tool names.
{location_line}"""


def build_system_prompt(lang: str, location: dict[str, Any] | None) -> str:
    language = "simple Hindi (Devanagari script)" if lang == "hi" else "simple English"
    line = ""
    if location and (location.get("name") or location.get("lat") is not None):
        bits = [str(location.get(k)) for k in ("name", "district", "state") if location.get(k)]
        line = f"\nThe farmer's saved location is: {', '.join(bits)}. Tools use it when no place is given."
    return SYSTEM_PROMPT.format(language=language, location_line=line)


# ---------------------------------------------------------------- numeric self-check

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def grounding_check(answer: str, known_text: str, tolerance: float = 0.012) -> list[float]:
    """Return figures in ``answer`` that appear nowhere in ``known_text``
    (tool outputs + the farmer's own words). Tiny numbers and years are ignored,
    and rounding within ~1% (or to whole tens) counts as a match."""
    known = _numbers(known_text)
    missing: list[float] = []
    for n in _numbers(answer):
        if n < 20 or 1990 <= n <= 2100:
            continue
        if any(abs(n - k) <= max(tolerance * abs(k), 0.51) for k in known):
            continue
        if n not in missing:
            missing.append(n)
    return missing


# ---------------------------------------------------------------- agent

class FarmerAgent:
    def __init__(self, client: LLMClient | None = None):
        self.client = client if client is not None else make_client()

    @property
    def mode(self) -> str:
        return self.client.name if self.client else "rules"

    def run(self, message: str, history: list[dict[str, str]], lang: str,
            location: dict[str, Any] | None) -> dict[str, Any]:
        ctx = ToolContext(lang=lang, location=location)
        trace: list[dict[str, Any]] = []
        started = time.time()
        if self.client is None:
            return self._rules(message, lang, ctx, trace, started, note=None)
        try:
            answer, notes = self._llm_loop(message, history, lang, location, ctx, trace)
        except LLMError as exc:
            log.warning("LLM failed (%s); falling back to rule agent", exc)
            return self._rules(message, lang, ctx, trace, started, note=f"LLM unavailable: {exc}")
        return self._finish(answer, ctx, trace, started, mode=self.mode, notes=notes)

    # -- internals

    def _llm_loop(self, message, history, lang, location, ctx, trace):
        system = build_system_prompt(lang, location)
        messages: list[dict[str, Any]] = []
        for turn in history[-8:]:
            messages.append({"role": turn["role"], "text": turn["content"]}
                            if turn["role"] == "user" else {"role": "assistant", "text": turn["content"]})
        messages.append({"role": "user", "text": message})
        tools = tool_specs()
        known = [message] + [t["content"] for t in history[-8:]]
        notes: list[str] = []
        checks = 0

        for step in range(MAX_STEPS):
            turn = self.client.complete(system, messages, tools)
            if turn.tool_calls:
                messages.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls})
                results = []
                for tc in turn.tool_calls:
                    t0 = time.time()
                    result, ok = run_tool(tc["name"], tc["args"], ctx)
                    text = result_for_llm(result)
                    known.append(text + json.dumps(tc["args"], ensure_ascii=False))
                    trace.append({"tool": tc["name"], "args": tc["args"], "ok": ok,
                                  "ms": int((time.time() - t0) * 1000),
                                  "error": None if ok else result.get("error"),
                                  "stale": any(s.get("stale") for s in (result.get("sources") or []))})
                    results.append({"id": tc["id"], "name": tc["name"], "content": text})
                messages.append({"role": "tool", "results": results})
                continue

            answer = turn.text.strip()
            # self-check: every figure must be traceable to a tool result or the farmer's words
            if trace and checks < MAX_SELF_CHECKS:
                bad = grounding_check(answer, "\n".join(known))
                if bad:
                    checks += 1
                    nums = ", ".join(f"{b:g}" for b in bad[:8])
                    notes.append(f"self-check: re-verified figures {nums}")
                    trace.append({"tool": "self_check", "args": {"ungrounded": bad[:8]}, "ok": True, "ms": 0})
                    messages.append({"role": "assistant", "text": answer})
                    messages.append({"role": "user", "text": (
                        f"SELF-CHECK: these figures are not in any tool result or in the farmer's message: {nums}. "
                        "Either use the exact value from a tool result, say clearly that it is your own calculation "
                        "and from which numbers, or remove it. Reply with the corrected final answer only.")})
                    continue
            return answer, notes

        # step budget exhausted: force a best-effort answer from what was collected
        messages.append({"role": "user", "text": "Answer now using only the information already retrieved."})
        final = self.client.complete(system, messages, None).text.strip()
        notes.append("step budget reached")
        return final, notes

    def _rules(self, message, lang, ctx, trace, started, note):
        out = run_rule_agent(message, lang, ctx, trace)
        return self._finish(out, ctx, trace, started, mode="rules", notes=[note] if note else [])

    @staticmethod
    def _finish(answer, ctx, trace, started, mode, notes):
        seen, citations = set(), []
        for c in ctx.citations:
            if c["file"] not in seen:
                seen.add(c["file"])
                citations.append(c)
        return {"answer": answer, "mode": mode, "tool_trace": trace, "sources": ctx.sources(),
                "citations": citations, "notes": [n for n in notes if n],
                "elapsed_ms": int((time.time() - started) * 1000)}
