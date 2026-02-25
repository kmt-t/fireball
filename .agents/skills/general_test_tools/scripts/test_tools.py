#!/usr/bin/env python3
import subprocess
import os
import sys
import tempfile
import json

# Configuration
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
RESULTS_FILE = os.path.join(PROJECT_ROOT, "docs/temp/test_tools_report.md")

class TestRunner:
    def __init__(self):
        self.results = []

    def run_test(self, name, command, stdin_data=None, expected_ret=0, check_output=None):
        print(f"[*] Running test: {name}...")
        try:
            # Use wsl if on windows
            full_cmd = command
            if sys.platform == "win32":
                full_cmd = ["wsl"] + command

            process = subprocess.Popen(
                full_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=PROJECT_ROOT,
                encoding='utf-8' # Ensure UTF-8 for WSL output
            )
            stdout, stderr = process.communicate(input=stdin_data)
            ret = process.returncode

            passed = (ret == expected_ret)
            if passed and check_output:
                passed = check_output(stdout, stderr)

            self.results.append({
                "name": name,
                "command": " ".join(command),
                "passed": passed,
                "ret": ret,
                "stdout": stdout[:500] + "..." if len(stdout) > 500 else stdout,
                "stderr": stderr[:500] + "..." if len(stderr) > 500 else stderr
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

    def generate_report(self):
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            f.write("# Automated Tool Testing Report\n\n")
            f.write(f"Timestamp: {subprocess.check_output(['date']).decode().strip()}\n\n")
            
            f.write("| Test Name | Status | Ret | Details |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")
            for r in self.results:
                status = "✅ PASS" if r.get("passed") else "❌ FAIL"
                ret = r.get("ret", "N/A")
                f.write(f"| {r['name']} | {status} | {ret} | {r.get('command', '')} |\n")
            
            f.write("\n## Detailed Failures\n")
            for r in self.results:
                if not r.get("passed"):
                    f.write(f"### {r['name']}\n")
                    f.write(f"**Command**: `{r.get('command')}`\n")
                    f.write(f"**STDOUT**:\n```\n{r.get('stdout')}\n```\n")
                    f.write(f"**STDERR**:\n```\n{r.get('stderr')}\n```\n")
                    if "error" in r:
                        f.write(f"**Error**: {r['error']}\n")

def main():
    runner = TestRunner()

    # 1. project_friction_audit
    runner.run_test(
        "friction_audit_help", 
        ["python3", ".agent/skills/project_friction_audit/scripts/audit_friction.py", "--help"]
    )
    runner.run_test(
        "friction_audit_stdin_paths", 
        ["python3", ".agent/skills/project_friction_audit/scripts/audit_friction.py", "--stdin-paths"],
        stdin_data="docs/requires/requirement_list.md\n"
    )
    runner.run_test(
        "friction_audit_json", 
        ["python3", ".agent/skills/project_friction_audit/scripts/audit_friction.py", "-j"],
    )

    # 2. project_code_generate (Quality checks)
    runner.run_test(
        "check_violations_help",
        ["python3", ".agent/skills/project_code_generate/scripts/check_violations.py", "--help"]
    )
    # Test violation detection with a string
    runner.run_test(
        "check_violations_detect",
        ["python3", ".agent/skills/project_code_generate/scripts/check_violations.py"],
        stdin_data="void* ptr = malloc(10);",
        expected_ret=1
    )
    runner.run_test(
        "check_naming_help",
        ["python3", ".agent/skills/project_code_generate/scripts/check_naming.py", "--help"]
    )

    # 3. general_codebase_explore
    runner.run_test(
        "explore_codebase_help",
        ["python3", ".agent/skills/general_codebase_explore/scripts/explore_codebase.py", "--help"]
    )
    runner.run_test(
        "explore_codebase_ls_json",
        ["python3", ".agent/skills/general_codebase_explore/scripts/explore_codebase.py", ".", "--ls", "-j"]
    )

    # 4. project_ollama_query (Check arg parsing)
    runner.run_test(
        "query_ollama_help",
        ["python3", ".agent/skills/project_ollama_query/scripts/query_ollama.py", "--help"],
    )

    # 5. embedded_cpp_check
    runner.run_test(
        "embedded_cpp_check_help",
        ["python3", ".agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py", "--help"]
    )
    runner.run_test(
        "embedded_cpp_check_json",
        ["python3", ".agent/skills/embedded_cpp_check/scripts/check_embedded_rules.py", ".", "-j"]
    )

    runner.generate_report()
    print(f"[OK] Testing complete. Report saved to {RESULTS_FILE}")

if __name__ == "__main__":
    main()
