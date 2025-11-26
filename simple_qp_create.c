#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <infiniband/verbs.h>

#define MAX_WR 128
#define MAX_SGE 1

int main(int argc, char *argv[]) {
    struct ibv_device **dev_list;
    struct ibv_device *dev;
    struct ibv_context *ctx;
    struct ibv_pd *pd;
    struct ibv_cq *cq;
    struct ibv_qp *qp;
    struct ibv_qp_init_attr qp_init_attr;
    int num_devices;
    int ret;

    printf("=== Simple QP Creation Test ===\n");

    // 1. 获取设备列表
    dev_list = ibv_get_device_list(&num_devices);
    if (!dev_list) {
        perror("ibv_get_device_list failed");
        return 1;
    }

    printf("Found %d RDMA devices\n", num_devices);
    for (int i = 0; i < num_devices; i++) {
        printf("  %d: %s\n", i, dev_list[i]->name);
    }

    // 2. 选择第一个MLX5设备
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

    printf("Using device: %s\n", dev->name);

    // 3. 打开设备上下文
    ctx = ibv_open_device(dev);
    if (!ctx) {
        perror("ibv_open_device failed");
        ibv_free_device_list(dev_list);
        return 1;
    }

    printf("Device context opened successfully\n");

    // 4. 分配保护域
    pd = ibv_alloc_pd(ctx);
    if (!pd) {
        perror("ibv_alloc_pd failed");
        ibv_close_device(ctx);
        ibv_free_device_list(dev_list);
        return 1;
    }

    printf("Protection domain allocated\n");

    // 5. 创建完成队列
    cq = ibv_create_cq(ctx, MAX_WR, NULL, NULL, 0);
    if (!cq) {
        perror("ibv_create_cq failed");
        ibv_dealloc_pd(pd);
        ibv_close_device(ctx);
        ibv_free_device_list(dev_list);
        return 1;
    }

    printf("Completion queue created with %d entries\n", MAX_WR);

    // 6. 初始化QP属性
    memset(&qp_init_attr, 0, sizeof(qp_init_attr));
    qp_init_attr.qp_type = IBV_QPT_RC;  // Reliable Connected
    qp_init_attr.send_cq = cq;
    qp_init_attr.recv_cq = cq;
    qp_init_attr.cap.max_send_wr = MAX_WR;
    qp_init_attr.cap.max_recv_wr = MAX_WR;
    qp_init_attr.cap.max_send_sge = MAX_SGE;
    qp_init_attr.cap.max_recv_sge = MAX_SGE;

    printf("Creating QP with type RC...\n");

    // 7. 创建QP - 这里会触发BF分配
    qp = ibv_create_qp(pd, &qp_init_attr);
    if (!qp) {
        perror("ibv_create_qp failed");
        ibv_destroy_cq(cq);
        ibv_dealloc_pd(pd);
        ibv_close_device(ctx);
        ibv_free_device_list(dev_list);
        return 1;
    }

    printf("QP created successfully!\n");
    printf("QP number: %d\n", qp->qp_num);
    printf("QP handle: %d\n", qp->handle);

    // 8. 检查驱动调试输出
    printf("\n=== Check driver debug output for BF type ===\n");
    printf("Run: dmesg | grep -i mlx5 | tail -20\n");
    printf("Or: journalctl -k | grep -i mlx5 | tail -20\n");

    // 等待一会儿让用户查看输出
    sleep(2);

    // 9. 清理资源
    printf("\nCleaning up...\n");

    ret = ibv_destroy_qp(qp);
    if (ret) {
        perror("ibv_destroy_qp failed");
    } else {
        printf("QP destroyed\n");
    }

    ibv_destroy_cq(cq);
    printf("CQ destroyed\n");

    ibv_dealloc_pd(pd);
    printf("PD deallocated\n");

    ibv_close_device(ctx);
    printf("Device context closed\n");

    ibv_free_device_list(dev_list);
    printf("Device list freed\n");

    printf("=== Test completed ===\n");
    return 0;
}
