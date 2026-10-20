import ctypes
import json
import math
import queue
import sys
import threading
import time
from pathlib import Path

from pynput import mouse

CONFIG_PATH = Path(__file__).with_name("config.json")
FRAME_RATE = 1000
FRAME_TIME = 1.0 / FRAME_RATE

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
LLKHF_INJECTED = 0x10
KEY_DOWN_MESSAGES = {WM_KEYDOWN, WM_SYSKEYDOWN}
KEY_UP_MESSAGES = {WM_KEYUP, WM_SYSKEYUP}

PUNCTUATION_VK = {
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
    "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
NAMED_VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
    "space": 0x20, "page_up": 0x21, "page_down": 0x22, "end": 0x23,
    "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "caps_lock": 0x14,
    "cmd": 0x5B, "cmd_l": 0x5B, "cmd_r": 0x5C, "menu": 0x5D,
    "shift_l": 0xA0, "shift_r": 0xA1, "ctrl_l": 0xA2, "ctrl_r": 0xA3,
    "alt_l": 0xA4, "alt_r": 0xA5,
}
for _n in range(1, 25):
    NAMED_VK[f"f{_n}"] = 0x70 + _n - 1

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(
        LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    ]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT, wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def resolve_vk(name):
    """Convert a config key name ('i', ';', 'caps_lock', 'esc') to a
    Windows virtual-key code."""
    if len(name) == 1:
        if name.isascii() and name.isalnum():
            return ord(name.upper())
        if name in PUNCTUATION_VK:
            return PUNCTUATION_VK[name]
        raise ValueError(f"Unsupported key: '{name}'")
    if name in NAMED_VK:
        return NAMED_VK[name]
    raise ValueError(f"Unknown key name: '{name}'")

class KeyboardHook(threading.Thread):
    """Runs a WH_KEYBOARD_LL hook. `handler(name, is_down)` returns True to
    block the key so no other application receives it."""

    def __init__(self, vk_to_name, handler):
        super().__init__(daemon=True)
        self.vk_to_name = vk_to_name
        self.handler = handler
        self.ready = threading.Event()
        self.error = None
        self.thread_id = None
        self._proc = None

    def _callback(self, n_code, w_param, l_param):
        if n_code == HC_ACTION:
            data = ctypes.cast(
                l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
            ).contents
            if not data.flags & LLKHF_INJECTED:
                name = self.vk_to_name.get(data.vkCode)
                if name is not None:
                    if w_param in KEY_DOWN_MESSAGES:
                        is_down = True
                    elif w_param in KEY_UP_MESSAGES:
                        is_down = False
                    else:
                        is_down = None
                    if is_down is not None:
                        try:
                            if self.handler(name, is_down):
                                return 1
                        except Exception as error:
                            print(f"Key handler error: {error}")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def run(self):
        hook = None
        try:
            self.thread_id = kernel32.GetCurrentThreadId()
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            self._proc = HOOKPROC(self._callback)
            hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._proc,
                kernel32.GetModuleHandleW(None), 0,
            )
            if not hook:
                self.error = ctypes.WinError(ctypes.get_last_error())
                return
            self.ready.set()

            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    self.error = ctypes.WinError(ctypes.get_last_error())
                    break
        except Exception as error:
            self.error = error
        finally:
            self.ready.set()
            if hook:
                user32.UnhookWindowsHookEx(hook)

    def stop(self):
        if self.thread_id:
            if not user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0):
                return

