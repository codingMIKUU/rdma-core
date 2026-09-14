#!/usr/bin/env python3
"""Check production diagnostic helpers/ABI without using RDMA devices."""
from pathlib import Path
import subprocess
import tempfile
from test_srm_legacy_cq_toggle import ROOT, KERNEL, KPATH, preprocess, function, compact, historical

DB = "MLX5_SRM_ENABLE_DB_SHARE_STATS"
CQ = "MLX5_SRM_ENABLE_CQE_CYCLE_STATS"


def declaration(source, name):
    start = source.index("struct " + name + " {")
    return source[start:source.index(";", source.index("}", start)) + 1]


def enabled(source, cq_mode=0):
    return preprocess("#define " + DB + " 1\n#define " + CQ + " 1\n" + source, cq_mode)


def main():
    user_header = (ROOT / "providers/mlx5/mlx5.h").read_text()
    kern_header = (KERNEL / KPATH / "scheduler.h").read_text()
    user_qp = (ROOT / "providers/mlx5/qp.c").read_text()
    kern_sched = (KERNEL / KPATH / "scheduler.c").read_text()

    # Statistics OFF preserves both the old posting and kernel DB bodies.
    for mode in (0, 1):
        for root, path, name, current in (
            (ROOT, "providers/mlx5/qp.c", "srm_try_direct_user_db", user_qp),
            (KERNEL, KPATH + "scheduler.c", "mlx5_srm_ring_shared_db", kern_sched),
        ):
            assert compact(function(preprocess(current, mode), name)) == compact(
                function(preprocess(historical(root, "fb906ede" if root == ROOT else
                                               "12da670", path), mode), name))
        for forbidden in ("mlx5_srm_record_kernel_db_share", "mlx5_srm_report_diag",
                          "srm_poll_srmc_once_untimed", "cqe_cycles", "db_previous"):
            assert forbidden not in preprocess(kern_sched, mode), forbidden
        assert "mlx5_srm_record_user_db_share" not in preprocess(user_qp, mode)
        measured = enabled(kern_sched, mode)
        # The wrapped body itself must remain unchanged in both CQ modes.
        body = function(measured, "srm_poll_srmc_once_untimed").replace(
            "srm_poll_srmc_once_untimed", "srm_poll_srmc_once")
        assert compact(body) == compact(function(preprocess(kern_sched, mode),
                                                 "srm_poll_srmc_once"))
    print("PASS: statistics OFF adds no DB/poll hooks; measured bodies unchanged")

    us = enabled(user_header)
    ks = enabled(kern_header)
    sc = enabled(kern_sched)
    defs = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef uint64_t __u64;
typedef uint32_t __u32;
typedef uint8_t __u8;
typedef uint64_t atomic64_t;
#define U64_MAX UINT64_MAX
static unsigned fls64(uint64_t x) { return x ? 64 - __builtin_clzll(x) : 0; }
#define READ_ONCE(x) __atomic_load_n(&(x), __ATOMIC_RELAXED)
#define WRITE_ONCE(x,v) __atomic_store_n(&(x),(v), __ATOMIC_RELAXED)
#define smp_load_acquire(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define smp_store_release(p,v) __atomic_store_n((p),(v), __ATOMIC_RELEASE)
#define smp_wmb() __atomic_thread_fence(__ATOMIC_RELEASE)
#define smp_rmb() __atomic_thread_fence(__ATOMIC_ACQUIRE)
static u64 div64_u64(u64 a, u64 b) { return a / b; }
static u64 div64_u64_rem(u64 a, u64 b, u64 *r) { *r = a % b; return a / b; }
'''
    for name in ("mlx5_srm_db_share", "mlx5_sq_ctrl_page"):
        defs += declaration(us, name) + "\n"
    for name in ("mlx5_srm_db_share", "mlx5_sq_ctrl_page"):
        defs += declaration(ks, name).replace("mlx5_srm_db_share", "kernel_db_share").replace(
            "mlx5_sq_ctrl_page", "kernel_ctrl_page") + "\n"
    defs += declaration(sc, "mlx5_srm_db_share_snapshot") + "\n"
    for name, source in (("mlx5_srm_record_user_db_share", us),
                         ("mlx5_srm_record_kernel_db_share", sc),
                         ("mlx5_srm_read_db_share", sc),
                         ("mlx5_srm_diag_ratio", sc)):
        result_type = ("bool" if name == "mlx5_srm_read_db_share" else
                       "u64" if name == "mlx5_srm_diag_ratio" else "void")
        defs += "static " + result_type + " " + function(source, name) + "\n"
    defs += declaration(sc, "mlx5_srm_cqe_cycle_stats") + "\n"
    defs += "static void " + function(sc, "mlx5_srm_cqe_hist_add") + "\n"
    defs += "static u64 " + function(sc, "mlx5_srm_cqe_p99_upper") + "\n"
    defs += r'''
