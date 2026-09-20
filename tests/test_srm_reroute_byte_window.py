#!/usr/bin/env python3
"""Exercise the production user DB function without accessing an RDMA device."""
from pathlib import Path
import subprocess
import tempfile
from test_srm_legacy_cq_toggle import function

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r"""
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <endian.h>
#include <rdma/mlx5-srm-reroute.h>
#if !MLX5_SRM_ENABLE_REROUTE
#define mlx5_srm_rr_seq(v) ((uint64_t)(v))
#endif
#define MLX5_SRM_ENABLE_DB_SHARE_STATS 0
#define MLX5_SRM_DIRECT_DB_HELP_STRIDE 4
#define MLX5_SRM_LARGE_DB_LIMIT_ACTIVE 0
#define MLX5_SRM_LARGE_DB_LIMIT 50
#define MLX5_SRM_DB_OWNER_FREE 0
#define MLX5_SRM_DB_OWNER_USER 1
#define MLX5_SRM_PUBLISH_USR_BITS 16
#define MLX5_SRM_PUBLISH_SEQ_MASK ((1ULL << 48) - 1)
#define unlikely(x) (x)
#define min_t(t,a,b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
struct mlx5_sq_ctrl_page {
    uint64_t db_tail, resv_idx, issued_total, completed_total, credit_limit;
    uint32_t db_owner;
    struct mlx5_srm_route_ctrl route;
};
struct mlx5_wqe_ctrl_seg { uint32_t opmod_idx_opcode, qpn_ds; };
struct mlx5_wqe_data_seg { uint32_t byte_count; };
struct mlx5_wq { uint32_t wqe_cnt, wqe_shift; };
struct mlx5_srm_mapping_bundle {
    struct mlx5_sq_ctrl_page *farm_credit_ctrl;
    void *farm_uar_reg, *farm_db, *sq_map;
    uint32_t farm_direct_db_batch;
};
struct srm_direct_db_stats {
    uint64_t not_head, owner_busy, owner_acquired, no_pending, credit_stalls,
             partial_credit, scan_calls, scan_ready_wqes, no_ready, db_calls,
             db_wqes, db_max;
};
static struct srm_direct_db_stats srm_direct_db_stats;
static int srm_direct_db_stats_begin(struct mlx5_srm_mapping_bundle *m,
    struct mlx5_sq_ctrl_page *c, struct srm_direct_db_stats *s)
{ (void)m; (void)c; (void)s; return 1; }
static unsigned rings, last_index, hints;
static void srm_publish_kernel_hot_hint(struct mlx5_srm_mapping_bundle *m)
{ (void)m; hints++; }
static void srm_direct_ring_db(struct mlx5_srm_mapping_bundle *m,
    struct mlx5_sq_ctrl_page *c, uint64_t tail, struct mlx5_wqe_ctrl_seg *last)
{
    assert(c->db_owner == MLX5_SRM_DB_OWNER_USER);
    assert(tail != c->db_tail);
    uint64_t count = mlx5_srm_rr_seq(tail - c->db_tail), bytes = 0;
    uint64_t outstanding = c->route.posted_bytes - c->route.completed_bytes;
    for (uint64_t n = 0; n < count; n++) {
        char *wqe = (char *)m->sq_map + ((c->db_tail + n) & 63) * 64;
        bytes += be32toh(((struct mlx5_wqe_data_seg *)(wqe + 48))->byte_count);
    }
    if (c->route.inflight_limit_bytes &&
        outstanding + bytes > c->route.inflight_limit_bytes) {
        assert(!outstanding && count == 1);
        assert(bytes > c->route.inflight_limit_bytes);
    }
    last_index = ((char *)last - (char *)m->sq_map) / 64;
    assert(last_index == ((tail - 1) & 63));
    rings++;
}
"""

