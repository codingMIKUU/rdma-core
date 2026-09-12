/* Called by test_srm_cq_dispatch.py; never linked into the provider. */
#include <assert.h>
#include <errno.h>
#include <linux/types.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "srm_cq_types.inc"

struct test_device { size_t page_size; };
struct ibv_context { struct test_device *device; };
struct ibv_cq { int cqe; struct ibv_context *context; };
struct ibv_qp { uint32_t qp_num; struct ibv_cq *send_cq; };
struct ibv_sge { uint32_t length; };
struct ibv_send_wr {
	uint64_t wr_id;
	enum ibv_wr_opcode opcode;
	int num_sge;
	struct ibv_sge *sg_list;
};
struct mlx5_resource { int unused; };
struct mlx5_srq { int unused; };
struct mlx5_qp;
struct mlx5_cq {
	struct { struct ibv_cq cq; } verbs_cq;
	pthread_mutex_t lock;
	struct mlx5_qp *srm_pending_head, *srm_pending_tail, *srm_attached_head;
	struct mlx5_srm_sw_cq *srm_sw_cq;
	uint32_t srm_sw_cq_size, srm_sw_cq_depth;
	uint8_t srm_cq_mode_known, srm_cq_dispatch, srm_dispatch_error_reported;
	int stall_enable, stall_adaptive_enable, stall_next_poll, stall_cycles;
	uint64_t stall_last_count;
	struct ibv_wc hardware[4];
	unsigned int hardware_count, cons_index, db_cons_index;
};
struct mlx5_qp {
	struct { struct ibv_qp qp; } verbs_qp;
	struct ibv_qp *ibv_qp;
	struct { uint32_t wqe_cnt; } sq;
	struct mlx5_cq *srm_completion_cq;
	struct mlx5_qp *srm_completion_next, *srm_attached_next;
	struct mlx5_srm_completion_marker *srm_completion_ring;
	struct mlx5_srm_dispatch_status *srm_dispatch_ring;
	struct mlx5_sq_ctrl_page *sq_ctrl, *srm_large_sq_ctrl;
	uint64_t srm_completion_head, srm_completion_tail;
	uint64_t srm_dispatch_small_cursor, srm_dispatch_large_cursor;
	uint32_t srm_completion_capacity, usr_rc_cnt;
	uint32_t sq_ctrl_slot_idx, srm_large_sq_ctrl_slot_idx;
	uint8_t srm_completion_queued, srm_large_fast_ready, srm_cq_attached;
	uint8_t srm_cq_mode_known, srm_cq_mode_legacy, srm_cq_dispatch;
};

#define to_mcq(cq) ((struct mlx5_cq *)(cq))
#define to_mdev(dev) (dev)
#define mlx5_modify_qp mlx5_ib_modify_qp
#define mlx5_spin_lock(lock) assert(!pthread_mutex_lock(lock))
#define mlx5_spin_unlock(lock) assert(!pthread_mutex_unlock(lock))
#define unlikely(value) (value)
#define align(value, alignment) (((value) + (alignment) - 1) & ~((alignment) - 1))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
enum { CQ_OK = 0, CQ_EMPTY = -1, CQ_POLL_ERR = -2 };
static int mlx5_stall_cq_dec_step, mlx5_stall_cq_inc_step;
static int mlx5_stall_cq_poll_min, mlx5_stall_cq_poll_max;
static void mlx5_stall_cycles_poll_cq(uint64_t value) { (void)value; }
static void mlx5_stall_poll_cq(void) {}
static void mlx5_get_cycles(uint64_t *value) { *value = 0; }
static int ibv_dontfork_range(void *pointer, size_t bytes)
{ assert(pointer && bytes); return 0; }
static void update_cons_index(struct mlx5_cq *cq)
{ cq->db_cons_index = cq->cons_index; }
static int mlx5_poll_one(struct mlx5_cq *cq, struct mlx5_resource **rsc,
			struct mlx5_srq **srq, struct ibv_wc *wc, int version)
{
	(void)rsc; (void)srq; (void)version;
	if (cq->cons_index == cq->hardware_count)
		return CQ_EMPTY;
	*wc = cq->hardware[cq->cons_index++];
	return CQ_OK;
}

