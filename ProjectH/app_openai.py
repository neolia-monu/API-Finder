import os
import re
import json
import traceback
import datetime
from flask import Flask, request, jsonify
from openai import OpenAI
from flask_cors import CORS

# ==== CONFIG ====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY environment variable first")

DATA_FILE = "payload.json"  # dataset file
MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

# ==== INIT ====
client = OpenAI(api_key=OPENAI_API_KEY)
app = Flask(__name__)
CORS(app)

# ==== LOAD DATASET ON STARTUP ====
try:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        DATASET = json.load(f).get("data", [])
    print(f"✅ Loaded {len(DATASET)} APIs from {DATA_FILE}")
except Exception as e:
    raise RuntimeError(f"❌ Failed to load dataset: {e}")

# ==== PROMPT ====
PROMPT_INSTRUCTIONS = """
You are an API Recommendation and Matching Assistant.

OVERVIEW
You will be given:
  - A dataset under top-level key "data": array of API records. Each record contains at least:
      - "api_name" (string)
      - "tags" (array of lowercase single-word tokens)
    Optionally: "summary", "tags_generated", other metadata.
  - Exactly one of:
      - "query_tags": array of lowercase single-word tags (may be empty)
      - "query_text": free-text string (may be empty)
    Exactly one of these will be non-empty.

GOAL
Return up to 5 best-matching APIs in strict JSON. Use tag matches first, supplement conservatively with semantic/meta signals (only estimate semantic/meta values when actual signals are not available). Be deterministic and explainable.

STEP A — IF query_text PROVIDED
1. Infer up to 5 canonical single-word technical tags from query_text.
   - Lowercase, strip punctuation, remove stopwords and generic tokens: 
     ["api", "data", "example", "demo", "service", "endpoint", "resource", "system", "json"].
   - Canonicalize plurals and common aliases (payments→payment, txn→transaction, acct→account, names→name).
   - Detect multi-word phrases and convert to tokens if helpful (e.g., "account name" → "account_name") but final inferred tags must be single-word tokens.
   - Never output fallback or background concepts (e.g., color, fruit, company names).
   - If no meaningful tag remains, "query_tags_inferred" = [].
   - Output them as top-level key "query_tags_inferred" (preserve order by importance).
   - Use these inferred tags as the effective query tags for matching.

STEP B — PREPARE MATCHING SET
- Effective query tags = query_tags (if provided) else query_tags_inferred.
- If effective query tags is empty, you may still return up to 5 APIs based on conservative semantic_estimate/meta_boost, but tag_match_fraction = 0.

STEP C — MATCHING RULES
- Find APIs where at least one token in API.tags intersects effective query tags → these are tag matches.
- Also allow APIs to be included via conservative semantic_estimate (0.0–0.6) when textual evidence suggests similarity; label these "semantic" matches. If included solely by semantic, matched_tags = [].
- Do NOT invent matches; matched_tags must only contain tags from the API record itself.

STEP D — SCORING (0.0–1.0)
For each candidate API compute:
  tag_match_fraction = (# matched_tags) / (# effective query tags)    (if denominator is 0 → fraction = 0)
  semantic_estimate in [0.0, 0.6] — use 0.0 if none; if you estimate >0, be conservative.
  meta_boost in [0.0, 0.6] — based on presence/quality of summary/metadata; conservative estimates only.

score_raw = 0.65 * tag_match_fraction + 0.25 * semantic_estimate + 0.10 * meta_boost
score = clamp(score_raw, 0.0, 1.0)
Round score to 3 decimal places.

Match type:
  - "tag" if matched via tags only,
  - "semantic" if via semantic_estimate only,
  - "both" if both tag matches and semantic evidence.

STEP E — OUTPUT FORMAT (STRICT JSON ONLY)
Return only this JSON object (no extra prose):

{
  // include this only when query_text provided
  "query_tags_inferred": ["tag1","tag2"],

  "results": [
    {
      "api_name": "string",
      "matched_tags": ["tagA","tagB"],   // must be subset of api.tags (empty if semantic-only)
      "tags": ["..."],                   // API's full tags array
      "summary": "string",               // empty string if not present
      "match_type": "tag" | "semantic" | "both",
      "score": 0.000,
      "why": "short <=20-word sentence explaining the match and reason (mention matched tags or conservative semantic justification)"
    },
    ...
  ]
}

RESULT RULES
- Return at most 5 API objects sorted by score descending.
- matched_tags length must be ≤ len(api.tags) and preserve API tag order.
- If both query_tags and query_text are empty: return {"results": []}.
- If effective query tags is empty but you return APIs via semantic/meta, set tag_match_fraction = 0 and be conservative (semantic_estimate ≤ 0.6).
- If you include non-zero semantic_estimate or meta_boost, justify briefly in the "why" sentence (stay within 20 words).
- If no APIs match by tags and semantic_estimate = 0 for all, return {"results": []}.
- Round numeric scores to 3 decimals.

EDGE CASES & CLARIFICATIONS
- Detect exclusions in query_text (phrases like "not X", "without X", "exclude X") and treat them as filters — exclude APIs containing that tag.
- For ambiguous or generic short queries (e.g., "need api"), remove all generic tokens and return empty inferred tags.
- If user requests "single API" or "one call" and no single API covers all effective tags, still return top matches but include in "why" that a composite solution is needed.
- Never output speculative meanings (like fruit, color, or brand) unless explicitly stated.
- Be deterministic, minimal, and clean — no filler words or speculative tags.

CONFIDENCE GUIDANCE
- Scores near 0.8–1.0 indicate strong relevance.
- 0.5–0.7 indicates partial fit; consider composite set.
- <0.4 indicates weak or ambiguous match.

FINAL: Output only the JSON described above — nothing else.
Query : 
"""

