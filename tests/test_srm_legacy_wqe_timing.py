#!/usr/bin/env python3
"""Hardware-free checks using the production timestamp/credit-free helpers."""
from pathlib import Path
import re
import subprocess
import tempfile
from test_srm_legacy_cq_toggle import ROOT, KERNEL, KPATH, preprocess, function, compact, historical
from test_srm_legacy_diagnostics import declaration

FLAG = "MLX5_SRM_ENABLE_WQE_TIMING"


def main():
    qp = (ROOT / "providers/mlx5/qp.c").read_text()
    cq = (ROOT / "providers/mlx5/cq.c").read_text()
    header = (ROOT / "providers/mlx5/mlx5.h").read_text()
    verbs = (ROOT / "providers/mlx5/verbs.c").read_text()
    sched = (KERNEL / KPATH / "scheduler.c").read_text()
    kh = (KERNEL / KPATH / "scheduler.h").read_text()
    kqp = (KERNEL / KPATH / "qp.c").read_text()
    for mode in (0, 1):
        for name, src in (("user post", qp), ("user CQ", cq), ("kernel", sched)):
            off = preprocess(src, mode)
            assert not re.search(r"\bmlx5_srm_timing_", off), name
        assert "srm_timing_entries" not in preprocess(header, mode)
        allocation = function(kqp, "mlx5_ib_alloc_srmc_publish")
        assert "MLX5_SRM_TIMING_MAP_BYTES" not in preprocess(allocation, mode)
        for root, path, src, name in (
            (ROOT, "providers/mlx5/qp.c", qp, "srm_try_direct_user_db"),
            (KERNEL, KPATH + "scheduler.c", sched, "scheduler_polling"),
            (KERNEL, KPATH + "scheduler.c", sched, "srm_poll_srmc_once"),
        ):
            assert compact(function(preprocess(src, mode), name)) == compact(
                function(preprocess(historical(root, "HEAD", path), mode), name)), name
        enabled_cq = preprocess("#define " + FLAG + " 1\n" + cq, mode)
        assert "mlx5_srm_timing_complete_wq" in function(enabled_cq, "mlx5_parse_cqe")
        if mode:
            assert "mlx5_srm_timing_complete_wq" in function(enabled_cq, "mlx5_srm_poll_watermarks")
    for h in (header, kh):
        assert re.search(r"#define " + FLAG + r" 0\b", h)
        assert "#define MLX5_SRM_CTRL_F_WQE_TIMING (1U << 2)" in h
    enabled_post = preprocess("#define " + FLAG + " 1\n" + qp, 1)
    assert "source=user" not in enabled_post
    assert "mlx5_srm_timing_" not in function(enabled_post, "srm_try_direct_user_db")
    print("PASS: timing OFF preserves legacy DB/scheduler/poll bodies; both CQ paths hooked")

    enabled = lambda s: preprocess("#define " + FLAG + " 1\n" + s, 1)
    us, q, v = enabled(header), enabled(header + "\n" + qp), enabled(header + "\n" + verbs)
    defs = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <pthread.h>
enum ibv_wc_status { IBV_WC_SUCCESS = 0, IBV_WC_GENERAL_ERR = 21 };
static uint64_t fake_tsc;
static uint64_t mlx5_srm_timing_rdtsc(void) { return fake_tsc; }
struct mlx5_cq { int lock; };
struct ibv_qp { struct mlx5_cq *send_cq; };
struct mlx5_qp { struct ibv_qp *ibv_qp; };
static struct mlx5_cq *to_mcq(struct mlx5_cq *cq) { return cq; }
static void mlx5_spin_lock(int *p) { assert(!*p); *p = 1; }
static void mlx5_spin_unlock(int *p) { assert(*p); *p = 0; }
'''
    for name in ("mlx5_srm_timing_entry", "mlx5_srm_wqe_timestamp",
                 "mlx5_srm_db_share", "mlx5_sq_ctrl_page"):
        defs += declaration(us, name) + "\n"
    fields = declaration(us, "mlx5_wq").split("uint64_t", 1)[0]
    defs += fields + "};\n"
    for name in ("mlx5_srm_wqe_timing_stats",):
        defs += declaration(q, name) + "\n"
    defs += "static struct mlx5_srm_wqe_timing_stats srm_wqe_timing_stats;\n"
    for name, result, source in (
        ("mlx5_srm_timestamp_array", "struct mlx5_srm_wqe_timestamp *", q),
        ("mlx5_srm_timing_complete", "void", q),
        ("mlx5_srm_timing_prepare", "int", q),
        ("mlx5_srm_timing_publish", "void", q),
        ("mlx5_srm_timing_complete_wq", "void", q),
        ("mlx5_srm_check_timing_mapping", "int", v),
    ):
        defs += "static " + result + " " + function(source, name) + "\n"
    body = r'''
