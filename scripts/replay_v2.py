#!/usr/bin/env python3
"""CLI: Replay arm trajectories with LEGS LOCKED + frame-triggered Inspire FTP hands.

This is the v2 replay path. It uses rt/arm_sdk so the onboard controller keeps
balancing while the SDK drives the recorded arm joints. Hand commands are fired
at specific frame numbers.

Usage::

    # Arms only (no hands)
    python scripts/replay_v2.py --network-interface eth0 \\
        --episode data/episodes/my_episode.h5 --speed 0.5

    # With hand commands at specific frames
    python scripts/replay_v2.py --network-interface eth0 \\
        --episode data/episodes/my_episode.h5 \\
        --hand-commands "0:open;120:right=close;300:right=open"

    # Hand schedule from JSON file
    python scripts/replay_v2.py --network-interface eth0 \\
        --episode data/episodes/my_episode.h5 \\
        --hand-commands config/hand_events_example.json

Controls during replay:
  P - Pause/resume
  Q - Quit safely
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from g1_record_replay.replay_v2 import run_replay_locked


def main():
    parser = argparse.ArgumentParser(
        description="Replay G1 arm trajectories (legs LOCKED) with optional hand events",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Replay at half speed (arms only, no hands)
  python scripts/replay_v2.py --network-interface eth0 \\
      --episode data/episodes/episode_001.h5 --speed 0.5

  # Replay with hand grasp at frame 120, release at frame 300
  python scripts/replay_v2.py --network-interface eth0 \\
      --episode data/episodes/episode_001.h5 \\
      --hand-commands "0:open;120:right=close;300:right=open"

  # Load hand schedule from JSON
  python scripts/replay_v2.py --network-interface eth0 \\
      --episode data/episodes/episode_001.h5 \\
      --hand-commands config/hand_events_example.json

  # Disable hands entirely (arms only)
  python scripts/replay_v2.py --network-interface eth0 \\
      --episode data/episodes/episode_001.h5 --no-hands

Hand command spec grammar (events separated by ';'):
  FRAME:POSE                  apply POSE to both hands
  FRAME:HAND=POSE             apply POSE to one hand (left/right)
  FRAME:HAND=POSE+HAND=POSE   multiple targets in one frame

  POSE can be a name (open, close, half, pinch) or six ints 0..1000:
    [pinky, ring, middle, index, thumb_bend, thumb_rotation]

Controls during replay:
  P - Pause/resume playback
  Q - Quit safely

NOTE: Robot must be standing in its normal balanced pose before replay starts.
      Legs/waist stay locked throughout (arm_sdk mode).
        """,
    )

    parser.add_argument(
        "--network-interface",
        type=str,
        default=None,
        help="Network interface connected to robot (e.g., eth0)",
    )
    parser.add_argument(
        "--episode",
        type=str,
        required=True,
        help="Path to episode HDF5 file to replay",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier (0.25 to 2.0, default: 1.0)",
    )
    parser.add_argument(
        "--hand-commands",
        type=str,
        default=None,
        help=(
            "Hand frame schedule: compact spec string or path to JSON file. "
            'Example: "0:open;120:right=close;300:right=open"'
        ),
    )
    parser.add_argument(
        "--no-hands",
        action="store_true",
        help="Disable Inspire FTP hand control (arms only)",
    )
    parser.add_argument(
        "--skip-safety",
        action="store_true",
        help="Skip safety confirmation prompt (NOT RECOMMENDED)",
    )

    args = parser.parse_args()

    episode_path = Path(args.episode)
    if not episode_path.exists():
        print(f"Error: Episode file not found: {args.episode}")
        sys.exit(1)

    run_replay_locked(
        network_interface=args.network_interface,
        episode_path=str(episode_path),
        playback_speed=args.speed,
        hand_commands=args.hand_commands,
        enable_hands=not args.no_hands,
        skip_safety=args.skip_safety,
    )


if __name__ == "__main__":
    main()