#include "srm_cq_functions.inc"

_Static_assert(sizeof(struct mlx5_srm_completion_marker) == 32,
	       "progress marker footprint changed");
_Static_assert(sizeof(struct mlx5_srm_dispatch_status) == 12,
	       "dispatch status footprint changed");
_Static_assert(sizeof(struct mlx5_srm_sw_cqe) == 32, "software CQE ABI");
_Static_assert(sizeof(struct mlx5_srm_sw_cq) == 128, "software CQ header ABI");

static struct test_device device = {4096};
static struct ibv_context context = {&device};

static void init_cq(struct mlx5_cq *cq)
{
	*cq = (struct mlx5_cq){0};
	cq->verbs_cq.cq = (struct ibv_cq){31, &context};
	assert(!pthread_mutex_init(&cq->lock, NULL));
}

static void init_qp(struct mlx5_qp *qp, struct mlx5_cq *cq,
		    struct mlx5_sq_ctrl_page ctrl[2], bool dispatch, unsigned int id)
{
	struct mlx5_modify_qp cmd = {0};
	*qp = (struct mlx5_qp){0};
	qp->verbs_qp.qp = (struct ibv_qp){1000 + id, &cq->verbs_cq.cq};
	qp->ibv_qp = &qp->verbs_qp.qp;
	qp->sq.wqe_cnt = 32;
	qp->sq_ctrl = &ctrl[0];
	qp->srm_large_sq_ctrl = &ctrl[1];
	qp->sq_ctrl_slot_idx = 11;
	qp->srm_large_sq_ctrl_slot_idx = 12;
	qp->srm_large_fast_ready = 1;
	qp->usr_rc_cnt = id;
	memset(ctrl, 0, sizeof(*ctrl) * 2);
	ctrl[0].completion_error_idx = ctrl[1].completion_error_idx = UINT64_MAX;
	assert(!mlx5_srm_record_cq_mode(qp,
		MLX5_IB_MODIFY_QP_RESP_MASK_CQ_MODE |
		(dispatch ? MLX5_IB_MODIFY_QP_RESP_MASK_CQ_DISPATCH : 0)));
	assert(!mlx5_srm_prepare_completion_cq(qp, &cmd));
	assert(!!cmd.srm_cq_buf_addr == dispatch);
	assert(!!cq->srm_sw_cq == dispatch);
}

static void queue(struct mlx5_qp *qp, int lane, uint64_t post, uint64_t wrid)
{
	struct ibv_sge sge = {4096};
	struct ibv_send_wr wr = {wrid, IBV_WR_RDMA_READ, 1, &sge};
	assert(!mlx5_srm_ensure_completion_space(qp));
	mlx5_srm_queue_completion(qp, lane ? qp->srm_large_sq_ctrl : qp->sq_ctrl,
				  post, wrid, &wr);
}

static void publish(struct mlx5_cq *cq, unsigned int usr, int lane,
		    uint64_t post, enum ibv_wc_status status, unsigned int vendor)
{
	struct mlx5_srm_sw_cq *ring = cq->srm_sw_cq;
	uint64_t producer = ring->producer;
	assert(producer - ring->consumer < cq->srm_sw_cq_depth);
	ring->entries[producer & (cq->srm_sw_cq_depth - 1)] =
		(struct mlx5_srm_sw_cqe){post, lane ? 12 : 11, usr, status, vendor, 0};
	__atomic_store_n(&ring->producer, producer + 1, __ATOMIC_RELEASE);
}

static void cleanup_qp(struct mlx5_qp *qp)
{
	mlx5_srm_remove_pending_completion(qp);
	mlx5_srm_detach_completion_cq(qp);
	free(qp->srm_completion_ring);
	free(qp->srm_dispatch_ring);
}

