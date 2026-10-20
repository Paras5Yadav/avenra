# Avenra node AMIs

Packer recipes for the AMIs Avenra's `workers` and `builders` node groups run.
Both start from the AWS EKS-optimized AL2023 image for one Kubernetes minor and
add the host setup Avenra's node services expect to find:

| Script | What it sets up |
| --- | --- |
| `00-wait-cloud-init.sh`, `00-grow-root.sh` | Waits for first boot to settle and grows the root filesystem |
| `01-partitions.sh` | `/space` on the root volume, a swap file, and a btrfs loopback image at `/avenra/cache` |
| `02-users.sh`, `08-sudoers.sh`, `10-ssh-key.sh` | The `deploy` and `dev` users, their sudo rules, and an optional SSH key |
| `03-packages.sh` | A full package upgrade, base tools, and `btrfs-progs` from AL2023's supplementary SPAL repository; automatic updates off, since nodes are patched by replacing the image |
| `04-firecracker.sh` | Firecracker and the jailer from their GitHub release, and a guest kernel from Firecracker's CI bucket, baked into the existing execution roots `/avenra/vendor/{firecracker,kernels}`; node-manager creates the separate, initially empty managed store `/avenra/vendor/artifacts` |
| `06-e2fsprogs.sh` | e2fsprogs 1.47, built from source; AL2023's 1.46 can't handle the seed rootfs |
| `07-sysctl.sh` | Avenra's host users and groups (from `infra/avenra/identity/node-identities.sh`), the `/space` directory skeleton, the `/sys/fs/cgroup/space` cgroup parent, `/dev/kvm` and `tun` access, loop devices, and kernel settings. The artifact store `04-firecracker.sh` filled stays root's: the runtime manager stages installs under `/avenra/cache` and node-manager publishes them into the store; the runtime manager's state directory is `/avenra/run/runtime-manager`, as other node services' are |
| `09-auditd.sh` | Audit rules on the Avenra binaries and `/dev/kvm` |
| `11-nvme-cache.sh` | A boot-time service that formats instance-store NVMe as the btrfs `/avenra/cache`, replacing the loopback image. The node's user data runs the same script, so it holds a lock, and a run that finished leaves a marker that makes later runs that boot a no-op. After granting the cache to `avenra-cache-rw`, it returns the rehydrated kernel and asset trees to root-only and makes the two shared cache parents sticky |
| `12-nodeadm-config-retry.sh` | Retries `nodeadm-config.service` on a transient IMDS failure at boot |
| `99-cleanup.sh` | Key-only SSH and image cleanup |

Nothing is downloaded from a private source, and every artifact the recipes
download directly is checked against a pinned SHA-256: the Firecracker release
and guest kernel in `04-firecracker.sh`, the e2fsprogs tarball in
`06-e2fsprogs.sh`. The Firecracker and e2fsprogs pins match the checksums their
publishers list. Firecracker's CI bucket publishes none for guest kernels, so
the kernel's pin records the file as first downloaded. The guest kernel is the newest pinned one for its
Firecracker CI series, not the newest the bucket lists, so a new upstream
kernel reaches an image only once someone pins it. Avenra's own node services aren't
baked in; they run on the node as Kubernetes workloads.

## Baking

With AWS credentials for the target account, from the repository root:

```bash
infra/avenra/packer/aws/build.sh --kubernetes-version 1.36 --type worker
infra/avenra/packer/aws/build.sh --kubernetes-version 1.36 --type builder
```

- `--kubernetes-version` is required. It picks the EKS base image, and so the
  kubelet minor of every node launched from the result; bake at the minor of the
  oldest control plane the image will join.
- `--region` defaults to `us-east-1`. The AMI lands in the account and region
  the credentials and flag name.
- The build instance gets a public IP in the region's default VPC. Pass
  `-var subnet_id=...` and `-var security_group_id=...` (and
  `-var ssh_interface=private_ip -var associate_public_ip_address=false`) when
  there's no default VPC or the build has to stay private; `build.sh` passes
  arguments it doesn't recognize through to `packer build`.

Each image is named `avenra-<role>-<timestamp>` and tagged `Role`
(`worker`/`builder`), `KubernetesVersion`, `GitSHA`, `GitBranch` and `BuiltAt`.
Consumers select on those tags: `modules/space` by `GitSHA` (or a name pattern)
and `Role`, the reference cluster by `Role`, `KubernetesVersion` and
`GitBranch`. `build.sh` refuses to bake a commit that already has an image of
the same role at a different minor, because a lookup by `GitSHA` and `Role`
would then take whichever is newest.

`GitSHA` and `GitBranch` record the local checkout: its `HEAD` commit and branch
name, with no check that the tree is clean or up to date. A consumer that trusts
`GitBranch=main` should be baked from a clean checkout of the current `main`,
and a detached checkout is tagged `GitBranch=HEAD`.
