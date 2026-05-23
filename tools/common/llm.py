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
OLLAMA_MODEL = "qwen2.5-coder:3b"

# API URLs
OLLAMA_URL = "http://localhost:11434/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SAKURA_URL = "https://api.ai.sakura.ad.jp/v1/chat/completions"
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

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
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": max_tokens},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body.get("response", "").strip()

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
            return call_ollama(prompt, model_name, max_tokens)
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API Error (HTTP {e.code}): {e.reason}\nBody: {err_content}")
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with LLM backend '{backend}': {e}")

def parse_llm_json_response(raw_text: str) -> dict:
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if json_match:
        text_to_parse = json_match.group(1)
    else:
        start = raw_text.find('{')
        end = raw_text.rfind('}')
        if start != -1 and end != -1:
            text_to_parse = raw_text[start:end+1]
        else:
            text_to_parse = raw_text

    try:
        return json.loads(text_to_parse)
    except json.JSONDecodeError as e:
        return {
            "status": "ERROR",
            "reason": f"Failed to parse LLM JSON response. Error: {e}",
            "suggestions": f"Raw LLM output was:\n{raw_text}"
        }
