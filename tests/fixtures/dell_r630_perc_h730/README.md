# Dell PowerEdge R630 with a PERC H730 Mini (LSI MegaRAID SAS-3 3108)

Real read-only output captured 2026-10-11 from a Proxmox VE host (`pve-r630`), sanitized
(service tag, UUID and drive serials replaced).

What it proves SmartLinux must handle:

- `lsblk` / `smartctl` on the OS see only **4 virtual drives** (`/dev/sda`..`sdd`, model
  "PERC H730 Mini"); `smartctl -i /dev/sda` answers "DELL or MegaRaid controller, please try
  adding '-d megaraid,N'".
- The **physical disks** (5 x Seagate ST1200MM0088, SAS 10k rpm) show up in `smartctl --scan`
  as `/dev/bus/0 -d megaraid,N`, N = 0,1,2,3,5 (4 is an empty slot: "INQUIRY failed").
  Several entries share the same path `/dev/bus/0`: the device type is what tells them apart.
- Each physical disk answers `smartctl -j -a -d megaraid,N /dev/bus/0` with SCSI/SAS data
  (health status, temperature, power-on time, grown defect list, error counter log,
  self-test log), no ATA attribute table.
- Server identity: `/sys/class/dmi/id/*` (readable) and `dmidecode` (root). Controller: `lspci -vmm`,
  `/sys/class/scsi_host/host*/proc_name` (`megaraid_sas`), `modinfo megaraid_sas`.
