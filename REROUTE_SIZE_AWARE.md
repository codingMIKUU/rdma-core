# Size-aware reroute 用户驱动

基线 `0f4cfea4e7a8b62ece2314ba772a6089cd691e18`，分支 `srm-reroute-size-aware`。
配套内核基线 `a767575`，同名分支。默认开关关闭，未安装到系统。

## 构建

```bash
cmake -S . -B build-reroute \
  -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Release -DNO_MAN_PAGES=1 -DENABLE_PYVERBS=0 \
  '-DCMAKE_C_FLAGS=-DMLX5_SRM_ENABLE_REROUTE=1 -DMLX5_SRM_ENABLE_CQE_SIMPLIFY=1'
cmake --build build-reroute -j8
```

产物在 `build-reroute/lib`，不执行 `make install`，也不改 PATH/ldconfig。
`MLX5_SRM_ENABLE_REROUTE` 默认定义在 `kernel-headers/rdma/mlx5-srm-reroute.h`；
可直接改成 1，也可用上面的编译参数覆盖。双方 REROUTE、CQE_SIMPLIFY、WQE_TIMING
必须一致。保持基线其他统计开关和性能参数不变。

- 直接分发：双方 `-DMLX5_SRM_ENABLE_CQE_SIMPLIFY=0`。
- 用户不参与 DB 对照：此库加 `-DMLX5_SRM_ENABLE_DIRECT_USER_DB=0`，无需修改应用。
- 延迟 token 的定向测试：此库额外加 `-DMLX5_SRM_REROUTE_TEST_HOOKS=1`；运行时
  `MLX5_SRM_REROUTE_DELAY_US=2000000 MLX5_SRM_REROUTE_DELAY_USER=7`，使指定用户
  第一次发布 token 前暂停 2 秒（每线程一次，未指定 USER 则所有线程）。钩子默认
  编译关闭，不用于性能测试。内核 `srm_reroute_test_source` 负责强制换路。
- 返回基线：重新构建 `-DMLX5_SRM_ENABLE_REROUTE=0` 并使用匹配的关闭版内核。
  不要混用开启/关闭的驱动。

运行窗口确认后，可用独立二进制/启动环境指定构建库。若应用不通过脚本覆盖库路径，
例如：

```bash
reroute_libdir="$(pwd -P)/build-reroute/lib"
env LD_LIBRARY_PATH="$reroute_libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    IBV_DRIVERS=mlx5 /absolute/path/to/test-binary [原测试参数]
```

这只是运行环境示例，不替代 General 的连接配置。若使用 sudo，需明确在 sudo 后
设置环境；默认 sudo 会删除 LD_LIBRARY_PATH。通过 `ldd` 和 `/proc/PID/maps`
核实实际库，不要仅凭命令行推断。当前 General 源码、脚本、参数和已有安装均未修改。

## 改动

1. RTR 响应取得四条 KQP 的 SQ/token/DB/控制页映射；同 context 内映射继续复用。
   响应 ABI、长度、能力校验在初始化执行，控制槽仍为 512B。
2. 正常发送缓存活动槽位与路由版本，不扫描备用池。冻结采用 resv 高位＋CAS，
   原有游标异常修复不能误把冻结位当成溢出。等待换路仍有基线 5 秒上限。
3. 预留前检查支持的 WR：单个非零长 SGE、READ/WRITE、显式 signaled、非 inline、
   单 64B WQEBB；不支持的操作不占 slot。
4. 用户 DB 抢 owner 前后均检查门禁；已预留请求仍能完成写入和 token 发布。
   正常用户直接 DB 保留，实际 DB 才计提交字节。新路预写不消耗发送信用。
5. 每路径保留原 WR metadata。直接 CQE 按还原的原物理 QPN 选择原 metadata；
   简化 CQE 除水位外查看仅迁移使用的原路径/完整序号完成记录，恢复原 WR ID、错误。
   简化模式保留“每逻辑 QP 一个待取走 signaled marker”的限制。
6. QP 销毁释放全部路径的映射引用、WR metadata 和时间戳数组。

状态机、运行参数、NOP 硬件回收保护、离线测试和硬件验收矩阵见配套内核仓库
`REROUTE_SIZE_AWARE.md`。本次不修改接收应用、不切换网卡/端口，也不保证换 KQP
一定改变网络中的物理路由。

## 验证状态

已编译 mlx5 provider：关闭、开启简化、开启直接分发＋时间戳、开启简化＋时间戳＋
关闭用户 DB＋启用延迟钩子。内核离线模拟验证两种完成方式和回绕边界。
尚未加载此版本模块，没有网卡 READ/WRITE 正确性/压力/吞吐结论。
