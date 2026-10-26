#!/usr/bin/env python3
"""
Proof-test for a "dynamic categories" decision pipeline, run on a requirements.txt.

Pipeline (one pass over the packages):
  1. A small Ollama LLM proposes the initial categories (from a sample of the packages).
  2. Kev (a Jev-style decision model) classifies each package into one of the
     categories plus a fixed "other" option.
  3. If Kev answers "other": the Ollama LLM proposes ONE new category (name + description).
       - rejected if it duplicates an existing category (embeddings, or lexical fallback)
       - rejected if the category cap is reached
       - Kev is then re-run on the same item with the new category in the options;
         the category is kept only if Kev now picks it (verification step)
  4. After a new category is accepted:
       - a sample of earlier assignments is re-checked (did the new category steal items?)
       - items still in "other" are retried (they may fit now)
  5. A final full re-pass with the final category set measures how stable the
     assignments are.

Kev is called exactly as documented by its author: POST /v1/systemone with a
"choice" question whose criteria is {option_name: description}. See SETUP.md.

Standard library only (no pip install needed).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

OTHER = "other"
OTHER_DESC = "None of the other categories describes this item well."
QUESTION_ID = "category"
INSTRUCTIONS = (
    "Which category best describes this Python package? "
    "Pick 'other' only if none of the other categories fits."
)
STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "are", "used", "use", "from",
    "into", "such", "not", "can", "any", "all", "tools", "tool", "related",
    "libraries", "library", "packages", "package", "python", "items", "like",
}


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- http
def http_json(url, payload=None, headers=None, timeout=120):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="GET" if payload is None else "POST")
    req.add_header("content-type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from None
    except urllib.error.URLError as e:
        raise ConnectionError(f"cannot reach {url}: {e.reason}") from None


# ------------------------------------------------------------------ requirements.txt
NAME_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


@dataclass
class Item:
    name: str            # normalised package name
    spec: str            # version spec (informational, not shown to the model)
    summary: str = ""    # optional PyPI summary (--enrich)

    @property
    def state(self) -> str:
        s = f"Package: {self.name}"
        if self.summary:
            s += f"\nDescription: {self.summary}"
        return s


def parse_requirements(path):
    items: dict[str, Item] = {}
    skipped: list[str] = []
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = re.split(r"\s+#", raw.strip(), maxsplit=1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-"):                      # -r, -e, --index-url, ...
                skipped.append(line)
                continue
            if re.match(r"^(git\+|hg\+|svn\+|bzr\+|https?:|file:|\.|/)", line):
                m = re.search(r"#egg=([A-Za-z0-9._-]+)", line)
                if not m:
                    skipped.append(line)
                    continue
                name, spec = m.group(1), "(direct URL)"
            else:
                m = NAME_RE.match(line)
                if not m:
                    skipped.append(line)
                    continue
                name = m.group(1)
                rest = line[m.end():].split(";")[0]       # drop environment markers
                rest = re.sub(r"^\s*\[[^\]]*\]", "", rest).strip()   # drop extras
                spec = "(direct URL)" if rest.startswith("@") else rest
            norm = re.sub(r"[-_.]+", "-", name).lower()
            items.setdefault(norm, Item(norm, spec))
    return list(items.values()), skipped


def enrich_items(items):
    """Optional: add the one-line PyPI summary so the models don't rely on name knowledge alone."""
    for it in items:
        try:
            info = http_json(f"https://pypi.org/pypi/{it.name}/json", timeout=10)["info"]
            it.summary = (info.get("summary") or "").strip()[:200]
        except Exception:
            it.summary = ""


# ---------------------------------------------------------------------------- Kev
@dataclass
class Decision:
    choice: str
    confidence: float
    probs: dict
    latency_ms: float = 0.0


