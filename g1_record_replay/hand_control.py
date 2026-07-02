"""Inspire FTP dexterous-hand control + frame-triggered scheduling.

The Inspire FTP hands are driven over their OWN DDS topics
(``rt/inspire_hand/ctrl/l`` and ``.../r``), completely independent from the G1
body (``rt/lowcmd`` / ``rt/arm_sdk``). That means we can layer hand commands on
top of an arm record/replay session without any conflict with the legs.

Two pieces live here:

* :class:`InspireFTPHands` - thin publisher wrapper around ``inspire_sdkpy``.
* :class:`HandSchedule`    - maps recorded frame numbers -> hand targets, so
  during replay you can e.g. "close the right hand at frame 120, open it again
  at frame 300".

IMPORTANT: create :class:`InspireFTPHands` *after* ``G1Interface.initialize()``
has run, because that call performs the one-and-only ``ChannelFactoryInitialize``
for the process. This module deliberately never initializes the DDS factory.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

# Angle command range for the Inspire FTP hand.
#
# NOTE: higher = MORE OPEN, lower = MORE CLOSED. The usable/safe range verified
# on this robot (see keyboad_hand_control.py) is 200 (closed) .. 800 (open).
# The hardware protocol accepts 0..1000, but we clamp commands to the safe band
# to avoid driving the fingers into their hard stops.
HAND_NUM_MOTORS = 6
ANGLE_MIN = 200  # fully closed (safe)
ANGLE_MAX = 800  # fully open (safe)
ANGLE_HARD_MIN = 0
ANGLE_HARD_MAX = 1000

# Finger order for each hand (matches xr_teleoperate / Unitree docs):
#   [pinky, ring, middle, index, thumb_bend, thumb_rotation]
FINGER_ORDER = ["pinky", "ring", "middle", "index", "thumb_bend", "thumb_rotation"]

# Named poses (angle_set values). Values match the tested keyboard controller:
#   open = 800, close = 200, half = 500.
POSES: Dict[str, List[int]] = {
    "open": [800, 800, 800, 800, 800, 800],
    "close": [200, 200, 200, 200, 200, 200],
    "half": [500, 500, 500, 500, 500, 500],
    # Pinch: index + thumb closed, other fingers open.
    "pinch": [800, 800, 800, 200, 200, 200],
    # Point: index finger extended, everything else closed.
    "point": [200, 200, 200, 800, 200, 200],
    # Thumbs up: thumb extended, fingers closed.
    "thumbs_up": [200, 200, 200, 200, 800, 800],
}

kTopicInspireFTPLeftCommand = "rt/inspire_hand/ctrl/l"
kTopicInspireFTPRightCommand = "rt/inspire_hand/ctrl/r"
kTopicInspireFTPLeftState = "rt/inspire_hand/state/l"
kTopicInspireFTPRightState = "rt/inspire_hand/state/r"


def _clamp_angles(angles) -> List[int]:
    """Coerce a 6-length iterable into clamped ints in the safe band [200, 800]."""
    vals = list(angles)
    if len(vals) != HAND_NUM_MOTORS:
        raise ValueError(f"Expected {HAND_NUM_MOTORS} hand angles, got {len(vals)}")
    return [int(max(ANGLE_MIN, min(ANGLE_MAX, round(float(v))))) for v in vals]


def resolve_pose(value) -> List[int]:
    """Resolve a pose name (e.g. 'close') or a 6-value list into angle_set ints."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in POSES:
            raise ValueError(f"Unknown hand pose '{value}'. Known: {sorted(POSES)}")
        return list(POSES[key])
    return _clamp_angles(value)


