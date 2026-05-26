"""
Pre-push check verification driver.
Executes static code linting, formatting verification, and unit test suites
natively to ensure compliance with remote GitHub Actions CI pipelines before code push.

Usage:
    python scripts/pre_push_check.py          # Run readonly checks
    python scripts/pre_push_check.py --fix    # Auto-fix lint errors and format code
"""

import argparse
import os
import subprocess
import sys
from typing import List


def run_command(cmd: List[str], description: str, cwd: str | None = None) -> bool:
    """Execute a subprocess command and print readable status output."""
    print(f"\n[Running] {description}...")
    print(f"  Command: {' '.join(cmd)}")
    if cwd:
        print(f"  Working directory: {cwd}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )
        if result.returncode == 0:
            print(f"  [SUCCESS] {description} passed cleanly.")
            return True
        else:
            print(f"  [FAILED] {description} reported errors (exit code {result.returncode}):")
            if result.stdout.strip():
                print(f"\n--- STDOUT ---\n{result.stdout.strip()}")
            if result.stderr.strip():
                print(f"\n--- STDERR ---\n{result.stderr.strip()}")
            return False
    except FileNotFoundError:
        executable = cmd[0]
        print(f"  [ERROR] Executable '{executable}' not found in PATH.")
        print("          Please ensure dependencies are installed (e.g. pip install ruff pytest).")
        return False
    except Exception as e:
        print(f"  [ERROR] Unhandled subprocess exception: {e}")
        return False


def npm_executable() -> str:
    """Return the npm executable name for the current OS."""
    return "npm.cmd" if os.name == "nt" else "npm"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run complete CI pre-push verifications locally.")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically apply ruff formatting and auto-fix lint violations.",
    )
    args = parser.parse_args()

    print("============================================================")
    print("      Project Vega: Pre-Push CI Verification Suite        ")
    print("============================================================")

    steps: list[tuple[List[str], str, str | None]] = []

    if args.fix:
        print("\nMode: AUTO-FIX enabled. Modifying files in-place to adhere to standard layouts.")
        steps.append(
            (
                [sys.executable, "-m", "ruff", "check", "--fix", "--exclude", "vega_ref", "."],
                "Ruff Auto-Fix Linter",
                None,
            )
        )
        steps.append(
            (
                [sys.executable, "-m", "ruff", "format", "--exclude", "vega_ref", "."],
                "Ruff Code Formatter",
                None,
            )
        )
    else:
        print("\nMode: CHECK-ONLY. Verifying code files without modifying content.")
        steps.append(
            (
                [sys.executable, "-m", "ruff", "check", "--exclude", "vega_ref", "."],
                "Ruff Linter Check",
                None,
            )
        )
        steps.append(
            (
                [sys.executable, "-m", "ruff", "format", "--check", "--exclude", "vega_ref", "."],
                "Ruff Format Conformance Check",
                None,
            )
        )

    # Always append the pytest regression framework suite
    steps.append(([sys.executable, "-m", "pytest"], "Pytest Unit Regression Suite", None))

    frontend_dir = "frontend"
    if os.path.exists(os.path.join(frontend_dir, "package.json")):
        npm = npm_executable()
        steps.append(([npm, "run", "lint"], "Frontend ESLint Check", frontend_dir))
        steps.append(([npm, "run", "typecheck"], "Frontend TypeScript Check", frontend_dir))

    success = True
    for step in steps:
        cmd = step[0]
        desc = step[1]
        cwd = step[2] if len(step) > 2 else None
        step_passed = run_command(cmd, desc, cwd=cwd)
        if not step_passed:
            success = False

    print("\n============================================================")
    if success:
        print("  [SUCCESS] All pre-push checks passed successfully! Ready to push.")
        print("============================================================")
        sys.exit(0)
    else:
        print("  [FAILURE] Pre-push verification encountered failures.")
        if not args.fix:
            print("     Tip: Run 'python scripts/pre_push_check.py --fix' to auto-format code.")
        print("============================================================")
        sys.exit(1)


if __name__ == "__main__":
    main()
