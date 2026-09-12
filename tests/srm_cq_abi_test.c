/* Hardware-free ABI checks; compiled by test_srm_cq_abi.py. */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <rdma/mlx5-abi.h>

_Static_assert(offsetof(struct mlx5_ib_modify_qp, srm_cq_buf_addr) == 16,
               "legacy modify-QP input prefix changed");
_Static_assert(sizeof(struct mlx5_ib_modify_qp) == 32,
               "modify-QP input extension changed");
_Static_assert(sizeof(struct mlx5_srm_sw_cqe) == 32,
               "software completion record size changed");
_Static_assert(offsetof(struct mlx5_srm_sw_cq, producer) == 0,
               "software producer offset changed");
_Static_assert(offsetof(struct mlx5_srm_sw_cq, consumer) == 64,
               "software consumer must occupy a separate cacheline");
_Static_assert(offsetof(struct mlx5_srm_sw_cq, entries) == 128,
               "software completion header size changed");
_Static_assert(MLX5_IB_MODIFY_QP_SRM_CQ_MODE == (1U << 1),
               "input capability conflicts with existing OOO flag");
_Static_assert(MLX5_IB_MODIFY_QP_RESP_MASK_CQ_MODE == (1U << 6),
               "completion-mode response capability changed");
_Static_assert(MLX5_IB_MODIFY_QP_RESP_MASK_CQ_DISPATCH == (1U << 7),
               "completion-mode response value changed");
_Static_assert(MLX5_IB_MODIFY_QP_RESP_MASK_CQ_DIRECT == (1U << 8),
               "direct CQE response conflicts with existing modes");
_Static_assert(MLX5_IB_MODIFY_QP_SRM_CQ_DIRECT == (1U << 2),
               "direct CQE input capability conflicts");
_Static_assert(sizeof(struct mlx5_srm_direct_cqe_meta) == 24 &&
               MLX5_SRM_DIRECT_CQE_SIZE == 64,
               "direct metadata must precede native srqn_uidx");

int main(void)
{
    puts("PASS: Hollow CQ input prefix, capability bits and software ring ABI");
    return 0;
}
