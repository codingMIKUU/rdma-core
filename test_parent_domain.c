#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <infiniband/verbs.h>
#include <infiniband/mlx5dv.h>

#define MAX_WR 128
#define MAX_SGE 1

int main(int argc, char *argv[]) {
    struct ibv_device **dev_list;
    struct ibv_device *dev;
    struct ibv_context *ctx;
    struct ibv_pd *pd;
    struct ibv_cq *cq;
    struct ibv_qp *qp;
    struct ibv_qp_init_attr_ex qp_init_attr = {};
    struct mlx5dv_qp_init_attr mlx5_qp_attr = {};
    int num_devices;
    int ret;

    printf("=== Testing Parent Domain BF Usage ===\n");

    // 获取设备列表
    dev_list = ibv_get_device_list(&num_devices);
    if (!dev_list) {
        perror("ibv_get_device_list failed");
        return 1;
    }

    // 选择MLX5设备
    dev = NULL;
    for (int i = 0; i < num_devices; i++) {
        if (strstr(dev_list[i]->name, "mlx5")) {
            dev = dev_list[i];
            break;
        }
    }

    if (!dev) {
        printf("No MLX5 device found\n");
        ibv_free_device_list(dev_list);
        return 1;
    }

    ctx = ibv_open_device(dev);
    if (!ctx) {
        perror("ibv_open_device failed");
        ibv_free_device_list(dev_list);
        return 1;
    }

    // 创建parent domain
    struct mlx5dv_pd pd_attr = {};
    pd_attr.comp_mask = 0;  // 不设置任何特殊属性

    pd = mlx5dv_alloc_pd(ctx, &pd_attr);
    if (!pd) {
        perror("mlx5dv_alloc_pd failed");
        ibv_close_device(ctx);
        ibv_free_device_list(dev_list);
        return 1;
    }

    printf("Parent domain allocated\n");

    // 创建CQ
    cq = ibv_create_cq(ctx, MAX_WR, NULL, NULL, 0);
    if (!cq) {
        perror("ibv_create_cq failed");
        ibv_dealloc_pd(pd);
        ibv_close_device(ctx);
        ibv_free_device_list(dev_list);
        return 1;
    }

    // 初始化QP属性 - 使用extended属性
    qp_init_attr.qp_type = IBV_QPT_RC;
    qp_init_attr.send_cq = cq;
    qp_init_attr.recv_cq = cq;
    qp_init_attr.cap.max_send_wr = MAX_WR;
    qp_init_attr.cap.max_recv_wr = MAX_WR;
    qp_init_attr.cap.max_send_sge = MAX_SGE;
    qp_init_attr.cap.max_recv_sge = MAX_SGE;
    qp_init_attr.comp_mask = IBV_QP_INIT_ATTR_PD;

    // 设置mlx5特定的属性
    mlx5_qp_attr.comp_mask = 0;  // 不设置特殊属性

    printf("Creating QP with parent domain...\n");

    // 创建QP - 这应该会检查parent domain的BF
    qp = mlx5dv_create_qp(ctx, &qp_init_attr, &mlx5_qp_attr);
    if (!qp) {
        perror("mlx5dv_create_qp failed");
        ibv_destroy_cq(cq);
        ibv_dealloc_pd(pd);
        ibv_close_device(ctx);
        ibv_free_device_list(dev_list);
        return 1;
    }

    printf("QP created successfully!\n");
    printf("QP number: %d\n", qp->qp_num);

    // 等待一会儿
    sleep(1);

    // 清理
    ibv_destroy_qp(qp);
    ibv_destroy_cq(cq);
    ibv_dealloc_pd(pd);
    ibv_close_device(ctx);
    ibv_free_device_list(dev_list);

    printf("=== Test completed ===\n");
    return 0;
}
