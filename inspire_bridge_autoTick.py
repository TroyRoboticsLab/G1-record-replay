#!/usr/bin/env python3
"""
Always-on Inspire Hand bridge:
- Brings up BOTH hands (right + left) automatically.
- Keeps retrying if a hand is offline at startup or drops later.
- One DDS init, two Modbus handlers under the hood (r / l).
- Default IPs: r=192.168.123.211, l=192.168.123.210 (edit below if needed).
"""

import time
import traceback

# Adjust these imports to your project layout if needed:
# If your ModbusDataHandler class lives elsewhere, change the import accordingly.
from inspire_sdkpy.inspire_hand_defaut import data_sheet
from inspire_sdkpy.inspire_sdk import ModbusDataHandler   # <-- change path if needed
from unitree_sdk2py.core.channel import ChannelFactoryInitialize

# === CONFIG ===
RIGHT = {"lr": "r", "ip": "192.168.123.211"}
LEFT  = {"lr": "l", "ip": "192.168.123.210"}

POLL_HZ = 50.0                # read/publish rate
RETRY_SECONDS = 3.0           # wait before retrying a failed connect
MODBUS_PORT = 6000
DEVICE_ID = 1
USE_SERIAL = False            # TCP by default; set True only if RS-485 (no touch topic)

# Optional: bind a specific NIC for DDS by setting this to e.g. "eth0".
# Leave as None for default behavior on a single machine.
DDS_NIC = None


class HandWorker:
    """
    Manages one hand (r or l): connect, read, and auto-reconnect.
    """
    def __init__(self, lr: str, ip: str):
        self.lr = lr
        self.ip = ip
        self.handler = None
        self._next_retry_at = 0.0

    def _create_handler(self):
        # We initialize DDS once globally, so initDDS=False here.
        return ModbusDataHandler(
            data=data_sheet,
            network=None,
            ip=self.ip,
            port=MODBUS_PORT,
            device_id=DEVICE_ID,
            LR=self.lr,
            use_serial=USE_SERIAL,
            initDDS=False,
        )

    def ensure_connected(self, now: float):
        """Try to (re)connect if we don't have a live handler yet."""
        if self.handler is not None:
            return

        if now < self._next_retry_at:
            return

        try:
            print(f"[bridge][{self.lr}] connecting to {self.ip}:{MODBUS_PORT} ...")
            self.handler = self._create_handler()
            print(f"[bridge][{self.lr}] connected.")
        except Exception as e:
            # Connection (or constructor) failed; schedule a retry
            self.handler = None
            self._next_retry_at = now + RETRY_SECONDS
            print(f"[bridge][{self.lr}] connect failed: {e}")
            # Uncomment for deep debug:
            # traceback.print_exc()

    def tick(self):
        """Poll and publish. If it throws, drop handler and retry later."""
        if self.handler is None:
            return
        try:
            self.handler.read()
        except Exception as e:
            print(f"[bridge][{self.lr}] read failed, will reconnect: {e}")
            # Uncomment for deep debug:
            # traceback.print_exc()
            self.handler = None
            # backoff will be set by ensure_connected() on next loop


def main():
    # Initialize Unitree DDS exactly once
    if DDS_NIC:
        ChannelFactoryInitialize(0, DDS_NIC)
        print(f"[bridge] DDS initialized on NIC '{DDS_NIC}'")
    else:
        ChannelFactoryInitialize(0)
        print("[bridge] DDS initialized (default NIC)")

    right = HandWorker(RIGHT["lr"], RIGHT["ip"])
    left  = HandWorker(LEFT["lr"],  LEFT["ip"])

    dt = 1.0 / max(POLL_HZ, 1.0)
    print(f"[bridge] autorun started: r@{RIGHT['ip']}  l@{LEFT['ip']}  rate={POLL_HZ} Hz  serial={USE_SERIAL}")

    try:
        while True:
            now = time.monotonic()
            right.ensure_connected(now)
            left.ensure_connected(now)

            # Poll whichever hands are currently connected
            right.tick()
            left.tick()

            time.sleep(dt)
    except KeyboardInterrupt:
        print("\n[bridge] stopped.")


if __name__ == "__main__":
    main()
