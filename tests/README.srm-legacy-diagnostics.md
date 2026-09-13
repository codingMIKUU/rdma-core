# Historical QPSwitch diagnostics (srm-before-token-bytes)

These diagnostics preserve the existing DB and CQ completion algorithms.
They are independent of `srm_stats_enable`, `srm_direct_db_stats_enable`, and
`MLX5_SRM_ENABLE_DB_BATCH_LOG`. Both new statistics switches default to **0**.

## Enable and build

In rdma-core `providers/mlx5/mlx5.h`:

```c
#define MLX5_SRM_ENABLE_DB_SHARE_STATS 1
```

In rdma-kerndriver `drivers/infiniband/hw/mlx5/scheduler.h`:

```c
#define MLX5_SRM_ENABLE_DB_SHARE_STATS 1
#define MLX5_SRM_ENABLE_CQE_CYCLE_STATS 1
#define MLX5_SRM_DIAG_INTERVAL_MS 1000U
```

DB share statistics require the updated provider AND kernel with the DB share
switch enabled; otherwise userspace DBs will not be counted. Kernel startup
sets a control-slot flag so a statistics-enabled provider does not write the
reserved area of an older/non-statistics kernel. This flag is not a provider
capability handshake: a kernel cannot detect an old/statistics-disabled
provider, so do not interpret its zero user counters as proof of zero user DBs.

CQ cycle statistics require only a kernel rebuild. The *CQ mode* switch
`MLX5_SRM_ENABLE_CQE_SIMPLIFY` must still match between kernel and provider,
as before. Values 0 and 1 are both supported by the new statistics. They
do not add CQ publication coalescing or mode negotiation.

After editing the macros, build the provider using your normal rdma-core
build directory (example, from rdma-core):

```bash
cmake -S . -B build -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Release -DNO_MAN_PAGES=1
cmake --build build --parallel 8
```

Check that no previous `CMAKE_C_FLAGS` override forces different macro values.
Stop RDMA programs, then rebuild/install/load the kernel using your existing
`kernel_make.sh` procedure. Do not unload modules with active applications.
Ensure General loads this provider rather than an old installed/MPI-bundled
one. This change does not install anything automatically or modify General.

Run the usual General command. In another terminal:

```bash
sudo dmesg -w | rg 'SRM_DB_SHARE_STATS|SRM_CQE_CYCLE_STATS'
```

No extra application argument or environment variable is needed. Reports are
emitted by each scheduler worker roughly once per interval; a worker stuck
inside a long poll/credit wait cannot emit until it returns to its main loop.
Counters remaining at program/module termination are not forcibly flushed.

## DB percentages

`SRM_DB_SHARE_STATS` counts only actual successful DBs, after the MMIO write,
not attempts, scans, credit claims, or WQE reservations:

- `user_db_calls`, `kernel_db_calls`: user/kernel DB operation counts.
- `user_db_pct_x100`, `kernel_db_pct_x100`: respective DB calls / total DB calls.
- `user_db_wqes`, `kernel_db_wqes`: WQEs actually included in those DBs.
- `user_wqe_pct_x100`, `kernel_wqe_pct_x100`: respective WQEs / total WQEs.
- `user_batch_avg_x100`, `kernel_batch_avg_x100`: WQEs per successful DB.
- `window_ms`: elapsed report interval.
- `sampled_kqps`, `deferred_kqps`: coherent per-KQP snapshots accepted/deferred.

For percentages, `2500` means 25.00%; for batch averages, `2500` means 25 WQEs.
No work gives zero denominators and zero percentages, not 100% kernel work.
Integer truncation can make two complementary percentages sum to 9999.

Example: user makes 100 DBs for 200 WQEs; kernel makes 25 DBs for 800 WQEs.
User DB-count share is 80%, but its WQE share is only 20%.

Unlike the old `SRM_DIRECT_DB_STATS`, these counters do not wait for a thread's
million-attempt TLS flush. They occupy one previously reserved cacheline per
KQP. Slot size remains 512 bytes; existing field offsets do not change.
Writers use the owner they already hold, with no additional lock or atomic
read-modify-write. A sequence counter protects the four-counter snapshot.
The kernel reader makes at most three attempts, never takes `db_owner`, and
retains the previous baseline if a writer is active.