static void cleanup_cq(struct mlx5_cq *cq)
{
	assert(!cq->srm_attached_head && !cq->srm_pending_head);
	if (cq->srm_sw_cq)
		assert(!munmap(cq->srm_sw_cq, cq->srm_sw_cq_size));
	assert(!pthread_mutex_destroy(&cq->lock));
}

static void test_progress(void)
{
	struct mlx5_cq cq;
	struct mlx5_qp qp;
	struct mlx5_sq_ctrl_page ctrl[2];
	struct ibv_wc wc[2];
	init_cq(&cq);
	init_qp(&qp, &cq, ctrl, false, 1);
	queue(&qp, 0, 7, 101);
	assert(!qp.srm_dispatch_ring && !cq.srm_sw_cq);
	assert(!poll_cq(&cq.verbs_cq.cq, 2, wc, 1));
	ctrl[0].cons_idx = 8;
	assert(poll_cq(&cq.verbs_cq.cq, 2, wc, 1) == 1);
	assert(wc[0].wr_id == 101 && wc[0].status == IBV_WC_SUCCESS);
	queue(&qp, 1, UINT64_MAX, 102);
	ctrl[1].cons_idx = 0;
	ctrl[1].completion_error_idx = 0;
	ctrl[1].completion_error_status = IBV_WC_REM_ACCESS_ERR;
	ctrl[1].completion_error_vendor = 77;
	assert(poll_cq(&cq.verbs_cq.cq, 2, wc, 0) == 1);
	assert(wc[0].status == IBV_WC_REM_ACCESS_ERR && wc[0].vendor_err == 77);
	cleanup_qp(&qp);
	cleanup_cq(&cq);
}

static void test_dispatch_order_and_hardware(void)
{
	struct mlx5_cq cq;
	struct mlx5_qp qp;
	struct mlx5_sq_ctrl_page ctrl[2];
	struct ibv_wc wc[4];
	init_cq(&cq);
	init_qp(&qp, &cq, ctrl, true, 2);
	queue(&qp, 0, 7, 201);
	queue(&qp, 1, 99, 202);
	ctrl[0].cons_idx = ctrl[1].cons_idx = 10000;
	publish(&cq, 2, 1, 99, IBV_WC_REM_ACCESS_ERR, 42);
	cq.hardware[0] = (struct ibv_wc){.wr_id = 999, .opcode = IBV_WC_RECV,
		.status = IBV_WC_SUCCESS, .qp_num = 123, .byte_len = 64};
	cq.hardware_count = 1;
	assert(poll_cq(&cq.verbs_cq.cq, 4, wc, 0) == 1);
	assert(!memcmp(&wc[0], &cq.hardware[0], sizeof(wc[0])));
	assert(cq.db_cons_index == 1 && qp.srm_completion_tail == 0);
	publish(&cq, 2, 0, 7, IBV_WC_SUCCESS, 0);
	assert(poll_cq(&cq.verbs_cq.cq, 4, wc, 1) == 2);
	assert(wc[0].wr_id == 201 && wc[0].byte_len == 4096);
	assert(wc[1].wr_id == 202 && wc[1].status == IBV_WC_REM_ACCESS_ERR);
	assert(wc[1].vendor_err == 42 && wc[1].qp_num == 1002);
	cleanup_qp(&qp);
	cleanup_cq(&cq);
}

