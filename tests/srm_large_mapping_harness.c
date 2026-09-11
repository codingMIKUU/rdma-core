/* Called by test_srm_large_mapping.py; never linked into the provider. */
#include <assert.h>
#include <errno.h>
#include <linux/types.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#include "srm_mapping_types.inc"

/* Only the enclosing objects are projected.  Types with ABI-sensitive sizes
 * and every function under test are extracted unchanged from production. */
struct test_wq {
	void *qend;
	void *wrid;
	uint32_t wqe_cnt, wqe_shift, max_post, max_gs, qp_state_max_gs;
};
struct mlx5_context {
	pthread_mutex_t srm_mapping_mutex;
	struct mlx5_srm_mapping_bundle *srm_mapping_bundles;
	void *srm_ctrl_map;
	size_t srm_ctrl_map_len;
	struct { struct { int cmd_fd; } context; } ibv_ctx;
};
struct mlx5_qp {
	struct mlx5_srm_mapping_bundle *srm_mapping, *srm_large_mapping;
	void *sq_mmap_buf, *sq_start, *srm_large_sq_mmap_buf, *srm_large_sq_start;
	size_t sq_mmap_len, srm_large_sq_mmap_len;
	struct mlx5_sq_ctrl_page *sq_ctrl, *srm_large_sq_ctrl;
	uint64_t *sq_publish_token, *srm_large_sq_publish_token;
	uint32_t sq_publish_depth, srm_large_sq_publish_depth;
	uint32_t sq_ctrl_slot_idx, srm_large_sq_ctrl_slot_idx;
	uint32_t sq_metadata_cnt, srm_large_sq_metadata_cnt;
	uint32_t srm_large_msg_threshold;
	bool hollow_rc, sender_side, srm_fast_ready, srm_large_fast_ready;
	struct test_wq sq, srm_large_sq;
};

struct map_record { void *pointer; size_t length; off_t offset; bool live; };
static struct map_record maps[32];
static int mmap_calls, munmap_calls, fail_mmap_call, fail_bundle_alloc;
static int cases;

static void *test_mmap(void *address, size_t length, int protection,
		       int flags, int fd, off_t offset)
{
	int index = mmap_calls++;
	assert(address == NULL && length > 0);
	assert(protection == (PROT_READ | PROT_WRITE));
	assert(flags == MAP_SHARED && fd == 123);
	assert(index < 32);
	if (mmap_calls == fail_mmap_call) {
		errno = ENOSPC;
		return MAP_FAILED;
	}
	maps[index] = (struct map_record){ calloc(1, length), length, offset, true };
	assert(maps[index].pointer);
	return maps[index].pointer;
}

static int test_munmap(void *pointer, size_t length)
{
	for (int i = 0; i < mmap_calls; ++i) {
		if (maps[i].pointer == pointer && maps[i].live) {
			assert(length == maps[i].length);
			maps[i].live = false;
			free(pointer);
			++munmap_calls;
			/* Deliberately clobber errno: failure paths must preserve it. */
			errno = EIO;
			return 0;
		}
	}
	assert(!"unmap of invalid, double-freed, or untracked mapping");
	return -1;
}

static void *test_calloc(size_t count, size_t length)
{
	if (fail_bundle_alloc) {
		errno = ENOMEM;
		return NULL;
	}
	return calloc(count, length);
}

#define mmap test_mmap
#define munmap test_munmap
#define calloc test_calloc
#include "srm_mapping_functions.inc"
#undef calloc
#undef munmap
#undef mmap

static void reset(struct mlx5_context *ctx)
{
	for (int i = 0; i < mmap_calls; ++i)
		assert(!maps[i].live);
	for (int i = 0; i < 32; ++i)
		maps[i] = (struct map_record){0};
	mmap_calls = munmap_calls = fail_mmap_call = fail_bundle_alloc = 0;
	*ctx = (struct mlx5_context){0};
	assert(!pthread_mutex_init(&ctx->srm_mapping_mutex, NULL));
	ctx->ibv_ctx.context.cmd_fd = 123;
}

