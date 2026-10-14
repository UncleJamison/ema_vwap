#!/usr/bin/env python3
"""Run migrate_data.py tests with proper isolation.

This script runs the migration tests in a way that doesn't interfere with
the existing production databases by using isolated temporary environments.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_tests():
    # Create a temporary directory for test isolation
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy project structure to temp dir
        test_project = Path(tmpdir) / "project"
        shutil.copytree(
            Path.cwd(),
            test_project,
            ignore=shutil.ignore_patterns(
                "data/candles_backup",  # Skip existing backups
                "*.bak",
                "*.sh",
                ".git",
            ),
        )

        # Create fresh test environment
        test_dir = test_project / "tests"
        test_dir.mkdir(parents=True, exist_ok=True)

        # Copy our migration test file
        shutil.copy(
            Path.cwd() / "tests" / "test_migrate_data.py",
            test_dir / "test_migrate_data.py",
        )

        # Run the migration test
        os.chdir(test_project)

        print("Running migration tests in isolated environment...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_migrate_data.py", "-v"],
            capture_output=True,
            text=True,
            check=False,
        )

        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)

        return result.returncode


if __name__ == "__main__":
    sys.exit(run_tests())
