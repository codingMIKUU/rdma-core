#!/usr/bin/env python3
"""Offline tests of the production FARM timing helpers; no RDMA/module needed."""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT.parent / "farm-rdma-kerndriver/drivers/infiniband/hw/mlx5"


def function(source, name):
    start = source.index(name + "(")
    start = source.rfind("\n", 0, start) + 1
    # Include a return type on the previous line if needed.
    if source[start:].startswith(name):
        start = source.rfind("\n", 0, start - 1) + 1
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    header = (ROOT / "providers/mlx5/mlx5.h").read_text()
    qp = (ROOT / "providers/mlx5/qp.c").read_text()
    verbs = (ROOT / "providers/mlx5/verbs.c").read_text()
    kernel_header = (KERNEL / "scheduler.h").read_text()
    kernel = (KERNEL / "scheduler.c").read_text()
    for name in ("mlx5_sq_ctrl_page", "mlx5_srm_kernel_cqe_timestamp"):
        fields = []
        for text in (header, kernel_header):
            body = re.search(r"struct " + name + r" \{(.*?)\n\}", text, re.S)[1]
            body = body.replace("__u64", "uint64_t").replace("__u32", "uint32_t").replace("__u8", "uint8_t")
            fields.append(re.sub(r"\s+", "", body))
        assert fields[0] == fields[1], name + " ABI differs"

    # Actual publish order: CQE data + embedded timestamps, owner, SQ recycle.
    flush = kernel[kernel.rindex("static inline void mlx5_srm_flush_cqe_publish_batch("):]
    assert flush.index("memcpy(ucqe,") < flush.index("mlx5_ib_srmc_publish_kernel_dispatch_tsc(")
    assert flush.index("mlx5_ib_srmc_publish_kernel_dispatch_tsc(") < flush.index("smp_store_release(&ucqe64->op_own")
    assert flush.index("smp_store_release(&ucqe64->op_own") < flush.index("mlx5_srm_ctrl_complete_defer(")
    assert verbs.count("if (mlx5_srm_check_timing_abi(&ctrl_base[") == 2
    assert "MLX5_SRM_KERNEL_CQE_TSC_OFFSET" not in header + kernel_header + kernel
    for source in (header, kernel_header):
        assert "#define MLX5_SRM_KERNEL_CQE_TIMING_ABI 3U" in source
        assert "#define MLX5_SRM_CQE_TIMING_MAGIC 0x4641524d54534333ULL" in source

    declarations = header[header.index("struct mlx5_sq_ctrl_page {"):header.index("#define MLX5_SRM_PUBLISH_USR_BITS")]
    timing = qp[qp.index("#if MLX5_SRM_ENABLE_WQE_TIMING"):qp.index("static inline uint64_t srm_monotonic_ns(void);")]
    clock = function(timing, "mlx5_srm_timing_rdtsc")
    timing = timing.replace(clock, "static inline uint64_t mlx5_srm_timing_rdtsc(void) { return fake_tsc; }")
    publish = function(kernel, "mlx5_ib_srmc_publish_kernel_dispatch_tsc")
    abi = function(verbs, "mlx5_srm_check_timing_abi")
    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef uint8_t u8;
#define MLX5_SRM_ENABLE_WQE_TIMING 1
#define MLX5_SRM_WRID_POST_MASK 0xffffffffULL
#define PAGE_SIZE 4096U
#define U64_MAX UINT64_MAX
#define WRITE_ONCE(p, v) __atomic_store_n(&(p), (v), __ATOMIC_RELAXED)
#define smp_wmb() __atomic_thread_fence(__ATOMIC_RELEASE)
#define smp_store_release(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define page_address(p) ((void *)(p))
#define BUILD_BUG_ON(c) _Static_assert(!(c), "layout")
#define MLX5_CQE_REQ 0
#define MLX5_CQE_REQ_ERR 13
static unsigned warnings;
#define WARN_ON_ONCE(c) ((c) ? (++warnings, 1) : 0)
static uint64_t fake_tsc;
static uint64_t rdtsc_ordered(void) { return fake_tsc; }
'''
    structs = r'''