static struct mlx5_qp new_qp(void)
{
	return (struct mlx5_qp){
		.hollow_rc = true, .sender_side = true,
		.sq_metadata_cnt = 256, .srm_large_sq_metadata_cnt = 256,
		.sq = { .wrid = (void *)1, .wqe_cnt = 256 },
		.srm_large_sq = { .wrid = (void *)1, .wqe_cnt = 256 },
	};
}

static struct mlx5_ib_modify_qp_resp response(bool direct)
{
	return (struct mlx5_ib_modify_qp_resp){
		.response_length = sizeof(struct mlx5_ib_modify_qp_resp),
		.comp_mask = direct ? MLX5_IB_MODIFY_QP_RESP_MASK_LARGE_FARM_DB : 0,
		.sq_state_mmap_offset = 0x1000, .sq_state_mmap_len = 16384,
		.kernel_qpn = 101, .sq_state_slot_idx = 1,
		.kernel_sq_wqe_cnt = 256, .kernel_sq_wqe_shift = 6,
		.sq_mmap_offset = 0x2000, .sq_mmap_len = 16384,
		.publish_mmap_offset = 0x3000, .publish_mmap_len = 4096,
		.publish_depth = 256,
		.farm_uar_mmap_offset = 0x4000, .farm_uar_mmap_len = 4096,
		.farm_uar_reg_offset = 256,
		.farm_db_mmap_offset = 0x5000, .farm_db_mmap_len = 4096,
		.farm_db_offset = 32, .farm_bf_buf_size = 256,
		.farm_credit_slot_idx = 3, .farm_direct_db_batch = 32,
		.large_kernel_qpn = 201, .large_sq_state_slot_idx = 5,
		.large_kernel_sq_wqe_cnt = 256, .large_kernel_sq_wqe_shift = 6,
		.large_kernel_sq_max_post = 85, .large_kernel_sq_max_gs = 9,
		.large_kernel_sq_qp_state_max_gs = 8,
		.large_sq_mmap_offset = 0x6000, .large_sq_mmap_len = 16384,
		.large_publish_mmap_offset = 0x7000, .large_publish_mmap_len = 4096,
		.large_publish_depth = 256, .large_msg_threshold = 4096,
		.large_farm_uar_mmap_offset = 0x8000, .large_farm_uar_mmap_len = 4096,
		.large_farm_uar_reg_offset = 512,
		.large_farm_db_mmap_offset = 0x9000, .large_farm_db_mmap_len = 4096,
		.large_farm_db_offset = 64, .large_farm_bf_buf_size = 256,
		.large_farm_credit_slot_idx = 7, .large_farm_direct_db_batch = 64,
	};
}

static int live_maps(void)
{
	int count = 0;
	for (int i = 0; i < mmap_calls; ++i)
		count += maps[i].live;
	return count;
}

static void finish(struct mlx5_context *ctx)
{
	mlx5_srm_release_mappings(ctx);
	assert(!ctx->srm_mapping_bundles && !ctx->srm_ctrl_map);
	assert(!live_maps());
	assert(!pthread_mutex_destroy(&ctx->srm_mapping_mutex));
	++cases;
}

