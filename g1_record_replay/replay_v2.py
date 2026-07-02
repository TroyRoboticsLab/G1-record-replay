"""Replay Mode v2 - replay with LEGS LOCKED + frame-triggered Inspire FTP hands.

Difference from the original :mod:`g1_record_replay.replay`:

* Uses ``use_arm_sdk=True`` so the onboard controller keeps balancing (legs/waist
  stay LOCKED) while the SDK drives only the recorded arm joints.
* Optionally fires Inspire FTP hand commands at specific frame numbers via a
  :class:`~g1_record_replay.hand_control.HandSchedule`.
"""

import sys
import select
import time
from typing import Optional

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.prompt import Confirm

from .core import G1Interface, DataManager, get_joint_indices
from .hand_control import HandSchedule, InspireFTPHands


class ReplayerV2:
    """Replay with arm_sdk (legs locked) and optional hand frame events."""

    def __init__(self, interface: G1Interface, data_manager: DataManager,
                 episode_path: str, playback_speed: float = 1.0,
                 transition_duration: float = 3.0,
                 hands: Optional[InspireFTPHands] = None,
                 hand_schedule: Optional[HandSchedule] = None):
        self.interface = interface
        self.data_manager = data_manager
        self.episode_path = episode_path
        self.playback_speed = float(np.clip(playback_speed, 0.25, 2.0))
        self.transition_duration = transition_duration
        self.hands = hands
        self.hand_schedule = hand_schedule or HandSchedule({})
        self.console = Console()

        self.console.print(f"[cyan]Loading episode: {episode_path}[/cyan]")
        self.episode_data = self.data_manager.load_episode(episode_path)

        self.joint_positions = self.episode_data["joint_positions"]
        self.timestamps = self.episode_data["timestamps"]
        self.metadata = self.episode_data["metadata"]

        self.joint_group = self.metadata.get("joint_group", "arms")
        self.joint_indices = self.metadata.get("joint_indices", None)
        if self.joint_indices is None:
            self.joint_indices = get_joint_indices(self.joint_group)

        self.running = False
        self.paused = False
        self.start_time = None
        self.pause_time = 0.0
        self.accumulated_pause_time = 0.0

        self._print_episode_info()

    def _print_episode_info(self):
        self.console.print("\n[bold cyan]Episode Information:[/bold cyan]")
        self.console.print(f"  Episode ID: {self.metadata.get('episode_id', 'unknown')}")
        self.console.print(f"  Joint group: {self.joint_group} ({len(self.joint_indices)} joints)")
        self.console.print(f"  Frames: {self.metadata.get('num_frames', len(self.timestamps))}")
        self.console.print(f"  Duration: {self.metadata.get('duration', 0):.2f}s")
        self.console.print(f"  Frequency: {self.metadata.get('frequency', 0):.1f}Hz")
        if "description" in self.metadata:
            self.console.print(f"  Description: {self.metadata['description']}")
        self.console.print(f"  Playback speed: {self.playback_speed}x")
        self.console.print("  Control mode: [bold green]arm_sdk (legs LOCKED)[/bold green]")
        if len(self.hand_schedule) > 0:
            self.console.print(f"  Hand events: {len(self.hand_schedule)}")
            for frame, left, right in self.hand_schedule.events:
                left_s = "left" if left is not None else "-"
                right_s = "right" if right is not None else "-"
                self.console.print(f"    frame {frame:4d}: {left_s} / {right_s}")
        self.console.print()

    def _check_keyboard_input(self) -> Optional[str]:
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1).lower()
        return None

    def _smooth_transition(self, start_pos: np.ndarray, target_pos: np.ndarray):
        self.console.print(
            f"[yellow]Transitioning to start position ({self.transition_duration}s)...[/yellow]"
        )
        start_time = time.time()
        while time.time() - start_time < self.transition_duration:
            elapsed = time.time() - start_time
            ratio = elapsed / self.transition_duration
            smooth_ratio = (1 - np.cos(ratio * np.pi)) / 2
            current_target = start_pos + (target_pos - start_pos) * smooth_ratio
            self.interface.send_joint_commands(current_target, joint_indices=self.joint_indices)
            time.sleep(0.002)
        self.interface.send_joint_commands(target_pos, joint_indices=self.joint_indices)
        self.console.print("[green]Transition complete[/green]")

    def _get_target_position(self, playback_time: float) -> Optional[np.ndarray]:
        if playback_time < 0:
            return self.joint_positions[0]
        if playback_time > self.timestamps[-1]:
            return None

        idx = np.searchsorted(self.timestamps, playback_time)
        if idx == 0:
            return self.joint_positions[0]
        if idx >= len(self.timestamps):
            return self.joint_positions[-1]

        t0 = self.timestamps[idx - 1]
        t1 = self.timestamps[idx]
        pos0 = self.joint_positions[idx - 1]
        pos1 = self.joint_positions[idx]
        alpha = (playback_time - t0) / (t1 - t0) if t1 > t0 else 0.0
        return pos0 + (pos1 - pos0) * alpha

    def _playback_frame_index(self, playback_time: float) -> int:
        """Discrete frame index for hand scheduling (0-based)."""
        if playback_time <= 0:
            return 0
        idx = int(np.searchsorted(self.timestamps, playback_time, side="right") - 1)
        return int(np.clip(idx, 0, len(self.timestamps) - 1))

    def _fire_hand_events(self, frame_idx: int):
        if self.hands is None or not self.hands.available:
            return
        for left, right in self.hand_schedule.due(frame_idx):
            self.hands.send(left=left, right=right)
            if left is not None or right is not None:
                self.console.print(f"[magenta]  hand @ frame {frame_idx}[/magenta]")

    def run(self):
        self.console.print("[bold red]WARNING: Robot will move. Ensure area is clear![/bold red]")
        response = input("Type 'yes' to continue: ")
        if response.lower() != "yes":
            self.console.print("[yellow]Replay cancelled[/yellow]")
            return

        self.console.print("\n[bold green]Starting replay (legs locked via arm_sdk)...[/bold green]")

        state = self.interface.get_joint_state()
        if state is None:
            self.console.print("[red]Failed to get robot state![/red]")
            return

        current_pos = state.positions
        target_pos = self.joint_positions[0]

        # Open hands before we start moving (if available).
        if self.hands is not None and self.hands.available:
            self.hands.open_all()
            time.sleep(0.3)

        self._smooth_transition(current_pos, target_pos)

        # Fire any hand events scheduled at frame 0.
        self.hand_schedule.reset()
        self._fire_hand_events(0)

        self.console.print("[bold cyan]Starting playback...[/bold cyan]")
        self.console.print("[bold]Press 'P' to pause/resume, 'Q' to quit[/bold]\n")

        self.running = True
        self.paused = False
        self.start_time = time.time()
        self.accumulated_pause_time = 0.0
        target_pos = None

        import tty
        import termios
        old_settings = termios.tcgetattr(sys.stdin)

        try:
            tty.setcbreak(sys.stdin.fileno())

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=self.console,
            ) as progress:
                task = progress.add_task("[cyan]Replaying...", total=100)

                while self.running:
                    current_time = time.time()

                    if self.paused:
                        if self.pause_time == 0:
                            self.pause_time = current_time
                        time.sleep(0.01)
                        key = self._check_keyboard_input()
                        if key == "p":
                            self.accumulated_pause_time += current_time - self.pause_time
                            self.pause_time = 0
                            self.paused = False
                            self.console.print("[green]Resumed[/green]")
                        elif key == "q":
                            self.console.print("\n[yellow]Quitting...[/yellow]")
                            self.running = False
                        continue

                    elapsed = current_time - self.start_time - self.accumulated_pause_time
                    playback_time = elapsed * self.playback_speed
                    frame_idx = self._playback_frame_index(playback_time)

                    target_pos = self._get_target_position(playback_time)
                    if target_pos is None:
                        self.console.print("\n[bold green]Playback complete![/bold green]")
                        self.running = False
                        break

                    self.interface.send_joint_commands(target_pos, joint_indices=self.joint_indices)
                    self._fire_hand_events(frame_idx)

                    progress_pct = min(100, (playback_time / self.timestamps[-1]) * 100)
                    progress.update(
                        task,
                        completed=progress_pct,
                        description=f"[cyan]Replaying... Frame: {frame_idx}/{len(self.timestamps) - 1}",
                    )

                    key = self._check_keyboard_input()
                    if key == "p":
                        self.paused = True
                        self.console.print("[yellow]Paused[/yellow]")
                    elif key == "q":
                        self.console.print("\n[yellow]Quitting...[/yellow]")
                        self.running = False

                    time.sleep(0.002)

        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

            if target_pos is not None:
                for _ in range(100):
                    self.interface.send_joint_commands(target_pos, joint_indices=self.joint_indices)
                    time.sleep(0.002)

            self.console.print("\n[bold green]Replay mode ended[/bold green]")