struct mlx5_wq {
    uint64_t *srm_timing_post_tsc_sum;
    uint32_t *srm_timing_post_wqes;
    struct mlx5_srm_kernel_cqe_ref *srm_timing_kernel_cqe;
    uint64_t srm_timing_pending_tsc_sum;
    uint32_t srm_timing_pending_wqes;
};
/* Same offsets as both real CQE layouts; production static assertions and
 * provider/kernel builds additionally validate the actual hardware headers. */
struct mlx5_cqe64 {
    uint8_t request_reserved[32];
    union { uint32_t srqn; uint32_t srqn_uidx; };
    uint8_t completion_fields[27];
    uint8_t op_own;
};
'''
    checks = r'''
#define DEPTH 256U
static uint64_t post_sum[DEPTH];
static uint32_t post_count[DEPTH];
static struct mlx5_srm_kernel_cqe_ref refs[DEPTH];
static struct mlx5_wq wq = {
    .srm_timing_post_tsc_sum = post_sum,
    .srm_timing_post_wqes = post_count,
    .srm_timing_kernel_cqe = refs,
};
static struct mlx5_cqe64 cq[DEPTH];
static void reset(void)
{
    memset(&srm_wqe_timing_stats, 0, sizeof(srm_wqe_timing_stats));
    memset(post_sum, 0, sizeof(post_sum));
    memset(post_count, 0, sizeof(post_count));
    memset(refs, 0, sizeof(refs));
    wq.srm_timing_pending_wqes = 0;
    wq.srm_timing_pending_tsc_sum = 0;
    memset(cq, 0, sizeof(cq));
}
static struct mlx5_srm_kernel_cqe_timestamp record(uint32_t slot)
{
    struct mlx5_srm_kernel_cqe_timestamp r;
    memcpy(&r, &cq[slot & (DEPTH - 1)], sizeof(r));
    return r;
}
static void prepare(uint32_t slot, uint64_t kernel_tsc, uint64_t dispatch_tsc)
{
    unsigned index = slot & (DEPTH - 1);
    mlx5_srm_timing_publish(&wq, index, slot, kernel_tsc - 1, true);
    fake_tsc = dispatch_tsc;
    cq[index].op_own = 0xf0; /* old owner/opcode must NOT be inspected */
    mlx5_ib_srmc_publish_kernel_dispatch_tsc(&cq[index], slot, kernel_tsc, 0);
    assert(record(slot).sequence == (((uint64_t)slot + 1) & MLX5_SRM_WRID_POST_MASK));
    assert(record(slot).kernel_dispatch_tsc == dispatch_tsc);
    assert(record(slot).magic == MLX5_SRM_CQE_TIMING_MAGIC);
    assert(cq[index].op_own == 0xf0); /* caller, not helper, commits owner */
}
static void consume(uint32_t slot)
{
    unsigned index = slot & (DEPTH - 1);
    mlx5_srm_timing_complete_wq(&wq, index, &cq[index]);
}
static void check(uint64_t n, uint64_t first, uint64_t second)
{
    assert(srm_wqe_timing_stats.kernel_to_user_cqes == n);
    assert(srm_wqe_timing_stats.kernel_poll_to_dispatch_cycles == first);
    assert(srm_wqe_timing_stats.dispatch_to_user_cycles == second);
    assert(srm_wqe_timing_stats.kernel_to_user_cycles == first + second);
}
int main(void)
{
    struct mlx5_sq_ctrl_page ctrl = {0};
    assert(sizeof(ctrl) == 128 && offsetof(struct mlx5_sq_ctrl_page, cons_idx) == 64);
    assert(mlx5_srm_check_timing_abi(&ctrl) == -1 && errno == EPROTO);
    ctrl.timing_abi = 2; /* old SQ-sidecar kernel must be rejected */
    assert(mlx5_srm_check_timing_abi(&ctrl) == -1 && errno == EPROTO);
    ctrl.timing_abi = MLX5_SRM_KERNEL_CQE_TIMING_ABI;
    assert(mlx5_srm_check_timing_abi(&ctrl) == 0);
    assert(sizeof(struct mlx5_cqe64) == 64);
    assert(MLX5_SRM_PUBLISH_MAP_BYTES(DEPTH) == DEPTH * 8);

    reset(); prepare(1, 100, 150); prepare(2, 130, 210);
    mlx5_srm_timing_poll_begin(); fake_tsc = 250;
    consume(1); consume(2);
    fake_tsc = 300; mlx5_srm_timing_poll_end(); check(2, 130, 240);
    assert(srm_wqe_timing_stats.post_to_cqe_cycles == 372);
    mlx5_srm_timing_poll_begin(); fake_tsc = 10000;
    mlx5_srm_timing_poll_end(); check(2, 130, 240); /* empty poll */
    /* Production report uses the same denominator and resets after printing. */
    srm_wqe_timing_stats.report_check_wqes = 1000000;
    mlx5_srm_timing_maybe_report(); check(0, 0, 0);

    reset(); prepare(3, 100, 150);
    mlx5_srm_timing_poll_begin(); fake_tsc = 9000;
    consume(3);
    fake_tsc = 10000; mlx5_srm_timing_poll_end(); check(1, 50, 9850);
    mlx5_srm_timing_poll_begin(); consume(3);
    mlx5_srm_timing_poll_end(); check(1, 50, 9850); /* no double counting */

    reset(); prepare(UINT32_MAX, 100, 150); /* expected sequence zero is valid */
    mlx5_srm_timing_poll_begin(); fake_tsc = 200;
    consume(UINT32_MAX);
    fake_tsc = 300; mlx5_srm_timing_poll_end(); check(1, 50, 150);

    reset(); prepare(4, UINT64_MAX - 99, UINT64_MAX - 49);
    mlx5_srm_timing_poll_begin(); fake_tsc = 20;
    consume(4);
    fake_tsc = 100; mlx5_srm_timing_poll_end(); check(1, 50, 150);

    reset(); uint64_t base = (1ULL << 63) - 100;
    prepare(1, base, base + 20); prepare(2, base + 10, base + 40);
    mlx5_srm_timing_poll_begin(); fake_tsc = base + 200;
    consume(1); consume(2);
    fake_tsc = base + 300; mlx5_srm_timing_poll_end(); check(2, 50, 540);

    for (unsigned bad = 0; bad < 6; ++bad) {
        reset(); prepare(1, 100, 150);
        struct mlx5_srm_kernel_cqe_timestamp r = record(1);
        if (bad == 0) r.sequence = UINT64_MAX;
        if (bad == 1) r.sequence++; /* actual CQE mismatch still rejected */
        if (bad == 2) r.kernel_dispatch_tsc = 0;
        if (bad == 3) r.kernel_dispatch_tsc = 99;
        if (bad == 4) r.kernel_dispatch_tsc = 10000;
        if (bad == 5) r.magic = 0;
        memcpy(&cq[1], &r, sizeof(r));
        mlx5_srm_timing_poll_begin(); fake_tsc = 200;
        consume(1);
        fake_tsc = 300; mlx5_srm_timing_poll_end(); check(0, 0, 0);
        assert(srm_wqe_timing_stats.kernel_to_user_missing +
               srm_wqe_timing_stats.kernel_to_user_invalid == 1);
    }

    /* One CQE for two WRs still counts as one handoff sample. */
    reset(); mlx5_srm_timing_publish(&wq, 1, 1, 50, false);
    prepare(2, 100, 150);
    mlx5_srm_timing_poll_begin(); fake_tsc = 200;
    consume(2);
    fake_tsc = 300; mlx5_srm_timing_poll_end(); check(1, 50, 150);
    assert(srm_wqe_timing_stats.cqe_wqes == 2);
    assert(srm_wqe_timing_stats.post_to_cqe_cycles == 451);

    /* Routing, length, hardware timestamp, syndrome/vendor code, WQE counter
     * and owner all live in the second 32 bytes and must remain unchanged. */
    for (unsigned error = 0; error < 2; ++error) {
        struct mlx5_cqe64 c, saved;
        memset(&c, 0x5a, sizeof(c)); saved = c;
        mlx5_ib_srmc_publish_kernel_dispatch_tsc(&c, 9, 100,
                                                error ? 0xd0 : 0);
        assert(!memcmp((char *)&c + 32, (char *)&saved + 32, 32));
        memcpy(&cq[9], &c, sizeof(c)); /* CQ cleaning/resizing carries record */
        assert(record(9).magic == MLX5_SRM_CQE_TIMING_MAGIC);
    }
    /* Unsupported receive/inline CQEs must never have payload overwritten. */
    for (unsigned flags = 0; flags < 3; ++flags) {
        struct mlx5_cqe64 c = {0}, saved = c;
        mlx5_ib_srmc_publish_kernel_dispatch_tsc(&c, 9, 100,
                                                flags == 0 ? 0x20 : flags * 4);
        assert(!memcmp(&c, &saved, sizeof(c)));
    }
    assert(warnings == 3);

    /* Regression: 4096 logical QPs share an 8-slot physical SQ. The SQ wraps
     * 512 times BEFORE ANY user polls. Each QP has one pending marker, with
     * its own software CQE. The old SQ side-array loses 4088/4096 samples. */
    enum { N = 4096, SQ = 8 };
    struct pending {
        struct mlx5_wq w;
        uint64_t sums[SQ]; uint32_t counts[SQ];
        struct mlx5_srm_kernel_cqe_ref refs[SQ];
        struct mlx5_cqe64 c;
    } *pending = calloc(N, sizeof(*pending));
    assert(pending);
    reset();
    for (unsigned i = 0; i < N; ++i) {
        struct pending *p = &pending[i];
        p->w.srm_timing_post_tsc_sum = p->sums;
        p->w.srm_timing_post_wqes = p->counts;
        p->w.srm_timing_kernel_cqe = p->refs;
        mlx5_srm_timing_publish(&p->w, i % SQ, i, 100, true);
        fake_tsc = 200 + i;
        mlx5_ib_srmc_publish_kernel_dispatch_tsc(&p->c, i, 150 + i, 0);
    }
    mlx5_srm_timing_poll_begin(); fake_tsc = 10000;
    for (unsigned i = 0; i < N; ++i)
        mlx5_srm_timing_complete_wq(&pending[i].w, i % SQ, &pending[i].c);
    fake_tsc = 11000; mlx5_srm_timing_poll_end();
    assert(srm_wqe_timing_stats.cqe_wqes == N);
    assert(srm_wqe_timing_stats.kernel_to_user_cqes == N);
    assert(srm_wqe_timing_stats.post_to_cqe_cycles == N * (11000 - 100));
    assert(srm_wqe_timing_stats.kernel_to_user_missing == 0);
    assert(srm_wqe_timing_stats.kernel_to_user_invalid == 0);
    assert(srm_wqe_timing_stats.missing_timestamps == 0);
    assert(srm_wqe_timing_stats.kernel_to_user_cycles ==
           srm_wqe_timing_stats.kernel_poll_to_dispatch_cycles +
           srm_wqe_timing_stats.dispatch_to_user_cycles);
    free(pending);
    puts("PASS: SQ reuse 512 times / 4096 pending CQEs: all valid, invalid=missing=0");
    puts("PASS: delayed/empty polls, identical endpoints, errors, CQE preservation, 32/64-bit wrap, ABI mismatch, honest invalid/missing detection");
}
'''
    with tempfile.TemporaryDirectory(prefix="farm-cqe-timing-test-") as tmp:
        src = Path(tmp) / "test.c"
        src.write_text(prelude + declarations + structs + timing + publish + abi + checks)
        exe = Path(tmp) / "test"
        subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                        str(src), "-pthread", "-o", str(exe)], check=True)
        result = subprocess.run([str(exe)], text=True, capture_output=True, check=True)
        print(result.stdout, end="")
        report = next(line for line in result.stderr.splitlines() if line.startswith("SRM_WQE_TIMING"))
        fields = dict(item.split("=", 1) for item in report.split()[1:])
        assert fields["kernel_to_user_cqes"] == "2"
        assert fields["timing_abi"] == "3"
        assert fields["post_to_cqe_avg_cycles"] == "186.00"
        assert fields["kernel_to_user_avg_cycles"] == "185.00"
        assert fields["kernel_poll_to_dispatch_avg_cycles"] == "65.00"
        assert fields["dispatch_to_user_avg_cycles"] == "120.00"
        print("PASS: report fields share the same CQE denominator; 65 + 120 = 185")


if __name__ == "__main__":
    main()