# ==== HELPERS ====
def extract_json_block(text: str):
    match = re.search(r"(\{[\s\S]*\})", text)
    return match.group(1) if match else None

def quick_repair(json_text: str):
    repaired = re.sub(r",\s*([}\]])", r"\1", json_text)
    repaired = re.sub(r"[\u0000-\u0019]+", "", repaired)
    return repaired

def extract_response_text(resp):
    """Robustly extracts text from OpenAI Responses API output object."""
    try:
        if hasattr(resp, "output_text") and resp.output_text:
            return resp.output_text.strip()
        if hasattr(resp, "output"):
            out = resp.output
            if isinstance(out, list) and len(out) > 0:
                first = out[0]
                if hasattr(first, "content"):
                    content = first.content
                    if isinstance(content, list) and len(content) > 0:
                        text_candidate = content[0]
                        if isinstance(text_candidate, dict) and "text" in text_candidate:
                            return text_candidate["text"].strip()
                        if hasattr(text_candidate, "text"):
                            return str(text_candidate.text).strip()
        return str(resp)
    except Exception as e:
        return f"[extract_response_text error: {e}]"

# ==== ROUTES ====
@app.route("/", methods=["GET"])
def home():
    #read the payloaf ==d file and return ot the client'
    with open("payload.json", "r") as f:
        data = json.load(f)
    return jsonify(data)

    #return jsonify(status="ok", message="POST /enrich with {'query': '...', or 'tags': [...]}")

@app.route("/enrich", methods=["POST"])
def enrich():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(error="invalid_json", message="Expected JSON body"), 400

    user_query = payload.get("query", "").strip()
    query_tags = payload.get("tags", [])

    if not user_query and not query_tags:
        return jsonify(error="missing_query", message="Provide 'query' or 'tags'"), 400

    structured_input = {
        "data": DATASET,
        "query_text": user_query if user_query else "",
        "query_tags": query_tags if query_tags else []
    }

    prompt = PROMPT_INSTRUCTIONS + json.dumps(structured_input, ensure_ascii=False)

    try:
        resp = client.responses.create(
            model=MODEL,
            input=prompt,
            reasoning={"effort": "minimal"},
            max_output_tokens=1200,
        )

        text = extract_response_text(resp).strip()
        if not text:
            return jsonify(error="empty_response", raw=str(resp)), 500

        json_block = extract_json_block(text)
        if not json_block:
            return jsonify(error="no_json_found", raw=text), 500

        try:
            parsed = json.loads(json_block)
        except json.JSONDecodeError:
            repaired = quick_repair(json_block)
            parsed = json.loads(repaired)

        # === Prepare output data ===
        output_data = {
            "query": user_query or query_tags,
            "inferred_tags": parsed.get("query_tags_inferred", []),
            "total_matches": len(parsed.get("results", [])),
            "results": parsed.get("results", [])
        }

        # === Save nicely formatted to file ===
        os.makedirs("outputs", exist_ok=True)
        filename = f"outputs/output_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"💾 Saved output → {filename}")

        # === Return pretty JSON to client ===
        return app.response_class(
            response=json.dumps(output_data, indent=2, sort_keys=True, ensure_ascii=False),
            status=200,
            mimetype="application/json"
        )

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify(error="openai_error", message=str(e), trace=tb.splitlines()[-1]), 500


if __name__ == "__main__":
    print("🚀 Starting enrichment API on http://127.0.0.1:3000")
    app.run(host="127.0.0.1", port=3000, debug=True)

