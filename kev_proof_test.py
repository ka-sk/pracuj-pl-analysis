#!/usr/bin/env python3
"""
Dynamic categorisation of a text file, one line = one item. Single file, standard library only.

  1. take a random sample of lines  -> Ollama returns the initial categories
  2. every line goes to Kev with those categories + "other"
  3. if Kev picks "other"           -> Ollama proposes a new category (name, what belongs, what does NOT
                                       belong), and is told Kev's top scores for that line
  4. Kev classifies the line again with the new category; if it does not pick it, the problem is
     logged (console + log file) and the category is NOT added
  5. a final pass with the final categories writes a CSV: line, top 3 categories with scores

Kev is called as documented by its author: POST /v1/systemone, "choice" question, criteria={option: description}.
"""
import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import urllib.error
import urllib.request

OTHER = "other"
OTHER_DESC = "None of the other categories describes this line well."
INSTRUCTIONS = ("Which category best describes this requirement line from a job posting? "
                "Pick 'other' only if none of the other categories fits.")
CONTEXT = ("The lines are requirement items from job postings (what is expected from the candidate) in Polish. ")
SYSTEM = "You design category labels for a text classifier. Reply with JSON only."

log = logging.getLogger("categorize")


# ------------------------------------------------------------------ http helpers
def post(url, payload, headers=None):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {url}: {e.reason}")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:40]


# ------------------------------------------------------------------------- Kev
def ask_kev(args, text, cats):
    """Returns (choice, confidence, [(category, probability), ...] sorted best first)."""
    resp = post(f"{args.kev_url}/v1/systemone", {
        "state": text,
        "model": "kev-latest",
        "questions": {"category": {
            "type": "choice",
            "instructions": INSTRUCTIONS,
            "criteria": {**cats, OTHER: OTHER_DESC},          # "other" is always an option
        }},
    }, {"Authorization": "Bearer " + os.environ.get("KEV_API_KEY", "local")})
    ans = resp["answers"]["category"]
    top = sorted(ans["probabilities"].items(), key=lambda kv: -kv[1])
    return ans["choice"], ans["confidence"], top


# ---------------------------------------------------------------------- Ollama
def ask_ollama(args, prompt, schema):
    for attempt in range(1, 4):
        resp = post(f"{args.ollama_url}/api/chat", {
            "model": args.ollama_model,
            "stream": False,
            "format": schema,                                   # structured JSON output
            "options": {"temperature": 0.2, "num_ctx": 4096},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        })
        try:
            return json.loads(resp["message"]["content"])
        except (KeyError, ValueError):
            log.warning("Ollama returned invalid JSON (attempt %d/3)", attempt)
    raise RuntimeError("Ollama returned invalid JSON 3 times")


def seed_categories(args, sample):
    schema = {"type": "object", "required": ["categories"], "properties": {"categories": {
        "type": "array", "items": {"type": "object", "required": ["name", "description"],
                                   "properties": {"name": {"type": "string"},
                                                  "description": {"type": "string"}}}}}}
    prompt = (f"{CONTEXT}\nBelow is a random sample of such lines.\n"
              + "\n".join(f"- {t}" for t in sample)
              + f"\n\nPropose {args.n_seed} broad, non-overlapping categories that would sort lines like these. "
                "Write the names and descriptions in English. name = 1-3 words, lowercase, snake_case; "
                "description = one sentence on what belongs in it. "
                "Do NOT include catch-all categories such as 'other' or 'misc'.")
    cats = {}
    for c in ask_ollama(args, prompt, schema).get("categories", []):
        name, desc = slug(c.get("name", "")), str(c.get("description", "")).strip()
        if name and name != OTHER and desc and name not in cats:
            cats[name] = desc
    if len(cats) < 2:
        raise RuntimeError("Ollama returned fewer than 2 usable categories")
    return cats