static void test_growth_and_reused_slots(void)
{
	struct mlx5_cq cq;
	struct mlx5_qp qp;
	struct mlx5_sq_ctrl_page ctrl[2];
	struct ibv_wc wc[16];
	init_cq(&cq);
	init_qp(&qp, &cq, ctrl, true, 3);
	for (unsigned int i = 0; i < 8; i++)
		queue(&qp, 0, i * 32, 300 + i);
	for (unsigned int i = 0; i < 4; i++)
		publish(&cq, 3, 0, i * 32, IBV_WC_SUCCESS, 0);
	assert(!mlx5_srm_drain_dispatch(&cq));
	assert(qp.srm_dispatch_small_cursor == 4);
	queue(&qp, 0, 256, 308);
	assert(qp.srm_completion_capacity == 16);
	assert(qp.srm_dispatch_small_cursor == 4);
	for (unsigned int i = 4; i < 9; i++)
		publish(&cq, 3, 0, i * 32, IBV_WC_SUCCESS, 0);
	assert(poll_cq(&cq.verbs_cq.cq, 16, wc, 1) == 9);
	for (unsigned int i = 0; i < 9; i++)
		assert(wc[i].wr_id == 300 + i);
	assert(qp.srm_dispatch_small_cursor == 9);
	cleanup_qp(&qp);
	cleanup_cq(&cq);
}

static void test_wrap_retirement_and_corruption(void)
{
	struct mlx5_cq cq;
	struct mlx5_qp qp;
	struct mlx5_sq_ctrl_page ctrl[2];
	struct ibv_wc wc[4];
	init_cq(&cq);
	init_qp(&qp, &cq, ctrl, true, 4);
	assert(!mlx5_srm_ensure_completion_space(&qp));
	qp.srm_completion_head = qp.srm_completion_tail = UINT64_MAX - 1;
	qp.srm_dispatch_small_cursor = qp.srm_dispatch_large_cursor = UINT64_MAX - 1;
	cq.srm_sw_cq->producer = cq.srm_sw_cq->consumer = UINT64_MAX - 1;
	for (unsigned int i = 0; i < 3; i++) {
		queue(&qp, 0, UINT64_MAX - 1 + i, 400 + i);
		publish(&cq, 4, 0, UINT64_MAX - 1 + i,
			i ? IBV_WC_WR_FLUSH_ERR : IBV_WC_LOC_PROT_ERR, 9);
	}
	assert(poll_cq(&cq.verbs_cq.cq, 4, wc, 0) == 3);
	assert(wc[0].wr_id == 400 && wc[2].wr_id == 402);
	assert(wc[2].status == IBV_WC_WR_FLUSH_ERR);
	assert(cq.srm_sw_cq->consumer == 1);
	publish(&cq, 4, 0, 5, IBV_WC_LOC_PROT_ERR, 8);
	assert(!poll_cq(&cq.verbs_cq.cq, 4, wc, 1));
	assert(cq.srm_dispatch_error_reported);
	cleanup_qp(&qp);
	cq.srm_dispatch_error_reported = 0;
	publish(&cq, 4, 0, 6, IBV_WC_LOC_PROT_ERR, 8);
	assert(!poll_cq(&cq.verbs_cq.cq, 4, wc, 1));
	assert(!cq.srm_dispatch_error_reported);
	cq.srm_sw_cq->producer += cq.srm_sw_cq_depth + 1;
	assert(poll_cq(&cq.verbs_cq.cq, 4, wc, 1) == CQ_POLL_ERR);
	cleanup_cq(&cq);
}

static void test_modes(void)
{
	struct mlx5_qp qp = {0};
	assert(mlx5_srm_record_cq_mode(&qp,
		MLX5_IB_MODIFY_QP_RESP_MASK_CQ_DISPATCH) == EPROTO);
	assert(!qp.srm_cq_mode_known);
	assert(!mlx5_srm_record_cq_mode(&qp, 0));
	assert(qp.srm_cq_mode_known && qp.srm_cq_mode_legacy && !qp.srm_cq_dispatch);
	assert(mlx5_srm_record_cq_mode(&qp,
		MLX5_IB_MODIFY_QP_RESP_MASK_CQ_MODE) == EPROTO);
}

int main(void)
{
	test_progress();
	test_dispatch_order_and_hardware();
	test_growth_and_reused_slots();
	test_wrap_retirement_and_corruption();
	test_modes();
	puts("Hollow CQ dispatch: 5 hardware-free production-function cases passed");
	return 0;
}
