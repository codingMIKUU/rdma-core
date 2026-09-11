#!/usr/bin/env python3
"""No-RDMA test of the *current* mlx5 large-lane mapping implementation.

Run directly, without building/installing libmlx5:
    python3 tests/test_srm_large_mapping.py

The C fixture stubs allocation and mmap only.  Mapper/release function bodies,
the response ABI, mapping bundle, and shared control page come from this checkout,
so the tests cannot silently exercise a copied implementation of their logic.
"""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def extract(text, declaration, semicolon=False):
    match = re.search(declaration, text, re.MULTILINE)
    if not match:
        raise RuntimeError("Cannot locate production declaration: " + declaration)
    begin = match.start()
    opening = text.index("{", match.start())
    # Preserve positions while hiding comments and strings from brace counting.
    visible = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                     lambda m: " " * len(m.group()), text, flags=re.DOTALL)
    depth = 1
    end = opening + 1
    while depth and end < len(text):
        depth += (visible[end] == "{") - (visible[end] == "}")
        end += 1
    if depth:
        raise RuntimeError("Unclosed production declaration")
    if semicolon:
        end = text.index(";", end) + 1
    return text[begin:end] + "\n"


def main():
    verbs = (ROOT / "providers/mlx5/verbs.c").read_text()
    header = (ROOT / "providers/mlx5/mlx5.h").read_text()
    abi = (ROOT / "kernel-headers/rdma/mlx5-abi.h").read_text()
    declarations = "\n".join([
        extract(abi, r"^struct mlx5_ib_modify_qp_resp\s*\{", True),
        extract(abi, r"^enum mlx5_ib_modify_qp_resp_mask\s*\{", True),
        extract(header, r"^struct mlx5_sq_ctrl_page\s*\{", True),
        extract(header, r"^struct mlx5_srm_mapping_bundle\s*\{", True),
    ])
    functions = "\n".join(extract(
        verbs, r"^(?:static )?(?:int|void) " + name + r"\([^;]*?\)\s*\{")
        for name in ("mlx5_srm_acquire_mapping", "mlx5_srm_acquire_large_mapping",
                     "mlx5_srm_release_mapping", "mlx5_srm_release_mappings"))
    with tempfile.TemporaryDirectory(prefix="srm-large-mapping-test-") as directory:
        temp = Path(directory)
        (temp / "srm_mapping_types.inc").write_text(declarations)
        (temp / "srm_mapping_functions.inc").write_text(functions)
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-I", str(temp), str(ROOT / "tests/srm_large_mapping_harness.c"),
            "-o", str(temp / "test-mapping"),
        ]
        subprocess.run(command, check=True)
        subprocess.run([str(temp / "test-mapping")], check=True)


if __name__ == "__main__":
    main()