def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    required = {
        "activation_key", "emergency_stop_key", "movement_keys",
        "speed", "diagonal_normalization", "acceleration",
        "inertia_enabled", "inertia_deceleration",
        "direction_change_braking",
    }

    missing = required - config.keys()
    if missing:
        raise ValueError(f"Missing settings: {sorted(missing)}")

    if set(config["movement_keys"]) != {"up", "down", "left", "right"}:
        raise ValueError("movement_keys must define up, down, left, right")

    for name in (
        "speed", "acceleration", "inertia_deceleration",
        "direction_change_braking",
    ):
        value = config[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value <= 10000
        ):
            raise ValueError(f"{name} must be greater than 0 and at most 10000")

    for name in ("inertia_enabled", "diagonal_normalization"):
        if not isinstance(config[name], bool):
            raise ValueError(f"{name} must be true or false")

    config.setdefault("left_click_key", "n")
    config.setdefault("right_click_key", "m")
    config.setdefault("scroll_up_key", "u")
    config.setdefault("scroll_down_key", "o")
    config.setdefault("scroll_speed", 1)
    config.setdefault("scroll_interval", 0.03)

    for name in (
        "activation_key", "emergency_stop_key", "left_click_key",
        "right_click_key", "scroll_up_key", "scroll_down_key",
    ):
        if not isinstance(config[name], str) or not config[name]:
            raise ValueError(f"{name} must be a non-empty string")

    for direction, key in config["movement_keys"].items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"Invalid movement key: {direction}")

    if (
        isinstance(config["scroll_speed"], bool)
        or not isinstance(config["scroll_speed"], int)
        or not 1 <= config["scroll_speed"] <= 20
    ):
        raise ValueError("scroll_speed must be an integer from 1 to 20")

    interval = config["scroll_interval"]
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not math.isfinite(interval)
        or not 0.01 <= interval <= 1.0
    ):
        raise ValueError("scroll_interval must be between 0.01 and 1.0")

    movement = [v.lower() for v in config["movement_keys"].values()]
    controls = [
        config["activation_key"].lower(),
        config["emergency_stop_key"].lower(),
        *movement,
        config["left_click_key"].lower(),
        config["right_click_key"].lower(),
        config["scroll_up_key"].lower(),
        config["scroll_down_key"].lower(),
    ]
    if len(set(controls)) != len(controls):
        raise ValueError("All control keys must be different")

    return config


def approach(current, target, amount):
    if current < target:
        return min(current + amount, target)
    if current > target:
        return max(current - amount, target)
    return current

