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
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
You are an API recommendation assistant. I will provide:
1) a dataset of existing API records under the key "data" (array). Each record includes at least "api_name" and "tags".
2) a user query, provided either as "query_tags" (array of lowercase single-word tags) or "query_text" (string). Exactly one of those will be non-empty.

Task:
- If "query_text" is provided, produce up to 5 canonical query tags by extracting meaningful single-word technical tokens (lowercase).
- Find all APIs where at least one tag intersects with the query tags (api.tags ∪ api.tags_generated). Also include APIs with strong semantic similarity (if given embedding, assume it's handled externally).
- For each matching API produce an object with these keys:
  - "api_name" (from dataset),
  - "matched_tags" (array of tags from the API that matched the query tags),
  - "tags" (the API's tag list),
  - "summary" (use existing summary if present),
  - "match_type" (one of "tag", "semantic", "both"),
  - "score" (0-1 number computed as: 0.65*tag_match_fraction + 0.25*semantic_estimate + 0.10*meta_boost — you may estimate semantic_estimate/meta_boost if not available),
  - "why" (one short sentence explaining the match).
- Return a JSON array named "results" sorted by score descending. Output must be valid JSON and nothing else.

Constraints:
- When computing tag_match_fraction use: (# matched tags) / (# query tags). If query_tags is empty, treat fraction as 0.
- Keep matched_tags ≤ number of api tags.
- If query_text was provided, include "query_tags_inferred" at top-level (array of inferred tags).
- Use only the supplied dataset and query to decide matches. If you estimate semantic or meta values, keep them conservative (0.0-0.6) unless strong textual evidence exists.
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
    return jsonify(status="ok", message="POST /enrich with {'query': '...', or 'tags': [...]}")

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

    prompt = PROMPT_INSTRUCTIONS + "\n" + json.dumps(structured_input, ensure_ascii=False)

    try:
        resp = client.responses.create(
            model=MODEL,
            input=prompt,
            temperature=0.0,
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