struct mlx5_srm_cq_workspace { struct mlx5_srm_cqe_cycle_stats cqe_cycles; };
struct mlx5_ib_sched;
struct mlx5_qp_ctrl_pool;
struct mlx5_ib_srmc;
struct ib_wc;
struct mlx5_ib_srm_sched_stats;
static u64 fake_tsc;
static int fake_cqes;
static u64 rdtsc_ordered(void) { return fake_tsc; }
static int srm_poll_srmc_once_untimed(struct mlx5_ib_sched *sched,
    struct mlx5_qp_ctrl_pool *pool, struct mlx5_ib_srmc *srmc,
    struct ib_wc *wc, void **cqe, struct mlx5_srm_cq_workspace *workspace,
    struct mlx5_ib_srm_sched_stats *stats) {
    (void)sched; (void)pool; (void)srmc; (void)wc; (void)cqe; (void)workspace; (void)stats;
    fake_tsc += fake_cqes > 0 ? 2000 : 200;
    return fake_cqes;
}
static int
'''
    defs += function(sc, "srm_poll_srmc_once") + "\n"
    body = r'''
static struct mlx5_sq_ctrl_page ctrl;
static unsigned done;
enum { NTHREAD = 8, NITER = 30000 };
static void *producer(void *argument) {
    uintptr_t kernel = (uintptr_t)argument & 1;
    for (int i = 0; i < NITER; i++) {
        uint32_t expected = 0;
        while (!__atomic_compare_exchange_n(&ctrl.db_owner, &expected, 1,
                 false, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) expected = 0;
        if (kernel) mlx5_srm_record_kernel_db_share(&ctrl, 7);
        else mlx5_srm_record_user_db_share(&ctrl, 3);
        __atomic_store_n(&ctrl.db_owner, 0, __ATOMIC_RELEASE);
    }
    __atomic_fetch_add(&done, 1, __ATOMIC_RELEASE);
    return NULL;
}
int main(void) {
    pthread_t threads[NTHREAD];
    struct mlx5_srm_db_share_snapshot snapshot;
    _Static_assert(sizeof(ctrl) == 512, "user ABI size");
    _Static_assert(sizeof(struct kernel_ctrl_page) == 512, "kernel ABI size");
    _Static_assert(offsetof(struct mlx5_sq_ctrl_page, db_share) == 320, "reserved cacheline");
    _Static_assert(offsetof(struct kernel_ctrl_page, db_share) == 320, "matching ABI");
    _Static_assert(offsetof(struct mlx5_sq_ctrl_page, latest_hot_hint) == 256, "hot ABI unchanged");
    _Static_assert(offsetof(struct kernel_ctrl_page, latest_hot_hint) == 256, "hot ABI unchanged");
    /* No matching kernel flag: the provider must not write reserved memory. */
    mlx5_srm_record_user_db_share(&ctrl, 3);
    assert(ctrl.db_share.seq == 0 && ctrl.db_share.user_calls == 0);
    ctrl.flags = 2;
    /* Sequence and event counters must tolerate wrap without lost deltas. */
    ctrl.db_share.seq = UINT32_MAX - 1;
    ctrl.db_share.user_calls = UINT64_MAX;
    ctrl.db_share.user_wqes = UINT64_MAX - 1;
    mlx5_srm_record_user_db_share(&ctrl, 3);
    assert(ctrl.db_share.seq == 0 && ctrl.db_share.user_calls == 0);
    assert(ctrl.db_share.user_wqes == 1);
    assert((uint64_t)(ctrl.db_share.user_calls - UINT64_MAX) == 1);
    ctrl.db_share.seq = 1;
    assert(!mlx5_srm_read_db_share(&ctrl.db_share, &snapshot));
    memset(&ctrl.db_share, 0, sizeof(ctrl.db_share));
    for (uintptr_t i = 0; i < NTHREAD; i++)
        assert(!pthread_create(&threads[i], NULL, producer, (void *)i));
    do {
        if (mlx5_srm_read_db_share(&ctrl.db_share, &snapshot)) {
            assert(snapshot.user_wqes == snapshot.user_calls * 3);
            assert(snapshot.kernel_wqes == snapshot.kernel_calls * 7);
        }
    } while (__atomic_load_n(&done, __ATOMIC_ACQUIRE) != NTHREAD);
    for (int i = 0; i < NTHREAD; i++) assert(!pthread_join(threads[i], NULL));
    assert(mlx5_srm_read_db_share(&ctrl.db_share, &snapshot));
    assert(snapshot.user_calls == NITER * NTHREAD / 2);
    assert(snapshot.kernel_calls == NITER * NTHREAD / 2);
    assert(snapshot.user_wqes == snapshot.user_calls * 3);
    assert(snapshot.kernel_wqes == snapshot.kernel_calls * 7);
    assert(mlx5_srm_diag_ratio(0, 0, 10000) == 0);
    assert(mlx5_srm_diag_ratio(3, 4, 10000) == 7500);
    assert(mlx5_srm_diag_ratio(12, 100, 10000) == 1200);
    assert(mlx5_srm_diag_ratio(3, 1, 100) == 300);
    puts("PASS: shared ABI, concurrent DB writers/snapshots, sequence/counter wrap, ratios");
    {
        struct mlx5_srm_cq_workspace workspace = {0};
        int returned[] = {32, 16, 0, -1};
        for (unsigned i = 0; i < sizeof(returned)/sizeof(returned[0]); i++) {
            fake_cqes = returned[i];
            assert(srm_poll_srmc_once(NULL, NULL, NULL, NULL, NULL, &workspace, NULL)
                   == returned[i]);
        }
        assert(workspace.cqe_cycles.calls == 4);
        assert(workspace.cqe_cycles.active_calls == 2);
        assert(workspace.cqe_cycles.cqes == 48);
        assert(workspace.cqe_cycles.active_cycles == 4000);
        assert(workspace.cqe_cycles.nonpositive_cycles == 400);
        assert(mlx5_srm_cqe_p99_upper(&workspace.cqe_cycles) == 127);
        assert(mlx5_srm_diag_ratio(workspace.cqe_cycles.active_cycles,
                                  workspace.cqe_cycles.cqes, 1) == 83);
        puts("PASS: production CQ wrapper: hardware-CQE denominator, empty/skip separation, return values");
    }
    {
        struct mlx5_srm_cqe_cycle_stats h = {0};
        assert(mlx5_srm_cqe_p99_upper(&h) == 0);
        mlx5_srm_cqe_hist_add(&h, 99 * 10, 99);
        mlx5_srm_cqe_hist_add(&h, 1000, 1);
        h.cqes = 100;
        assert(mlx5_srm_cqe_p99_upper(&h) == 10);
        mlx5_srm_cqe_hist_add(&h, 1000, 1);
        h.cqes++;
        assert(mlx5_srm_cqe_p99_upper(&h) == 1023);
        uint64_t values[] = {0, 1, 7, 8, 15, 16, 31, 32, 205, 283,
                             UINT64_C(1) << 63, UINT64_MAX};
        for (unsigned i = 0; i < sizeof(values)/sizeof(values[0]); i++) {
            memset(&h, 0, sizeof(h));
            mlx5_srm_cqe_hist_add(&h, values[i], 1);
            h.cqes = 1;
            uint64_t upper = mlx5_srm_cqe_p99_upper(&h);
            assert(upper >= values[i]);
            assert(upper - values[i] <= values[i] / 8);
        }
        puts("PASS: CQE-weighted nearest-rank P99, bucket boundaries and u64 extremes");
    }
}
'''
    with tempfile.TemporaryDirectory(prefix="srm-diag-test-") as tmp:
        binary = str(Path(tmp) / "diagnostics")
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-pthread", "-x", "c", "-", "-o", binary],
                       input=defs + body, text=True, check=True)
        subprocess.run([binary], check=True)


if __name__ == "__main__":
    main()