def main():
    if sys.platform != "win32":
        raise OSError(
            "This version blocks typing using a Windows keyboard hook, "
            "so it only runs on Windows."
        )

    config = load_config()

    activation_key = config["activation_key"].lower()
    stop_key = config["emergency_stop_key"].lower()
    movement_keys = {
        direction: value.lower()
        for direction, value in config["movement_keys"].items()
    }

    left_click_key = config["left_click_key"].lower()
    right_click_key = config["right_click_key"].lower()
    scroll_up_key = config["scroll_up_key"].lower()
    scroll_down_key = config["scroll_down_key"].lower()
    scroll_speed = config["scroll_speed"]
    scroll_interval = config["scroll_interval"]

    control_names = [
        activation_key, stop_key, *movement_keys.values(),
        left_click_key, right_click_key, scroll_up_key, scroll_down_key,
    ]
    vk_to_name = {resolve_vk(name): name for name in control_names}

    state_lock = threading.Lock()
    pressed_keys = set()
    swallowed = set()
    click_queue = queue.SimpleQueue()
    running = threading.Event()
    running.set()
    mouse_controller = mouse.Controller()
    held_mouse_buttons = set()

    velocity_x = velocity_y = 0.0
    remainder_x = remainder_y = 0.0
    scroll_elapsed = 0.0

    def handle_key(name, is_down):
        """Called from the hook thread. Returns True to block the key."""
        if not is_down:
            with state_lock:
                pressed_keys.discard(name)
                was_swallowed = name in swallowed
                swallowed.discard(name)
                if was_swallowed and name == left_click_key:
                    click_queue.put(("release", mouse.Button.left))
                elif was_swallowed and name == right_click_key:
                    click_queue.put(("release", mouse.Button.right))
            return was_swallowed

        with state_lock:
            already_pressed = name in pressed_keys
            pressed_keys.add(name)
            active = activation_key in pressed_keys

            if name == activation_key:
                swallowed.add(name)
                return True

            if name == stop_key:
                if active:
                    swallowed.add(name)
                should_stop = active
            else:
                should_stop = False
                if active:
                    swallowed.add(name)

        if name == stop_key:
            if should_stop:
                print("\nEmergency stop requested.")
                running.clear()
            return should_stop

        if not active:
            return False

        if not already_pressed:
            if name == left_click_key:
                click_queue.put(("press", mouse.Button.left))
            elif name == right_click_key:
                click_queue.put(("press", mouse.Button.right))

        return True

    debug = bool(config.get("debug", False))

    def traced_handle_key(name, is_down):
        block = handle_key(name, is_down)
        if debug:
            print(
                f"{name} {'down' if is_down else 'up'} -> "
                f"{'BLOCKED' if block else 'passed through'}"
            )
        return block

    hook = KeyboardHook(vk_to_name, traced_handle_key)

    print("Keyboard-to-mouse controller (keys are NOT typed while active)")
    print(f"Hold {activation_key.upper()} to activate")
    print(f"Movement: {movement_keys}")
    print(f"Left click: {left_click_key.upper()}")
    print(f"Right click: {right_click_key.upper()}")
    print(f"Scroll up: {scroll_up_key.upper()}")
    print(f"Scroll down: {scroll_down_key.upper()}")
    print(f"Emergency stop: {stop_key.upper()}")
    print(
        "Key codes: "
        + ", ".join(f"{n}=0x{vk:02X}" for vk, n in vk_to_name.items())
    )
    print("Press Ctrl+C or use the emergency stop key to exit.")

    hook.start()
    if not hook.ready.wait(timeout=5.0):
        hook.stop()
        hook.join(timeout=1.0)
        raise TimeoutError("Keyboard hook initialization timed out")
    if hook.error:
        hook.join(timeout=1.0)
        raise OSError(f"Could not install keyboard hook: {hook.error}")

    previous_time = time.perf_counter()

    try:
        while running.is_set():
            if hook.error is not None:
                raise OSError(f"Keyboard hook stopped unexpectedly: {hook.error}")
            if not hook.is_alive():
                raise OSError("Keyboard hook thread stopped unexpectedly")
            frame_start = time.perf_counter()
            dt = min(frame_start - previous_time, 0.05)
            previous_time = frame_start

            while True:
                try:
                    action, button = click_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if action == "press":
                        if button not in held_mouse_buttons:
                            mouse_controller.press(button)
                            held_mouse_buttons.add(button)
                    elif action == "release":
                        if button in held_mouse_buttons:
                            mouse_controller.release(button)
                            held_mouse_buttons.discard(button)
                except Exception as error:
                    print(f"Mouse action failed: {error}")

            with state_lock:
                keys = set(pressed_keys)

            active = activation_key in keys

            if not active:
                for button in tuple(held_mouse_buttons):
                    try:
                        mouse_controller.release(button)
                    except Exception as error:
                        print(f"Mouse release failed: {error}")
                    finally:
                        held_mouse_buttons.discard(button)
                velocity_x = velocity_y = 0.0
                remainder_x = remainder_y = 0.0
                scroll_elapsed = 0.0
            else:
                dx = (
                    int(movement_keys["right"] in keys)
                    - int(movement_keys["left"] in keys)
                )
                dy = (
                    int(movement_keys["down"] in keys)
                    - int(movement_keys["up"] in keys)
                )

                if config["diagonal_normalization"] and (dx or dy):
                    magnitude = math.hypot(dx, dy)
                    dx /= magnitude
                    dy /= magnitude

                if dx or dy:
                    target_x = dx * config["speed"]
                    target_y = dy * config["speed"]

                    def update_velocity(current, target):
                        reversing = current * target < 0
                        stopping = target == 0 and current != 0
                        rate = (
                            config["direction_change_braking"]
                            if reversing or stopping
                            else config["acceleration"]
                        )
                        return approach(current, target, rate * dt)

                    velocity_x = update_velocity(velocity_x, target_x)
                    velocity_y = update_velocity(velocity_y, target_y)
                elif config["inertia_enabled"]:
                    slowdown = config["inertia_deceleration"] * dt
                    velocity_x = approach(velocity_x, 0.0, slowdown)
                    velocity_y = approach(velocity_y, 0.0, slowdown)
                else:
                    velocity_x = velocity_y = 0.0

                remainder_x += velocity_x * dt
                remainder_y += velocity_y * dt
                move_x = math.trunc(remainder_x)
                move_y = math.trunc(remainder_y)
                remainder_x -= move_x
                remainder_y -= move_y

                if move_x or move_y:
                    mouse_controller.move(move_x, move_y)

                scroll_up = scroll_up_key in keys
                scroll_down = scroll_down_key in keys

                if scroll_up != scroll_down:
                    scroll_elapsed += dt
                    if scroll_elapsed >= scroll_interval:
                        direction = 1 if scroll_up else -1
                        try:
                            mouse_controller.scroll(
                                0, direction * scroll_speed
                            )
                        except Exception as error:
                            print(f"Mouse scroll failed: {error}")
                        scroll_elapsed = 0.0
                else:
                    scroll_elapsed = 0.0

            elapsed = time.perf_counter() - frame_start
            time.sleep(max(0.0, FRAME_TIME - elapsed))

    except KeyboardInterrupt:
        print("\nStopped from terminal.")
    finally:
        running.clear()
        hook.stop()
        hook.join(timeout=1.0)
        for button in tuple(held_mouse_buttons):
            try:
                mouse_controller.release(button)
            except Exception as error:
                print(f"Mouse release failed during shutdown: {error}")
            finally:
                held_mouse_buttons.discard(button)
        print("Program stopped.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Startup or configuration error: {error}")