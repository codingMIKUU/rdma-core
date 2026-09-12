# SRM per-WQE timing

This measures SRM (`IBV_QPT_SRM`), not FARM. Two compile-time switches must
match: `MLX5_SRM_ENABLE_WQE_TIMING` in `providers/mlx5/mlx5.h` and in the
matching srm-rdma-kerndriver `drivers/infiniband/hw/mlx5/scheduler.h`.
Both are disabled in the committed defaults. Set both to 1 for diagnosis;
set both to 0 and rebuild to remove timestamp reads, sidecars, and counting.
The local working copy may retain an experiment's enabled setting.

## Intervals and output

The start timestamp is taken at entry to the provider's `mlx5_post_send`,
before its SQ lock or preparation. This excludes the tiny libibverbs inline
dispatch before entering the provider. It is saved per accepted software
WQE, outside the hardware WQE. Failed posts create no timestamp sample.
The pre-existing SRM send implementation accepts one WR per invocation;
this instrumentation does not change that WR-chain behavior.
For strict per-WQE post-to-CQE timing, make every WQE signaled (in the
current SRM-General benchmark this means `kAppUnsigBatch=1`). SRM can send
different WRs through different physical KQPs, so a later marker on one
KQP does not prove earlier WRs on another KQP have completed. The legacy
unsignaled grouping below is only an observation metric, not that proof.

* `SRM_DB_TIMING algorithm=srm source=kernel` is printed to dmesg by each
  scheduler after 1,000,000 instrumented WQEs have been doorbelled. The end
  is after `mlx5r_ring_db(qp, 1, ctrl)` and a write barrier draining CPU WC
  doorbell writes. It measures CPU submission, not NIC acknowledgment or
  packets appearing on the wire. Queue waiting, credit stalls, scheduler
  delay, kernel WQE copying, and the doorbell operation are all included.
  `db_calls` is the number examined; `db_wqes` is the number with valid
  timestamps; `post_to_db_cycles` is their sum; `post_to_db_avg_cycles` is
  that sum divided by `db_wqes`. Kernel averages are integer TSC ticks.
* `SRM_WQE_TIMING algorithm=srm` is printed to userspace stderr after each
  polling thread observes 1,000,000 WQEs (or invalid/error samples).
  `cqe_wqes`, `post_to_cqe_cycles`, and `post_to_cqe_avg_cycles` measure from
  the same start to the provider consuming the corresponding send CQE.
  With unsignaled WRs, timestamps accumulate until the next signaled WR;
  that CQE supplies the observation time for the covered group. This is
  not a measurement of each unsignaled WR's physical NIC completion time.
  An unsignaled suffix without a later signaled CQE cannot be reported.
  SRM reuses a software slot after the kernel copies it, potentially before
  the prior CQE arrives. If another signaled WR reuses the same timing slot,
  all ambiguous CQE samples are dropped and counted as missing until that
  slot's pending CQEs drain. They never borrow a newer WR's timestamp.
  Keep the outstanding workload within the SQ depth for complete coverage;
  this instrumentation does not fix the legacy application's WR-ID reuse
  rules. A diagnostic-only per-slot lock protects concurrent post/poll.

`missing_timestamps` and `invalid_timestamps` do not enter either average.
In the user report, a missing/ambiguous timestamp increments the missing
counter once per CQ marker, because its covered WQE count is unknown. In
the kernel report each missing counter increment is one doorbelled WQE.
`error_wqes` counts user completions with error/flush separately and excludes
them from the CQE latency average. Reports reset their counters; a trailing
interval below one million is not automatically printed at exit. Kernel
and user reporting windows need not align. Combine reports with
`sum(cycles) / sum(valid_wqes)`, not an unweighted mean of per-thread means.

Both intervals use TSC ticks, not changing core-frequency cycles. User and
scheduler may run on different CPUs: synchronize/bind them on a host with
synchronized invariant TSCs. Negative signed deltas are rejected; a fixed
cross-CPU TSC offset that remains positive cannot be detected automatically.

## Shared metadata and compatibility

Each SRM SQ gets a page-aligned sidecar of 16 bytes per SQ slot (allocation
rounded to a page), plus the existing userspace completion timing arrays.
The provider publishes `{post_tsc, u32 sequence, valid}` before publishing
the software WQE's ready flag. The kernel reads after acquiring ready and
checks the sequence against the source WQE's 16-bit counter. It snapshots
and clears validity before releasing the source slot, then measures its
actual doorbell. This prevents consuming stale timestamps after slot reuse;
the slot index and source counter checks handle their existing wrapping.
Userspace may advance its counter while scanning occupied slots, so the
kernel does not assume its absolute counter equals the producer counter.
No timestamp is placed in hardware-interpreted WQE fields.

The create-QP request appends a version, address and slot count; original
fields keep their offsets. The kernel checks version, type, count,
alignment and ring bounds, pins the sidecar, and acknowledges support with
`MLX5_IB_CREATE_QP_RESP_MASK_SRM_TIMING`. A timed provider rejects a kernel
that does not acknowledge support; a timing-disabled new kernel rejects a
timed request. Non-timed clients send zero extension fields. Both client and
kernel updates are therefore required to enable this diagnostic.
The sidecar is detached with RCU protection on QP destruction and freed
after scheduler stop on module exit. This does not otherwise alter the
legacy SRM software-SQ/connection lifecycle.

## Build and view

After setting both header macros to 1, build the existing directories:

```bash
cd /home/lingbo11/zxm/srm-rdma-core
cmake --build build --target mlx5 --parallel 8

cd /home/lingbo11/zxm/srm-rdma-kerndriver
make -j8
# Install/reload the rebuilt kernel using your normal kernel_make.sh workflow
# after stopping the test processes. Compilation above does not reload it.
```

If an existing build directory is root-owned, build as its owner or fix its
ownership deliberately; do not switch the benchmark to an unannounced new
library directory. Ensure SRM-General loads this rebuilt provider.

Terminal 1:

```bash
sudo dmesg -w | grep --line-buffered 'SRM_DB_TIMING algorithm=srm'
```

Terminal 2: append `2> srm-cqe-timing.log` to the actual SRM sender launch.

```bash
grep 'SRM_WQE_TIMING algorithm=srm' srm-cqe-timing.log
```

Enabling timing adds TSC reads, a shared sidecar access and per-WQE counting;
it is diagnostic and can affect performance. Disabling both macros removes
that work at compilation, so use disabled builds for final throughput runs.

## CPU-only regression check

After configuring/building rdma-core, compile against the generated headers
in that build directory. No RDMA device or kernel reload is needed:

```bash
SRM_TIMING_BUILD=build
SRM_TEST_DIR=$(mktemp -d /tmp/srm-timing-test.XXXXXX)
cc -D_GNU_SOURCE -DMLX5_SRM_ENABLE_WQE_TIMING=1 \
  -ffunction-sections -fdata-sections \
  -I"$SRM_TIMING_BUILD/include" -I. -Iproviders/mlx5 \
  tests/srm_wqe_timing_test.c -Wl,--gc-sections -pthread \
  -o "$SRM_TEST_DIR/test_timing"
"$SRM_TEST_DIR/test_timing"
```

This uses the production helpers to check request/slot ABI layout, grouped
sum arithmetic, TSC wrapping, negative and error rejection, overlapping
slot reuse and recovery after ambiguous CQEs drain.