`scope=kqp_deltas` means both sides are sampled together for each KQP, not a
globally atomic instant across all KQPs. Normally these are the same report
window; deferred KQPs are omitted and their deltas carried into a later
report, which is marked by `deferred_kqps`. Do not compare an affected single
interval as if it were a strict synchronized rate. Use stable complete
windows or aggregate multiple reports. The cumulative u64 counter deltas and
the u32 snapshot sequence tolerate wrap. The scope is the existing worker's
KQP lifetime, not arbitrary live KQP resets/reconfiguration.

## CQ cycles per hardware CQE

`SRM_CQE_CYCLE_STATS` reports:

```text
scope=poll_and_publish
cqe_avg_cycles = active_poll_cycles / cqes
```

Timing brackets the unchanged `srm_poll_srmc_once()` operation, including
budget refresh, kernel CQ locking/polling/parsing, completion publication,
SQ recycle and shared credit return. In mode 0 this includes native user-CQE
routing/copy; in mode 1 it includes per-hardware-CQE watermark publication.
Only invocations returning **positive hardware CQE counts** contribute to
this average. It is a ratio of sums, not an average of per-poll averages.

- `poll_calls`: all invocations of this scheduler CQ operation.
- `active_poll_calls`: invocations returning at least one hardware CQE.
- `cqes`: hardware CQEs returned by those invocations, **not completed WQEs**.
  One signaled CQE can cover many preceding unsignaled WQEs.
- `active_poll_cycles`: their total timed cycles.
- `cqe_avg_cycles`: the requested amortized cycles per hardware CQE.
- `nonpositive_poll_calls`, `nonpositive_poll_cycles`: empty, no-budget and
  error returns, kept separate from the requested average.

The outer `poll_srmc_inline()` queue selection and userspace WC construction
are not timed. Neither are the new counter updates and periodic report
formatting. This is whole-poll completion-processing cost, NOT a timer around
only memcpy/cons_idx stores, per-CQE latency, or post-to-completion latency.
No extra publication-operation count or per-operation timing is added.

Two ordered TSC reads are used per invocation, not per CQE. These are TSC
ticks, not PMU unhalted CPU cycles; interrupts/preemption and existing enabled
diagnostics can affect them. Do not assume current turbo frequency converts
TSC ticks to nanoseconds. Each nonempty poll amortizes its polling and final
bookkeeping over the actual CQEs it returned.

With switches off, hooks, fields in the private CQ workspace, timestamps,
counter updates and new reporting are compiled out. With switches on, there
is measurement overhead (including shared cacheline writes and printk);
benchmark absolute peak throughput again with them off. No claim is made
that enabled statistics have zero performance impact.

## Checks performed (no installation or RDMA run)

From rdma-core:

```bash
python3 tests/test_srm_legacy_cq_toggle.py
python3 tests/test_srm_legacy_diagnostics.py
for cq in 0 1; do
  for db in 0 1; do
    cmake -S . -B "/tmp/qpswitch-diag-build.2O8za0/cq${cq}-db${db}" \
      -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Release -DNO_MAN_PAGES=1 -DNO_PYVERBS=1 \
      -DCMAKE_C_FLAGS="-DMLX5_SRM_ENABLE_CQE_SIMPLIFY=${cq} -DMLX5_SRM_ENABLE_DB_SHARE_STATS=${db}"
    cmake --build "/tmp/qpswitch-diag-build.2O8za0/cq${cq}-db${db}" \
      --target mlx5 --parallel 8
  done
done
```

From rdma-kerndriver:

```bash
python3 tests/check_srm_kernel_compile.py --diagnostics
```

The kernel check compiles scheduler/cq/qp using recorded Kbuild commands in
all eight mode/DB-stat/CQ-stat combinations. The provider check builds four
mode/DB-stat combinations. Tests exercise real counter/snapshot helpers with
concurrent writers, counter/sequence wrap, ABI sizes/offsets, percentage
math, and the production timing wrapper with mock CQ returns. They also
verify unchanged hot-path function bodies when statistics are disabled.
All build outputs are temporary. No live `build/lib`, environment, installed
library, module, or running benchmark is changed by these validation commands.