static void test_legacy_and_new(void)
{
	for (int direct = 0; direct <= 1; ++direct) {
		struct mlx5_context ctx;
		struct mlx5_qp qp = new_qp(), second = new_qp();
		struct mlx5_ib_modify_qp_resp resp = response(direct);
		struct mlx5_srm_mapping_bundle *small, *large;
		int calls;
		reset(&ctx);
		if (!direct) {
			resp.response_length = offsetof(struct mlx5_ib_modify_qp_resp,
							large_farm_uar_mmap_offset);
			/* No capability: never use even poisoned extension fields. */
			resp.large_farm_uar_reg_offset = UINT32_MAX;
			resp.large_farm_db_offset = UINT32_MAX;
		}
		assert(!mlx5_srm_acquire_mapping(&ctx, &qp, &resp));
		assert(qp.srm_fast_ready && mmap_calls == 5);
		assert(!mlx5_srm_acquire_large_mapping(&ctx, &qp, &resp));
		assert(mmap_calls == (direct ? 9 : 7));
		assert(qp.srm_large_fast_ready);
		small = qp.srm_mapping;
		large = qp.srm_large_mapping;
		assert(small != large && large->refs == 1);
		assert(qp.sq_ctrl != qp.srm_large_sq_ctrl);
		assert(qp.srm_large_sq_ctrl ==
		       &((struct mlx5_sq_ctrl_page *)ctx.srm_ctrl_map)[5]);
		assert(qp.srm_large_sq.qend == (char *)large->sq_map + 16384);
		assert(qp.srm_large_sq.max_post == 85 && qp.srm_large_sq.max_gs == 9);
		assert((uint64_t)maps[5].offset == resp.large_sq_mmap_offset);
		assert((uint64_t)maps[6].offset == resp.large_publish_mmap_offset);
		if (direct) {
			assert(large->farm_uar_reg == (char *)maps[7].pointer + 512);
			assert((void *)large->farm_db == (char *)maps[8].pointer + 64);
			assert((uint64_t)maps[7].offset == resp.large_farm_uar_mmap_offset);
			assert((uint64_t)maps[8].offset == resp.large_farm_db_mmap_offset);
			assert(large->farm_uar_map != small->farm_uar_map);
			assert(large->farm_db_map != small->farm_db_map);
			assert(large->farm_credit_ctrl ==
			       &((struct mlx5_sq_ctrl_page *)ctx.srm_ctrl_map)[7]);
			assert(large->farm_credit_ctrl != small->farm_credit_ctrl);
			assert(large->farm_direct_db_batch == 64);
		} else {
			assert(!large->farm_uar_map && !large->farm_db_map);
			assert(!large->farm_credit_ctrl);
		}
		calls = mmap_calls;
		assert(!mlx5_srm_acquire_large_mapping(&ctx, &second, &resp));
		assert(mmap_calls == calls && large->refs == 2);
		assert(second.srm_large_mapping == large);
		mlx5_srm_release_mapping(&ctx, &qp);
		assert(!qp.srm_mapping && !qp.srm_large_mapping);
		assert(!qp.srm_fast_ready && !qp.srm_large_fast_ready);
		assert(large->refs == 1 && live_maps() == (direct ? 5 : 3));
		mlx5_srm_release_mapping(&ctx, &second);
		assert(!ctx.srm_mapping_bundles && live_maps() == 1);
		finish(&ctx);
	}
}

static void reject(struct mlx5_ib_modify_qp_resp resp)
{
	struct mlx5_context ctx;
	struct mlx5_qp qp = new_qp();
	reset(&ctx);
	errno = 0;
	assert(mlx5_srm_acquire_large_mapping(&ctx, &qp, &resp) == EINVAL);
	assert(errno == EINVAL && mmap_calls == 0 && !qp.srm_large_mapping);
	finish(&ctx);
}

#define INVALID(field, value) do { \
	struct mlx5_ib_modify_qp_resp resp = response(true); \
	resp.field = (value); reject(resp); \
} while (0)

static void test_invalid_responses(void)
{
	INVALID(response_length, sizeof(struct mlx5_ib_modify_qp_resp) - 1);
	INVALID(large_kernel_qpn, 0);
	INVALID(large_sq_mmap_len, 16383);
	INVALID(large_publish_mmap_len, 1024);
	INVALID(large_publish_depth, 255);
	INVALID(large_kernel_sq_wqe_shift, 8 * sizeof(size_t));
	INVALID(large_kernel_sq_wqe_shift, 8 * sizeof(size_t) - 1);
	INVALID(large_sq_state_slot_idx, 32);
	INVALID(large_farm_uar_mmap_len, 0);
	INVALID(large_farm_db_mmap_len, 0);
	INVALID(large_farm_bf_buf_size, 0);
	INVALID(large_farm_bf_buf_size, UINT32_MAX);
	INVALID(large_farm_direct_db_batch, 0);
	INVALID(large_farm_uar_reg_offset, UINT32_MAX);
	INVALID(large_farm_uar_reg_offset, 4096 - 256);
	INVALID(large_farm_db_offset, UINT32_MAX);
	INVALID(large_farm_db_offset, 4096 - 7);
	INVALID(large_farm_credit_slot_idx, 32);
	INVALID(large_farm_credit_slot_idx, UINT32_MAX);
}

