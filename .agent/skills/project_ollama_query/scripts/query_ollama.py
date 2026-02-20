#!/usr/bin/env python3
import sys
import json
import urllib.request
import argparse
import os
import datetime
from pathlib import Path

# Config
OLLAMA_URL = "http://localhost:11434/api/generate"
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent.parent.parent.parent
# Co-agent facts are isolated in a sub-folder to prevent context contamination
BRAIN_DIR = WORKSPACE_ROOT / ".agent" / "brain" / "co_agent"

# High-density knowledge extractor
SYSTEM_PROMPT = """
[SYSTEM: KNOWLEDGE COMPRESSION AGENT]
GOAL: Thoroughly extract comprehensive technical details, architectural facts, quantitative data (memory size, constraints, etc.), and component relationships from the input.
STRICT RULES:
- NO conversational filler. NO greetings. NO "Here is the summary".
- Extract a LARGE volume of structured information using Markdown (bullet points, code snippets, headers).
- Do not summarize away important technical details. Retain numbers, function names, metrics, and constraints.
- Organize insights logically depending on the instruction.
- The output MUST serve as a complete reference for the main AI agent, bypassing its need to read the original files.
- Do NOT output thin predicate logic; provide a substantive, detailed extraction.
"""

def query_ollama(model, prompt):
    data = {
        "model": model, 
        "system": SYSTEM_PROMPT.strip(),
        "prompt": prompt, 
        "stream": False,
        "options": {
            "temperature": 0.0,
            "stop": [],
            "num_ctx": 2048
        },
        "keep_alive": "5m"
    }
    
    req = urllib.request.Request(
        OLLAMA_URL, 
        data=json.dumps(data).encode('utf-8'), 
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get('response', '')
    except Exception as e:
        print(f"Error communicating with Ollama: {e}", file=sys.stderr)
        return None

def main():
    parser = argparse.ArgumentParser(description="Ollama Co-agent proxy (query_ollama.py)")
    parser.add_argument("scope", help="The target scope (e.g., wamr_memory)")
    parser.add_argument("instruction", help="The instruction or task")
    parser.add_argument("files", nargs="*", help="Optional list of files to analyze. If omitted, reads from stdin.")
    parser.add_argument("-m", "--model", default="phi3:mini", help="The Ollama model to use.")
    
    args = parser.parse_args()
    
    context = ""
    
    # Read files
    for file_name in args.files:
        file_path = Path(file_name)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                context += f"\n--- File: {file_name} ---\n"
                context += f.read()
        else:
            print(f"Warning: File {file_path} not found.", file=sys.stderr)
            
    # Handle piped input (stdin)
    if not sys.stdin.isatty():
        piped_content = sys.stdin.read()
        if piped_content:
            context += f"\n--- [PIPED CONTEXT] ---\n{piped_content}"
            
    if not context.strip():
        print("Error: No input context provided. Please provide files or pipe data via stdin.", file=sys.stderr)
        sys.exit(1)
        
    prompt_template = f"Task: {args.instruction}\n\nContext to extract predicates from:\n{{text}}\n"
            
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)
    final_scope = args.scope if "_" in args.scope else f"product_{args.scope}"
    output_path = BRAIN_DIR / f"{final_scope}.atc"
    
    # Chunking logic to avoid Ollama context window crashes (HTTP 500)
    CHUNK_SIZE = 1500
    chunks = [context[i:i+CHUNK_SIZE] for i in range(0, len(context), CHUNK_SIZE)]
    
    with open(output_path, "a", encoding='utf-8', newline='\n') as f:
        f.write("# Ollama Coagent Generated Logic List\n")
        f.write(f"# Scope: {args.scope}\n")
        f.write(f"# Timestamp: {datetime.datetime.now().isoformat()}\n")
        f.write(f"# Task: {args.instruction}\n")
        if args.files:
            f.write(f"# Source Files: {', '.join(args.files)}\n\n")
            
        for idx, chunk in enumerate(chunks):
            print(f"Sending request to Ollama ({args.model}) for scope '{args.scope}' (Chunk {idx+1}/{len(chunks)})...", file=sys.stderr)
            prompt = prompt_template.format(text=chunk)
            response = query_ollama(args.model, prompt)
            
            if not response:
                print(f"Failed to get response from Ollama for chunk {idx+1}. Skipping...", file=sys.stderr)
                continue
                
            f.write(response.strip())
            f.write("\n\n---\n\n")
            
            if idx == 0:
                print(f"--- Logic Preview (Chunk 1) ---", file=sys.stderr)
                print(response.strip()[:500] + "...", file=sys.stderr)
        
    print(f"Result saved to {output_path}")
    
if __name__ == "__main__":
    main()
