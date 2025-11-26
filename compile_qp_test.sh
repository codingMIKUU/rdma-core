#!/bin/bash

echo "=== Compiling Simple QP Creation Test ==="
echo "Using current rdma-core workspace libraries"
echo

# 编译命令 - 使用当前工作区的库
gcc -I./build/include -I./kernel-headers -o simple_qp_create simple_qp_create.c \
    -L./build/lib -Wl,-rpath=./build/lib -libverbs -lmlx5

if [ $? -eq 0 ]; then
    echo "Compilation successful!"
    echo
    
    # 验证链接的库
    echo "Linked libraries:"
    ldd simple_qp_create | grep -E "(libibverbs|libmlx5)"
    echo
    
    echo "You can now run:"
    echo "  ./run_qp_test.sh"
    echo "or"
    echo "  export MLX5_DEBUG_BF=1 && ./simple_qp_create"
else
    echo "Compilation failed!"
    exit 1
fi
