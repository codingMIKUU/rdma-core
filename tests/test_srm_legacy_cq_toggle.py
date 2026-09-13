#!/usr/bin/env python3
"""Hardware-free checks of the historical CQ switch and watermark poller.

Uses production function bodies (not a second implementation). Historical
comparisons require git history and a sibling rdma-kerndriver checkout.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT.parent / "rdma-kerndriver"
KPATH = "drivers/infiniband/hw/mlx5/"
FLAG = "MLX5_SRM_ENABLE_CQE_SIMPLIFY"


def historical(root, revision, path):
    return subprocess.check_output(["git", "show", revision + ":" + path],
                                   cwd=root, text=True)


def preprocess(source, mode):
    source = re.sub(r"^\s*#\s*include[^\n]*", "", source, flags=re.M)
    return subprocess.check_output(["cc", "-E", "-P", "-x", "c", "-",
                                    "-D" + FLAG + "=" + str(mode),
                                    "-DKERNEL_VERSION(a,b,c)=(((a)<<16)|((b)<<8)|(c))"],
                                   input=source, text=True)


def function(source, name):
    # Select a definition, skipping forward declarations and calls.
    pattern = r"\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{"
    match = re.search(pattern, source)
    if not match:
        raise AssertionError("Missing function " + name)
    start = match.start()
    pos = source.index("{", match.start()) + 1
    depth = 1
    while depth:
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
        pos += 1
    return source[start:pos]


def compact(source):
    # Equivalent spelling in poll_cq: initialization before vs inside for.
    source = re.sub(r"npolled\s*=\s*0\s*;\s*for\s*\(\s*;",
                    "for (npolled = 0;", source)
    return re.sub(r"\s+", "", source)


def check_history():
    for path, name, off, on, root in [
        ("providers/mlx5/cq.c", "poll_cq", "a3eb20f7", "a9ff6e90", ROOT),
        (KPATH + "scheduler.c", "srm_poll_srmc_once", "b286657", "d73981d", KERNEL),
    ]:
        current = (root / path).read_text()
        for mode, revision in ((0, off), (1, on)):
            actual = function(preprocess(current, mode), name)
            expected = function(preprocess(historical(root, revision, path), mode), name)
            assert compact(actual) == compact(expected), (name, mode, revision)
            print("PASS:", name, "mode", mode, "matches", revision)

    for path in ("providers/mlx5/qp.c", "providers/mlx5/verbs.c"):
        disabled = preprocess((ROOT / path).read_text(), 0)
        assert "srm_completion_pending" not in disabled, path
        assert "srm_queue_completion" not in disabled, path
    header = (ROOT / "providers/mlx5/mlx5.h").read_text()
    disabled = preprocess(header, 0)
    assert "srm_completion_pending" not in disabled
    assert "srm_pending_head" not in disabled
    enabled = preprocess(header, 1)
    assert "srm_completion_pending" in enabled and "srm_pending_head" in enabled
    for root, path in ((ROOT, "providers/mlx5/mlx5.h"), (KERNEL, KPATH + "scheduler.h")):
        assert re.search(r"#define\s+" + FLAG + r"\s+0\b", (root / path).read_text())
    print("PASS: defaults OFF; no watermark metadata or post hooks in mode 0")


def check_watermarks():
    src = preprocess((ROOT / "providers/mlx5/cq.c").read_text(), 1)
    body = function(src, "mlx5_srm_poll_watermarks")
    stub = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#define unlikely(x) (x)
enum ibv_wc_status { IBV_WC_SUCCESS = 0, IBV_WC_LOC_PROT_ERR = 4 };
enum { IBV_WC_RDMA_WRITE = 1, IBV_WC_RDMA_READ = 2 };
struct ibv_wc { uint64_t wr_id; int opcode; uint32_t qp_num, byte_len;
                enum ibv_wc_status status; uint32_t vendor_err; };
struct mlx5_sq_ctrl_page { uint64_t cons_idx, completion_error_idx;
                          uint32_t completion_error_status, completion_error_vendor; };
struct mlx5_qp {
    struct mlx5_qp *srm_completion_next;
    struct mlx5_sq_ctrl_page *srm_completion_ctrl;
    uint64_t srm_completion_idx, srm_completion_wr_id;
    uint32_t srm_completion_byte_len;
    int srm_completion_opcode, srm_completion_pending, srm_completion_queued;
    struct { struct { uint32_t qp_num; } qp; } verbs_qp;
};
struct mlx5_cq { struct mlx5_qp *srm_pending_head, *srm_pending_tail; };
static int
'''
    test = r'''
static void arm(struct mlx5_cq *cq, struct mlx5_qp *qp,
                struct mlx5_sq_ctrl_page *ctrl, uint64_t idx, uint64_t wrid) {
    memset(qp, 0, sizeof(*qp));
    qp->srm_completion_ctrl = ctrl;
    qp->srm_completion_idx = idx;
    qp->srm_completion_wr_id = wrid;
    qp->srm_completion_opcode = IBV_WC_RDMA_READ;
    qp->srm_completion_byte_len = 2048;
    qp->verbs_qp.qp.qp_num = 17;
    qp->srm_completion_pending = qp->srm_completion_queued = 1;
    if (cq->srm_pending_tail) cq->srm_pending_tail->srm_completion_next = qp;
    else cq->srm_pending_head = qp;
    cq->srm_pending_tail = qp;
}
int main(void) {
    struct mlx5_cq cq = {0};
    struct mlx5_qp a, b;
    struct mlx5_sq_ctrl_page ca = {.cons_idx=4, .completion_error_idx=UINT64_MAX};
    struct mlx5_sq_ctrl_page cb = {.cons_idx=1, .completion_error_idx=UINT64_MAX};
    struct ibv_wc wc[2];
    arm(&cq, &a, &ca, 4, 101);
    arm(&cq, &b, &cb, 0, 202);
    assert(mlx5_srm_poll_watermarks(&cq, 1, wc) == 1);
    assert(wc[0].wr_id == 202 && cq.srm_pending_head == &a);
    assert(cq.srm_pending_tail == &a && !b.srm_completion_pending);
    assert(mlx5_srm_poll_watermarks(&cq, 2, wc) == 0);
    ca.cons_idx = 5;
    assert(mlx5_srm_poll_watermarks(&cq, 0, wc) == 0);
    assert(mlx5_srm_poll_watermarks(&cq, 2, wc) == 1);
    assert(wc[0].wr_id == 101 && wc[0].byte_len == 2048 && wc[0].qp_num == 17);
    assert(wc[0].status == IBV_WC_SUCCESS && !cq.srm_pending_head && !cq.srm_pending_tail);
    /* Completion target crosses the 64-bit wrap; do not report it early. */
    ca.cons_idx = UINT64_MAX;
    arm(&cq, &a, &ca, UINT64_MAX, 303);
    assert(mlx5_srm_poll_watermarks(&cq, 1, wc) == 0);
    ca.cons_idx = 0;
    assert(mlx5_srm_poll_watermarks(&cq, 1, wc) == 1 && wc[0].wr_id == 303);
    ca.cons_idx = ca.completion_error_idx = 7;
    ca.completion_error_status = IBV_WC_LOC_PROT_ERR;
    ca.completion_error_vendor = 0x87;
    arm(&cq, &a, &ca, 6, 404);
    assert(mlx5_srm_poll_watermarks(&cq, 1, wc) == 1);
    assert(wc[0].status == IBV_WC_LOC_PROT_ERR && wc[0].vendor_err == 0x87);
    puts("PASS: production watermark poller: readiness, shared CQ, limits, wrap, WC identity/error");
}
'''
    with tempfile.TemporaryDirectory(prefix="srm-cq-toggle-test-") as tmp:
        executable = str(Path(tmp) / "watermarks")
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-x", "c", "-", "-o", executable],
                       input=stub + body + test, text=True, check=True)
        subprocess.run([executable], check=True)


if __name__ == "__main__":
    check_history()
    check_watermarks()
