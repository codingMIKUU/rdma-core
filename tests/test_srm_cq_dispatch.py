#!/usr/bin/env python3
"""Exercise current Hollow completion functions without RDMA hardware.

Run: python3 tests/test_srm_cq_dispatch.py
The fixture projects enclosing objects and stubs hardware polling only; queue,
growth, mode negotiation, software-ring drain and CQ merge bodies are extracted
unchanged from this checkout.
"""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from test_srm_large_mapping import extract


ROOT = Path(__file__).resolve().parents[1]


def main():
    header = (ROOT / "providers/mlx5/mlx5.h").read_text()
    abi = (ROOT / "kernel-headers/rdma/mlx5-abi.h").read_text()
    verbs = (ROOT / "libibverbs/verbs.h").read_text()
    declarations = []
    for source, names in (
        (verbs, ("enum ibv_wc_status", "enum ibv_wc_opcode", "enum ibv_wr_opcode",
                 "struct ibv_wc")),
        (abi, ("struct mlx5_ib_burst_info", "struct mlx5_ib_modify_qp",
               "enum mlx5_ib_modify_qp_resp_mask", "struct mlx5_srm_sw_cqe",
               "struct mlx5_srm_sw_cq")),
        (header, ("struct mlx5_sq_ctrl_page", "struct mlx5_srm_completion_marker",
                  "struct mlx5_srm_dispatch_status")),
    ):
        declarations.extend(extract(source, "^" + name + r"\s*\{", True)
                            for name in names)
    declarations.append(re.search(r"^#define MLX5_SRM_SW_CQ_MAX_DEPTH.*$", abi,
                                  re.MULTILINE).group())
    functions = []
    for filename, names in (
        ("qp.c", ("mlx5_srm_wr_data_bytes", "mlx5_srm_wc_opcode",
                  "mlx5_srm_ensure_completion_space", "mlx5_srm_queue_completion")),
        ("cq.c", ("mlx5_srm_dispatch_event", "mlx5_srm_drain_dispatch",
                  "mlx5_srm_poll_watermarks", "poll_cq")),
        ("verbs.c", ("mlx5_srm_prepare_completion_cq", "mlx5_srm_record_cq_mode",
                     "mlx5_srm_remove_pending_completion",
                     "mlx5_srm_detach_completion_cq")),
    ):
        source = (ROOT / "providers/mlx5" / filename).read_text()
        functions.extend(extract(source, r"^static(?: inline)? "
                                 r"(?:uint32_t|int|void|enum ibv_wc_opcode)\s+" +
                                 name + r"\([^;]*?\)\s*\{") for name in names)
    with tempfile.TemporaryDirectory(prefix="srm-cq-dispatch-test-") as directory:
        temp = Path(directory)
        (temp / "srm_cq_types.inc").write_text("\n".join(declarations))
        (temp / "srm_cq_functions.inc").write_text("\n".join(functions))
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-I", str(temp), str(ROOT / "tests/srm_cq_dispatch_harness.c"),
            "-o", str(temp / "test-cq"),
        ]
        subprocess.run(command, check=True)
        subprocess.run([str(temp / "test-cq")], check=True)


if __name__ == "__main__":
    main()
