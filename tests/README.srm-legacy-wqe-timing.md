# Historical QPSwitch: WQE/DB timing and amortized CQE P99

This ports the timing metrics from `srm-cq-progress-poll-log`, without its
MPI completion implementation. It does not change DB eligibility, credit
policy, routing, CQ moderation or the fixed-window contract of this branch.

## Switches

For WQE timing, set the same compile-time macro in **both**:

- rdma-core: `providers/mlx5/mlx5.h`
- rdma-kerndriver: `drivers/infiniband/hw/mlx5/scheduler.h`

```c
#define MLX5_SRM_ENABLE_WQE_TIMING 1  /* new default is 0 */
```

For CQE mean/P99, enable only in kernel `scheduler.h`:

```c
#define MLX5_SRM_ENABLE_CQE_CYCLE_STATS 1
```

The CQE simplification macro still needs to match across the two builds.
Existing CQE/DB statistics switch values and benchmark parameters are not
changed by this implementation. Runtime `srm_stats_enable` is not required.

Rebuild rdma-core with the original build procedure, then rebuild/install
the kernel driver after stopping and cleaning up the benchmark. Use the
provider you just built when launching General. Do not mix an old timing
branch with this one: bit 1 is DB_SHARE_STATS here; timing uses bit 2.
Mapping setup rejects missing timing capability or an undersized sidecar.

Disable WQE timing in both headers and rebuild both to remove its extra
timestamp memory, clock reads, CQ locking, counters and reporting hooks.
Disabling CQE cycle statistics removes its timer, histogram and report.
Capability validation is a setup-only check, not a per-WQE branch.

## View results

In one terminal:

```bash
sudo dmesg -w | rg 'SRM_DB_TIMING|SRM_CQE_CYCLE_STATS'
```

In `RDMA-General/sender-scalability`, use the normal benchmark launcher:

```bash
sudo bash run-servers.sh 2>qpswitch-timing.stderr.log
```

Inspect the file from another terminal:

```bash
tail -f qpswitch-timing.stderr.log | rg 'SRM_WQE_TIMING'
```

The example runs the benchmark; it is not a build command. This change does
not install modules, run this command or modify the launcher for you.

## Measurement boundaries

| Record / field | Meaning |
| --- | --- |
| `SRM_WQE_TIMING post_to_cqe_avg_cycles` | Provider `mlx5_post_send()` entry to user CQ parsing/watermark completion, averaged over successfully completed timed WQEs. Not NIC completion time, not application code after `ibv_poll_cq()` returns. |
| `SRM_DB_TIMING source=kernel post_to_db_avg_cycles` | Each WQE's post entry to kernel DB helper return, averaged over valid timestamps for WQEs doorbelled by the kernel. Output in dmesg. |

User completion records use per-thread counters; kernel DB records use
per-worker counters. Each reports after 1,000,000 observed WQEs, then resets;
their output intervals are independent. Slow threads can take considerably
longer to print. Unfinished windows are not printed at exit.

There is deliberately no `source=user` DB timing. The intended measurement
runs with direct user DB disabled, so every sampled DB is performed by the
kernel. If direct user DB is enabled anyway, WQEs doorbelled entirely by user
space have no `SRM_DB_TIMING` sample, although their post-to-CQE latency can
still appear in `SRM_WQE_TIMING`.

`db_wqes` / `cqe_wqes` are the **valid sample denominators**, not necessarily
all processed WQEs. Missing timestamps, inconsistent/negative TSC deltas and
completion errors are counted separately and excluded from latency averages.
No wr_id is modified. All WRs in one ibv_post_send linked list share that
call's entry timestamp. Unsignaled WQEs, if used, are timed to the CQE that
cumulatively acknowledges them, not to an independently observable CQE.

## CQE P99 is explicitly a batch-amortized distribution

Existing `cqe_avg_cycles` remains:

```text
sum(nonempty poll-and-publish cycles) / sum(returned hardware CQEs)
```

For each nonempty poll, its cycles/CQE mean is rounded up and inserted into
the histogram with weight equal to its CQE count. New output:

```text
cqe_batch_avg_p99_cycles_upper=... p99_weight=cqes
```

This is the upper bound of the bin containing nearest-rank P99, **not the
P99 of individually timestamped CQEs** and not whole-poll P99. A single slow
CQE can be diluted by its batch. Histogram bins are exact below 16 cycles,
then have eight sub-buckets per power of two (at most ~12.5% bucket width,
plus integer rounding). Empty polls are excluded. The histogram resets with
the existing one-second report and uses 3,968 bytes per CQ workspace.
The original mean includes batch setup, locking, routing/publication and
credit return. Histogram accounting is outside the elapsed interval.

## Storage, concurrency and measurement overhead

- Shared timestamp sidecar: 16 bytes per physical publish slot, after the
  unchanged token array. Never part of a hardware WQE. Allocated only when on.
- Provider timestamp FIFO: 16 bytes per entry, initially 16 entries per active
  logical SQ and grown geometrically with outstanding WQEs. Small/large SQs
  are separate. It survives physical slot recycling and is freed at QP destroy.
- FIFO growth is before physical reservation, so allocation failure cannot
  introduce a publish hole. CQ locks protect posting versus completion.
- Kernel DB timestamp snapshot precedes MMIO; the completion code never
  rereads timestamp slots after they could have been recycled.
- The existing 16-bit CQE counter/SQ live-window assumptions still apply;
  this instrumentation does not expand supported CQE counter ambiguity or
  permit extra pending signaled markers in the historical simplified path.

WQE timing ON adds per-WQE stores, FIFO operations/locks, kernel timestamp
scans, clock reads and periodic logs. It does not scan or time the direct-user-
DB path. These perturb throughput and measured latency;
do not compare an instrumented run to an uninstrumented run as if identical.
CQE P99 adds only per-poll histogram accounting to the existing timers, but
is also not guaranteed free. Cross-core intervals require synchronized TSCs;
negative deltas are rejected, but positive clock skew is not detectable here.

## Validation commands (no device traffic / installation)

From rdma-core:

```bash
python3 tests/test_srm_legacy_diagnostics.py
python3 tests/test_srm_legacy_wqe_timing.py
```

From rdma-kerndriver (configured Kbuild tree required):

```bash
python3 tests/check_srm_kernel_compile.py --wqe-timing --diagnostics --units scheduler cq qp
```

Provider temporary build matrix (no installation or runtime build replacement):

```bash
task_build=$(mktemp -d /tmp/qpswitch-wqe-timing-build.XXXXXX)
for timing_cq in 0 1; do
  for timing_on in 0 1; do
    variant_dir="$task_build/cq$timing_cq-timing$timing_on"
    cmake -S . -B "$variant_dir" -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Release \
      -DNO_MAN_PAGES=1 -DNO_PYVERBS=1 \
      -DCMAKE_C_FLAGS="-DMLX5_SRM_ENABLE_CQE_SIMPLIFY=$timing_cq -DMLX5_SRM_ENABLE_WQE_TIMING=$timing_on"
    cmake --build "$variant_dir" --target mlx5 --parallel 8
  done
done
```