class Kev:
    """Minimal client for Kev's TypeSafe-compatible /v1/systemone endpoint."""

    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.calls = 0
        self.latencies: list[float] = []

    def check(self):
        return http_json(f"{self.base}/v1/models", headers=self.headers, timeout=15)

    def classify(self, state: str, criteria: dict) -> Decision:
        payload = {
            "state": state,
            "model": "kev-latest",
            "questions": {
                QUESTION_ID: {
                    "type": "choice",
                    "instructions": INSTRUCTIONS,
                    "criteria": criteria,        # {option: description}
                }
            },
        }
        t0 = time.perf_counter()
        resp = http_json(f"{self.base}/v1/systemone", payload, self.headers)
        wall = (time.perf_counter() - t0) * 1000
        ans = resp["answers"][QUESTION_ID]
        self.calls += 1
        self.latencies.append(wall)
        return Decision(ans["choice"], float(ans.get("confidence", 0.0)),
                        ans.get("probabilities", {}), float(resp.get("latency_ms", 0.0)))


# -------------------------------------------------------------------------- Ollama
class Ollama:
    def __init__(self, base_url: str, model: str, embed_model: str | None):
        self.base = base_url.rstrip("/")
        self.model = model
        self.embed_model = embed_model
        self.calls = 0

    def check(self):
        tags = http_json(f"{self.base}/api/tags", timeout=15)
        have = [m["name"] for m in tags.get("models", [])]

        def present(wanted):
            return any(h == wanted or h.startswith(wanted + ":") for h in have)

        missing = [m for m in (self.model, self.embed_model) if m and not present(m)]
        if missing:
            raise RuntimeError(
                f"Ollama model(s) not pulled: {', '.join(missing)}. "
                f"Run: " + " && ".join(f"ollama pull {m}" for m in missing)
                + f"   (installed: {', '.join(have) or 'none'})"
            )

    def chat_json(self, system: str, user: str, schema: dict, retries: int = 2) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "format": schema,                                   # structured output
            "options": {"temperature": 0.2, "num_ctx": 4096},
        }
        last = None
        for _ in range(retries + 1):
            self.calls += 1
            resp = http_json(f"{self.base}/api/chat", payload, timeout=300)
            try:
                return json.loads(resp["message"]["content"])
            except (KeyError, json.JSONDecodeError) as e:
                last = e
        raise RuntimeError(f"Ollama returned invalid JSON after {retries + 1} tries: {last}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = http_json(f"{self.base}/api/embed",
                         {"model": self.embed_model, "input": texts}, timeout=120)
        return resp["embeddings"]


def clean_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


SEED_SCHEMA = {
    "type": "object",
    "properties": {"categories": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
        "required": ["name", "description"]}}},
    "required": ["categories"],
}
PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
    "required": ["name", "description"],
}


def seed_categories(ollama: Ollama, sample: list[Item], n: int) -> dict[str, str]:
    listing = "\n".join(f"- {i.name}" + (f": {i.summary}" if i.summary else "") for i in sample)
    user = (
        f"Below are Python packages from a project's requirements file.\n{listing}\n\n"
        f"Propose {n} broad, non-overlapping categories that together would sort packages "
        "like these. For each: 'name' = 1-3 words, lowercase, snake_case; 'description' = one "
        "sentence saying what belongs in it. Do NOT include catch-all categories such as "
        "'other', 'misc' or 'utilities'."
    )
    data = ollama.chat_json("You design category labels for a text classifier. Reply with JSON only.",
                            user, SEED_SCHEMA)
    cats: dict[str, str] = {}
    for c in data.get("categories", []):
        name, desc = clean_name(str(c.get("name", ""))), str(c.get("description", "")).strip()
        if name and name != OTHER and desc and name not in cats:
            cats[name] = desc
    if len(cats) < 2:
        raise RuntimeError(f"Ollama proposed fewer than 2 usable categories: {data}")
    return dict(list(cats.items())[:n])


def propose_category(ollama: Ollama, item: Item, cats: dict[str, str]) -> tuple[str, str] | None:
    existing = "\n".join(f"- {k}: {v}" for k, v in cats.items())
    user = (
        "A classifier sorted items into the categories below, but this item fit none of them.\n\n"
        f"Existing categories:\n{existing}\n\nItem:\n{item.state}\n\n"
        "Invent ONE new category for this item. Rules:\n"
        "- It must be broad enough to cover other similar items, not a label for this single item. "
        "Never use the item's own name.\n"
        "- It must not overlap with an existing category.\n"
        "- name: 1-3 words, lowercase, snake_case.\n"
        "- description: one sentence saying what belongs in it and, if helpful, what does not."
    )
    data = ollama.chat_json("You design category labels for a text classifier. Reply with JSON only.",
                            user, PROPOSE_SCHEMA)
    name, desc = clean_name(str(data.get("name", ""))), str(data.get("description", "")).strip()
    if not name or not desc or name == OTHER:
        return None
    if item.name.replace("-", "_") in name:                 # label named after the item itself
        return None
    return name, desc


