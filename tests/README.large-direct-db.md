# Hollow RC large-lane user doorbells

The large queue previously had SQ, publish-token and control mappings, but no
doorbell-record (DBR), UAR or worker-credit mapping in its provider bundle.
`srm_try_direct_user_db()` therefore returned immediately for that queue.

The kernel now exports the actual large KQP's DBR and UAR, plus its owner's
existing shared credit slot. The provider attaches them to the large bundle.
The existing post-send path already selects that bundle for a large WQE, so
there is no new branch in the per-WQE send loop. User DB keeps the existing
head/stride eligibility, per-KQP `db_owner`, complete-prefix and credit rules.
Kernel fallback and CQ completion accounting are unchanged. Small and large
queues do not share their DBR or owner lock; they still share worker credits.

## Enablement and compatibility

- Keep the kernel's existing `MLX5_SRM_ENABLE_LARGE_KERNEL_QP` switch. At `0`,
  the new mappings are not created. This change does not turn the switch on.
- At `1`, a matching provider receives the appended `large_farm_*` fields and
  `MLX5_IB_MODIFY_QP_RESP_MASK_LARGE_FARM_DB` capability. The existing provider
  `MLX5_SRM_ENABLE_DIRECT_USER_DB` switch must also be enabled for user DB.
- Old provider + new kernel: insufficient response capacity suppresses the new
  capability and mappings; the large queue retains kernel-only DB.
- New provider + old kernel: absence of the capability retains kernel-only DB.
- The old response prefix and ordinary RC/ECE/DCT response sizes are preserved.
  No MVAPICH2 or RDMA-General source change is needed for this extension.

On new QP attachment, the existing diagnostic gains a field:

```text
SRM_LARGE_KERNEL_SQ ... threshold=4096 user_db=1
```

`user_db=1` means large user DB is enabled and mapped, not that every post rings
a user doorbell. Ownership, credit or incomplete-prefix conditions may still
send work to the kernel. `user_db=0` means the large lane remains kernel-only.
This is an attachment-time message, not per-WQE logging.

## Performance boundaries

There is no new send-loop operation with size splitting disabled. Kernel QPs
gain two setup-only pointer fields at the end of the structure; enabled large
mapping bundles gain two mmap operations (UAR and DBR), reused by QPs sharing a
bundle. No per-WQE allocation, syscall or diagnostic was added.

With splitting enabled, large user DB now performs real scanning, ownership
and credit operations. It may reduce scheduler latency/work, but may also
increase contention or reduce doorbell batch size. A throughput improvement is
not guaranteed by compilation or the hardware-free tests.

## Hardware-free checks

From the rdma-core repository:

```bash
python3 tests/test_srm_large_mapping.py
CC='cc -fsanitize=address,undefined -fno-omit-frame-pointer' \
  python3 tests/test_srm_large_mapping.py
```

From the matching rdma-kerndriver repository:

```bash
python3 tests/test_srm_large_direct_db.py
python3 tests/test_srm_multipeer.py
python3 tests/check_srm_kernel_compile.py --units qp main
```

The mapping tests extract production functions and replace allocation/mmap with
mocks. They check ABI gating, large-lane resource identity, credit mapping,
mapping reuse, error cleanup and release. They do not issue hardware doorbells.
The kernel compile checker uses temporary objects and checks size splitting
both off and on; it does not install or reload modules.

After rebuilding both drivers, applications must load the updated provider
(including any rdma-core copy bundled in an MVAPICH2 installation). Only reload
the new kernel module after all jobs using the old module have exited. Hardware
correctness and performance still require a separate run; these tests do not
establish them.