BODY = r"""
static struct mlx5_sq_ctrl_page ctrl, credit;
static struct mlx5_srm_mapping_bundle mapping;
static struct mlx5_wq wq = {64, 6};
static uint64_t tokens[64], sq[64 * 8];
static void init(uint64_t start, unsigned n, uint32_t size, uint32_t limit)
{
    memset(&ctrl, 0, sizeof(ctrl)); memset(&credit, 0, sizeof(credit));
    memset(sq, 0, sizeof(sq)); memset(tokens, 0, sizeof(tokens));
    memset(&srm_direct_db_stats, 0, sizeof(srm_direct_db_stats));
    ctrl.db_tail = start; ctrl.resv_idx = mlx5_srm_rr_seq(start + n);
    ctrl.route.user_db = 1; ctrl.route.inflight_limit_bytes = limit;
    credit.credit_limit = 1024; rings = hints = 0;
    mapping = (struct mlx5_srm_mapping_bundle) {&credit, sq, sq, sq, 64};
    for (unsigned i = 0; i < n; i++) {
        uint64_t post = mlx5_srm_rr_seq(start + i), index = post & 63;
        struct mlx5_wqe_ctrl_seg *w = (void *)&sq[index * 8];
        w->opmod_idx_opcode = htobe32(((uint32_t)(uint16_t)post << 8) | 8);
        w->qpn_ds = htobe32((77 << 8) | 4);
        ((struct mlx5_wqe_data_seg *)((char *)w + 48))->byte_count = htobe32(size);
        tokens[index] = (((post + 1) & MLX5_SRM_PUBLISH_SEQ_MASK) << 16) | 7;
    }
}
static void attempt(uint64_t published)
{
    srm_try_direct_user_db(&mapping, &ctrl, tokens, 64, &wq, 77,
                          published, false);
    assert(ctrl.db_owner == MLX5_SRM_DB_OWNER_FREE);
}
int main(void)
{
    /* Payload parser: XRC remote segments are NOT payload; inline flag is
     * NOT part of the length; zero-SGE WRs must not read stale slot data. */
    init(0, 1, 1234, 32768);
    assert(mlx5_srm_payload_bytes(sq) == 1234);
    ((struct mlx5_wqe_ctrl_seg *)sq)->opmod_idx_opcode = htobe32(0x10);
    assert(mlx5_srm_payload_bytes(sq) == 1234);
    ((struct mlx5_wqe_ctrl_seg *)sq)->qpn_ds = htobe32((77 << 8) | 3);
    assert(mlx5_srm_payload_bytes(sq) == 0);
    ((struct mlx5_wqe_ctrl_seg *)sq)->opmod_idx_opcode = htobe32(0x0a);
    ((struct mlx5_wqe_data_seg *)((char *)sq + 32))->byte_count = htobe32(99);
    assert(mlx5_srm_payload_bytes(sq) == 99);
    ((struct mlx5_wqe_ctrl_seg *)sq)->qpn_ds = htobe32((77 << 8) | 4);
    assert(mlx5_srm_payload_bytes(sq) == 99 + 1234);
    ((struct mlx5_wqe_ctrl_seg *)sq)->opmod_idx_opcode = htobe32(0x08);
    ((struct mlx5_wqe_data_seg *)((char *)sq + 48))->byte_count = htobe32(0x80000008);
    assert(mlx5_srm_payload_bytes(sq) == 8);
    ((struct mlx5_wqe_ctrl_seg *)sq)->opmod_idx_opcode = 0;
    assert(mlx5_srm_payload_bytes(sq) == 0);
    const uint64_t starts[] = {0, 65534, (1ULL<<48)-2,
#if MLX5_SRM_ENABLE_REROUTE
                              MLX5_SRM_REROUTE_MASK-1};
#else
                              UINT64_MAX-1};
#endif
    for (unsigned i = 0; i < sizeof(starts)/sizeof(starts[0]); i++) {
        uint64_t start = starts[i];
        init(start, 32, 2048, 32768);
        attempt(start);
        assert(rings == 1 && credit.issued_total == 16);
        assert(ctrl.db_tail == mlx5_srm_rr_seq(start + 16));
        assert(ctrl.route.posted_bytes == 32768);
        attempt(ctrl.db_tail); /* full cap: no extra claim or doorbell */
        assert(rings == 1 && credit.issued_total == 16);
        ctrl.route.completed_bytes = 4096; /* physical CQ releases 2 WQEs */
        credit.completed_total = 2;
        attempt(ctrl.db_tail);
        assert(rings == 2 && credit.issued_total == 18);
        assert(ctrl.db_tail == mlx5_srm_rr_seq(start + 18));
    }
    /* Stride helper cannot send a truncated prefix, nor leak worker credit. */
    init(0, 8, 8192, 32768); attempt(7);
    assert(!rings && !ctrl.db_tail && !credit.issued_total && hints == 1);
    attempt(3); /* own endpoint exactly fits */
    assert(rings == 1 && ctrl.db_tail == 4 && credit.issued_total == 4);
    /* Exact-cap WQE, oversized singleton, and explicitly disabled cap. */
    init(0, 1, 32768, 32768); attempt(0);
    assert(rings == 1 && ctrl.route.posted_bytes == 32768);
    init(0, 1, 32769, 32768); attempt(0);
    assert(rings == 1 && credit.issued_total == 1 && ctrl.db_tail == 1);
    assert(ctrl.route.posted_bytes == 32769);
    init(0, 8, 8192, 0); attempt(0);
    assert(rings == 1 && ctrl.route.posted_bytes ==
           (MLX5_SRM_ENABLE_REROUTE ? 65536U : 0U));
    /* Worker limit, missing token, DB gate and owner contention remain valid. */
    init(0, 8, 8192, 32768); credit.credit_limit = 2; attempt(0);
    assert(ctrl.db_tail == 2 && ctrl.route.posted_bytes == 16384);
    init(0, 8, 8192, 32768); tokens[2] = 0; attempt(0);
    assert(ctrl.db_tail == 2 && ctrl.route.posted_bytes == 16384);
    init(0, 8, 8192, 32768); ctrl.route.user_db = 0; attempt(0);
#if MLX5_SRM_ENABLE_REROUTE
    assert(!rings && !credit.issued_total);
#else
    assert(rings == 1 && credit.issued_total == 4); /* no route gate */
#endif
    init(0, 8, 8192, 32768); ctrl.db_owner = 2;
    srm_try_direct_user_db(&mapping, &ctrl, tokens, 64, &wq, 77, 0, false);
    assert(!rings && ctrl.db_owner == 2 && !credit.issued_total);
    init(0, 8, 8192, 32768);
    ctrl.route.posted_bytes = ctrl.route.completed_bytes = UINT64_MAX - 100;
    attempt(0);
    assert(ctrl.route.posted_bytes - ctrl.route.completed_bytes == 32768);
    /* Oversized WRs may not share a DB or bypass existing in-flight data. */
    const uint32_t sizes[] = {32769, 65536, 0x7fffffffU};
    for (unsigned k = 0; k < sizeof(starts)/sizeof(starts[0]); k++) {
        for (unsigned j = 0; j < sizeof(sizes)/sizeof(sizes[0]); j++) {
            uint64_t start = starts[k], initial = UINT64_MAX - 100;
            init(start, 3, sizes[j], 32768);
            ctrl.route.posted_bytes = ctrl.route.completed_bytes = initial;
            attempt(start);
            assert(rings == 1 && credit.issued_total == 1);
            assert(ctrl.db_tail == mlx5_srm_rr_seq(start + 1));
            assert(ctrl.route.posted_bytes - initial == sizes[j]);
            attempt(ctrl.db_tail);
            assert(rings == 1 && credit.issued_total == 1);
            ctrl.route.completed_bytes = ctrl.route.posted_bytes;
            credit.completed_total = 1;
            attempt(ctrl.db_tail);
            assert(rings == 2 && credit.issued_total == 2);
            assert(ctrl.db_tail == mlx5_srm_rr_seq(start + 2));
        }
    }
    init(0, 3, 8192, 32768);
    ((struct mlx5_wqe_data_seg *)((char *)sq + 64 + 48))->byte_count = htobe32(65536);
    attempt(0); /* stop before large WR, do not grow the batch budget */
    assert(rings == 1 && ctrl.db_tail == 1 && ctrl.route.posted_bytes == 8192);
    attempt(1); /* 24KiB available is not an empty physical QP */
    assert(rings == 1 && credit.issued_total == 1);
    ctrl.route.completed_bytes = 8192; credit.completed_total = 1;
    attempt(1);
    assert(rings == 2 && ctrl.db_tail == 2 && credit.issued_total == 2);
    assert(ctrl.route.posted_bytes == 8192 + 65536);
    attempt(2); /* the small successor must also wait for the oversized WR */
    assert(rings == 2 && credit.issued_total == 2);
    ctrl.route.completed_bytes = ctrl.route.posted_bytes; credit.completed_total = 2;
    attempt(2);
    assert(rings == 3 && ctrl.db_tail == 3);
    /* Existing helper/full-prefix and no-credit rules remain in force. */
    init(0, 4, 65536, 32768); attempt(3);
    assert(!rings && !credit.issued_total && !ctrl.db_tail && hints == 1);
    attempt(0);
    assert(rings == 1 && credit.issued_total == 1 && ctrl.db_tail == 1);
    init(0, 2, 65536, 32768); credit.credit_limit = 0; attempt(0);
    assert(!rings && !credit.issued_total && !ctrl.db_tail);
    init(0, 2, 65536, 32768); tokens[0] = 0; attempt(0);
    assert(!rings && !credit.issued_total && !ctrl.db_tail);
    init(0, 2, 65536, 0); attempt(0);
    assert(rings == 1 && credit.issued_total == 2 && ctrl.db_tail == 2);
    puts("PASS: production user DB: per-QP byte window, head/stride, exact/oversize,"
         " disabled cap, worker credit refund, holes, gates, 16/48/63/64-bit wrap");
}
"""