# ----------------------------------------------------------------- duplicate check
def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in STOPWORDS}


def lexical_duplicate(name: str, desc: str, cats: dict[str, str]) -> str | None:
    for cn, cd in cats.items():
        if SequenceMatcher(None, name, cn).ratio() >= 0.85:
            return cn
        a, b = tokens(desc), tokens(cd)
        if a and b and len(a & b) / len(a | b) >= 0.6:
            return cn
    return None


def cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


# --------------------------------------------------------------------- pipeline
@dataclass
class Row:
    item: Item
    category: str
    confidence: float
    first_category: str
    notes: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, kev: Kev, ollama: Ollama, args, rng: random.Random):
        self.kev, self.ollama, self.args, self.rng = kev, ollama, args, rng
        self.cats: dict[str, str] = {}
        self.meta: dict[str, dict] = {}
        self.rows: dict[str, Row] = {}
        self.events: list[dict] = []
        self.count = Counter()
        self._emb: dict[str, list[float]] = {}

    # -- helpers
    def criteria(self, cats=None) -> dict[str, str]:
        return {**(self.cats if cats is None else cats), OTHER: OTHER_DESC}   # 'other' always last

    def decide(self, item: Item, cats=None) -> Decision:
        return self.kev.classify(item.state, self.criteria(cats))

    def record(self, item: Item, d: Decision, note: str = "") -> None:
        row = self.rows.get(item.name)
        if row is None:
            row = self.rows[item.name] = Row(item, d.choice, d.confidence, d.choice)
        else:
            row.category, row.confidence = d.choice, d.confidence
        if note:
            row.notes.append(note)

    def add_category(self, name: str, desc: str, origin: str, idx: int) -> None:
        self.cats[name] = desc
        self.meta[name] = {"origin": origin, "added_at_item": idx}

    def is_duplicate(self, name: str, desc: str) -> str | None:
        if name in self.cats:
            return name
        if not self.ollama.embed_model:
            return lexical_duplicate(name, desc, self.cats)
        texts = {k: f"{k.replace('_', ' ')}: {v}" for k, v in self.cats.items()}
        texts["__new__"] = f"{name.replace('_', ' ')}: {desc}"
        todo = [k for k in texts if k not in self._emb or k == "__new__"]
        for k, v in zip(todo, self.ollama.embed([texts[k] for k in todo])):
            self._emb[k] = v
        best = max(self.cats, key=lambda c: cosine(self._emb[c], self._emb["__new__"]))
        if cosine(self._emb[best], self._emb["__new__"]) >= self.args.sim_threshold:
            return best
        return None

    # -- main per-item logic
    def process(self, item: Item, idx: int) -> str:
        d = self.decide(item)
        if d.choice != OTHER:
            self.record(item, d)
            return d.choice
        if len(self.cats) >= self.args.max_categories:
            self.count["rejected_cap"] += 1
            self.record(item, d, "other: category cap reached")
            return OTHER
        self.count["proposals"] += 1
        try:
            cand = propose_category(self.ollama, item, self.cats)
        except RuntimeError as e:
            cand = None
            self.events.append({"item": item.name, "event": "proposer_error", "detail": str(e)})
        if cand is None:
            self.count["proposer_failed"] += 1
            self.record(item, d, "other: proposer gave no usable category")
            return OTHER
        name, desc = cand
        dup = self.is_duplicate(name, desc)
        if dup:
            self.count["rejected_duplicate"] += 1
            self.events.append({"item": item.name, "event": "rejected_duplicate",
                                "proposed": name, "duplicate_of": dup})
            self.record(item, d, f"other: proposed '{name}' duplicates '{dup}'")
            return OTHER
        trial = {**self.cats, name: desc}
        d2 = self.decide(item, trial)                      # verification re-run
        if d2.choice != name:
            self.count["rejected_verification"] += 1
            self.events.append({"item": item.name, "event": "rejected_verification",
                                "proposed": name, "kev_chose": d2.choice})
            self.record(item, d, f"other: proposed '{name}' failed verification (Kev chose {d2.choice})")
            return OTHER
        self.add_category(name, desc, "proposed", idx)
        self.count["accepted"] += 1
        self.events.append({"item": item.name, "event": "accepted", "category": name, "description": desc})
        self.record(item, d2, f"created category '{name}'")
        self.regression(name, exclude=item.name)
        self.retry_others(name, exclude=item.name)
        return name

    def regression(self, new_name: str, exclude: str) -> None:
        pool = [r for r in self.rows.values() if r.category != OTHER and r.item.name != exclude]
        sample = self.rng.sample(pool, min(len(pool), self.args.regress_sample))
        moved = []
        for r in sample:
            d = self.decide(r.item)
            if d.choice != r.category:
                moved.append({"item": r.item.name, "from": r.category, "to": d.choice})
                r.notes.append(f"moved {r.category}->{d.choice} after '{new_name}' was added")
                r.category, r.confidence = d.choice, d.confidence
        self.events.append({"event": "regression_check", "new_category": new_name,
                            "checked": len(sample), "moved": len(moved), "details": moved})
        self.count["regression_moved"] += len(moved)

    def retry_others(self, new_name: str, exclude: str) -> None:
        recovered = []
        for r in self.rows.values():
            if r.category == OTHER and r.item.name != exclude:
                d = self.decide(r.item)
                if d.choice != OTHER:
                    r.notes.append(f"recovered from other -> {d.choice} after '{new_name}' was added")
                    r.category, r.confidence = d.choice, d.confidence
                    recovered.append(r.item.name)
        if recovered:
            self.events.append({"event": "others_recovered", "after": new_name, "items": recovered})
            self.count["others_recovered"] += len(recovered)

    def final_pass(self) -> int:
        changed = 0
        for r in self.rows.values():
            d = self.decide(r.item)
            if d.choice != r.category:
                changed += 1
                r.notes.append(f"final pass: {r.category}->{d.choice}")
            r.category, r.confidence = d.choice, d.confidence
        return changed


