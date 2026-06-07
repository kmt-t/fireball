import os
import re
import sys
import json
import urllib.request
import urllib.error

# Default models
SAKURA_MODEL = "gpt-oss-120b"
OPEN_ROUTER_MODEL = "google/gemma-4-31b-it:free"
GEMINI_MODEL = "gemini-3.1-flash-lite"
OLLAMA_MODEL = "gemma4:26b-a4b-it-qat"

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
    req = urllib.request.Request(SAKURA_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()

def call_openrouter(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
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
    req = urllib.request.Request(OPENROUTER_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()

def call_gemini(prompt: str, api_key: str, model: str) -> str:
    url = GEMINI_URL_TEMPLATE.format(model=model, key=api_key)
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0
        },
        "service_tier": "flex"
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["candidates"][0]["content"]["parts"][0]["text"].strip()

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
        status = fields["status"].upper()
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
        status = fields["status"].upper()
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

    normalized_status = status[0].strip().upper()
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
PASS または FAIL または UNCERTAIN

## REASON
1〜3文で根拠を書く。

## SUGGESTIONS
改善案がなければ空欄。FAILの場合のみ短く書く。
"""

def call_llm(prompt: str, backend: str = None, model: str = None, max_tokens: int = 1024) -> str:
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
            prompt = force_markdown_contract(prompt)
            return call_ollama(prompt, model_name, max_tokens)
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API Error (HTTP {e.code}): {e.reason}\nBody: {err_content}")
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with LLM backend '{backend}': {e}")
