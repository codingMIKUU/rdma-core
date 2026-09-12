# Three Hollow completion modes

Select in the **kernel** `drivers/infiniband/hw/mlx5/scheduler.h`:

```c
#define MLX5_SRM_ENABLE_CQE_SIMPLIFY 2
```

| Value | Startup `cq_mode` | Delivery |
| --- | --- | --- |
| 0 (default) | dispatch | Kernel 32-byte software event; provider matches attached-QP markers and polls pending QPs. |
| 1 | progress | Kernel KQP watermark/error; provider checks saved signaled markers. |
| 2 | direct | Kernel native 64-byte CQE with full immutable identity; CQ-local UIDX lookup and bounded WC return. |

The updated provider negotiates this at QP INIT/RTR. There is no matching
userspace mode macro to keep in sync. Mode 2 retains multiple outstanding
signaled markers, original `wr_id`/opcode, errors and ordered logical-QP
completion across small/large KQPs. It uses a separate mmap-backed send ring
so real hardware SRQ receive CQEs can still share the application CQ safely.
This is an adaptation of the historical native-CQE-copy mechanism, **not** a
restoration of unsafe CPU/NIC writes into one CQ ring.

Mode 2 replaces global attached/pending-QP scans with a CQ-local sparse
index and a FIFO containing only QPs whose oldest marker is ready. The
index has 256 pointers per allocated leaf plus a dynamically grown directory;
it is allocated on attach and freed with the CQ. This scope is important:
old CQEs must not dereference a resource index reused on another CQ. Per-lane
marker matching remains necessary for multi-signaled and dual-lane ordering;
the common single-lane match is the next marker, not a scan of all QPs.

Shared ring memory is page-rounded `128 + 64 * (cq.cqe + 1)` bytes for direct,
versus `128 + 32 * (cq.cqe + 1)` for dispatch. Both use the existing 32-byte
signaled marker plus a 12-byte status slot. Progress has no software CQ or
status slots. Ordinary RC/XRC do not allocate these Hollow resources.

## Build and use

Use branch `srm-cq-progress-poll-cq3` in both `rdma-core` and `rdma-kerndriver`.
With the existing in-place `build` configuration, while applications are stopped:

```bash
cd /path/to/rdma-core
python3 tests/test_srm_cq_abi.py
python3 tests/test_srm_cq_dispatch.py
cmake --build build --target mlx5 --parallel 8
```

This rebuilds the provider in `build/lib`; it does not install system libraries.
RDMA-General must resolve that provider. For an existing MVAPICH bundled
installation instead use its established deployment script:

```bash
cd /path/to/mvapich2-2.3.7
JOBS=8 contrib/hollow-rc/rebuild_rdma_core.sh
```

After selecting kernel mode 2, rebuild/install/load the kernel through the
normal `sudo bash kernel_make.sh` procedure, with no active RDMA jobs. On a
bidirectional test update both hosts; on each host the provider and module
must match. Existing launch commands remain unchanged. Confirm:

```text
HOLLOW_CQ cqn=... cq_mode=direct
```

Only the first deployment needs a provider update. Subsequent 0/1/2 switches
need a kernel rebuild/reload, not an application rebuild. New providers retain
INIT-probe compatibility with old two-mode/progress-only kernels; mode-2
kernels reject providers missing the direct capability explicitly.

Direct supports the existing `ibv_poll_cq()`/CQE-version-1 interface, not CQEX
or resizing a CQ with software storage. It does not add identities for
arbitrary unsignaled-error WRs: unmatched errors are diagnosed rather than
fabricating an application's `wr_id`. Follow the existing signaled/window
contract. Scheduler SQs do not support inline-scatter CQE payloads.

Compile and mocked production-function tests do not establish hardware
throughput. Historical 180 Gb/s must be remeasured at identical workload,
signaled frequency, SQ size, DB parameters, CPU bindings and logging settings.
Detailed kernel behavior and the compile matrix are documented in
`rdma-kerndriver/tests/README.srm-cq-mode.md`.
