#!/usr/bin/env python3
"""CLI: Record arm trajectories with LEGS LOCKED (arm_sdk mode).

This is the v2 record path. It uses rt/arm_sdk so the onboard controller keeps
balancing while only the arms go passive for kinesthetic teaching.

Usage::

    python scripts/record_v2.py --network-interface eth0 --name "reach_to_object"
    python scripts/record_v2.py --network-interface eth0 --name "pick" --show-positions

Controls during recording:
  S - Stop and save
  C - Cancel without saving
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from g1_record_replay.record_v2 import run_recording_locked


def main():
    parser = argparse.ArgumentParser(
        description="Record G1 arm trajectories (legs LOCKED via arm_sdk)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Record arms only (legs stay locked, arms free to move by hand)
  python scripts/record_v2.py --network-interface eth0 --name "reach_motion"

  # With live joint position display
  python scripts/record_v2.py --network-interface eth0 --name "pick" --show-positions

  # Record at 30 Hz
  python scripts/record_v2.py --network-interface eth0 --name "slow_reach" --frequency 30

Controls during recording:
  S - Stop and save recording
  C - Cancel without saving

NOTE: This uses rt/arm_sdk. The robot must already be standing in its normal
balanced pose (NOT in debug mode). Legs/waist stay locked throughout.
        """,
    )

    parser.add_argument(
        "--network-interface",
        type=str,
        default=None,
        help="Network interface connected to robot (e.g., eth0)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Name/description for the episode",
    )
    parser.add_argument(
        "--frequency",
        type=float,
        default=50.0,
        help="Recording frequency in Hz (default: 50.0)",
    )
    parser.add_argument(
        "--joint-group",
        type=str,
        choices=["arms", "waist"],
        default="arms",
        help="Which joints to record (default: arms). legs/all are not allowed in arm_sdk mode.",
    )
    parser.add_argument(
        "--show-positions",
        action="store_true",
        help="Display current joint positions in real-time during recording",
    )
    parser.add_argument(
        "--skip-safety",
        action="store_true",
        help="Skip safety confirmation prompt (NOT RECOMMENDED)",
    )

    args = parser.parse_args()

    run_recording_locked(
        network_interface=args.network_interface,
        frequency=args.frequency,
        episode_name=args.name,
        joint_group=args.joint_group,
        show_positions=args.show_positions,
        skip_safety=args.skip_safety,
    )


if __name__ == "__main__":
    main()
