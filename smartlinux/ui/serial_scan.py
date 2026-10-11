"""Serial scan worker for "Scan controller", "Scan all disks" and a row's "↻".

Strictly one read at a time (never in parallel: no load spike on the controller or the SSH
connection): first the vendor tool read (once, if requested), then each disk through the app's
existing per-disk read. A disk that fails is reported and the scan goes on; Cancel stops cleanly
between two reads (a read already started is allowed to finish).
"""
from typing import Callable, List, Optional

from PySide6.QtCore import QThread, Signal

from ..core.models import DiskInfo


class SerialScanWorker(QThread):
    progress = Signal(str, int, int)      # text, steps done, total steps
    vendor_done = Signal(object)          # whatever vendor_job returned
    vendor_failed = Signal(str)
    disk_done = Signal(object, object)    # (disk as listed, updated DiskInfo)
    disk_failed = Signal(object, str)     # (disk as listed, error)
    scan_finished = Signal(bool)          # cancelled?

    def __init__(self, disks: List[DiskInfo], read_disk: Callable[[DiskInfo], DiskInfo],
                 vendor_job: Optional[Callable[[], object]] = None):
        super().__init__()
        self.disks = list(disks)
        self.read_disk = read_disk
        self.vendor_job = vendor_job
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self):
        total = len(self.disks) + (1 if self.vendor_job else 0)
        done = 0
        if self.vendor_job and not self._cancel:
            self.progress.emit("Reading the RAID state (vendor tool)…", done, total)
            try:
                self.vendor_done.emit(self.vendor_job())
            except Exception as e:  # the disks are still read with smartctl
                self.vendor_failed.emit(str(e) or e.__class__.__name__)
            done += 1
        n = len(self.disks)
        for i, disk in enumerate(self.disks, 1):
            if self._cancel:
                break
            self.progress.emit(f"Disk {i} of {n}", done, total)
            try:
                self.disk_done.emit(disk, self.read_disk(disk))
            except Exception as e:  # one disk never aborts the others
                self.disk_failed.emit(disk, str(e) or e.__class__.__name__)
            done += 1
        self.progress.emit("", done, total)
        self.scan_finished.emit(self._cancel)
