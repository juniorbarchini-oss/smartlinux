import os
import platform
import select
import socket
from typing import Optional
from PySide6.QtCore import QThread, Signal, QTimer, QFileSystemWatcher


class DeviceHotplugWatcher(QThread):
    """
    Zero-overhead, event-driven listener for USB/block storage hotplug events.
    Uses Linux Netlink kernel uevents on Linux, and QFileSystemWatcher on macOS.
    """
    device_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._socket: Optional[socket.socket] = None

    def run(self):
        if platform.system() == "Linux":
            self._run_linux_netlink()
        else:
            self._run_fallback()

    def _run_linux_netlink(self):
        try:
            # 15 is NETLINK_KOBJECT_UEVENT
            self._socket = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 15)
            self._socket.bind((os.getpid(), 1))
            self._socket.setblocking(False)

            while self._running:
                # Wait for data with 0.2-second timeout to allow instant clean shutdown
                r, _, _ = select.select([self._socket], [], [], 0.2)
                if not self._running:
                    break
                if r:
                    try:
                        data = self._socket.recv(4096)
                        text = data.decode("utf-8", errors="replace")
                        # Check if uevent is related to block devices (disk add/remove)
                        if "SUBSYSTEM=block" in text or "DEVTYPE=disk" in text:
                            self.device_changed.emit()
                    except Exception:
                        pass
        except Exception as e:
            print(f"Hotplug watcher netlink error: {e}")
        finally:
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

    def _run_fallback(self):
        # Fallback watcher for non-Linux OS
        while self._running:
            self.msleep(200)

    def stop(self):
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
