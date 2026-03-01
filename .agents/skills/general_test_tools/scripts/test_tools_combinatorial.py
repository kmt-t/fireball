#!/usr/bin/env python3
import subprocess
import os
import sys
import json
import time

# Configuration
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
RESULTS_FILE = os.path.join(PROJECT_ROOT, "docs/temp/test_tools_combinatorial_report.md")
DOCKER_WRAPPER = ".agent/skills/general_docker_run/scripts/docker-run-command.sh"

class CombinatorialTestRunner:
    def __init__(self):
        self.results = []
        self.start_time = time.time()

    def run(self, name, command, stdin_data=None, expected_ret=0, check_str=None, check_json=False, use_docker=False):
        print(f"[*] Testing {name} (Docker: {use_docker})...")
        try:
            if use_docker:
                # Wrap command with docker-run-command.sh
                # Note: docker-run-command.sh takes the command as arguments
                full_cmd = ["bash", DOCKER_WRAPPER] + command
            else:
                full_cmd = command

            if sys.platform == "win32":
                # Convert backslashes for WSL
                processed_cmd = []
                for part in full_cmd:
                    if "/" in part or "\\" in part:
                        processed_cmd.append(part.replace("\\", "/"))
                    else:
                        processed_cmd.append(part)
                full_cmd = ["wsl"] + processed_cmd

            process = subprocess.Popen(
                full_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=PROJECT_ROOT,
                encoding='utf-8'
            )
            stdout, stderr = process.communicate(input=stdin_data)
            ret = process.returncode

            passed = (ret == expected_ret)
            if passed and check_str:
                passed = (check_str in stdout or check_str in stderr)
            if passed and check_json and stdout.strip():
                try:
                    json.loads(stdout)
                except:
                    passed = False
            elif passed and check_json and not stdout.strip():
                passed = False # Expected JSON but got empty

            self.results.append({
                "name": name,
                "command": " ".join(command),
                "docker": use_docker,
                "stdin": "YES" if stdin_data else "NO",
                "passed": passed,
                "ret": ret,
                "ret_expected": expected_ret,
                "stdout": stdout,
                "stderr": stderr
            })
            return passed
        except Exception as e:
            self.results.append({
                "name": name,
                "command": " ".join(command),
                "passed": False,
                "error": str(e)
            })
            return False

    def report(self):
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        duration = time.time() - self.start_time
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            f.write("# Combinatorial Tool Testing Report\n\n")
            f.write(f"- **Date**: {time.ctime()}\n")
            f.write(f"- **Duration**: {duration:.2f}s\n\n")
            
            f.write("## Summary\n\n")
            total = len(self.results)
            passed = sum(1 for r in self.results if r.get("passed"))
            f.write(f"- Total: {total}\n")
            f.write(f"- Passed: {passed}\n")
            f.write(f"- Failed: {total - passed}\n\n")

            f.write("## Matrix\n\n")
            f.write("| Test Name | Env | Mode | Expected | Status | Details |\n")
            f.write("| :--- | :--- | :--- | :--- | :--- | :--- |\n")
            for r in self.results:
                status = "✅ PASS" if r.get("passed") else "❌ FAIL"
                env = "Docker" if r.get("docker") else "Host"
                mode = "Pipe" if r.get("stdin") == "YES" else "CLI"
                f.write(f"| {r['name']} | {env} | {mode} | {r.get('ret_expected')} | {status} | `{r['command']}` |\n")

            f.write("\n## Failure Analysis\n\n")
            for r in self.results:
                if not r.get("passed"):
                    f.write(f"### {r['name']}\n")
                    f.write(f"**Ret**: {r.get('ret')} (Expected {r.get('ret_expected')})\n")
                    f.write("**Stdout**:\n```\n{0}\n```\n".format(r.get("stdout", "")[:1000]))
                    f.write("**Stderr**:\n```\n{0}\n```\n".format(r.get("stderr", "")[:1000]))
                    if "error" in r:
                        f.write(f"**Exception**: {r['error']}\n")

def main():
    runner = CombinatorialTestRunner()

    # --- 1. project_friction_audit ---
    # CLI Arg
    runner.run("friction_cli_file", 
               ["python3", ".agent/skills/project_friction_audit/scripts/audit_friction.py", "docs/requires/requirement_list.md"])
    # Pipe Path
    runner.run("friction_pipe_path", 
               ["python3", ".agent/skills/project_friction_audit/scripts/audit_friction.py", "-p"],
               stdin_data="docs/requires/requirement_list.md\n")
    # JSON output
    runner.run("friction_json", 
               ["python3", ".agent/skills/project_friction_audit/scripts/audit_friction.py", "-j"],
               check_json=True)

    # --- 2. project_code_generate ---
    # check_violations: Stdin Content
    runner.run("violations_pipe_content",
               ["python3", ".agent/skills/project_code_generate/scripts/check_violations.py"],
               stdin_data="void* p = malloc(10);", expected_ret=1)
    # check_naming: Pipe Content
    runner.run("naming_pipe_content_ok",
               ["python3", ".agent/skills/project_code_generate/scripts/check_naming.py"],
               stdin_data="struct my_type {};", expected_ret=0)
    runner.run("naming_pipe_content_fail",
               ["python3", ".agent/skills/project_code_generate/scripts/check_naming.py"],
               stdin_data="struct MyType {};", expected_ret=1)

    # --- 3. general_codebase_explore (DOCKER REQUIRED for clang-check-18) ---
    # Symbols + JSON (In Docker)
    runner.run("explore_symbols_json_docker",
               ["python3", ".agent/skills/general_codebase_explore/scripts/explore_codebase.py", "inc/gen/vsoc.hxx", "--symbols", "-j"],
               use_docker=True, check_json=True)
    # Keywords (Host is fine)
    runner.run("explore_keywords_cli",
               ["python3", ".agent/skills/general_codebase_explore/scripts/explore_codebase.py", "docs/requires/requirement_list.md", "--keywords"])

    # --- 4. project_ollama_query ---
    # Stdin Context
    runner.run("ollama_stdin_error",
               ["python3", ".agent/skills/project_ollama_query/scripts/query_ollama.py", "test", "instruct"],
               stdin_data="Some context data", expected_ret=1) # Expected failure due to no network in CI or mock

    # --- 5. embedded_cpp_check ---
    # CLI + Recursive
    runner.run("cpp_check_recursive",
               ["python3", ".agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py", "-r", "inc/gen/"])
    # JSON output
    runner.run("cpp_check_json", 
               ["python3", ".agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py", "inc/gen/", "-j"],
               check_json=True)

    # --- Combinatorial: Stdin Content vs Stdin Path ---
    # audit_friction with content (if supported, but it's file-based)
    # let's test check_violations with --stdin-paths
    runner.run("violations_pipe_path",
               ["python3", ".agent/skills/project_code_generate/scripts/check_violations.py", "-p"],
               stdin_data="inc/gen/vsoc.hxx\n")

    runner.report()
    print(f"[DONE] Report: {RESULTS_FILE}")

if __name__ == "__main__":
    main()
