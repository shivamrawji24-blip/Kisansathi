"""Tiny retrieval engine over the bilingual advisory notes in ``knowledge/``.

* Files are markdown with a front-matter block (title, source) and ``## sections``
  holding ``EN:`` and ``HI:`` paragraphs.
* BM25 ranking, pure Python, no model download -> instant start-up, easy to explain.
* Hindi queries are expanded with English keywords so they also hit the English text.
* Every hit carries the *source note* of its file so answers can cite where advice comes from.
* A minimum-score threshold means "no good match" is reported instead of a forced answer.

Drop more ``.md`` files (e.g. text extracted from ICAR/KVK advisories) into
``knowledge/`` using the same layout and they are indexed automatically.
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BASE_DIR

KNOWLEDGE_DIR = BASE_DIR / "knowledge"
MIN_SCORE = 1.2

_TOKEN_RE = re.compile(r"[\wऀ-ॿ]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "it", "my", "i", "me",
         "how", "what", "do", "does", "should", "can", "with", "when", "at", "be", "this", "that", "from",
         "है", "हैं", "का", "की", "के", "में", "को", "से", "और", "या", "पर", "क्या", "कैसे", "मेरी", "मेरा", "मैं"}

HI_EN = {
    "गर्मी": "heat", "लू": "heatwave heat", "पाला": "frost", "ठंड": "cold", "शीतलहर": "cold wave",
    "बारिश": "rain", "बरसात": "rain", "भारी": "heavy", "जलभराव": "waterlogging", "सूखा": "drought dry",
    "सिंचाई": "irrigation", "पानी": "water", "खाद": "fertiliser", "उर्वरक": "fertiliser", "यूरिया": "urea nitrogen",
    "नाइट्रोजन": "nitrogen", "मिट्टी": "soil", "जाँच": "test", "जांच": "test", "कीट": "pest", "कीड़े": "pest insects",
    "रोग": "disease", "बीमारी": "disease", "कीटनाशक": "pesticide", "दवा": "pesticide spray", "छिड़काव": "spray",
    "भंडारण": "storage", "मंडी": "mandi market", "भाव": "price", "दाम": "price", "बेचना": "sell", "बेचें": "sell",
    "खराब": "spoilage", "सड़": "spoilage", "कटाई": "harvest", "टपक": "drip", "फव्वारा": "sprinkler",
    "गोबर": "manure farmyard", "कम्पोस्ट": "compost", "टमाटर": "tomato", "आलू": "potato", "प्याज": "onion",
    "गेहूं": "wheat", "धान": "rice paddy", "चना": "chickpea", "सरसों": "mustard", "कपास": "cotton",
    "फफूंद": "fungal", "झुलसा": "blight", "माइट": "mites",
}


# Crop names are rare words, so BM25 would let them outrank the actual topic
# ("tomato heat" must find the heat advice, not the frost note that mentions tomato).
CROP_TERMS = {"tomato", "potato", "onion", "wheat", "rice", "paddy", "maize", "mustard", "soybean", "chickpea",
              "cotton", "banana", "spinach", "टमाटर", "आलू", "प्याज", "गेहूं", "धान", "चना", "सरसों", "कपास", "केला", "पालक"}
CROP_WEIGHT = 0.2


def _stem(tok: str) -> str:
    if tok.isascii() and len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def expand_query(query: str) -> list[str]:
    tokens = tokenize(query)
    extra: list[str] = []
    for tok in tokens:
        for hi, en in HI_EN.items():
            if tok.startswith(hi):  # crude Hindi stemming: बारिश -> बारिशों
                extra += en.split()
    return tokens + extra


@dataclass
class Chunk:
    file: str
    title: str
    source: str
    en: str
    hi: str
    tokens: list[str]


def _parse_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    meta = {"title": path.stem, "source": "unspecified"}
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    chunks = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        en = " ".join(re.findall(r"^EN:\s*(.+)$", body, re.M)).strip()
        hi = " ".join(re.findall(r"^HI:\s*(.+)$", body, re.M)).strip()
        if not (en or hi):
            continue
        chunks.append(Chunk(path.name, title.strip(), meta["source"], en, hi,
                            tokenize(f"{title} {en} {hi}")))
    return chunks


class Index:
    def __init__(self, directory: Path = KNOWLEDGE_DIR):
        self.chunks: list[Chunk] = []
        for path in sorted(directory.glob("*.md")):
            self.chunks += _parse_file(path)
        self.n = len(self.chunks)
        self.avg_len = (sum(len(c.tokens) for c in self.chunks) / self.n) if self.n else 0
        df: Counter[str] = Counter()
        for c in self.chunks:
            df.update(set(c.tokens))
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.tf = [Counter(c.tokens) for c in self.chunks]

    def search(self, query: str, lang: str = "en", k: int = 3) -> list[dict[str, Any]]:
        q = expand_query(query)
        if not q or not self.n:
            return []
        k1, b = 1.5, 0.75
        scored = []
        for i, c in enumerate(self.chunks):
            score = 0.0
            dl = len(c.tokens)
            for t in set(q):
                f = self.tf[i].get(t, 0)
                if f:
                    weight = CROP_WEIGHT if t in CROP_TERMS else 1.0
                    score += weight * self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (self.avg_len or 1)))
            if score >= MIN_SCORE:
                scored.append((score, c))
        scored.sort(key=lambda s: s[0], reverse=True)
        out = []
        for score, c in scored[:k]:
            body = (c.hi if lang == "hi" and c.hi else c.en or c.hi)
            out.append({"title": c.title, "text": body, "score": round(score, 2),
                        "file": c.file, "source": c.source})
        return out


_index: Index | None = None
_lock = threading.Lock()


def get_index() -> Index:
    global _index
    with _lock:
        if _index is None:
            _index = Index()
        return _index


def reset_index() -> None:
    global _index
    with _lock:
        _index = None


def search_advisory(query: str, lang: str = "en", k: int = 3) -> dict[str, Any]:
    hits = get_index().search(query, lang, k)
    return {"query": query, "hits": hits, "found": bool(hits)}
