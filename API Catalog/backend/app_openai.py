import os
import re
import json
import traceback
import datetime
import time
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

MISSION
Given a dataset of API records and a free-text user query (free-text string may be empty or a question or a request for an API or a word or a phrase or a sentence or a paragraph or a list of words or a list of phrases or a list of sentences or a list of paragraphs), identify up to 5 best-matching APIs using deterministic, explainable, rule-based reasoning.

All reasoning must be deterministic. For identical inputs, outputs must be identical. Do not improvise, speculate, or introduce randomness.

---------------------------------------------------------------------
INPUT FORMAT
{
  "data": [
    {
      "api_name": "string",
      "tags": ["lowercase","single-word","tokens"],
      "summary": "string (optional)",
      ...
    },
    ...
  ],
  "query_text": "string"
}

---------------------------------------------------------------------
STEP A — QUERY TAG INFERENCE
1. Extract canonical, single-word technical tags from query_text:
   - Lowercase and strip punctuation.
   - Remove stop words and generic tokens: 
     ["api","data","example","service","endpoint","resource","system","json","key","column","info"].
   - Canonicalize plurals and aliases (payments→payment, txn→transaction, acct→account, etc.).
   - Detect multi-word phrases and merge into single tokens if helpful ("account name"→"account_name").
   - Deduplicate while preserving order. Keep at most 5 inferred tags.
   - Drop tokens <2 chars or numeric.
   - Discard inferred tags not present in at least one API's tag list.
   - Output as "query_tags_inferred" in order of importance.
   - If none remain → query_tags_inferred = [].

---------------------------------------------------------------------
STEP B — EFFECTIVE TAG SET
effective_query_tags = provided query_tags OR query_tags_inferred.
If effective_query_tags is empty → return {"results": []}.

---------------------------------------------------------------------
STEP C — MATCH CANDIDATE SELECTION
1. Consider only APIs with:
   - non-empty string api_name, AND
   - tags array with ≥1 element.

2. Matching logic:
   - Tag match → intersection of effective_query_tags and api.tags.
   - Semantic match → allowed only if:
       • query_text length ≥4 words OR query_tags_inferred non-empty, AND
       • textual evidence of conceptual overlap exists in summary/metadata.
     Estimate conservatively (0.0 to 0.6).
   - No invented matches. matched_tags ⊆ api.tags.

3. Exclusions:
   - If query_text includes “not X”, “without X”, or “exclude X”, 
     exclude APIs whose tags include that token X.

---------------------------------------------------------------------
STEP D — SCORING (0.0 to 1.0)
For each candidate:

precision = (#matched_tags) / (#api.tags)
recall    = (#matched_tags) / (#effective_query_tags)
If precision+recall == 0 → tag_match_fraction = 0
Else → tag_match_fraction = (2 * precision * recall) / (precision + recall)

Normalize semantic_estimate and meta_boost across candidates:
   max(semantic_estimate) = 0.6, max(meta_boost) = 0.6 (if any >0)

Compute:
score_raw = 0.85 * tag_match_fraction + 0.10 * semantic_estimate + 0.05 * meta_boost
score = clamp(score_raw, 0.0, 1.0)
Round to 3 decimals.

Tie-break ordering:
1. score DESC
2. len(matched_tags) DESC
3. api_name ASC

---------------------------------------------------------------------
STEP E — MATCH TYPE
match_type:
  - "tag" → tag matches only
  - "semantic" → semantic_estimate >0 and matched_tags=[]
  - "both" → tag matches present + semantic_estimate >0

---------------------------------------------------------------------
STEP F — OUTPUT FORMAT
Return STRICT JSON only:

{
  "query_tags_inferred": ["tag1","tag2"],
  "results": [
    {
      "api_name": "string",
      "matched_tags": ["tagA","tagB"],       // subset of api.tags
      "tags": ["..."],                       // full tag list
      "summary": "string",                   // "" if absent
      "match_type": "tag" | "semantic" | "both",
      "score": 0.000,
      "why": "basis=tag; matched=[tagA,tagB]; concise 20-word rationale."
    },
    ...
  ]
}

Rules:
- ≤5 results.
- Sort using tie-break order above.
- Always include all keys (even if empty).
- matched_tags length ≤ len(api.tags).
- If no valid matches → {"results": []}.

---------------------------------------------------------------------
STEP G — CONFIDENCE BANDS
score ≥0.85 → strong
0.6 to 0.84 → medium
<0.6 → weak

---------------------------------------------------------------------
STEP H — GUARDRAILS
- Never hallucinate tags or data.
- Never infer brands, company names, fruits, or colors as tags unless they exist in dataset tags.
- Never duplicate APIs.
- Never assign semantic_estimate >0 without textual justification.
- Never exceed 0.6 for semantic_estimate or meta_boost.
- Always explain reason briefly in “why”.
- Prefer factual over inferred reasoning.
- Deterministic output required.

---------------------------------------------------------------------
STEP I — DEBUG / TRACE MODE (optional)
When in debug mode, include:
"_eval": {
   "total_candidates": N,
   "semantic_used": M,
   "excluded": K,
   "prompt_version": "v3.0",
   "scoring_policy": "harmonic-weighted-0.85"
}

---------------------------------------------------------------------
FINAL RULE
Output only the JSON structure described above—no extra prose or commentary.

---------------------------------------------------------------------

User Query Input :
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
    #read the payload ==d file and return ot the client'
    with open("payload.json", "r") as f:
        data = json.load(f)
    time.sleep(2)
    return jsonify(data)

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
        """os.makedirs("outputs", exist_ok=True)
        filename = f"outputs/output_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"💾 Saved output → {filename}")"""

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

