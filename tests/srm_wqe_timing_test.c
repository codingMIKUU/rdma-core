/* CPU-only tests for the actual provider timing helpers; no RDMA device needed.
 * Compile with function sections and --gc-sections to discard unused verbs. */
#include <arpa/inet.h>
#define MLX5_SRM_ENABLE_WQE_TIMING 1
#define MLX5_SRM_ENABLE_DIRECT_USER_DB 1
#include "../providers/mlx5/qp.c"

int main(void)
{
	const uint32_t depth = 4;
	uint64_t *publish = calloc(1, MLX5_SRM_TIMING_MAP_BYTES(depth));
	struct mlx5_srm_wqe_timestamp *entries;
	struct mlx5_srm_db_timing_batch snapshot;
	uint64_t first = UINT64_MAX - 1;
	uint64_t anchor = mlx5_srm_timing_rdtsc();
	uint64_t expected_sum = 0;
	uint64_t done;
	uint32_t i, d;

	assert(publish);
	entries = mlx5_srm_timestamp_array(publish, depth);
	/* A DB batch crossing the u64 reservation sequence wrap. */
	for (i = 0; i < depth; i++) {
		uint64_t slot = first + i;
		entries[slot & (depth - 1)].post_tsc = anchor - 1000000 - i;
		entries[slot & (depth - 1)].sequence = slot + 1;
		expected_sum += anchor - 1000000 - i;
	}
	snapshot = mlx5_srm_timing_db_snapshot(publish, depth, first, depth);
	assert(snapshot.valid == depth && !snapshot.missing && !snapshot.invalid);
	assert(snapshot.post_tsc_sum == expected_sum);

	/* Simulate CQ-driven slot reuse after DB. Aggregation must consume only
	 * the captured values, even if every shared timestamp has been replaced. */
	memset(entries, 0xa5, depth * sizeof(*entries));
	done = mlx5_srm_timing_rdtsc();
	mlx5_srm_timing_db_complete(&snapshot, done);
	assert(srm_db_timing_stats.db_calls == 1);
	assert(srm_db_timing_stats.db_wqes == depth);
	assert(srm_db_timing_stats.post_to_db_cycles == done * depth - expected_sum);

	/* Missing generation, missing timestamp, future TSC, and one valid WR. */
	for (i = 0; i < depth; i++) {
		entries[i].post_tsc = anchor;
		entries[i].sequence = i + 1;
	}
	entries[0].sequence = 99;
	entries[1].post_tsc = 0;
	entries[2].post_tsc = mlx5_srm_timing_rdtsc() + 1000000000ULL;
	snapshot = mlx5_srm_timing_db_snapshot(publish, depth, 0, depth);
	assert(snapshot.valid == 1 && snapshot.missing == 2 && snapshot.invalid == 1);

	/* Failed/flushed markers do not become successful completion latencies. */
	mlx5_srm_timing_complete(expected_sum, depth, IBV_WC_WR_FLUSH_ERR);
	assert(srm_wqe_timing_stats.cqe_wqes == 0);
	assert(srm_wqe_timing_stats.error_wqes == depth);
	mlx5_srm_timing_complete(expected_sum, depth, IBV_WC_SUCCESS);
	assert(srm_wqe_timing_stats.cqe_wqes == depth);
	assert(srm_wqe_timing_stats.post_to_cqe_cycles >= anchor * depth - expected_sum);

	/* Both timestamp-sum overflow and a TSC wrap preserve modular deltas. */
	{
		uint64_t starts[] = {UINT64_MAX - 50, UINT64_MAX - 20, 5};
		uint64_t sum = 0, individual = 0;
		for (i = 0; i < 3; i++) {
			sum += starts[i];
			individual += 100 - starts[i];
		}
		assert(100ULL * 3 - sum == individual);
	}
	for (d = 1; d <= 65536; d *= 2) {
		assert(!(MLX5_SRM_TIMING_OFFSET(d) & 15));
		for (i = 0; i < d; i++) {
			size_t off = MLX5_SRM_TIMING_OFFSET(d) + i * sizeof(*entries);
			assert(off % 4096 + sizeof(*entries) <= 4096);
			assert(off + sizeof(*entries) <= MLX5_SRM_TIMING_MAP_BYTES(d));
		}
	}
	free(publish);
	puts("srm_wqe_timing: wrap, snapshot/reuse, invalid/error samples and mmap bounds PASS");
	return 0;
}