def _safety_panel(console: Console, joint_group: str) -> bool:
    console.print(Panel.fit(
        "[bold red]REPLAY (LEGS LOCKED) SAFETY CHECK[/bold red]\n\n"
        f"About to replay: [bold cyan]{joint_group}[/bold cyan] via [bold]rt/arm_sdk[/bold]\n\n"
        "[yellow]This will:[/yellow]\n"
        "  1. Keep the onboard controller running (legs + waist stay LOCKED)\n"
        f"  2. Drive the [bold]{joint_group}[/bold] joints along the recorded trajectory\n"
        "  3. Optionally fire Inspire FTP hand commands at scheduled frames\n\n"
        "[bold]Requirements:[/bold]\n"
        "  - Robot is standing in its normal balanced pose\n"
        "  - Area around the robot is clear\n"
        "  - E-stop is within reach",
        title="REPLAY SAFETY (arm_sdk)",
        border_style="red",
    ))
    return Confirm.ask("\n[bold]Proceed with replay?[/bold]", default=False)


def run_replay_locked(network_interface: Optional[str] = None,
                      episode_path: str = None,
                      playback_speed: float = 1.0,
                      hand_commands: Optional[str] = None,
                      enable_hands: bool = True,
                      skip_safety: bool = False):
    """
    Run replay with legs locked (arm_sdk) and optional hand frame events.

    Args:
        network_interface: Network interface name
        episode_path: Path to episode HDF5 file
        playback_speed: Speed multiplier (0.25 - 2.0)
        hand_commands: Hand schedule. Either a compact spec string or a JSON file
                       path. Examples::

                           "0:open;120:right=close;300:right=open"
                           config/hand_events_pick.json

        enable_hands: If False, skip Inspire FTP hand control entirely.
        skip_safety: Skip the confirmation prompt (NOT RECOMMENDED)
    """
    console = Console()

    if not episode_path:
        console.print("[red]Error: Episode path is required[/red]")
        return

    try:
        dm = DataManager()
        episode_data = dm.load_episode(episode_path)
        joint_group = episode_data["metadata"].get("joint_group", "arms")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Error loading episode: {e}[/red]")
        return

    if not skip_safety:
        if not _safety_panel(console, joint_group):
            console.print("[yellow]Replay aborted.[/yellow]")
            return
    else:
        console.print("[bold red]SAFETY CHECKS SKIPPED[/bold red]")
        time.sleep(1)

    hand_schedule = HandSchedule.parse(hand_commands)

    interface = None
    try:
        interface = G1Interface(network_interface, use_arm_sdk=True)
        interface.initialize()
        interface.capture_hold_positions()
        interface.engage_arm_sdk()
        console.print("[green]Legs/waist locked (arm_sdk weight = 1.0).[/green]")

        # Create the hand publishers AFTER interface.initialize() has run
        # ChannelFactoryInitialize; building DDS publishers before the factory
        # exists raises "'NoneType' object has no attribute '_ref'".
        hands = InspireFTPHands(enabled=enable_hands)

        # Verify the Inspire hand driver is actually subscribed on THIS DDS
        # session (same domain + network interface). A plain Write() always
        # "succeeds", so we probe for a matched subscriber explicitly.
        if enable_hands and hands.available:
            console.print("[cyan]Probing Inspire hand driver (rt/inspire_hand/ctrl/*)...[/cyan]")
            matched = hands.probe(timeout=1.5)
            if matched["left"] or matched["right"]:
                console.print(
                    f"[green]Hand driver detected -> left={matched['left']} "
                    f"right={matched['right']}.[/green]"
                )
            else:
                console.print(Panel.fit(
                    "[bold red]No Inspire hand driver subscriber found on "
                    "rt/inspire_hand/ctrl/l|r.[/bold red]\n\n"
                    "Hand commands will be published but NOTHING will move.\n\n"
                    "[yellow]Most likely causes:[/yellow]\n"
                    "  1. The hand driver (Headless_driver / inspire_sdk) is not running.\n"
                    f"  2. The driver is on a different DDS network interface than\n"
                    f"     [bold]{network_interface or 'auto'}[/bold]. This process is pinned to\n"
                    "     that interface (ChannelFactory is a process-wide singleton),\n"
                    "     so the driver must use the SAME interface.\n\n"
                    "[bold]Fix:[/bold] start the hand driver bound to the same interface,\n"
                    f"e.g. export CYCLONEDDS to use [bold]{network_interface or 'the robot NIC'}[/bold],\n"
                    "then re-run. (Your keyboard test worked because it and the driver\n"
                    "both used auto-detect and landed on the same NIC.)",
                    title="HAND DRIVER NOT REACHABLE",
                    border_style="red",
                ))

        data_manager = DataManager()
        replayer = ReplayerV2(
            interface, data_manager, episode_path, playback_speed,
            hands=hands, hand_schedule=hand_schedule,
        )
        replayer.run()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Error: {e}[/bold red]")
        import traceback
        traceback.print_exc()
    finally:
        if interface is not None:
            interface.shutdown()
