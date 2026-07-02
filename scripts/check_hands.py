#!/usr/bin/env python3
"""CLI: Verify Inspire FTP hand connectivity over DDS (no robot arm motion).

This is a fast way to answer "why don't the hands move during replay?". It
initializes DDS on a chosen network interface, then:

  1. Probes rt/inspire_hand/ctrl/l|r for a matched subscriber (the hand driver).
  2. Optionally cycles open -> close -> open so you can see the hands move.

Because DDS ``ChannelFactory`` is a process-wide singleton, the hand driver
(Headless_driver / inspire_sdk) must be running on the SAME interface you pass
here. If the probe fails on one interface, try another (omit --network-interface
to auto-detect, which is what the keyboard test uses).

Usage::

    # Auto-detect interface (matches keyboad_hand_control.py default)
    python scripts/check_hands.py

    # Pin to the robot interface (matches replay_v2.py)
    python scripts/check_hands.py --network-interface enP7s7

    # Probe only, don't move the hands
    python scripts/check_hands.py --no-move

    # Move only one hand
    python scripts/check_hands.py --hand right
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _listen_state(args):
    """Subscribe to rt/inspire_hand/state/* and report received samples.

    The bridge (inspire_bridge_autoTick.py) publishes these at POLL_HZ. If we
    receive them here, cross-machine DDS from the G1 Jetson to this machine is
    working on the current interface -> the ctrl direction should work too.
    """
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from inspire_sdkpy import inspire_dds

    counters = {"l": 0, "r": 0}

    def make_handler(lr):
        def _cb(_msg):
            counters[lr] += 1
        return _cb

    hands_to_watch = ["l", "r"] if args.hand == "both" else [args.hand[0]]
    subs = []
    for lr in hands_to_watch:
        topic = f"rt/inspire_hand/state/{lr}"
        sub = ChannelSubscriber(topic, inspire_dds.inspire_hand_state)
        sub.Init(make_handler(lr), 10)
        subs.append(sub)
        print(f"Subscribed to {topic}")

    print(f"Listening {args.listen_seconds}s for state samples from the bridge...")
    import time as _t
    _t.sleep(args.listen_seconds)

    any_rx = False
    for lr in hands_to_watch:
        n = counters[lr]
        any_rx = any_rx or n > 0
        print(f"  {lr}: received {n} state samples")

    if any_rx:
        print(
            "\nCross-machine DDS is WORKING on this interface. The bridge is alive\n"
            "and reachable. If ctrl still doesn't move the hands, the issue is the\n"
            "ctrl message content or the bridge's Modbus link to the hand hardware."
        )
    else:
        print(
            "\nNo state samples received. Either the bridge isn't running, or DDS\n"
            "discovery between this machine and the G1 Jetson is not working on this\n"
            "interface. Try the other --network-interface, and confirm the bridge is\n"
            "running on the G1 Jetson."
        )


def main():
    parser = argparse.ArgumentParser(
        description="Check Inspire FTP hand DDS connectivity and optionally cycle open/close",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--network-interface",
        type=str,
        default=None,
        help="DDS network interface (e.g., enP7s7). Omit to auto-detect.",
    )
    parser.add_argument(
        "--hand",
        choices=["left", "right", "both"],
        default="both",
        help="Which hand(s) to move during the test (default: both)",
    )
    parser.add_argument(
        "--no-move",
        action="store_true",
        help="Only probe for the driver; do not move the hands",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for a matched subscriber during probe (default: 2.0)",
    )
    parser.add_argument(
        "--listen-state",
        action="store_true",
        help=(
            "Subscribe to rt/inspire_hand/state/* (what the bridge PUBLISHES) and "
            "report whether samples arrive. Best test of cross-machine DDS."
        ),
    )
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=3.0,
        help="How long to listen for state samples (default: 3.0)",
    )
    args = parser.parse_args()

    from unitree_sdk2py.core.channel import ChannelFactoryInitialize
    from g1_record_replay.hand_control import InspireFTPHands, POSES

    if args.network_interface:
        print(f"Initializing DDS on interface: {args.network_interface}")
        ChannelFactoryInitialize(0, args.network_interface)
    else:
        print("Initializing DDS with auto-detected interface")
        ChannelFactoryInitialize(0)

    if args.listen_state:
        _listen_state(args)
        return

    hands = InspireFTPHands(enabled=True)
    if not hands.available:
        print("ERROR: Could not create Inspire hand publishers (SDK/import problem).")
        sys.exit(2)

    print(f"Probing for hand driver (timeout={args.timeout}s)...")
    matched = hands.probe(timeout=args.timeout)
    print(f"  left  subscriber matched: {matched['left']}")
    print(f"  right subscriber matched: {matched['right']}")

    if not (matched["left"] or matched["right"]):
        print(
            "\nNo subscriber found. The hand driver is either not running, or it is\n"
            "on a DIFFERENT network interface than this process. Because DDS is a\n"
            "process-wide singleton, they must share the same interface.\n"
            "Try re-running with (or without) --network-interface to match the driver."
        )
        sys.exit(1)

    if args.no_move:
        print("\nDriver reachable. Skipping movement (--no-move).")
        return

    left = args.hand in ("left", "both")
    right = args.hand in ("right", "both")

    print("\nCycling hands: OPEN -> CLOSE -> OPEN (watch the fingers)...")
    for label, pose in [("OPEN", "open"), ("CLOSE", "close"), ("OPEN", "open")]:
        angles = POSES[pose]
        print(f"  -> {label} ({angles})")
        hands.send(
            left=angles if left else None,
            right=angles if right else None,
        )
        time.sleep(1.5)

    print("\nDone. If the fingers moved, replay_v2.py will work on this interface.")


if __name__ == "__main__":
    main()
