# srm-before-token-bytes: historical CQ switch

This branch is based on `a9ff6e90` (2026-08-26), the last experimental
completion-watermark snapshot before the MVAPICH2 adaptations. Pair it with
`rdma-kerndriver` branch `srm-before-token-bytes`, based on `d73981d`.
It is not the later MPI-compatible three-mode implementation.

## Select the mode at compile time

Set the same value in both files:

- rdma-core: `providers/mlx5/mlx5.h`
- rdma-kerndriver: `drivers/infiniband/hw/mlx5/scheduler.h`

```c
#define MLX5_SRM_ENABLE_CQE_SIMPLIFY 0
```

- `0` (default): restore the native CQE path from `a3eb20f7` / `b286657`.
  The kernel copies native CQEs to the existing user CQ and batches per-KQP
  SQ completion updates. Userspace polls the native CQ and looks up `wrid[]`.
  The watermark marker, pending list, post-time CQ lock and watermark poll
  are compiled out, not selected by a branch on each WQE.
- `1`: preserve the `a9ff6e90` / `d73981d` watermark implementation.
  The kernel publishes KQP completion progress; userspace retains one pending
  signaled marker per logical QP and constructs a WC when it completes.
  Post a fixed window ending in a signaled WR and poll it before the next
  window. This is NOT the later multi-marker MVAPICH implementation.

The two drivers MUST be rebuilt with matching values. This historical ABI
does not negotiate CQ mode at runtime. Mixing modes can hang or lose
completions. There is no mode `2`, environment variable or module parameter
for this switch; invalid numeric values fail compilation.

Only CQ handling is selected. The August 26 snapshot's hot queue, 512-byte
control slots, KQP configuration and other parameters remain in both modes.
Mode 0 is therefore not a whole-tree rollback to August 25. Later MVAPICH2,
multi-node/size-split extensions and lifecycle fixes are not imported.

## Rebuild for use (not run by the switch implementation)

After selecting the same macro in both trees, from the workspace:

```bash
cmake -S rdma-core -B rdma-core/build -DIN_PLACE=1 \
  -DCMAKE_BUILD_TYPE=Release -DNO_MAN_PAGES=1
cmake --build rdma-core/build --parallel 8
```

Rebuild/reinstall the matching kernel module using your existing procedure,
after stopping RDMA applications. Rebuild RDMA-General against these headers
and libraries as well. A Git switch alone does not replace loaded modules or
existing binaries. Do not use an MPI installation's bundled modern rdma-core
with this historical tree: even the private AH/provider ABI predates MPI.
If a prior CMake/Kbuild command set `-DMLX5_SRM_ENABLE_CQE_SIMPLIFY`, remove or
update that override before relying on the value edited in the header.

## Non-RDMA validation

```bash
# Run in rdma-core (expects its sibling rdma-kerndriver and git history).
python3 tests/test_srm_legacy_cq_toggle.py
# Run in an already configured rdma-kerndriver tree.
python3 tests/check_srm_kernel_compile.py
```

The first test compares selected production poll functions to the historical
commits, checks that watermark metadata is absent in mode 0, and executes the
production watermark poller with readiness, shared-CQ, wrap and error cases.
The second compiles scheduler/cq/qp in both modes using recorded Kbuild flags
and temporary output objects. Neither test installs or loads anything.
Compilation and these tests are not a hardware throughput guarantee.

### Build verification performed for this change

Both provider variants were configured and built in the temporary directory
`/tmp/qpswitch-legacy-cq-build.k7REZJ`, using the following commands for each
mode (run from rdma-core):

```bash
for mode in 0 1; do
  cmake -S . -B "/tmp/qpswitch-legacy-cq-build.k7REZJ/mode${mode}" \
    -DIN_PLACE=1 -DCMAKE_BUILD_TYPE=Release -DNO_MAN_PAGES=1 \
    -DNO_PYVERBS=1 -DCMAKE_C_FLAGS="-DMLX5_SRM_ENABLE_CQE_SIMPLIFY=${mode}"
  cmake --build "/tmp/qpswitch-legacy-cq-build.k7REZJ/mode${mode}" \
    --target mlx5 --parallel 8
done
```

The two Python checks above were also run. The kernel check compiles
`scheduler.c`, `cq.c` and `qp.c` in both modes with the recorded local Kbuild
flags. No install command, module reload, General rebuild, system linker
configuration change, or performance benchmark was run. Existing live
`build/lib` libraries were not replaced.
