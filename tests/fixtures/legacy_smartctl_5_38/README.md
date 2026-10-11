# Legacy device fixtures (WD My Cloud EX4, smartctl 5.38)

Real output captured 2026-10-11 from a WD My Cloud EX4 NAS (Linux 3.2.40 armv5tel,
BusyBox 1.20, `smartctl 5.38`), disk WDC WD30EFRX-68EUZN0. Serial redacted.

What this device proves SmartLinux must handle:

- SSH offers only `ssh-dss` host keys (OpenSSH 7.5). paramiko >= 4 and OpenSSH >= 9.8
  cannot negotiate it; PuTTY `plink` can.
- `smartctl` is 5.38 (2008): **no `-j` (JSON), no `--scan`**. See
  `smartctl_json_unsupported.txt` / `smartctl_scan_unsupported.txt`.
  Use `-d sat -a|-H|-i|-l selftest|-l error` and parse the text.
- BusyBox: no `lsblk`, no `python3`, no `udevadm`, no `$((..))`, no `which`
  (use `command -v`). Disks come from `/proc/partitions` + `/sys/block/*/device`.
- `smartctl` lives at `/usr/local/modules/bin/smartctl` (also symlinked in `/usr/bin`).
- Login is root: no sudo needed.