# ------------------------------------------------------------------------- report
def write_outputs(pipe: Pipeline, args, final_changed: int | None, elapsed: float) -> None:
    os.makedirs(args.out_dir, exist_ok=True)
    counts = Counter(r.category for r in pipe.rows.values())
    cats_out = {
        k: {"description": v, **pipe.meta[k], "count": counts.get(k, 0)}
        for k, v in pipe.cats.items()
    }
    with open(os.path.join(args.out_dir, "categories.json"), "w", encoding="utf-8") as f:
        json.dump({"categories": cats_out, "other_count": counts.get(OTHER, 0)}, f, indent=2)
    with open(os.path.join(args.out_dir, "events.json"), "w", encoding="utf-8") as f:
        json.dump(pipe.events, f, indent=2)
    with open(os.path.join(args.out_dir, "assignments.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["package", "category", "confidence", "needs_review", "first_pass_category", "notes"])
        for r in pipe.rows.values():
            review = r.category == OTHER or r.confidence < args.min_confidence
            w.writerow([r.item.name, r.category, f"{r.confidence:.3f}", int(review),
                        r.first_category, "; ".join(r.notes)])

    log("\n" + "=" * 70)
    log("CATEGORIES")
    for k, v in pipe.cats.items():
        members = [r.item.name for r in pipe.rows.values() if r.category == k]
        shown = ", ".join(members[:8]) + (" ..." if len(members) > 8 else "")
        log(f"  {k} [{pipe.meta[k]['origin']}] ({len(members)}): {v}\n      {shown}")
    others = [r.item.name for r in pipe.rows.values() if r.category == OTHER]
    log(f"  {OTHER} ({len(others)}): {', '.join(others[:12])}{' ...' if len(others) > 12 else ''}")
    low = sum(1 for r in pipe.rows.values() if r.category != OTHER and r.confidence < args.min_confidence)
    log("\nSTATS")
    log(f"  packages: {len(pipe.rows)}   categories: {len(pipe.cats)} "
        f"({sum(1 for m in pipe.meta.values() if m['origin'] == 'proposed')} created during the run)")
    for k in ("proposals", "accepted", "rejected_duplicate", "rejected_verification",
              "rejected_cap", "proposer_failed", "regression_moved", "others_recovered"):
        log(f"  {k}: {pipe.count[k]}")
    if final_changed is not None:
        log(f"  final re-pass changed {final_changed} of {len(pipe.rows)} assignments "
            f"({100 * final_changed / max(1, len(pipe.rows)):.1f}%)")
    log(f"  low-confidence (<{args.min_confidence}) assigned items flagged for review: {low}")
    lat = sorted(pipe.kev.latencies)
    if lat:
        log(f"  Kev calls: {pipe.kev.calls}   p50 {lat[len(lat) // 2]:.0f} ms   "
            f"p95 {lat[int(len(lat) * 0.95) - 1 if len(lat) > 1 else 0]:.0f} ms (round trip, incl. HTTP)")
    log(f"  Ollama calls: {pipe.ollama.calls}   total time: {elapsed:.1f}s")
    log(f"\nWrote {args.out_dir}/assignments.csv, categories.json, events.json")


# ---------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("requirements", nargs="?", default="requirements.txt")
    ap.add_argument("--kev-url", default="http://127.0.0.1:8009")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--ollama-model", default="llama3.2:3b", help="small instruct model, already pulled")
    ap.add_argument("--embed-model", default=None,
                    help="optional Ollama embedding model (e.g. nomic-embed-text) for duplicate detection; "
                         "without it a lexical check is used")
    ap.add_argument("--sim-threshold", type=float, default=0.85, help="cosine threshold for duplicates")
    ap.add_argument("--n-seed", type=int, default=6, help="number of initial categories")
    ap.add_argument("--seed-sample", type=int, default=80, help="packages shown to the LLM for seeding")
    ap.add_argument("--max-categories", type=int, default=25)
    ap.add_argument("--min-confidence", type=float, default=0.2,
                    help="assigned items below this Kev confidence are flagged needs_review "
                         "(they do NOT trigger new categories)")
    ap.add_argument("--regress-sample", type=int, default=20,
                    help="earlier assignments re-checked after each new category")
    ap.add_argument("--no-final-pass", action="store_true")
    ap.add_argument("--enrich", action="store_true", help="add PyPI one-line summaries to each item")
    ap.add_argument("--shuffle", action="store_true", help="process packages in random order")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    t0 = time.perf_counter()

    items, skipped = parse_requirements(args.requirements)
    if not items:
        sys.exit(f"No packages found in {args.requirements}")
    log(f"Parsed {len(items)} unique packages ({len(skipped)} option/URL lines skipped)")

    kev = Kev(args.kev_url, os.environ.get("KEV_API_KEY", "local"))
    ollama = Ollama(args.ollama_url, args.ollama_model, args.embed_model)
    try:
        info = kev.check()
        log(f"Kev server OK at {args.kev_url}")
        ollama.check()
        log(f"Ollama OK at {args.ollama_url} (model {args.ollama_model})")
    except (ConnectionError, RuntimeError) as e:
        sys.exit(f"Preflight failed: {e}\nSee SETUP.md.")
    _ = info

    if args.enrich:
        log("Fetching PyPI summaries ...")
        enrich_items(items)
    if args.shuffle:
        rng.shuffle(items)

    pipe = Pipeline(kev, ollama, args, rng)
    sample = rng.sample(items, min(len(items), args.seed_sample))
    log(f"Asking {args.ollama_model} for {args.n_seed} initial categories ...")
    for name, desc in seed_categories(ollama, sample, args.n_seed).items():
        pipe.add_category(name, desc, "seed", -1)
        log(f"  seed: {name} - {desc}")

    for idx, item in enumerate(items):
        before = len(pipe.cats)
        cat = pipe.process(item, idx)
        row = pipe.rows[item.name]
        flag = "  <-- NEW CATEGORY" if len(pipe.cats) > before else ""
        log(f"[{idx + 1}/{len(items)}] {item.name} -> {cat} (conf {row.confidence:.2f}){flag}")

    final_changed = None if args.no_final_pass else pipe.final_pass()
    write_outputs(pipe, args, final_changed, time.perf_counter() - t0)


if __name__ == "__main__":
    main()