static void test_failure_cleanup(void)
{
	for (int stage = 1; stage <= 6; ++stage) {
		struct mlx5_context ctx;
		struct mlx5_qp qp = new_qp();
		struct mlx5_ib_modify_qp_resp resp = response(true);
		int expected = stage == 6 ? ENOMEM : ENOSPC;
		reset(&ctx);
		fail_mmap_call = stage == 6 ? 0 : stage;
		fail_bundle_alloc = stage == 6;
		assert(mlx5_srm_acquire_large_mapping(&ctx, &qp, &resp) == expected);
		assert(errno == expected && !qp.srm_large_mapping);
		assert(!ctx.srm_mapping_bundles);
		/* The successful shared control mapping is context-owned and retained
		 * for subsequent QPs; all QP-owned mappings must have been released. */
		assert(live_maps() == (stage == 1 ? 0 : 1));
		assert(munmap_calls == (stage <= 2 ? 0 : stage - 2));
		finish(&ctx);
	}
}

static void test_cache_mismatch(void)
{
	for (int change = 0; change < 5; ++change) {
		struct mlx5_context ctx;
		struct mlx5_qp qp = new_qp(), second = new_qp();
		struct mlx5_ib_modify_qp_resp resp = response(true);
		reset(&ctx);
		assert(!mlx5_srm_acquire_large_mapping(&ctx, &qp, &resp));
		switch (change) {
		case 0: resp.comp_mask = 0; break;
		case 1: resp.large_farm_uar_reg_offset += 8; break;
		case 2: resp.large_farm_db_offset += 8; break;
		case 3: resp.large_farm_credit_slot_idx += 1; break;
		case 4: resp.large_farm_direct_db_batch += 1; break;
		}
		assert(mlx5_srm_acquire_large_mapping(&ctx, &second, &resp) == EPROTO);
		assert(errno == EPROTO && mmap_calls == 5);
		assert(qp.srm_large_mapping->refs == 1 && !second.srm_large_mapping);
		mlx5_srm_release_mapping(&ctx, &qp);
		finish(&ctx);
	}
}

static void test_context_pool_mismatch(void)
{
	struct mlx5_context ctx;
	struct mlx5_qp qp = new_qp(), second = new_qp();
	struct mlx5_ib_modify_qp_resp resp = response(true);
	reset(&ctx);
	assert(!mlx5_srm_acquire_large_mapping(&ctx, &qp, &resp));
	/* A different KQP must not replace this context's existing control pool
	 * with one of a different size, nor damage the first mapping bundle. */
	resp.large_kernel_qpn++;
	resp.large_sq_state_slot_idx++;
	resp.sq_state_mmap_len *= 2;
	assert(mlx5_srm_acquire_large_mapping(&ctx, &second, &resp) == EPROTO);
	assert(errno == EPROTO && mmap_calls == 5 && live_maps() == 5);
	assert(ctx.srm_ctrl_map_len == 16384);
	assert(!second.srm_large_mapping && qp.srm_large_mapping->refs == 1);
	assert(!pthread_mutex_trylock(&ctx.srm_mapping_mutex));
	assert(!pthread_mutex_unlock(&ctx.srm_mapping_mutex));
	mlx5_srm_release_mapping(&ctx, &qp);
	finish(&ctx);
}

int main(void)
{
	_Static_assert(sizeof(struct mlx5_sq_ctrl_page) == 512,
		       "extract the actual control-page ABI, not a dummy-sized stub");
	_Static_assert(sizeof(struct mlx5_ib_modify_qp_resp) -
		offsetof(struct mlx5_ib_modify_qp_resp, large_farm_uar_mmap_offset) == 48,
		"large DB extension must remain append-only and 48 bytes");
	test_legacy_and_new();
	test_invalid_responses();
	test_failure_cleanup();
	test_cache_mismatch();
	test_context_pool_mismatch();
	printf("PASS: %d production mapper cases (legacy/new lanes, cache/refcounts, "
	       "bounds, mmap/calloc failures, release and errno preservation)\n", cases);
	return 0;
}
