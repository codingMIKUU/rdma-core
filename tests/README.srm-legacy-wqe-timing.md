# Historical QPSwitch: WQE/DB timing and amortized CQE P99

This ports the timing metrics from `srm-cq-progress-poll-log`, without its
MPI completion implementation. It does not change DB eligibility, credit
policy, routing, CQ moderation or the fixed-window contract of this branch.

## Switches

For WQE timing, set the same compile-time macro in **both**:

- rdma-core: `providers/mlx5/mlx5.h`
- rdma-kerndriver: `drivers/infiniband/hw/mlx5/scheduler.h`

```c
#define MLX5_SRM_ENABLE_WQE_TIMING 1
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
provider you just built when launching General. Rebuild **both** drivers for
the combined user/kernel DB report. Bit 1 is DB_SHARE_STATS here; timing uses
bit 2 and combined DB timing advertises bit 5. The new provider rejects an
older timing kernel lacking bit 5, or an undersized sidecar. An older provider
cannot check this new capability: do not use it with the new timing kernel.

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
| `SRM_WQE_TIMING post_to_cqe_avg_cycles` | Provider `mlx5_post_send()` entry to user poll return instrumentation. Not NIC completion time or application code after `ibv_poll_cq()` returns. Unchanged by this DB accounting update. |
| `SRM_DB_TIMING source=all user_post_to_db_avg_cycles` | Post entry to actual **user** DB helper return; average over `user_timed_wqes`. |
| `SRM_DB_TIMING source=all kernel_post_to_db_avg_cycles` | Post entry to actual **kernel** DB helper return; average over `kernel_timed_wqes`. |
| `SRM_DB_TIMING source=all post_to_db_avg_cycles` | Combined cycle sum / combined **valid WQE count**, not average of batch averages or a 50/50 mean of user/kernel means. |

User completion records still use per-thread counters and report after
1,000,000 observed WQEs. DB records now use per-worker aggregation of per-KQP
counters and print in dmesg once per `MLX5_SRM_DIAG_INTERVAL_MS` (1000 ms),
when there was DB activity. Their output intervals are independent; unfinished
windows are not printed at exit. No separate user-DB stderr output is added.

`*_db_wqes` and `db_wqes` count actual WQEs sent, while `*_timed_wqes` and
`timed_wqes` are valid denominators. `user_timing_excluded` and kernel
`missing/invalid_timestamps` expose rejected samples. The calculation is:

```text
post_to_db_avg_cycles =
    (user_post_to_db_cycles + kernel_post_to_db_cycles) /
    (user_timed_wqes + kernel_timed_wqes)
```

Every successfully doorbelled application WQE is checked, including when a
thread rings DB for other threads' WQEs. Failed scans, owner/credit failures,
copied-but-not-doorbelled WQEs and synthetic retirement NOPs are not samples.
Migrated WQEs retain the original post timestamp and are counted on the path
that actually sends them. CQE simplification does not change this DB metric.

`scope=kqp_deltas` is not an atomic cluster-wide sampling boundary. A busy
counter snapshot is retried at most three times, then **both** user and kernel
contributions for that KQP are deferred without loss (`deferred_kqps`). Their
next successful report spans the deferred time too. No owner acquisition is
needed for reporting. `DB_SHARE_STATS` and `CQE_CYCLE_STATS` can remain off;
only matching `WQE_TIMING=1` is required for the combined DB timing.

Missing timestamps, inconsistent/negative TSC deltas and completion errors
are excluded from the appropriate latency averages; zero valid count prints
an average of 0 and must not be interpreted as zero latency.
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

- Shared timestamp sidecar: 32 bytes per physical publish slot, after the
  unchanged token array. Never part of a hardware WQE. Allocated only when on.
- Provider timestamp FIFO: 24 bytes per entry on x86-64, initially 16 entries per active
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
scans, clock reads and periodic logs. User DB timing adds timestamp loads/sums
inside its **existing** readiness scan (no second pass), one ordered TSC after
each actual DB, and cumulative updates under the existing `db_owner`/sequence.
It reuses 24 reserved bytes of the DB-share cacheline; control slots stay
512 bytes and no extra timestamp mapping is needed. Kernel-only private
aggregation/snapshot arrays use about 104 KiB per worker when timing is on.
No new syscall, global lock, shared atomic fetch-add or per-WQE printk is added.
These still perturb throughput and measured latency;
do not compare an instrumented run to an uninstrumented run as if identical.
CQE P99 adds only per-poll histogram accounting to the existing timers, but
is also not guaranteed free. Cross-core intervals require synchronized TSCs;
negative deltas are rejected, but positive clock skew is not detectable here.

## Validation commands (no device traffic / installation)

From rdma-core:

```bash
python3 tests/test_srm_db_timing_all.py
python3 tests/test_srm_reroute_byte_window.py
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

## Combined DB timing validation in this workspace

No installation, module reload, remote synchronization, live `build/` library
replacement or runtime environment change was performed. Benchmark throughput
and instrumentation overhead still need measurement on hardware. Checks used:

```bash
# From rdma-core: production helpers with ASan/UBSan and mock doorbells.
python3 tests/test_srm_db_timing_all.py
python3 tests/test_srm_reroute_byte_window.py

# From rdma-kerndriver: recorded Kbuild flags, temporary output objects.
python3 tests/check_srm_kernel_compile.py --wqe-timing --diagnostics \
  --units scheduler --reroute 0
python3 tests/check_srm_kernel_compile.py --wqe-timing --diagnostics \
  --private-cq 1 --latency-cq-priority 1 --reroute 1 --units scheduler cq qp

# From rdma-core: temporary provider build, not used by the running application.
task_db_build=$(mktemp -d /tmp/qpswitch-db-timing-build.XXXXXX)
cmake -S . -B "$task_db_build" -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Release \
  -DNO_MAN_PAGES=1 -DNO_PYVERBS=1 -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_C_FLAGS='-DMLX5_SRM_ENABLE_WQE_TIMING=1 -DMLX5_SRM_ENABLE_DB_SHARE_STATS=0 -DMLX5_SRM_ENABLE_REROUTE=0'
cmake --build "$task_db_build" --target mlx5 --parallel 8
for db_variant in '1 1 0' '1 1 1' '0 0 0'; do
  read -r timing_on share_on cq_mode <<< "$db_variant"
  cmake -S . -B "$task_db_build" \
    -DCMAKE_C_FLAGS="-DMLX5_SRM_ENABLE_WQE_TIMING=$timing_on -DMLX5_SRM_ENABLE_DB_SHARE_STATS=$share_on -DMLX5_SRM_ENABLE_CQE_SIMPLIFY=$cq_mode -DMLX5_SRM_ENABLE_REROUTE=1"
  cmake --build "$task_db_build" --target mlx5 --parallel 8
done
```

Compile-command overrides in these checks do not modify header defaults.
For real experiments, use your existing rdma-core build and kernel install
scripts after selecting matching macros. PRIVATE_CQ now supports either
CQE_SIMPLIFY mode; select the same CQE_SIMPLIFY value in both drivers.
Latency-CQ priority requires PRIVATE_CQ, not CQE_SIMPLIFY. No completion-mode
change is needed just to enable timing.
