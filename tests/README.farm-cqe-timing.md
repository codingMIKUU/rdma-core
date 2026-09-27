# FARM：CQE 自带时间戳及内核到用户延迟

## 修复的问题

旧版 timing ABI=2 把时间戳放在物理 SQ 槽位对应的共享数组中。内核发布
软件 CQE 后即可回收 SQ，但用户可能稍后才 poll CQ。新请求复用 SQ 时覆盖
旧时间戳，导致大量 sequence mismatch，且总延迟与分段延迟使用不同样本。

ABI=3 改为将两枚软件 TSC、完整的 32 位完成序号（用 u64 存放）和 magic
放在软件 Send CQE 前 32 字节。内核 XRC initiator 不启用 scatter-to-CQE，
该区域不含请求完成需要的业务字段；硬件 CQE 本身不修改。CQE 后 32 字节
的 uidx、byte_cnt、硬件 timestamp、错误 syndrome、QPN、counter、owner
均保留。普通 RC 和 SRQ 接收 CQE 不参与该统计。

SQ 后续复用不再影响旧 CQE；CQ clean/resize 复制 CQE 时也携带时间戳。
不改变 CQE 长度，不新增队列、锁或 syscall，不再分配 SQ 时间戳共享数组。
防护检查遇到意外 inline READ/atomic 数据或非请求 CQE 时警告并跳过打点，
不覆盖这些数据；本次不扩展原 FARM 对 scatter-to-CQE 的支持。

## 开关与部署

两个 FARM 仓库均沿用 `srm-farm` 分支，不需要修改 QPSwitch、SRM 或 General。
沿用以下两个文件的原有宏，当前工作区两边均为 `1`：

- `farm-rdma-core/providers/mlx5/mlx5.h`
- `farm-rdma-kerndriver/drivers/infiniband/hw/mlx5/scheduler.h`

```c
#define MLX5_SRM_ENABLE_WQE_TIMING 1
```

**必须同时重编用户驱动和内核驱动，并在停止 RDMA 应用后重载内核模块。**
本次公布 timing ABI=3，控制槽仍是 128B，`cons_idx` 仍在偏移 64。
启用统计的新用户驱动拒绝 ABI=2 或统计关闭的内核，避免按错误布局解释
时间戳。更新的是发送端 FARM 的用户库和内核模块两者。
不要把旧的统计用户驱动与新的统计内核混用；旧用户驱动没有 ABI 检查能力。

选好宏以后，使用现有构建方式，不新增运行时环境变量或安装目录
（本次未执行以下部署命令）：

```bash
cd /home/lingbo11/zxm/farm-rdma-core
cmake --build build --target mlx5 -j8

# 停止测试进程后执行；原脚本会编译、安装、卸载并加载内核模块。
cd /home/lingbo11/zxm/farm-rdma-kerndriver
sudo bash kernel_make.sh
```

继续用原来链接 FARM 库的 General 运行方式。统计写到应用 **stderr**，仍然
每线程累计处理约 100 万个 WQE 后输出一条 `SRM_WQE_TIMING algorithm=farm`。
多个 signaled marker 共用一次 `ibv_poll_cq()` 返回时，使用同一个结束 TSC。

## 三个时间点

- **T0：内核硬件 CQ poll 函数返回之后。** 与现有 QPSwitch/FARM 的口径一致，
  一批 CQE 共用 T0；不包括网卡发送时间，也不包括该次硬件 poll 函数本身。
- **T1：该 CQE 的内容已复制、uidx 已修改，即将发布 owner 位时。** 每个 CQE
  单独打点，CQE 自带时间戳先写完，再 release-store CQE 的 owner。
  不能在 owner 发布后才写 T1，否则用户可能先取走 CQE，读到缺失时间戳。
- **T2：用户 `ibv_poll_cq()` 的锁、consumer index 和 stall 处理结束之后，
  返回前打点。** 不包含随后统计汇总、日志输出和函数尾部的少量指令。

T1 是发布提交点的近似，不是网卡/CPU 确认用户已经看见 CQE 的时间：最后的
时间戳及序号存储、owner 发布开销落在第二段。第一段不包括发布后内核处理
其他 CQE、更新 CQ 游标或归还 SQ 信用的全部工作。两段是 **elapsed TSC**，
不能解释为纯 CPU active cycles；中断/抢占可能增加第一段，应用忙于 post、
晚调用 poll 以及 poll 一批其他 CQE 可能增加第二段。

## 输出字段

三个平均值使用相同分母 `kernel_to_user_cqes`，按有效 CQE 加权，而不是
先求每批平均再平均：

