# PERCCLI (Dell PERC H730 Mini) on a Dell PowerEdge R630

Real read-only output of `perccli64` 007.1910.0000.0000 (Dell/Broadcom), captured 2026-10-11 on a
Proxmox host. Sanitized (drive/controller serials, SAS addresses, service tag).
Companion of `../dell_r630_perc_h730/` (what `smartctl`/`lsblk` see on the same machine).

Commands (all read-only, JSON via the trailing `J`):

| File | Command |
|---|---|
| `version.txt` | `perccli64 -v` |
| `show_ctrlcount.json` | `perccli64 show ctrlcount J` |
| `c0_show.json` | `/c0 show J` (identity, VD LIST, PD LIST, TOPOLOGY, BBU_Info) |
| `c0_show_all.json` | `/c0 show all J` |
| `c0_vall_show_all.json` | `/c0/vall show all J` (per VD: PDs, properties, `OS Drive Name`) |
| `c0_eall_sall_show_all.json` | `/c0/eall/sall show all J` (per drive: state, Media/Other Error Count, Predictive Failure Count, SMART alert) |
| `c0_bbu_show_all.json` | `/c0/bbu show all J` (battery) |
| `c0_cv_show_all.json` | `/c0/cv show all J` -> "Cachevault doesn't exist": NOT an error, the controller has a BBU |
| `c0_dall_show.json` | `/c0/dall show J` (drive groups / topology) |

What this machine has: 1 controller (PERC H730 Mini, FW 4.260.00-7126), 4 virtual drives and
5 physical SAS drives. VD0 `SO` = RAID1 of slots 0+1 (`/dev/sda`); VD1..3 `VMDATA1..3` = RAID0 of a
SINGLE drive each (slots 2, 3, 5 -> `/dev/sdb`, `/dev/sdc`, `/dev/sdd`): no redundancy.
`perccli64`'s DID equals the N in `smartctl -d megaraid,N`.
