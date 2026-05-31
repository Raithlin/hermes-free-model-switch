#!/usr/bin/env python3
"""Wrapper that reverts model config — invoked by revert-free-model cron job."""

import os
import subprocess
import sys

script = os.path.join(os.path.dirname(__file__), "set-model.py")
result = subprocess.run(
    [sys.executable, script, "--revert"],
    capture_output=True, text=True, timeout=30,
)
sys.stdout.write(result.stdout)
if result.returncode:
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