class InspireFTPHands:
    """Publisher for the Inspire FTP left/right hands.

    Safe to construct even if the SDK/hardware is missing: it simply becomes a
    no-op (``available == False``) so arm replay can still run.
    """

    def __init__(self, enabled: bool = True, verbose: bool = True,
                 repeat: int = 5, burst_dt: float = 0.02):
        self.enabled = enabled
        self.verbose = verbose
        # A single best-effort DDS write is often dropped if the hand driver
        # isn't polling at that instant, so every command is sent as a short
        # burst (matches the tested keyboad_hand_control.py: 5x @ ~20-50Hz).
        self.repeat = max(1, int(repeat))
        self.burst_dt = max(0.0, float(burst_dt))
        self.available = False
        self._left_pub = None
        self._right_pub = None
        self._inspire_hand_default = None

        if not enabled:
            if verbose:
                print("[hands] Disabled (arms-only run).")
            return

        try:
            from unitree_sdk2py.core.channel import ChannelPublisher
            from inspire_sdkpy import inspire_dds
            import inspire_sdkpy.inspire_hand_defaut as inspire_hand_default

            self._inspire_hand_default = inspire_hand_default

            self._left_pub = ChannelPublisher(kTopicInspireFTPLeftCommand, inspire_dds.inspire_hand_ctrl)
            self._left_pub.Init()
            self._right_pub = ChannelPublisher(kTopicInspireFTPRightCommand, inspire_dds.inspire_hand_ctrl)
            self._right_pub.Init()

            self.available = True
            if verbose:
                print("[hands] Inspire FTP hand publishers ready "
                      "(rt/inspire_hand/ctrl/l, rt/inspire_hand/ctrl/r).")
        except Exception as e:  # noqa: BLE001 - degrade gracefully
            self.available = False
            if verbose:
                print(f"[hands] Inspire FTP unavailable ({e}). Hand commands will be skipped.")

    def _write(self, publisher, angle_set: List[int]):
        msg = self._inspire_hand_default.get_inspire_hand_ctrl()
        msg.angle_set = list(angle_set)
        msg.mode = 0b0001  # angle control
        return publisher.Write(msg)

    def send(self, left: Optional[List[int]] = None, right: Optional[List[int]] = None,
             repeat: Optional[int] = None):
        """Send angle_set to the hands as a short burst.

        ``None`` leaves a hand unchanged. Angles are clamped to the safe
        band [200, 800]. The command is written ``repeat`` times (defaults to
        ``self.repeat``) to survive dropped best-effort DDS packets.
        """
        if not self.available:
            return
        left_a = _clamp_angles(left) if left is not None else None
        right_a = _clamp_angles(right) if right is not None else None
        n = self.repeat if repeat is None else max(1, int(repeat))
        for i in range(n):
            if left_a is not None:
                self._write(self._left_pub, left_a)
            if right_a is not None:
                self._write(self._right_pub, right_a)
            if i < n - 1 and self.burst_dt > 0:
                time.sleep(self.burst_dt)

    def probe(self, timeout: float = 1.5) -> Dict[str, bool]:
        """Check whether a subscriber (the hand driver) is matched on each ctrl topic.

        Uses ``Write(msg, timeout)`` which, unlike a plain ``Write``, waits for a
        matched reader and returns ``False`` if none appears. Returns e.g.
        ``{'left': True, 'right': False}``. When a side is matched it also sends
        an initial "open" command (the probe write is a real message).
        """
        result = {"left": False, "right": False}
        if not self.available:
            return result
        open_angles = list(POSES["open"])
        msg = self._inspire_hand_default.get_inspire_hand_ctrl()
        msg.angle_set = open_angles
        msg.mode = 0b0001
        result["left"] = bool(self._left_pub.Write(msg, timeout))
        msg2 = self._inspire_hand_default.get_inspire_hand_ctrl()
        msg2.angle_set = open_angles
        msg2.mode = 0b0001
        result["right"] = bool(self._right_pub.Write(msg2, timeout))
        return result

    def send_pose(self, pose: str, hand: str = "both"):
        """Send a named pose to 'left', 'right', or 'both' hands."""
        angles = resolve_pose(pose)
        left = angles if hand in ("both", "left") else None
        right = angles if hand in ("both", "right") else None
        self.send(left=left, right=right)

    def open_all(self):
        self.send(left=POSES["open"], right=POSES["open"])

    def close_all(self):
        self.send(left=POSES["close"], right=POSES["close"])