def main():
    qp = (ROOT / "providers/mlx5/qp.c").read_text()
    body = PRELUDE
    for name, result in (("srm_claim_direct_db_credit", "uint32_t"),
                         ("srm_release_direct_db_credit", "void"),
                         ("srm_try_direct_user_db", "void")):
        body += "\nstatic " + result + " " + function(qp, name) + "\n"
    # The real post path must no longer reject a WR merely for exceeding
    # the byte window; admission happens under db_owner in the DB helper.
    post = function(qp, "_mlx5_post_send")
    assert "mlx5_srm_rr_payload_allowed(" not in post
    assert "inflight_limit_bytes" not in post[:post.index("srm_reserve_wqe_blocking(")]
    assert "mlx5_srm_rr_byte_can_post(" in function(qp, "srm_try_direct_user_db")
    with tempfile.TemporaryDirectory(prefix="srm-byte-window-") as tmp:
        binary = str(Path(tmp) / "user-db")
        for reroute in (0, 1):
            subprocess.run(["cc", "-std=gnu11", "-O1", "-g",
                        "-fsanitize=address,undefined",
                        "-DMLX5_SRM_ENABLE_REROUTE=" + str(reroute),
                        "-I" + str(ROOT / "kernel-headers"),
                        "-x", "c", "-", "-o", binary],
                       input=body + BODY, text=True, check=True)
            subprocess.run([binary], check=True)

if __name__ == "__main__":
    main()
