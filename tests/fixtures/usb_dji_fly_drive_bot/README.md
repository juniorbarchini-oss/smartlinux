# DJI Fly Drive (Seagate USB bridge, VID:PID 0bc2:a020) in Bulk-Only mode

Same physical drive as `../usb_dji_fly_drive/`, but captured after forcing `usb-storage`
instead of `uas` with the kernel quirk `0bc2:a020:u` (an ATA SSD, model IND-S325S001TX,
sits behind the bridge).

- In **UAS** mode the bridge answers only SCSI: `-d sat` fails ("unsupported field in
  scsi command"), `-d scsi`/`sat,auto` give health OK, model "Seagate DJI Fly Drive",
  serial `000000002HC015KJ`, temperature 0, no attributes.
- In **Bulk-Only** mode (this folder) plain `smartctl -a` and `-d sat` return the full ATA
  table (196 power-on hours, 36 C, 533 power cycles, 29 attributes), model
  `IND-S325S001TX`, serial `H0000000000000000` (redacted). The serial differs between modes.
- Switching took ~20 s (device is reset and re-enumerated; the /dev/sdX node may change).