int main(void) {
    struct mlx5_cq cq = {0};
    struct ibv_qp ibqp = {&cq};
    struct mlx5_qp qp = {&ibqp};
    struct mlx5_wq wq = {0}, large = {0};
    for (unsigned i = 0; i < 40; i++) {
        assert(!mlx5_srm_timing_prepare(&qp, &wq));
        mlx5_srm_timing_publish(&qp, &wq, 65530 + i * 2, 100 + i);
    }
    assert(wq.srm_timing_capacity == 64 && wq.srm_timing_count == 40);
    fake_tsc = 1000;
    mlx5_srm_timing_complete_wq(&wq, (uint16_t)(65530 + 39 * 2), IBV_WC_SUCCESS);
    assert(wq.srm_timing_count == 0);
    assert(srm_wqe_timing_stats.cqe_wqes == 40);
    assert(srm_wqe_timing_stats.post_to_cqe_cycles == 40 * 900 - 39 * 40 / 2);
    /* Independent large lane, multiple signaled completions and u64 slot wrap. */
    assert(!mlx5_srm_timing_prepare(&qp, &large));
    mlx5_srm_timing_publish(&qp, &large, UINT64_MAX, 100);
    mlx5_srm_timing_publish(&qp, &large, 0, 150);
    mlx5_srm_timing_complete_wq(&large, 65535, IBV_WC_SUCCESS);
    assert(large.srm_timing_count == 1 && wq.srm_timing_count == 0);
    mlx5_srm_timing_complete_wq(&large, 19, IBV_WC_SUCCESS);
    assert(large.srm_timing_count == 1 && srm_wqe_timing_stats.missing_timestamps == 1);
    mlx5_srm_timing_complete_wq(&large, 0, IBV_WC_GENERAL_ERR);
    assert(large.srm_timing_count == 0 && srm_wqe_timing_stats.error_wqes == 1);
    /* Ring grows after wrap without changing FIFO order. */
    for (unsigned i = 0; i < 80; i++) {
        assert(!mlx5_srm_timing_prepare(&qp, &wq));
        mlx5_srm_timing_publish(&qp, &wq, i, 100);
    }
    mlx5_srm_timing_complete_wq(&wq, 79, IBV_WC_SUCCESS);
    assert(!wq.srm_timing_count && wq.srm_timing_capacity == 128);
    puts("PASS: logical timestamp FIFO growth/reuse, cumulative completions, separate lanes and counter wrap");
    {
        uint64_t memory[64] = {0};
        struct mlx5_srm_wqe_timestamp *a = mlx5_srm_timestamp_array(memory, 8);
        assert((char *)a - (char *)memory == 8 * sizeof(uint64_t));
        a[0].sequence = 1;
        a[0].post_tsc = 200;
        assert(a[0].sequence == 1 && a[0].post_tsc == 200);
        struct mlx5_sq_ctrl_page ctrl = {.flags=4};
        assert(!mlx5_srm_check_timing_mapping(&ctrl, 8, 192));
        assert(mlx5_srm_check_timing_mapping(&ctrl, 8, 191) == EPROTO);
        ctrl.flags = 2; /* DB_SHARE_STATS must not masquerade as timing. */
        assert(mlx5_srm_check_timing_mapping(&ctrl, 8, 4096) == EPROTO);
        puts("PASS: timestamp sidecar layout/sequence and mapping ABI checks");
    }
    free(wq.srm_timing_entries);
    free(large.srm_timing_entries);
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="srm-legacy-timing-test-") as temp:
        binary = str(Path(temp) / "timing")
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-pthread", "-x", "c", "-", "-o", binary],
                       input=defs + body, text=True, check=True)
        subprocess.run([binary], check=True)


if __name__ == "__main__":
    main()