| 字段 | 含义 |
| --- | --- |
| `timing_abi=3` | 新版 CQE 自带时间戳格式 |
| `post_to_cqe_scope=post_entry_to_user_poll_return` | 总延迟也统一以用户 poll 返回前为结束点 |
| `kernel_poll_to_dispatch_cycles` | 所有有效 CQE 的 `T1 - T0` 之和 |
| `kernel_poll_to_dispatch_avg_cycles` | 内核 poll 返回到该 CQE 发布的平均延迟 |
| `dispatch_to_user_cycles` | 所有有效 CQE 的 `T2 - T1` 之和 |
| `dispatch_to_user_avg_cycles` | 发布到用户 poll 返回的平均延迟 |
| `kernel_to_user_cycles` | 所有有效 CQE 的 `T2 - T0` 之和，保留原字段 |
| `kernel_to_user_avg_cycles` | 总平均延迟，保留原字段 |
| `kernel_to_user_cqes` | 三个指标共同的有效 CQE 数 |
| `kernel_to_user_missing` | 记录/magic/时间戳缺失或没有有效 poll 上下文 |
| `kernel_to_user_invalid` | CQE 序号不匹配或时间顺序异常等 |

整数总和严格满足：

```text
kernel_poll_to_dispatch_cycles + dispatch_to_user_cycles
    == kernel_to_user_cycles
```

平均值也满足这个关系，但显示两位小数时可能有 0.01 cycle 的舍入误差。
正常运行、每 WQE 一个 CQE 时，应看到 `cqe_wqes == kernel_to_user_cqes`，
以及 `kernel_to_user_missing=0 kernel_to_user_invalid=0 missing_timestamps=0`。
这是修复后的预期，**不是强制将异常清零**。若 missing/invalid 仍较多，
平均值只代表有效样本，应检查库/模块匹配、TSC 同步及实际 CQ 溢出等问题。
遇到这些情况不等待、不重试，也不影响正常 CQE 分发，只排除该条统计。

`post_to_cqe_avg_cycles` 本次统一在用户 poll 返回前结束，与两段交付延迟
使用同一个结束点。`post_excl_db_avg_cycles` 和 DB 计时仍保持原口径；前者
已经扣除了 DB，不能再减一次。两者相加是整个 post 函数耗时，并不精确
等于 post→DB 提交点，因此相减得到的 Others 仍不能称为纯网卡服务时间。
多个 WQE 一个 signaled CQE 时，总延迟按覆盖的 WQE 数统计，交付延迟按
CQE 数统计，分母仍有区别。扩展 CQ 的 start_poll/next_poll/end_poll 不是
本次统计接口；本次针对普通 `ibv_poll_cq()`。

## 开销和正确性

- 启用时，每个被分发 CQE 进行一次内核有序 TSC 读取、写入 32B 记录及
  用户态校验和累加；不逐 CQE 打印，不新增锁或 syscall。
- 时间戳跟随软件 CQE 的 owner 发布协议，避免 SQ 槽位代际覆盖；用户
  读到 owner 并完成读屏障后，在发布 CQ consumer index 前读取记录。
- 用户 poll 结束时批量累加三段，使用先做无符号减法再判有效的方式，支持
  TSC 乘样本数/绝对时间戳求和的模 2^64 回绕。
- 关闭宏时新热路径代码和时间戳数组都不生成；不改 FARM 的 DB、CQ 调度
  或参数。开启统计有开销，不能用统计开启的吞吐替代未插桩性能基线。

## 本次验证（未安装、未重载、未跑 RDMA 硬件测试）

离线测试直接抽取生产代码，用模拟 TSC 和 CQE，开启 ASan/UBSan：

```bash
cd /home/lingbo11/zxm/farm-rdma-core
python3 tests/test_farm_cqe_timing.py
```

回归场景：4096 个逻辑 QP 各有一个待消费 CQE，共用 8-slot 物理 SQ，先完成
512 次 SQ 周转，用户才消费 CQE，4096 条全部有效，invalid/missing 均为 0。
另覆盖单/多 CQE、空 poll、延迟 poll、重复消费、真正缺失/失配/未来时间戳、
32/64 位回绕、相同结束点、错误 CQE 字段保留、CQE 复制和 ABI=2 拒绝。

临时用户库编译命令（实际使用的测试目录如下；不替换 `build/`）：

```bash
for timing in 1 0; do
  cmake -S /home/lingbo11/zxm/farm-rdma-core \
    -B /tmp/farm-cqe-owned-timing.Fpn5oZ/provider \
    -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Debug \
    -DNO_MAN_PAGES=1 -DNO_PYVERBS=1 \
    -DCMAKE_C_FLAGS="-DMLX5_SRM_ENABLE_WQE_TIMING=$timing"
  cmake --build /tmp/farm-cqe-owned-timing.Fpn5oZ/provider --target mlx5 -j8
done
```

内核验证使用现有 `.scheduler.o.cmd`/`.qp.o.cmd` 记录中的 Kbuild 命令，
分别附加 `-DMLX5_SRM_ENABLE_WQE_TIMING=0/1`，移除依赖文件输出选项，
把 `-o` 改到同一 `/tmp/farm-cqe-owned-timing.Fpn5oZ/` 目录。四组对象均通过
编译，没有覆盖源目录的内核对象、修改环境或运行 `kernel_make.sh`。
