# Simple QP Creation Test for BF Type Analysis

这个测试程序用于创建基本的RDMA QP，并通过驱动调试输出观察BF（BlueFlame）分配类型。

## 文件说明

- `simple_qp_create.c` - 主要的QP创建测试程序
- `simple_qp_create` - 编译后的可执行文件
- `run_qp_test.sh` - 运行脚本，包含环境变量设置和日志检查
- `compile_qp_test.sh` - 编译脚本，确保使用当前工作区库
- `QP_TEST_README.md` - 本说明文档

## 使用方法

### 1. 编译程序
```bash
./compile_qp_test.sh
```

或者手动编译：
```bash
gcc -I./build/include -I./kernel-headers -o simple_qp_create simple_qp_create.c \
    -L./build/lib -Wl,-rpath=./build/lib -libverbs -lmlx5
```

### 2. 运行测试
```bash
./run_qp_test.sh
```

或者直接运行：
```bash
export MLX5_DEBUG_BF=1
./simple_qp_create
```

### 3. 查看驱动调试输出
```bash
# 检查内核日志
dmesg | grep -i mlx5 | tail -20

# 或者使用journalctl
journalctl -k | grep -i mlx5 | tail -20
```

## 预期输出

### 程序输出示例：
```
=== Simple QP Creation Test ===
Found 2 RDMA devices
  0: mlx5_0
  1: mlx5_1
Using device: mlx5_0
Device context opened successfully
Protection domain allocated
Completion queue created with 128 entries
Creating QP with type RC...
QP created successfully!
QP number: 12345
QP handle: 67890

=== Check driver debug output for BF type ===
Run: dmesg | grep -i mlx5 | tail -20
Or: journalctl -k | grep -i mlx5 | tail -20
```

### 驱动调试输出示例：
```
mlx5_core: QP 12345 allocated dynamic BF (page_id: 5)
mlx5_core: QP 12345 BF register at offset 0x1000
```

或

```
mlx5_core: QP 12345 using static BF (index: 3)
mlx5_core: QP 12345 BF register at 0x7f8b8c0d5000
```

## BF类型判断

- **动态BF**: 驱动输出包含 "dynamic BF" 或 "page_id"
- **静态BF**: 驱动输出包含 "static BF" 或 "index"

## 故障排除

1. **编译失败**: 确保rdma-core已正确构建，头文件在build/include中
2. **运行失败**: 检查RDMA设备是否可用，权限是否正确
3. **无调试输出**: 确认驱动中已添加调试语句，或尝试设置更多环境变量

## 清理

运行完成后，程序会自动清理所有资源。可以通过Ctrl+C中断程序。
