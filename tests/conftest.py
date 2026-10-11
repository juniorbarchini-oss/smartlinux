"""Shared fixtures: a fake sysfs tree for USB storage tests (no hardware needed).

Layout mirrors what was verified on a real DJI Fly Drive (0bc2:a020):
  /sys/block/sda/device -> .../usb2/2-1/2-1.2/2-1.2:1.0/host0/target0:0:0/0:0:0:0
  /sys/bus/usb/devices/2-1.2 (idVendor, idProduct, manufacturer, product)
  /sys/bus/usb/devices/2-1.2/2-1.2:1.0/driver -> .../drivers/uas
  /sys/module/usb_storage/parameters/quirks
"""
import importlib.machinery
import importlib.util
import os
import shutil

import pytest

HELPER_SRC = os.path.join(os.path.dirname(__file__), "..", "packaging", "common", "usb-mode-helper")


class FakeSysfs:
    def __init__(self, root, bus_id="2-1.2", vid="0bc2", pid="a020", driver="uas", block="sda",
                 manufacturer="Seagate", product="DJI Fly Drive", quirks=""):
        self.root = str(root)
        self.bus_id = bus_id
        self.usb_dir = os.path.join(self.root, "devices", "pci0000:00", "0000:00:14.0", "usb2", "2-1", bus_id)
        self.intf = os.path.join(self.usb_dir, f"{bus_id}:1.0")
        self.drivers = os.path.join(self.root, "bus", "usb", "drivers")
        os.makedirs(self.intf)
        for name, value in (("idVendor", vid), ("idProduct", pid),
                            ("manufacturer", manufacturer), ("product", product)):
            self._write(os.path.join(self.usb_dir, name), value + "\n")
        self._write(os.path.join(self.intf, "bInterfaceClass"), "08\n")
        for drv in ("uas", "usb-storage", "usb"):
            os.makedirs(os.path.join(self.drivers, drv), exist_ok=True)
        self._write(os.path.join(self.drivers, "usb", "bind"), "")
        self._write(os.path.join(self.drivers, "usb", "unbind"), "")
        devs = os.path.join(self.root, "bus", "usb", "devices")
        os.makedirs(devs)
        os.symlink(self.usb_dir, os.path.join(devs, bus_id))
        os.makedirs(os.path.join(self.root, "block"))
        self._write(self.quirks_path, quirks + "\n")
        self.block = None
        self.set_driver(driver)
        self.set_block(block)

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    @property
    def quirks_path(self):
        return os.path.join(self.root, "module", "usb_storage", "parameters", "quirks")

    def quirks(self):
        with open(self.quirks_path) as f:
            return f.read().strip()

    def set_driver(self, driver):
        link = os.path.join(self.intf, "driver")
        if os.path.islink(link):
            os.remove(link)
        if driver:
            os.symlink(os.path.join(self.drivers, driver), link)

    def driver(self):
        link = os.path.join(self.intf, "driver")
        return os.path.basename(os.readlink(link)) if os.path.islink(link) else None

    def set_block(self, name):
        """Simulates (re-)enumeration: the disk shows up as /dev/<name> (None = gone)."""
        scsi = os.path.join(self.intf, "host0", "target0:0:0", "0:0:0:0")
        if self.block:
            os.remove(os.path.join(self.root, "block", self.block))
            shutil.rmtree(os.path.join(scsi, "block", self.block))
        self.block = name
        if name:
            bdir = os.path.join(scsi, "block", name)
            os.makedirs(bdir)
            os.symlink(scsi, os.path.join(bdir, "device"))
            os.symlink(bdir, os.path.join(self.root, "block", name))

    def bound_requests(self):
        with open(os.path.join(self.drivers, "usb", "bind")) as f:
            return f.read()


@pytest.fixture
def fake_sysfs(tmp_path):
    def make(**kwargs):
        return FakeSysfs(tmp_path / "sys", **kwargs)
    return make


@pytest.fixture(scope="session")
def helper():
    loader = importlib.machinery.SourceFileLoader("usb_mode_helper", HELPER_SRC)
    spec = importlib.util.spec_from_loader("usb_mode_helper", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
