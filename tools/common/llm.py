import os
import re
import sys
import json
import urllib.request
import urllib.error

# Default models
SAKURA_MODEL = "preview/gemma-4-31B-it"
OPEN_ROUTER_MODEL = "google/gemma-4-31b-it:free"
GEMINI_MODEL = "gemma-4-31b-it"
OLLAMA_MODEL = "gemma4:12b-it-qat"

# API URLs
OLLAMA_URL = "http://localhost:11434/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SAKURA_URL = "https://api.ai.sakura.ad.jp/v1/chat/completions"
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "600"))
OLLAMA_THINK = os.environ.get("OLLAMA_THINK", "false").strip().lower() == "true"

def call_sakura(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "service_tier": "flex"
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    import time
    max_retries = 8
    base_delay = 5.0
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(SAKURA_URL, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read())
                return body["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                delay = base_delay * (2 ** attempt)
                import sys
                sys.stderr.write(f"\n[Sakura API 429] Rate limit hit. Waiting {delay}s before retry... (Attempt {attempt+1}/{max_retries})\n")
                time.sleep(delay)
                continue
            else:
                raise
    raise RuntimeError("LLM API Error (HTTP 429): Quota exceeded after maximum retries on Sakura backend.")

def call_openrouter(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(OPENROUTER_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()

def call_gemini(prompt: str, api_key: str, model: str) -> str:
    url = GEMINI_URL_TEMPLATE.format(model=model, key=api_key)

    # Determine responseSchema based on prompt structure
    if "S-QUALITY-PLACEHOLDER" in prompt and "S-QUALITY-AMBIGUITY" in prompt:
        schema = {
            "type": "object",
            "properties": {
                "S-QUALITY-PLACEHOLDER": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "reason": {"type": "string"},
                        "suggestions": {"type": "string"}
                    },
                    "required": ["status", "reason", "suggestions"]
                },
                "S-QUALITY-AMBIGUITY": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "reason": {"type": "string"},
                        "suggestions": {"type": "string"}
                    },
                    "required": ["status", "reason", "suggestions"]
                },
                "S-QUALITY-API": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "reason": {"type": "string"},
                        "suggestions": {"type": "string"}
                    },
                    "required": ["status", "reason", "suggestions"]
                }
            },
            "required": ["S-QUALITY-PLACEHOLDER", "S-QUALITY-AMBIGUITY", "S-QUALITY-API"]
        }
    elif "SUMMARY" in prompt and "ITEMS" in prompt:
        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "status": {"type": "string"},
                            "reason": {"type": "string"},
                            "suggestions": {"type": "string"}
                        },
                        "required": ["id", "status", "reason"]
                    }
                }
            },
            "required": ["summary", "items"]
        }
    else:
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "reason": {"type": "string"},
                "suggestions": {"type": "string"}
            },
            "required": ["status", "reason", "suggestions"]
        }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": schema
        }
    }
    import time
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    max_retries = 8
    base_delay = 5.0

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read())
                return body["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                delay = base_delay * (2 ** attempt)
                import sys
                sys.stderr.write(f"\n[Gemini API 429] Rate limit hit. Waiting {delay}s before retry... (Attempt {attempt+1}/{max_retries})\n")
                time.sleep(delay)
                continue
            else:
                raise
    raise RuntimeError("LLM API Error (HTTP 429): Quota exceeded after maximum retries.")

def call_ollama(prompt: str, model: str, max_tokens: int) -> str:
    options = {
        "temperature": 0.0,
        "num_predict": max_tokens,
        "num_ctx": OLLAMA_NUM_CTX,
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "think": OLLAMA_THINK,
        "options": options,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        body = json.loads(resp.read())
    return body.get("response", "").strip()


def parse_llm_markdown_response(raw_text: str) -> dict:
    def _normalize_status(value: str) -> str:
        normalized = value.strip().upper()
        if normalized == "WARN":
            return "UNCERTAIN"
        return normalized

    # Try parsing as JSON first (Structured Outputs / JSON mode)
    try:
        data = json.loads(raw_text.strip())
        if isinstance(data, dict):
            # Check if it is single-rule schema or multi-rule schema
            has_status = False
            for k in data.keys():
                if k.lower() == "status":
                    has_status = True
                    break
            
            if has_status:
                normalized_data = {}
                for k, v in data.items():
                    k_lower = k.lower()
                    if k_lower in ["status", "reason", "suggestions"]:
                        if k_lower == "status":
                            normalized_data["status"] = _normalize_status(str(v))
                        else:
                            normalized_data[k_lower] = str(v)
                return normalized_data
            else:
                normalized_multi = {}
                for rule_name, rule_data in data.items():
                    if isinstance(rule_data, dict):
                        normalized_rule = {}
                        for k, v in rule_data.items():
                            k_lower = k.lower()
                            if k_lower in ["status", "reason", "suggestions"]:
                                if k_lower == "status":
                                    normalized_rule["status"] = _normalize_status(str(v))
                                else:
                                    normalized_rule[k_lower] = str(v)
                        normalized_multi[rule_name] = normalized_rule
                if normalized_multi:
                    return normalized_multi
    except Exception:
        pass

    sections: dict[str, list[str]] = {}
    current = None

    for line in raw_text.splitlines():
        m = re.match(r"^#{1,3}\s*([A-Za-z0-9_-]+)\s*$", line.strip())
        if m:
            current = m.group(1).upper()
            sections[current] = []
            continue
        if current:
            sections[current].append(line)

    if not sections:
        fields = {"status": "", "reason": "", "suggestions": ""}
        current_field = None
        for line in raw_text.splitlines():
            m = re.match(r"^(STATUS|REASON|SUGGESTIONS)\s*:\s*(.*)$", line.strip(), re.IGNORECASE)
            if m:
                current_field = m.group(1).lower()
                fields[current_field] = m.group(2).strip()
                continue
            if current_field and line.strip():
                fields[current_field] = (fields[current_field] + "\n" + line.strip()).strip()
        status = _normalize_status(fields["status"])
        if status in ["PASS", "FAIL", "UNCERTAIN", "ERROR"]:
            fields["status"] = status
            return fields
        return {}

    multi_rule = {}
    for name, lines in sections.items():
        if not name.startswith("S-"):
            continue
        fields = {"status": "", "reason": "", "suggestions": ""}
        current_field = None
        for line in lines:
            m = re.match(r"^(STATUS|REASON|SUGGESTIONS)\s*:\s*(.*)$", line.strip(), re.IGNORECASE)
            if m:
                current_field = m.group(1).lower()
                fields[current_field] = m.group(2).strip()
                continue
            if current_field and line.strip():
                fields[current_field] = (fields[current_field] + "\n" + line.strip()).strip()
        status = _normalize_status(fields["status"])
        if status in ["PASS", "FAIL", "UNCERTAIN", "ERROR"]:
            fields["status"] = status
            multi_rule[name] = fields
    if multi_rule:
        return multi_rule

    def section_text(name: str) -> str:
        return "\n".join(sections.get(name, [])).strip()

    status = section_text("STATUS").splitlines()
    if not status:
        return {}

    normalized_status = _normalize_status(status[0].strip())
    if normalized_status not in ["PASS", "FAIL", "UNCERTAIN", "ERROR"]:
        return {}

    result = {
        "status": normalized_status,
        "reason": section_text("REASON"),
        "suggestions": section_text("SUGGESTIONS"),
    }
    review_points = section_text("REVIEW_POINTS")
    risk_level = section_text("RISK_LEVEL")
    if review_points:
        result["review_points"] = [line.strip("- ").strip() for line in review_points.splitlines() if line.strip()]
    if risk_level:
        result["risk_level"] = risk_level.splitlines()[0].strip()
    return result


def force_markdown_contract(prompt: str) -> str:
    if "S-QUALITY-PLACEHOLDER" in prompt and "S-QUALITY-AMBIGUITY" in prompt:
        return f"""{prompt}

【最終出力指示】
上のJSON指定よりも、この最終指示を優先してください。
Markdownのみで回答してください。コードブロック、前置き、後書きは禁止です。

## S-QUALITY-PLACEHOLDER
STATUS: PASS または FAIL
REASON: 1〜2文で根拠を書く。
SUGGESTIONS: 改善案がなければ空欄。

## S-QUALITY-AMBIGUITY
STATUS: PASS または FAIL
REASON: 1〜2文で根拠を書く。
SUGGESTIONS: 改善案がなければ空欄。

## S-QUALITY-API
STATUS: PASS または FAIL
REASON: 1〜2文で根拠を書く。
SUGGESTIONS: 改善案がなければ空欄。
"""
    return f"""{prompt}

【最終出力指示】
上のJSON指定よりも、この最終指示を優先してください。
Markdownのみで回答してください。コードブロック、前置き、後書きは禁止です。

## STATUS
PASS または FAIL または WARN または UNCERTAIN

## REASON
1〜3文で根拠を書く。

## SUGGESTIONS
改善案がなければ空欄。FAILの場合のみ短く書く。
"""

def call_llm(
    prompt: str,
    backend: str = None,
    model: str = None,
    max_tokens: int = 1024,
    apply_contract: bool = True,
) -> str:
    sakura_key = os.environ.get("SAKURA_AI_API_KEY", "").strip()
    openrouter_key = os.environ.get("OPEN_ROUTER_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")).strip()

    if not backend:
        if sakura_key:
            backend = "sakura"
        elif openrouter_key:
            backend = "openrouter"
        elif gemini_key:
            backend = "gemini"
        else:
            backend = "ollama"

    if apply_contract:
        prompt = force_markdown_contract(prompt)

    try:
        if backend == "sakura":
            if not sakura_key:
                raise ValueError("SAKURA_AI_API_KEY environment variable is not set.")
            model_name = model or SAKURA_MODEL
            return call_sakura(prompt, sakura_key, model_name, max_tokens)
        elif backend == "openrouter":
            if not openrouter_key:
                raise ValueError("OPEN_ROUTER_API_KEY environment variable is not set.")
            model_name = model or OPEN_ROUTER_MODEL
            return call_openrouter(prompt, openrouter_key, model_name, max_tokens)
        elif backend == "gemini":
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY / GOOGLE_API_KEY environment variable is not set.")
            model_name = model or GEMINI_MODEL
            return call_gemini(prompt, gemini_key, model_name)
        else:
            model_name = model or OLLAMA_MODEL
            return call_ollama(prompt, model_name, max_tokens)
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API Error (HTTP {e.code}): {e.reason}\nBody: {err_content}")
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with LLM backend '{backend}': {e}")
