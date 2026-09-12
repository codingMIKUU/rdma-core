#!/usr/bin/env python3
"""Compile checks against the actual exported CQ delivery ABI; no RDMA needed."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="srm-cq-abi-") as directory:
        binary = str(Path(directory) / "test")
        subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
            "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-I", str(ROOT / "kernel-headers"),
            str(ROOT / "tests/srm_cq_abi_test.c"), "-o", binary,
        ], check=True)
        subprocess.run([binary], check=True)


if __name__ == "__main__":
    main()
