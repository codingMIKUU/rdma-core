#!/bin/bash

echo "=== Running Simple QP Creation Test ==="
echo "This will create a QP and show BF allocation type via driver debug output"
echo

# 设置环境变量以启用更多调试信息
export MLX5_DEBUG_BF=1
export MLX5_DEBUG_MASK=0xFFFF  # 如果支持的话

echo "Environment variables set:"
echo "  MLX5_DEBUG_BF=$MLX5_DEBUG_BF"
echo "  MLX5_DEBUG_MASK=$MLX5_DEBUG_MASK"
echo

# 运行程序
echo "Running QP creation test..."
./simple_qp_create

echo
echo "=== Check kernel logs for BF type information ==="
echo "Recent MLX5 driver messages:"
dmesg | grep -i mlx5 | tail -10

echo
echo "=== Alternative: Check with journalctl ==="
echo "journalctl -k -f | grep -i mlx5  (in another terminal)"
