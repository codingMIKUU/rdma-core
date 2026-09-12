# QPSwitch per-WQE timing

Set `MLX5_SRM_ENABLE_WQE_TIMING` to `1` in both
`providers/mlx5/mlx5.h` and the matching kernel driver's
`drivers/infiniband/hw/mlx5/scheduler.h`, then rebuild both. Both default to `0`.
The provider checks the kernel control-page capability and mmap size during
Hollow QP attachment; mismatched new builds fail with a timing diagnostic.
No application or wire-format change is required. FARM is independent.

Each successfully published WQE receives the TSC captured at entry to the
provider's `mlx5_post_send`. All WRs in a linked-list call share that call's
entry time. The timestamp includes SQ reservation/backoff and WQE construction.

* `SRM_DB_TIMING algorithm=qpswitch source=kernel`: read with `sudo dmesg -w`.
  One scheduler worker accumulates its actual doorbelled WQEs.
* `SRM_DB_TIMING algorithm=qpswitch source=user`: application stderr, emitted
  only when direct user DB is enabled and actually used. One posting thread
  accumulates its actual doorbelled WQEs, including WQEs submitted by others.
* `SRM_WQE_TIMING algorithm=qpswitch`: application stderr, one CQ-polling thread
  accumulates post-to-polled-completion cycles. Both dispatched-CQE and
  completion-watermark modes are supported, as are small and large KQPs.

Each stream prints after at least 1,000,000 covered WQEs and resets its window.
The final partial window is not printed. These streams need not have identical
windows or sample counts. `*_cycles` is the sum and `*_avg_cycles` is the sum
divided by the corresponding valid `db_wqes` / `cqe_wqes`; kernel averages are
integer cycles. Combine streams by adding sums and counts, not by averaging
their averages. To capture stderr: `bash run-servers.sh 2>qpswitch-timing.log`
(supply the script's normal arguments).

The DB endpoint is the ordered CPU TSC immediately after the existing DB
submission helper returns (after its MMIO write, and the existing WC flush on
the user path). This is not a device acknowledgement or on-wire departure
timestamp. Timestamp sums are copied **before** DB, so CQ recycling cannot
overwrite samples. Shared records have a full reservation sequence and live in
debug-only memory after the unchanged publish-token array, never in hardware
WQE fields. They cost 16 extra bytes per physical SQ slot when enabled.

CQ timing uses private immutable markers, not the reused physical SQ slots.
Unsignaled WQEs are attributed to their next signaled marker on the same lane;
their latency means "completion observed via that marker", not an independently
generated hardware CQE. To measure one actual CQE per WQE, signal every WR.
Trailing unsignaled WQEs without a marker are not included in CQ averages.
WC errors/flushes are excluded and counted in `error_wqes`.

`missing_timestamps` means absent/stale metadata; `invalid_timestamps` means
an impossible negative cycle delta (e.g. an unsynchronized TSC). Use synchronized
invariant TSCs across the participating CPUs; these are TSC ticks, not varying
core-frequency cycles. Enabled measurement adds per-WQE timestamp stores/reads,
TSC instructions and occasional logging. Disabled builds compile those operations
and storage out, apart from one initialization-time compatibility check.

CPU-only regression check (run from rdma-core after its usual configure):

```bash
cc -O2 -std=gnu11 -ffunction-sections -fdata-sections \
  -Ibuild/include -I/usr/include/libnl3 -I/usr/include/drm \
  tests/srm_wqe_timing_test.c -Wl,--gc-sections -lpthread \
  -o /tmp/srm_wqe_timing_test
/tmp/srm_wqe_timing_test
```
