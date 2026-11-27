#!/usr/bin/env python3
"""
scripts/smoke_real.py

Manual smoke test exercising delegate v2 with real Codex and Gemini CLIs.
NEVER RUN IN CI.

Steps performed:
1. Initialize a temporary git repository with commit and delegate.toml.
2. delegate task new smoke-1 --title "Smoke Test Task" --files a.py
3. delegate work smoke-1 --brief <brief> (runs real Codex worker)
4. delegate verify smoke-1 (gate: python -c "import a")
5. delegate review smoke-1 --goal "Add hello function in a.py" (real Codex + agy)
6. delegate status smoke-1
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time


def run_step(step_num: int, name: str, cmd: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    print(f"\n[{step_num}/5] Running: {name}")
    print(f"Command: {' '.join(cmd)}")
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    elapsed = time.perf_counter() - start
    print(f"[{step_num}/5] {name}: exit={proc.returncode}, duration={elapsed:.2f}s")
    if proc.stdout.strip():
        print("--- stdout ---")
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr.strip())
    return proc


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent

    # Find delegate executable or run module with current python
    delegate_exe = shutil.which("delegate")
    if delegate_exe:
        base_cmd = [delegate_exe]
    else:
        base_cmd = [sys.executable, "-m", "delegate.cli"]

    # Ensure repo root is on PYTHONPATH for child processes
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")

    with tempfile.TemporaryDirectory(prefix="delegate_smoke_") as tmp_dir:
        repo = pathlib.Path(tmp_dir).resolve()
        print(f"Created temporary git repository: {repo}")

        # Git setup
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Smoke Tester"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "smoke@example.com"], cwd=repo, check=True)

        (repo / "README.md").write_text("# Smoke Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True)

        # delegate.toml
        toml_content = (
            'gemini_model = "gemini-3.8-flash-high"\n\n'
            "[gates]\n"
            'test = \'python -c "import a"\'\n'
        )
        (repo / "delegate.toml").write_text(toml_content, encoding="utf-8")

        # Brief for real Codex worker
        brief_path = repo / "brief.md"
        brief_content = (
            "# Task: Add hello function in a.py\n"
            "Repo: .\n\n"
            "## Do\n"
            "Create a.py with a function `hello()` that returns 'world'.\n\n"
            "## Done when\n"
            "`python -c \"import a; assert a.hello() == 'world'\"` passes.\n\n"
            "## Rules\n"
            "- Touch only: a.py\n\n"
            "## Reply\n"
            "Done.\n"
        )
        brief_path.write_text(brief_content, encoding="utf-8")

        # Step 1: delegate task new
        res1 = run_step(
            1,
            "delegate task new",
            base_cmd + ["task", "new", "smoke-1", "--title", "Smoke Test Task", "--files", "a.py"],
            cwd=repo,
        )
        if res1.returncode != 0:
            print("delegate task new failed!", file=sys.stderr)
            return res1.returncode

        # Step 2: delegate work (real Codex)
        res2 = run_step(
            2,
            "delegate work (real Codex)",
            base_cmd + ["work", "smoke-1", "--brief", str(brief_path)],
            cwd=repo,
        )
        if res2.returncode != 0:
            print("delegate work returned non-zero exit code.", file=sys.stderr)

        # Step 3: delegate verify
        res3 = run_step(
            3,
            "delegate verify (gate test)",
            base_cmd + ["verify", "smoke-1"],
            cwd=repo,
        )

        # Step 4: delegate review (real Codex + agy)
        res4 = run_step(
            4,
            "delegate review (real Codex + agy)",
            base_cmd + ["review", "smoke-1", "--goal", "Add hello function in a.py"],
            cwd=repo,
        )

        # Step 5: delegate status
        res5 = run_step(
            5,
            "delegate status",
            base_cmd + ["status", "smoke-1"],
            cwd=repo,
        )

        print("\nSmoke test run completed.")
        return 0 if (res1.returncode == 0 and res5.returncode == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
