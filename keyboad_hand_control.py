#!/usr/bin/env python3
import sys, time, argparse
from typing import List

WINDOWS = sys.platform.startswith("win")
if WINDOWS:
    import msvcrt
else:
    import tty, termios, select

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelFactoryInitialize
from inspire_sdkpy import inspire_hand_defaut, inspire_dds

# Correct mapping: CLOSED at 200, OPEN at 800
SAFE_MIN = 200  # full close
SAFE_MAX = 800  # full open

def send_angles(pub: ChannelPublisher, angles: List[int], label: str):
    cmd = inspire_hand_defaut.get_inspire_hand_ctrl()
    cmd.mode = 0b0001  # angle mode (matches your example)
    cmd.angle_set = list(angles)
    ok = pub.Write(cmd)
    print(f"[{label}] angles={angles} -> {'OK' if ok else 'no sub'}")

def pose_open():
    # fully open
    return [SAFE_MAX] * 6

def pose_full_close():
    # fully closed for grasping
    return [SAFE_MIN] * 6

# def pose_half_close():
#     mid = (SAFE_MIN + SAFE_MAX) // 2  # ~500
#     return [mid] * 6

def pose_half_close():
    mid = 500 # ~500
    return [mid] * 6

def pose_pinch():
    # bias the last two joints to close (min) while others stay open
    arr = [SAFE_MAX] * 6
    arr[-3] = SAFE_MIN
    arr[-2] = SAFE_MIN
    arr[-1] = SAFE_MIN
    return arr

def thumbs_up():
    # bias the last two joints to close (min) while others stay open
    arr = [SAFE_MIN] * 6
    arr[-2] = SAFE_MAX
    arr[-1] = SAFE_MAX
    return arr

def pose_point():
    arr = [SAFE_MIN] * 6
    arr[-3] = SAFE_MAX
    return arr

def test1():
    arr = [800, 800, 800, 680, 640, 200]
    return arr

def test2():
    arr = [800, 800, 800, 800, 800, 200]
    return arr
def test3():
    arr = [800, 800, 800, 800, 800, 800]
    return arr
def test4():
    arr = [800, 800, 800, 800, 200, 800]
    return arr
def test5():
    arr = [800, 800, 800, 800, 800, 800]
    return arr

def get_key_nonblocking(timeout=0.05):
    if WINDOWS:
        start = time.time()
        while time.time() - start < timeout:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                try:
                    return ch.decode("utf-8").lower()
                except:
                    return ""
            time.sleep(0.005)
        return ""
    else:
        dr, _, _ = select.select([sys.stdin], [], [], timeout)
        if dr:
            return sys.stdin.read(1).lower()
        return ""

# def grasp_object(pub):
#     print("[GRASP] starting")

#     # Start open
#     current = [SAFE_MAX] * 6

#     # Gradually close
#     for step in range(SAFE_MAX, SAFE_MIN, -20):
#         angles = [step] * 6
#         send_angles(pub, angles, "GRASP_STEP")
#         time.sleep(0.05)

#     print("[GRASP] done")

def grasp_object(pub):
    for step in range(SAFE_MAX, SAFE_MIN, -20):
        angles = [step] * 6
        send_angles(pub, angles, "GRASP_STEP")
        time.sleep(0.05)
def main():
    ap = argparse.ArgumentParser(description="Keyboard controller for a single Inspire hand")
    ap.add_argument("--hand", choices=["r", "l"], required=True, help="Target hand: r or l")
    ap.add_argument("--nic", default=None, help='DDS NIC (e.g., "eth0"); omit for default')
    ap.add_argument("--repeat", type=int, default=5, help="Burst count per keypress")
    ap.add_argument("--hz", type=float, default=20.0, help="Burst rate (Hz)")
    args = ap.parse_args()

    if args.nic:
        ChannelFactoryInitialize(0, args.nic)
    else:
        ChannelFactoryInitialize(0)

    topic = f"rt/inspire_hand/ctrl/{args.hand}"
    pub = ChannelPublisher(topic, inspire_dds.inspire_hand_ctrl)
    pub.Init()
    print(f"[key] -> {topic} | keys: f=full close, o=open, h=half, p=pinch, 0=point q=quit")

    old_settings = None
    if not WINDOWS:
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

    try:
        while True:
            k = get_key_nonblocking(0.05)
            if not k:
                continue

            if k == "q":
                print("[key] quit")
                break

            if k == "f":
                pose = pose_full_close(); label = "FULL_CLOSE"
            elif k == "o":
                pose = pose_open();       label = "OPEN"
            elif k == "h":
                pose = pose_half_close(); label = "HALF_CLOSE"
            elif k == "p":
                pose = pose_pinch();      label = "PINCH"
            elif k == "0":
                pose = pose_point(); label = "POINT"
            elif k == "t":
                pose = thumbs_up(); label = "THUMBS_UP"
            elif k == "l":
                pose = test1(); label ="test1"
            elif k == "z":
                pose = test2(); label ="test2"

            elif k == "x":
                pose = test3(); label ="test3"
            elif k == "c":
                pose = test4(); label ="test4"
            elif k == "v":
                pose = test5(); label ="test5"
            
            elif k == "g":
                pose = grasp_object(pub);label ="grasp_object"
            

            else:
                continue

            dt = 1.0 / max(args.hz, 1.0)
            for _ in range(max(args.repeat, 1)):
                send_angles(pub, pose, label)
                time.sleep(dt)

    finally:
        if not WINDOWS and old_settings is not None:
            import termios as _t
            _t.tcsetattr(sys.stdin, _t.TCSADRAIN, old_settings)

if __name__ == "__main__":
    main()