def propose_category(args, text, cats, top):
    """Ask Ollama for ONE new category. Kev's top scores for the line are included so the
    description can also say what the category must NOT contain."""
    schema = {"type": "object", "required": ["name", "description", "not_included"], "properties": {
        "name": {"type": "string"}, "description": {"type": "string"}, "not_included": {"type": "string"}}}
    existing = "\n".join(f"- {n}: {d}" for n, d in cats.items())
    scores = "\n".join(f"- {n}: {p:.2f}" for n, p in top[:3])
    prompt = (f"{CONTEXT}\nA classifier sorted lines into the categories below, but chose 'other' for this line.\n\n"
              f"Existing categories:\n{existing}\n\n"
              f"Line:\n{text}\n\n"
              f"Classifier scores for this line (closest options, probability):\n{scores}\n\n"
              "Invent ONE new category for this line. Write everything in English.\n"
              "- name: 1-3 words, lowercase, snake_case; broad enough to fit similar lines, not just this one.\n"
              "- description: one sentence on what belongs in it.\n"
              "- not_included: one sentence on what does NOT belong in it, especially what separates it "
              "from the closest categories in the scores above.")
    data = ask_ollama(args, prompt, schema)
    name, desc = slug(data.get("name", "")), str(data.get("description", "")).strip()
    excluded = str(data.get("not_included", "")).strip()
    return name, f"{desc} Does NOT include: {excluded}" if excluded else desc


# -------------------------------------------------------------------------- main
def load_lines(path, limit: int = None):
    lines, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            text = re.sub(r"\s+", " ", re.sub(r"[​‌‍﻿]", "", raw)).strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                lines.append(text)
            if limit is not None and len(lines) >= limit:
                break
    return lines


def main():
    ap = argparse.ArgumentParser(description="Dynamic categorisation with Ollama + Kev")
    ap.add_argument("input", nargs="?", default="test_data/requirements.txt")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--log", default="categorize.log")
    ap.add_argument("--kev-url", default="http://127.0.0.1:8009")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--ollama-model", default="llama3.2:3b")
    ap.add_argument("--n-seed", type=int, default=6, help="number of initial categories")
    ap.add_argument("--sample", type=int, default=60, help="random lines sent to Ollama for the initial categories")
    ap.add_argument("--max-categories", type=int, default=25)
    ap.add_argument("--seed", type=int, default=0, help="random seed of the initial sample")
    ap.add_argument("--categ-limit", type=int, default=None, help="limit number of samples to categorise")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.log, "w", "utf-8")])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    lines = load_lines(args.input, args.categ_limit)
    if not lines:
        sys.exit(f"no lines in {args.input}")
    log.info("loaded %d unique lines from %s", len(lines), args.input)

    # 1. initial categories from a random sample
    sample = random.Random(args.seed).sample(lines, min(args.sample, len(lines)))
    cats = seed_categories(args, sample)
    for n, d in cats.items():
        log.info("seed category %s: %s", n, d)

    # 2-4. one pass: classify, extend categories on "other", verify
    for i, text in enumerate(lines, 1):
        choice, conf, top = ask_kev(args, text, cats)
        if choice == OTHER and len(cats) < args.max_categories:
            try:
                name, desc = propose_category(args, text, cats, top)
            except RuntimeError as e:
                log.error("line %d: could not get a new category from Ollama: %s | %s", i, e, text)
                continue
            if not name or name == OTHER or name in cats:
                log.error("line %d: proposal '%s' is empty/reserved/already exists | %s", i, name, text)
                continue
            new_choice, _, new_top = ask_kev(args, text, {**cats, name: desc})   # verification pass
            if new_choice == name:
                cats[name] = desc
                log.info("line %d: NEW category '%s': %s | %s", i, name, desc, text)
            else:
                log.error("line %d: VERIFICATION FAILED, proposed '%s' (%s) but Kev chose '%s' "
                          "(top: %s) | %s", i, name, desc, new_choice,
                          ", ".join(f"{n}={p:.2f}" for n, p in new_top[:3]), text)
        if i % 25 == 0:
            log.info("processed %d/%d lines, %d categories", i, len(lines), len(cats))

    log.info("---- Exiting categorization ----")
    # 5. final pass with the final category set -> CSV
    header = ["line_no", "text"] + [f"{k}{n}" for n in (1, 2, 3) for k in ("category_", "score_")] + ["kev_confidence"]
    counts = {}
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, text in enumerate(lines, 1):
            choice, conf, top = ask_kev(args, text, cats)
            counts[choice] = counts.get(choice, 0) + 1
            top3 = [x for n, p in top[:3] for x in (n, f"{p:.3f}")]
            w.writerow([i, text] + top3 + [""] * (6 - len(top3)) + [f"{conf:.3f}"])

    log.info("final categories (%d) and line counts by top choice:", len(cats))
    for n, d in cats.items():
        log.info("  %s (%d): %s", n, counts.get(n, 0), d)
    log.info("  %s (%d)", OTHER, counts.get(OTHER, 0))
    log.info("wrote %s (errors and decisions in %s)", args.out, args.log)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")