#!/usr/bin/env python3
"""Hardware-free tests of production user/kernel post-to-DB accounting.

Needs the matching sibling rdma-kerndriver checkout. Does not install modules,
change macro defaults, or touch either live build directory.
"""
from pathlib import Path
import subprocess
import tempfile

from test_srm_legacy_cq_toggle import function, preprocess, compact, historical
from test_srm_legacy_diagnostics import declaration
from test_srm_reroute_byte_window import PRELUDE, BODY

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT.parent / "rdma-kerndriver"
KP = "drivers/infiniband/hw/mlx5/"


def main():
    uh = (ROOT / "providers/mlx5/mlx5.h").read_text()
    uq = (ROOT / "providers/mlx5/qp.c").read_text()
    kh = (KERNEL / KP / "scheduler.h").read_text()
    ks = (KERNEL / KP / "scheduler.c").read_text()
    # Compare against this checkout's base, not an unrelated ancient branch.
    for mode in (0, 1):
        for share in (0, 1):
            defines = ("#define MLX5_SRM_ENABLE_WQE_TIMING 0\n"
                       "#define MLX5_SRM_ENABLE_PRIVATE_CQ 0\n"
                       "#define MLX5_SRM_ENABLE_CQE_CYCLE_STATS 0\n"
                       f"#define MLX5_SRM_ENABLE_DB_SHARE_STATS {share}\n")
            for root, path, names, source in (
                (ROOT, "providers/mlx5/qp.c", ["srm_try_direct_user_db"], uq),
                (KERNEL, KP + "scheduler.c",
                 ["mlx5_srm_ring_shared_db", "scheduler_polling"], ks),
            ):
                old = preprocess(defines + historical(root, "HEAD", path), mode)
                new = preprocess(defines + source, mode)
                for name in names:
                    assert compact(function(new, name)) == compact(function(old, name)), name
    print("PASS: WQE_TIMING=0 preserves production DB/scheduler function bodies")

    defs = PRELUDE.replace("#define MLX5_SRM_ENABLE_DB_SHARE_STATS 0", "")
    defs = defs.replace("struct mlx5_sq_ctrl_page {", declaration(uh, "mlx5_srm_db_share") +
                        "\nstruct mlx5_sq_ctrl_page {\n"
                        "uint32_t flags; struct mlx5_srm_db_share db_share;")
    defs = defs.replace("static unsigned rings, last_index, hints;",
                        "static unsigned rings, last_index, hints;\n"
                        "static uint64_t fake_tsc = 1000;\n"
                        "static void *recycle_at_db;\n"
                        "static uint64_t mlx5_srm_timing_rdtsc(void) { return fake_tsc; }")
    defs = defs.replace("    rings++;", "    rings++;\n"
                        "    if (recycle_at_db) memset(recycle_at_db, 0, 64 * 32);")
    defs += "\n#define MLX5_SRM_CTRL_F_DB_SHARE_STATS (1U << 1)\n"
    defs += declaration(uh, "mlx5_srm_wqe_timestamp") + "\n"
    defs += declaration(uh, "mlx5_srm_db_timing_batch") + "\n"
    defs += "static struct mlx5_srm_wqe_timestamp *mlx5_srm_timestamp_array(uint64_t *p, unsigned n) { return (void *)(p+n); }\n"
    defs += "static void " + function(uh, "mlx5_srm_record_user_db_share") + "\n"
    defs += "static void " + function(uq, "mlx5_srm_timing_db_add") + "\n"
    for name, result in (("srm_claim_direct_db_credit", "uint32_t"),
                         ("srm_release_direct_db_credit", "void"),
                         ("srm_try_direct_user_db", "void")):
        defs += "static " + result + " " + function(uq, name) + "\n"

    # Kernel helpers/report with a small mock control pool; real function bodies.
    defs += r'''
typedef unsigned long long u64;
typedef int64_t s64;
typedef uint32_t u32;
#define READ_ONCE(x) __atomic_load_n(&(x), __ATOMIC_RELAXED)
#define WRITE_ONCE(x,v) __atomic_store_n(&(x), (v), __ATOMIC_RELAXED)
#define smp_load_acquire(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define smp_store_release(p,v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define smp_rmb() __atomic_thread_fence(__ATOMIC_ACQUIRE)
#define smp_wmb() __atomic_thread_fence(__ATOMIC_RELEASE)
#define PAGE_SIZE 4096
#define MLX5_SRM_TIMING_OFFSET(n) ((n) * sizeof(uint64_t))
#define MLX5_SRM_ENABLE_CQE_CYCLE_STATS 0
#define MLX5_SRM_DIAG_INTERVAL_MS 1000
#define NUM_SRMC 4
#define time_before(a,b) ((long)((a)-(b)) < 0)
#define msecs_to_jiffies(x) (x)
#define jiffies_to_msecs(x) (x)
static unsigned long jiffies;
static char report[8192];
#define pr_info(...) snprintf(report, sizeof(report), __VA_ARGS__)
static u64 div64_u64(u64 a, u64 b) { return a / b; }
static u64 div64_u64_rem(u64 a, u64 b, u64 *r) { *r = a % b; return a / b; }
static u64 rdtsc_ordered(void) { return fake_tsc; }
static void *page_address(void *p) { return p; }
struct mlx5_ib_srmc { void **publish_pages; unsigned publish_depth, publish_npages; };
struct mlx5_ib_sched_worker { unsigned kqp_begin, kqp_end, worker_id; };
struct mlx5_qp_ctrl_pool { struct mlx5_sq_ctrl_page ctrl[NUM_SRMC]; };
static struct mlx5_sq_ctrl_page *mlx5_sq_ctrl_get_slot(struct mlx5_qp_ctrl_pool *p, u32 i)
{ return &p->ctrl[i]; }
'''
    defs += declaration(ks, "mlx5_srm_db_timing_batch").replace(
        "mlx5_srm_db_timing_batch", "kernel_batch") + "\n"
    defs += declaration(ks, "mlx5_srm_db_timing_stats") + "\n"
    defs += declaration(ks, "mlx5_srm_db_share_snapshot") + "\n"
    defs += "static struct kernel_batch " + function(ks, "mlx5_srm_timing_db_snapshot").replace(
        "mlx5_srm_db_timing_batch", "kernel_batch") + "\n"
    defs += "static void " + function(ks, "mlx5_srm_timing_db_complete").replace(
        "mlx5_srm_db_timing_batch", "kernel_batch") + "\n"
    defs += "static bool " + function(ks, "mlx5_srm_read_db_share") + "\n"
    defs += "static u64 " + function(ks, "mlx5_srm_diag_ratio") + "\n"
    defs += r'''
struct mlx5_srm_cq_workspace {
    struct mlx5_srm_db_timing_stats db_timing[NUM_SRMC];
    struct mlx5_srm_db_share_snapshot db_timing_previous[NUM_SRMC];
    struct mlx5_srm_db_share_snapshot db_previous[NUM_SRMC];
    unsigned long diag_last_report;
};
'''
    defs += "static void " + function(ks, "mlx5_srm_report_diag") + "\n"

    fixtures = BODY[:BODY.index("int main(void)")]
    fixtures = fixtures.replace("tokens[64]", "tokens[64 + 64 * 4]")
    fixtures = fixtures.replace("ctrl.db_tail = start;", "recycle_at_db = NULL; fake_tsc = 1000; ctrl.db_tail = start;")
    fixtures = fixtures.replace("        tokens[index] =", """
        struct mlx5_srm_wqe_timestamp *t = &mlx5_srm_timestamp_array(tokens, 64)[index];
        t->post_tsc = 100 + i * 10; t->sequence = post + 1;
        tokens[index] =""")
    tests = r'''
int main(void)
{
    init(0, 4, 1024, 0); attempt(0);
    assert(ctrl.db_share.user_calls == 1 && ctrl.db_share.user_wqes == 4);
    assert(ctrl.db_share.user_timed_wqes == 4 && ctrl.db_share.user_post_to_db_cycles == 3540);
    assert(ctrl.db_share.user_timing_excluded == 0);
    attempt(0); assert(ctrl.db_share.user_calls == 1); /* no re-DB */
    init(0, 8, 1024, 0); tokens[1] = 0; attempt(7);
    assert(!rings && !ctrl.db_share.user_calls); /* helper scanned but could not DB */
    attempt(0); assert(rings == 1 && ctrl.db_share.user_timed_wqes == 1);
    assert(ctrl.db_share.user_post_to_db_cycles == 900);
    init(0, 4, 1024, 2048); attempt(0);
    assert(ctrl.db_share.user_timed_wqes == 2 && ctrl.db_share.user_post_to_db_cycles == 1790);
    attempt(2); assert(ctrl.db_share.user_calls == 1); /* byte credit stall */
    init(0, 4, 1024, 0); credit.credit_limit = 0; attempt(0);
    assert(!rings && !ctrl.db_share.user_calls);
    init(0, 4, 1024, 0); ctrl.db_owner = 2;
    srm_try_direct_user_db(&mapping, &ctrl, tokens, 64, &wq, 77, 0, false);
    assert(!rings && !ctrl.db_share.user_calls); /* owner busy */
    init(0, 4, 1024, 0);
    recycle_at_db = mlx5_srm_timestamp_array(tokens, 64); attempt(0);
    assert(ctrl.db_share.user_post_to_db_cycles == 3540); /* no sidecar reads AFTER MMIO */
    init(0, 4, 1024, 0);
    mlx5_srm_timestamp_array(tokens, 64)[1].sequence = 77;
    mlx5_srm_timestamp_array(tokens, 64)[2].post_tsc = 0;
    attempt(0);
    assert(ctrl.db_share.user_timed_wqes == 2 && ctrl.db_share.user_timing_excluded == 2);
    assert(ctrl.db_share.user_post_to_db_cycles == 1770);
    init(0, 4, 1024, 0);
    mlx5_srm_timestamp_array(tokens, 64)[1].post_tsc = 1001; attempt(0);
    assert(ctrl.db_share.user_timed_wqes == 0 && ctrl.db_share.user_timing_excluded == 4);
    assert(ctrl.db_share.user_post_to_db_cycles == 0); /* future TSC not folded into mean */
    const uint64_t starts[] = {65534, (1ULL << 48)-2,
#if MLX5_SRM_ENABLE_REROUTE
        MLX5_SRM_REROUTE_MASK-1
#else
        UINT64_MAX-1
#endif
    };
    for (unsigned i = 0; i < sizeof(starts)/sizeof(starts[0]); i++) {
        init(starts[i], 4, 1024, 0); attempt(starts[i]);
        assert(ctrl.db_share.user_timed_wqes == 4);
        assert(ctrl.db_share.user_post_to_db_cycles == 3540);
    }
    init(0, 4, 1024, 0); fake_tsc = UINT64_MAX - 10;
    for (unsigned i = 0; i < 4; i++) mlx5_srm_timestamp_array(tokens, 64)[i].post_tsc = fake_tsc - 100;
    attempt(0); assert(ctrl.db_share.user_post_to_db_cycles == 400); /* sum overflow */
    init(0, 4, 1024, 0);
    ctrl.db_share.seq = UINT32_MAX - 1;
    ctrl.db_share.user_calls = UINT64_MAX;
    ctrl.db_share.user_wqes = ctrl.db_share.user_timed_wqes = UINT64_MAX - 1;
    ctrl.db_share.user_post_to_db_cycles = UINT64_MAX - 100;
    attempt(0);
    assert(ctrl.db_share.seq == 0 && ctrl.db_share.user_calls == 0);
    assert(ctrl.db_share.user_wqes == 2 && ctrl.db_share.user_timed_wqes == 2);
    assert(ctrl.db_share.user_post_to_db_cycles == 3439); /* cumulative counter wrap */
    puts("PASS: user actual-prefix weighting, abort/stall/busy, recycle, stale/future TSC, wraps");

    init(0, 4, 1024, 0);
    void *pages[] = {tokens};
    struct mlx5_ib_srmc kqp = {pages, 64, 1};
    struct kernel_batch batch = mlx5_srm_timing_db_snapshot(&kqp, 0, 4);
    struct mlx5_srm_db_timing_stats stats = {0};
    memset(mlx5_srm_timestamp_array(tokens, 64), 0, 64 * 32);
    mlx5_srm_timing_db_complete(&stats, &batch, 1000);
    assert(stats.checked == 4 && stats.db_wqes == 4 && stats.post_to_db_cycles == 3540);
    batch = mlx5_srm_timing_db_snapshot(&kqp, 0, 4);
    mlx5_srm_timing_db_complete(&stats, &batch, 1000);
    assert(stats.checked == 8 && stats.missing == 4 && stats.db_wqes == 4);
    init(0, 4, 1024, 0);
    mlx5_srm_timestamp_array(tokens, 64)[1].post_tsc = 1001;
    batch = mlx5_srm_timing_db_snapshot(&kqp, 0, 4);
    assert(batch.invalid == 1 && batch.valid == 3);
    for (unsigned i = 0; i < sizeof(starts)/sizeof(starts[0]); i++) {
        init(starts[i], 4, 1024, 0);
        batch = mlx5_srm_timing_db_snapshot(&kqp, starts[i], 4);
        assert(batch.valid == 4 && batch.post_tsc_sum == 460);
    }
    puts("PASS: kernel snapshot before MMIO and missing timestamp denominator");

#if !MLX5_SRM_ENABLE_DB_SHARE_STATS
    struct mlx5_srm_cq_workspace workspace = {0};
    struct mlx5_qp_ctrl_pool pool = {0};
    struct mlx5_ib_sched_worker worker = {0, 2, 0};
    init(0, 4, 1024, 0); attempt(0); pool.ctrl[0] = ctrl;
    workspace.db_timing[0] = (struct mlx5_srm_db_timing_stats) {1, 2, 6000, 0, 0, 2};
    pool.ctrl[0].db_share.seq |= 1; /* racing writer: defer BOTH sources */
    jiffies = 1000; mlx5_srm_report_diag(0, &worker, &pool, &workspace);
    assert(!report[0] && workspace.db_timing[0].db_wqes == 2);
    pool.ctrl[0].db_share.seq++;
    jiffies = 2000; mlx5_srm_report_diag(0, &worker, &pool, &workspace);
    assert(strstr(report, "user_timed_wqes=4 ") && strstr(report, "kernel_timed_wqes=2 "));
    assert(strstr(report, " post_to_db_cycles=9540 post_to_db_avg_cycles=1590"));
    assert(!workspace.db_timing[0].db_wqes);
    report[0] = 0;
    jiffies = 3000; mlx5_srm_report_diag(0, &worker, &pool, &workspace);
    assert(!report[0]); /* idle window cannot repeat counts */
    puts("PASS: combined WQE-weighted report, deferred windows, no double count, timing without SHARE");
#endif
}
'''
    with tempfile.TemporaryDirectory(prefix="srm-db-timing-all-") as tmp:
        binary = str(Path(tmp) / "test")
        for reroute in (0, 1):
            for share in (0, 1):
                subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-Werror=implicit-function-declaration",
                                "-fsanitize=address,undefined", "-DMLX5_SRM_ENABLE_WQE_TIMING=1",
                                f"-DMLX5_SRM_ENABLE_REROUTE={reroute}",
                                f"-DMLX5_SRM_ENABLE_DB_SHARE_STATS={share}",
                                "-I" + str(ROOT / "kernel-headers"),
                                "-x", "c", "-", "-o", binary],
                               input=defs + fixtures + tests, text=True, check=True)
                subprocess.run([binary], check=True)
        # Verify real shared structs are still layout-identical; no extra mmap.
        abi = "#include <stdint.h>\n#include <stddef.h>\n#include <rdma/mlx5-srm-reroute.h>\n"
        abi += "typedef uint64_t atomic64_t;\n"
        for header, prefix in ((uh, "user_"), (kh, "kernel_")):
            for name in ("mlx5_srm_db_share", "mlx5_sq_ctrl_page"):
                abi += declaration(header, name).replace("mlx5_srm_db_share", prefix + "share").replace(
                    "mlx5_sq_ctrl_page", prefix + "ctrl") + "\n"
        abi += '''
_Static_assert(sizeof(struct user_share) == 64 && sizeof(struct kernel_share) == 64, "cacheline");
_Static_assert(sizeof(struct user_ctrl) == 512 && sizeof(struct kernel_ctrl) == 512, "ctrl");
_Static_assert(offsetof(struct user_ctrl, db_share) == 320 && offsetof(struct kernel_ctrl, db_share) == 320, "offset");
_Static_assert(offsetof(struct user_ctrl, route) == 384 && offsetof(struct kernel_ctrl, route) == 384, "route");
int main(void) { return 0; }
'''
        subprocess.run(["cc", "-std=gnu11", "-I" + str(ROOT / "kernel-headers"),
                        "-x", "c", "-", "-o", binary], input=abi, text=True, check=True)
        subprocess.run([binary], check=True)
    print("PASS: actual user/kernel shared ABI stays 64B/512B")


if __name__ == "__main__":
    main()
