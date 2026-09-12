/* CPU-only regression tests of the production static timing helpers. */
#include <arpa/inet.h>
#include "../providers/mlx5/qp.c"
#include <assert.h>
#include <stddef.h>

_Static_assert(sizeof(struct mlx5_srm_timing_slot) == 16, "slot ABI");
_Static_assert(offsetof(struct mlx5_ib_create_qp, srm_timing_addr) == 56, "ABI prefix");
_Static_assert(sizeof(struct mlx5_ib_create_qp) == 72, "request ABI");

int main(void)
{
    struct mlx5_wq wq = {0};
    struct mlx5_srm_cqe_timing slot = {0};
    wq.srm_cqe_timing = &slot;

    /* Exercise production accumulation including unsigned sum wrap. */
    mlx5_srm_timing_complete(100 + 110, 2, false, 200);
    assert(srm_wqe_timing_stats.cqe_wqes == 2);
    assert(srm_wqe_timing_stats.post_to_cqe_cycles == 190);
    mlx5_srm_timing_complete(UINT64_MAX - 9, 1, false, 20);
    assert(srm_wqe_timing_stats.post_to_cqe_cycles == 220);
    mlx5_srm_timing_complete(400, 1, false, 200);
    assert(srm_wqe_timing_stats.invalid_timestamps == 1);
    mlx5_srm_timing_complete(100, 1, true, 200);
    assert(srm_wqe_timing_stats.errored_wqes == 1);

    memset(&srm_wqe_timing_stats, 0, sizeof(srm_wqe_timing_stats));
    mlx5_srm_timing_publish(&wq, 0, 10, false);
    mlx5_srm_timing_publish(&wq, 0, 20, true);
    assert(slot.wqes == 2 && slot.post_tsc_sum == 30);
    mlx5_srm_timing_complete_wq(&wq, 0, false);
    assert(srm_wqe_timing_stats.cqe_wqes == 2 && !slot.pending_cqes);

    /* Two signaled WRs reuse one software slot before either CQE. Neither
     * may be measured using the other's timestamp; recover after drain. */
    mlx5_srm_timing_publish(&wq, 0, 30, true);
    mlx5_srm_timing_publish(&wq, 0, 40, true);
    assert(slot.poisoned && slot.pending_cqes == 2);
    mlx5_srm_timing_complete_wq(&wq, 0, false);
    mlx5_srm_timing_complete_wq(&wq, 0, false);
    assert(srm_wqe_timing_stats.cqe_wqes == 2);
    assert(srm_wqe_timing_stats.missing_timestamps == 2);
    mlx5_srm_timing_publish(&wq, 0, 50, true);
    mlx5_srm_timing_complete_wq(&wq, 0, false);
    assert(srm_wqe_timing_stats.cqe_wqes == 3);
    assert(!slot.pending_cqes && !slot.poisoned);
    puts("SRM timing ABI, accumulation, wrap, invalid/error and reuse tests PASS");
    return 0;
}