class HandSchedule:
    """Maps frame index -> hand targets, consumed in order during replay.

    Internally each event is ``frame -> (left_angles|None, right_angles|None)``.
    ``None`` for a hand means "leave it as-is at this frame".
    """

    def __init__(self, events: Dict[int, Tuple[Optional[List[int]], Optional[List[int]]]]):
        # Sorted list of (frame, left, right)
        self._events = [
            (int(f), events[f][0], events[f][1]) for f in sorted(events.keys())
        ]
        self._cursor = 0

    def __len__(self):
        return len(self._events)

    @property
    def events(self):
        return list(self._events)

    def reset(self):
        self._cursor = 0

    def due(self, frame_idx: int) -> List[Tuple[Optional[List[int]], Optional[List[int]]]]:
        """Return (and consume) all events with ``event_frame <= frame_idx``."""
        out = []
        while self._cursor < len(self._events) and self._events[self._cursor][0] <= frame_idx:
            _, left, right = self._events[self._cursor]
            out.append((left, right))
            self._cursor += 1
        return out

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------
    @classmethod
    def from_spec(cls, spec: str) -> "HandSchedule":
        """Parse a compact spec string.

        Grammar (events separated by ';', targets within an event by '+')::

            "10:open ; 120:right=close ; 300:right=open+left=close"

        A bare pose (e.g. ``120:close``) applies to BOTH hands. A prefixed
        target ``right=close`` / ``left=open`` applies to one hand.
        """
        events: Dict[int, Tuple[Optional[List[int]], Optional[List[int]]]] = {}
        if not spec:
            return cls(events)

        for raw_event in spec.split(";"):
            raw_event = raw_event.strip()
            if not raw_event:
                continue
            if ":" not in raw_event:
                raise ValueError(f"Bad hand event '{raw_event}' (expected 'FRAME:TARGET').")
            frame_str, targets_str = raw_event.split(":", 1)
            frame = int(frame_str.strip())

            left = right = None
            existing = events.get(frame)
            if existing:
                left, right = existing

            for target in targets_str.split("+"):
                target = target.strip()
                if not target:
                    continue
                if "=" in target:
                    hand, pose = target.split("=", 1)
                    hand = hand.strip().lower()
                    angles = resolve_pose(pose.strip())
                    if hand in ("l", "left"):
                        left = angles
                    elif hand in ("r", "right"):
                        right = angles
                    else:
                        raise ValueError(f"Unknown hand '{hand}' in '{target}'.")
                else:
                    angles = resolve_pose(target)
                    left = list(angles)
                    right = list(angles)

            events[frame] = (left, right)

        return cls(events)

    @classmethod
    def from_json(cls, path: str) -> "HandSchedule":
        """Load a schedule from JSON.

        Format::

            {
              "10":  {"left": "open",  "right": "open"},
              "120": {"right": "close"},
              "300": {"right": [800,800,800,800,800,800]}
            }
        """
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        events: Dict[int, Tuple[Optional[List[int]], Optional[List[int]]]] = {}
        for frame_str, spec in raw.items():
            if str(frame_str).startswith("_"):
                continue
            frame = int(frame_str)
            left = resolve_pose(spec["left"]) if "left" in spec and spec["left"] is not None else None
            right = resolve_pose(spec["right"]) if "right" in spec and spec["right"] is not None else None
            events[frame] = (left, right)
        return cls(events)

    @classmethod
    def parse(cls, value: Optional[str]) -> "HandSchedule":
        """Convenience: treat ``value`` as a JSON file path if it exists, else a spec."""
        if not value:
            return cls({})
        if os.path.isfile(value) or value.strip().endswith(".json"):
            return cls.from_json(value)
        return cls.from_spec(value)
