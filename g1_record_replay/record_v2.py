"""Record Mode v2 - kinesthetic recording with the LEGS LOCKED.

Difference from the original :mod:`g1_record_replay.record`:

The original path used ``use_motion_switcher=True`` which internally calls
``MotionSwitcher.ReleaseMode()``. That drops the robot into debug mode and the
legs go LIMP. This v2 path uses ``use_arm_sdk=True`` instead: the onboard
controller keeps balancing (legs/waist stay LOCKED) while only the arm joints
are made passive so you can move them by hand to teach a trajectory.

Recorded episodes are 100% compatible with the original replay/visualize tools;
the arm joint positions are stored exactly the same way.
"""

import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .core import G1Interface, DataManager, get_joint_indices
from .record import Recorder


def _safety_panel(console: Console, joint_group: str) -> bool:
    console.print(Panel.fit(
        "[bold cyan]RECORD (LEGS LOCKED) SAFETY CHECK[/bold cyan]\n\n"
        f"About to record: [bold cyan]{joint_group}[/bold cyan] via [bold]rt/arm_sdk[/bold]\n\n"
        "[yellow]This will:[/yellow]\n"
        "  1. Keep the onboard controller running (legs + waist stay LOCKED / balancing)\n"
        f"  2. Make the [bold]{joint_group}[/bold] joints passive (free to move by hand)\n"
        "  3. Record joint positions as you guide the arms\n\n"
        "[bold]Requirements:[/bold]\n"
        "  - Robot is already standing in its normal balanced pose\n"
        "  - The robot is NOT in debug mode\n"
        "  - Area around the arms is clear\n"
        "  - Keep the E-stop within reach",
        title="RECORD SAFETY (arm_sdk)",
        border_style="cyan",
    ))
    return Confirm.ask("\n[bold]Ready to start recording?[/bold]", default=True)


def run_recording_locked(network_interface: Optional[str] = None,
                         frequency: float = 50.0,
                         episode_name: Optional[str] = None,
                         joint_group: str = "arms",
                         show_positions: bool = False,
                         skip_safety: bool = False):
    """
    Run recording with legs locked (arm_sdk).

    Args:
        network_interface: Network interface name (e.g., 'eth0', 'enp2s0')
        frequency: Recording frequency in Hz
        episode_name: Name/description for the episode
        joint_group: Which joints to record. Should be 'arms' (recommended) or
                     'waist'. 'legs'/'all' are refused because arm_sdk must not
                     fight the balancing controller for the legs.
        show_positions: Show live joint positions while recording
        skip_safety: Skip the confirmation prompt (NOT RECOMMENDED)
    """
    console = Console()

    if joint_group in ("legs", "all"):
        console.print(
            f"[bold red]Refusing to record joint_group='{joint_group}' in arm_sdk mode.[/bold red]\n"
            "[yellow]arm_sdk keeps the legs locked by the onboard controller; it must not "
            "command the legs. Use --joint-group arms (or waist).[/yellow]"
        )
        return

    if not skip_safety:
        if not _safety_panel(console, joint_group):
            console.print("[yellow]Recording cancelled.[/yellow]")
            return

    interface = None
    try:
        # arm_sdk => legs/waist stay locked, arms become passive.
        interface = G1Interface(network_interface, use_arm_sdk=True)
        interface.initialize()

        # Capture the standing posture to hold the non-arm joints at, then engage
        # the arm_sdk blend weight so our commands take effect.
        interface.capture_hold_positions()
        interface.engage_arm_sdk()
        console.print("[green]Legs/waist locked at current posture (arm_sdk weight = 1.0).[/green]")

        data_manager = DataManager()
        recorder = Recorder(interface, data_manager, frequency, episode_name,
                            joint_group, show_positions)
        recorder.run()

        # Tell the user the frame count so they can schedule hand events on replay.
        num_frames = len(recorder.timestamps)
        if num_frames > 0:
            console.print(
                f"\n[bold cyan]Recorded {num_frames} frames.[/bold cyan] "
                "Use frame numbers with replay's --hand-commands, e.g. "
                f"[green]\"{num_frames // 4}:right=close;{(num_frames * 3) // 4}:right=open\"[/green]"
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Error: {e}[/bold red]")
        import traceback
        traceback.print_exc()
    finally:
        if interface is not None:
            interface.shutdown()
