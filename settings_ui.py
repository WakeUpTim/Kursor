import json
import math
import queue
import os
import re
import string
import subprocess
import sys
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    pystray = None
    Image = ImageDraw = None
    TRAY_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
MAIN_PATH = BASE_DIR / "main.py"

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

VALID_KEYS = (
    set(string.ascii_lowercase) | set(string.digits)
    | set(PUNCTUATION_VK) | set(NAMED_VK)
)

VK_TO_NAME = {}
for _c in string.ascii_lowercase:
    VK_TO_NAME[ord(_c.upper())] = _c
for _c in string.digits:
    VK_TO_NAME[ord(_c)] = _c
for _name, _vk in PUNCTUATION_VK.items():
    VK_TO_NAME[_vk] = _name
for _name, _vk in NAMED_VK.items():
    if _name != "cmd":
        VK_TO_NAME.setdefault(_vk, _name)

KEYSYM_NAMES = {
    "semicolon": ";", "equal": "=", "comma": ",", "minus": "-",
    "period": ".", "slash": "/", "grave": "`", "bracketleft": "[",
    "backslash": "\\", "bracketright": "]", "apostrophe": "'",
    "Caps_Lock": "caps_lock", "Escape": "esc", "space": "space",
    "Tab": "tab", "Return": "enter", "BackSpace": "backspace",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Home": "home", "End": "end", "Prior": "page_up", "Next": "page_down",
    "Insert": "insert", "Delete": "delete", "Menu": "menu",
    "Shift_L": "shift_l", "Shift_R": "shift_r",
    "Control_L": "ctrl_l", "Control_R": "ctrl_r",
    "Alt_L": "alt_l", "Alt_R": "alt_r",
    "Super_L": "cmd_l", "Super_R": "cmd_r",
}

DISPLAY = {
    "alt_l": "Left Alt", "alt_r": "Right Alt",
    "ctrl_l": "Left Ctrl", "ctrl_r": "Right Ctrl",
    "shift_l": "Left Shift", "shift_r": "Right Shift",
    "cmd": "Left Win", "cmd_l": "Left Win", "cmd_r": "Right Win",
    "esc": "Esc", "caps_lock": "Caps Lock", "page_up": "Page Up",
    "page_down": "Page Down",
}

def display_name(name):
    if name in DISPLAY:
        return DISPLAY[name]
    if len(name) == 1:
        return name.upper()
    return name.replace("_", " ").title()


def event_to_name(event):
    """Convert a Tk key event to a config key name, or None if unsupported."""
    sym = event.keysym
    if sym in KEYSYM_NAMES:
        return KEYSYM_NAMES[sym]
    if re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", sym):
        return sym.lower()
    if sys.platform == "win32" and event.keycode in VK_TO_NAME:
        return VK_TO_NAME[event.keycode]
    if len(sym) == 1 and sym.isascii() and sym.isalnum():
        return sym.lower()
    return None

DEFAULT_KEYS = {
    "activation_key": "alt_r", "emergency_stop_key": "esc",
    "up": "i", "down": "k", "left": "j", "right": "l",
    "left_click_key": "n", "right_click_key": "m",
    "scroll_up_key": "p", "scroll_down_key": ";",
}
DEFAULT_NUMBERS = {
    "speed": 1200, "acceleration": 3000, "direction_change_braking": 9000,
    "inertia_deceleration": 5000, "scroll_speed": 1, "scroll_interval": 0.03,
}
DEFAULT_FLAGS = {
    "inertia_enabled": True, "diagonal_normalization": True, "debug": False,
}
RANGES = {
    "speed": (50, 10000), "acceleration": (100, 10000),
    "direction_change_braking": (100, 10000),
    "inertia_deceleration": (100, 10000),
    "scroll_speed": (1, 20), "scroll_interval": (0.01, 1.0),
}

LAYOUT_FIELDS = [
    "up", "down", "left", "right",
    "left_click_key", "right_click_key", "scroll_up_key", "scroll_down_key",
]
BUILTIN_LAYOUTS = {
    "IJKL": {
        "up": "i", "down": "k", "left": "j", "right": "l",
        "left_click_key": "n", "right_click_key": "m",
        "scroll_up_key": "p", "scroll_down_key": ";",
    },
    "WASD": {
        "up": "w", "down": "s", "left": "a", "right": "d",
        "left_click_key": "q", "right_click_key": "e",
        "scroll_up_key": "r", "scroll_down_key": "f",
    },
    "Vim (HJKL)": {
        "up": "k", "down": "j", "left": "h", "right": "l",
        "left_click_key": "n", "right_click_key": "m",
        "scroll_up_key": "u", "scroll_down_key": "o",
    },
    "Arrow keys": {
        "up": "up", "down": "down", "left": "left", "right": "right",
        "left_click_key": "z", "right_click_key": "x",
        "scroll_up_key": "a", "scroll_down_key": "s",
    },
}

CONTROL_ACTIONS = [
    ("activation_key", "Activate (hold)"),
    ("emergency_stop_key", "Emergency stop"),
    ("left_click_key", "Left click"),
    ("right_click_key", "Right click"),
    ("scroll_up_key", "Scroll up"),
    ("scroll_down_key", "Scroll down"),
]
MOVEMENT_ACTIONS = [
    ("up", "Move up"), ("down", "Move down"),
    ("left", "Move left"), ("right", "Move right"),
]
ACTION_LABELS = dict(CONTROL_ACTIONS + MOVEMENT_ACTIONS)

def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def load_settings():
    """Read config.json (if any) and return validated settings."""
    notes = []
    raw = {}
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as file:
                raw = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            notes.append(f"Could not read config.json ({error}); using defaults.")
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

    keys = dict(DEFAULT_KEYS)
    movement = raw.get("movement_keys")
    movement = movement if isinstance(movement, dict) else {}
    for action in DEFAULT_KEYS:
        if action in ("up", "down", "left", "right"):
            value = movement.get(action)
        else:
            value = raw.get(action)
        if isinstance(value, str) and value.lower() in VALID_KEYS:
            keys[action] = value.lower()
        elif value is not None:
            notes.append(
                f"Unsupported key {value!r} for {ACTION_LABELS.get(action, action)}; "
                "using the default."
            )

    if len(set(keys.values())) != len(keys):
        notes.append("config.json had duplicate keys; reset keys to defaults.")
        keys = dict(DEFAULT_KEYS)

    numbers = dict(DEFAULT_NUMBERS)
    for name, (lo, hi) in RANGES.items():
        value = raw.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            numbers[name] = clamp(value, lo, hi)

    flags = dict(DEFAULT_FLAGS)
    for name in flags:
        if isinstance(raw.get(name), bool):
            flags[name] = raw[name]

    custom = {}
    raw_custom = raw.get("custom_layouts")
    if isinstance(raw_custom, dict):
        for layout_name, layout in raw_custom.items():
            if (
                isinstance(layout, dict)
                and set(layout) == set(LAYOUT_FIELDS)
                and all(
                    isinstance(v, str) and v in VALID_KEYS
                    for v in layout.values()
                )
                and len(set(layout.values())) == len(LAYOUT_FIELDS)
            ):
                custom[str(layout_name)] = dict(layout)

    extra = {
        k: v for k, v in raw.items()
        if k not in (
            set(DEFAULT_KEYS) | set(DEFAULT_NUMBERS) | set(DEFAULT_FLAGS)
            | {"movement_keys", "custom_layouts"}
        )
    }
    return {
        "extra": extra, "keys": keys, "numbers": numbers, "flags": flags,
        "custom_layouts": custom, "notes": notes,
    }


def write_config(config):
    temp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)
        file.write("\n")
    os.replace(temp, CONFIG_PATH)

class SettingsApp:
    def __init__(self, root):
        self.root = root
        # Clean up a log file left behind by older versions of Kursor.
        try:
            (BASE_DIR / "controller.log").unlink(missing_ok=True)
        except OSError:
            pass
        self.loading = True
        self.dirty = False
        self.capturing = None
        self.proc = None
        self.started_at = 0.0
        self.tray_icon = None
        self.tray_thread = None
        self._ui_actions = queue.SimpleQueue()
        self._allow_destroy = False

        settings = load_settings()
        self.extra = settings["extra"]
        self.keys = settings["keys"]
        self.custom_layouts = settings["custom_layouts"]

        self.vars = {
            name: tk.DoubleVar(value=value)
            for name, value in settings["numbers"].items()
        }
        self.flag_vars = {
            name: tk.BooleanVar(value=value)
            for name, value in settings["flags"].items()
        }
        self.layout_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        self.run_var = tk.StringVar(value="Controller: stopped")
        self.key_buttons = {}
        self.scales = {}

        self.build_ui()

        self.root.bind("<KeyPress>", self.on_key_press)
        self.root.bind("<Unmap>", self.on_minimize)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        for var in self.vars.values():
            var.trace_add("write", lambda *_: self.on_change())
        for var in self.flag_vars.values():
            var.trace_add("write", lambda *_: self.on_change())

        self.loading = False
        self.refresh_keys()
        self.refresh_layout_label()
        self.update_inertia_state()
        if settings["notes"]:
            self.status_var.set(" ".join(settings["notes"]))
            self.set_dirty(True, keep_status=True)
        elif not CONFIG_PATH.exists():
            self.status_var.set("No config.json yet. Creating it and starting the controller...")
        self.poll()
        self.root.after(150, self.start_controller)

    def build_ui(self):
        self.root.title("Kursor settings")
        self.root.resizable(False, False)
        outer = ttk.Frame(self.root, padding=12)
        outer.grid(row=0, column=0)

        bold = ("Segoe UI", 9, "bold") if sys.platform == "win32" else None

        layout_frame = ttk.LabelFrame(outer, text="Layout", padding=8)
        layout_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(layout_frame, text="Preset:").grid(row=0, column=0, padx=(0, 6))
        self.layout_combo = ttk.Combobox(
            layout_frame, textvariable=self.layout_var,
            state="readonly", width=24,
        )
        self.layout_combo.grid(row=0, column=1, padx=(0, 8))
        self.layout_combo.bind("<<ComboboxSelected>>", self.on_layout_selected)
        ttk.Button(
            layout_frame, text="Save as…", command=self.save_layout_as
        ).grid(row=0, column=2, padx=(0, 4))
        self.delete_layout_btn = ttk.Button(
            layout_frame, text="Delete", command=self.delete_layout
        )
        self.delete_layout_btn.grid(row=0, column=3)
        self.refresh_layout_values()

        keys_frame = ttk.LabelFrame(
            outer, text="Keys  (click a key, then press the new one)",
            padding=8,
        )
        keys_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        keys_frame.columnconfigure(2, minsize=24)
        ttk.Label(keys_frame, text="Controls", font=bold).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        ttk.Label(keys_frame, text="Movement", font=bold).grid(
            row=0, column=3, columnspan=2, sticky="w", pady=(0, 4)
        )
        for index, (action, label) in enumerate(CONTROL_ACTIONS):
            self.add_key_row(keys_frame, index + 1, 0, action, label)
        for index, (action, label) in enumerate(MOVEMENT_ACTIONS):
            self.add_key_row(keys_frame, index + 1, 3, action, label)

        move_frame = ttk.LabelFrame(outer, text="Mouse movement", padding=8)
        move_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.add_slider(move_frame, 0, "speed", "Top speed (px/sec)")
        self.add_slider(move_frame, 1, "acceleration", "Acceleration")
        self.add_slider(
            move_frame, 2, "direction_change_braking",
            "Braking / direction change",
        )
        ttk.Checkbutton(
            move_frame, text="Enable inertia (cursor glides to a stop)",
            variable=self.flag_vars["inertia_enabled"],
            command=self.update_inertia_state,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.add_slider(
            move_frame, 4, "inertia_deceleration",
            "Inertia stop rate (higher = stops sooner)",
        )
        ttk.Checkbutton(
            move_frame, text="Same speed on diagonals (normalize)",
            variable=self.flag_vars["diagonal_normalization"],
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        scroll_frame = ttk.LabelFrame(outer, text="Scrolling", padding=8)
        scroll_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.add_slider(scroll_frame, 0, "scroll_speed", "Lines per step")
        self.add_slider(
            scroll_frame, 1, "scroll_interval",
            "Seconds between steps (lower = faster)", decimals=2,
        )

        ttk.Checkbutton(
            outer,
            text="Debug output (disabled when launched from settings)",
            variable=self.flag_vars["debug"],
        ).grid(row=4, column=0, sticky="w", pady=(0, 8))

        ttk.Label(outer, textvariable=self.status_var, wraplength=480,
                  foreground="#555555").grid(row=5, column=0, sticky="w")
        ttk.Label(outer, textvariable=self.run_var).grid(
            row=6, column=0, sticky="w", pady=(2, 6)
        )
        buttons = ttk.Frame(outer)
        buttons.grid(row=7, column=0, sticky="ew")
        ttk.Button(
            buttons, text="Reset to defaults", command=self.reset_defaults
        ).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(buttons, text="Minimize to tray", command=self.minimize_to_tray).grid(
            row=0, column=1, padx=(0, 4)
        )
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="Save", command=self.save).grid(
            row=0, column=3, padx=(0, 4)
        )
        self.start_btn = ttk.Button(
            buttons, text="Save & start", command=self.start_controller
        )
        self.start_btn.grid(row=0, column=4, padx=(0, 4))
        self.stop_btn = ttk.Button(
            buttons, text="Stop", command=self.stop_controller
        )
        self.stop_btn.grid(row=0, column=5)

    def add_key_row(self, parent, row, col, action, label):
        ttk.Label(parent, text=label).grid(
            row=row, column=col, sticky="w", padx=(0, 8), pady=2
        )
        button = ttk.Button(
            parent, width=14, takefocus=False,
            command=lambda a=action: self.toggle_capture(a),
        )
        button.grid(row=row, column=col + 1, pady=2)
        self.key_buttons[action] = button

    def add_slider(self, parent, row, name, label, decimals=0):
        lo, hi = RANGES[name]
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=2
        )
        scale = ttk.Scale(
            parent, from_=lo, to=hi, variable=self.vars[name],
            orient="horizontal", length=240,
        )
        scale.grid(row=row, column=1, pady=2)
        readout = ttk.Label(parent, width=7, anchor="e")
        readout.grid(row=row, column=2, padx=(6, 0))

        def update(*_):
            readout.config(text=f"{self.vars[name].get():.{decimals}f}")

        self.vars[name].trace_add("write", update)
        update()
        self.scales[name] = scale

    def set_dirty(self, dirty, keep_status=False):
        self.dirty = dirty
        self.root.title("Kursor settings" + (" *" if dirty else ""))
        if dirty and not keep_status:
            self.status_var.set("Unsaved changes.")

    def on_change(self):
        if not self.loading:
            self.set_dirty(True)

    def update_inertia_state(self):
        enabled = self.flag_vars["inertia_enabled"].get()
        self.scales["inertia_deceleration"].state(
            ["!disabled"] if enabled else ["disabled"]
        )

    def refresh_keys(self):
        for action, button in self.key_buttons.items():
            if self.capturing == action:
                button.config(text="Press a key…")
            else:
                button.config(text=display_name(self.keys[action]))

    def all_layouts(self):
        layouts = dict(BUILTIN_LAYOUTS)
        layouts.update(self.custom_layouts)
        return layouts

    def refresh_layout_values(self):
        self.layout_combo.config(values=list(self.all_layouts()))

    def refresh_layout_label(self):
        current = {field: self.keys[field] for field in LAYOUT_FIELDS}
        label = "Custom (unsaved)"
        for name, layout in self.all_layouts().items():
            if layout == current:
                label = name
                break
        self.layout_var.set(label)
        self.delete_layout_btn.state(
            ["!disabled"] if label in self.custom_layouts else ["disabled"]
        )

    def on_layout_selected(self, _event=None):
        name = self.layout_var.get()
        layout = self.all_layouts().get(name)
        if layout is None:
            return
        blocked = [
            display_name(key) for key in layout.values()
            if key in (self.keys["activation_key"], self.keys["emergency_stop_key"])
        ]
        if blocked:
            messagebox.showwarning(
                "Layout conflict",
                f"The '{name}' layout uses {', '.join(blocked)}, which is "
                "your activation or emergency-stop key.\n\n"
                "Change that key first, then apply the layout.",
            )
            self.refresh_layout_label()
            return
        self.keys.update(layout)
        self.refresh_keys()
        self.refresh_layout_label()
        self.on_change()

    def save_layout_as(self):
        name = simpledialog.askstring(
            "Save layout", "Name for this layout:", parent=self.root
        )
        if not name or not name.strip():
            return
        name = name.strip()
        if name in BUILTIN_LAYOUTS or name == "Custom (unsaved)":
            messagebox.showerror(
                "Save layout", f"'{name}' is a reserved name. Pick another."
            )
            return
        if name in self.custom_layouts and not messagebox.askyesno(
            "Save layout", f"Replace the existing layout '{name}'?"
        ):
            return
        self.custom_layouts[name] = {f: self.keys[f] for f in LAYOUT_FIELDS}
        self.refresh_layout_values()
        self.refresh_layout_label()
        self.set_dirty(True)
        self.status_var.set(f"Layout '{name}' stored. Click Save to keep it.")

    def delete_layout(self):
        name = self.layout_var.get()
        if name not in self.custom_layouts:
            return
        if not messagebox.askyesno("Delete layout", f"Delete layout '{name}'?"):
            return
        del self.custom_layouts[name]
        self.refresh_layout_values()
        self.refresh_layout_label()
        self.set_dirty(True)

    def toggle_capture(self, action):
        if self.capturing == action:
            self.capturing = None
            self.status_var.set("Capture cancelled.")
            self.refresh_keys()
            return
        if self.controller_running():
            self.stop_controller()
            self.status_var.set(
                "Controller stopped so keys can be captured. "
                "Use Save & start to run it again."
            )
        self.capturing = action
        self.refresh_keys()
        self.root.focus_set()
        self.status_var.set(
            f"Press the new key for '{ACTION_LABELS[action]}' "
            "(click the button again to cancel)."
        )

    def on_key_press(self, event):
        if self.capturing is None:
            return None
        name = event_to_name(event)
        if name is None:
            self.status_var.set(
                f"'{event.keysym}' isn't supported. Try another key."
            )
            return "break"

        action = self.capturing
        self.capturing = None
        note = ""
        for other, current in self.keys.items():
            if other != action and current == name:
                self.keys[other] = self.keys[action]
                note = (
                    f" '{display_name(name)}' was used by "
                    f"'{ACTION_LABELS[other]}', so the two were swapped."
                )
                break
        self.keys[action] = name
        self.refresh_keys()
        self.refresh_layout_label()
        self.set_dirty(True, keep_status=True)
        self.status_var.set(
            f"'{ACTION_LABELS[action]}' is now {display_name(name)}.{note} "
            "Unsaved changes."
        )
        return "break"

    def build_config(self):
        config = dict(self.extra)
        keys = self.keys

        def number(name, as_int):
            lo, hi = RANGES[name]
            value = clamp(self.vars[name].get(), lo, hi)
            return int(round(value)) if as_int else round(value, 3)

        config.update({
            "activation_key": keys["activation_key"],
            "emergency_stop_key": keys["emergency_stop_key"],
            "movement_keys": {
                d: keys[d] for d in ("up", "down", "left", "right")
            },
            "left_click_key": keys["left_click_key"],
            "right_click_key": keys["right_click_key"],
            "scroll_up_key": keys["scroll_up_key"],
            "scroll_down_key": keys["scroll_down_key"],
            "speed": number("speed", True),
            "acceleration": number("acceleration", True),
            "direction_change_braking": number("direction_change_braking", True),
            "inertia_deceleration": number("inertia_deceleration", True),
            "scroll_speed": number("scroll_speed", True),
            "scroll_interval": number("scroll_interval", False),
            "inertia_enabled": self.flag_vars["inertia_enabled"].get(),
            "diagonal_normalization": self.flag_vars["diagonal_normalization"].get(),
            "debug": self.flag_vars["debug"].get(),
        })
        if self.custom_layouts:
            config["custom_layouts"] = self.custom_layouts
        else:
            config.pop("custom_layouts", None)
        return config

    def save(self):
        if len(set(self.keys.values())) != len(self.keys):
            messagebox.showerror(
                "Cannot save", "Every control must use a different key."
            )
            return False
        try:
            write_config(self.build_config())
        except OSError as error:
            messagebox.showerror("Cannot save", f"Could not write config.json:\n{error}")
            return False
        self.set_dirty(False, keep_status=True)
        self.status_var.set(f"Saved to {CONFIG_PATH.name}.")
        return True

    def reset_defaults(self):
        if not messagebox.askyesno(
            "Reset to defaults",
            "Reset all keys and settings to the defaults?\n"
            "(Your saved layouts are kept.)",
        ):
            return
        self.loading = True
        self.keys = dict(DEFAULT_KEYS)
        for name, value in DEFAULT_NUMBERS.items():
            self.vars[name].set(value)
        for name, value in DEFAULT_FLAGS.items():
            self.flag_vars[name].set(value)
        self.loading = False
        self.refresh_keys()
        self.refresh_layout_label()
        self.update_inertia_state()
        self.set_dirty(True)

    def controller_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start_controller(self):
        if not MAIN_PATH.exists():
            messagebox.showerror("Cannot start", f"main.py not found in:\n{BASE_DIR}")
            return
        if not self.save():
            return
        if self.controller_running():
            self.stop_controller()
        try:
            try:
                (BASE_DIR / "controller.log").unlink(missing_ok=True)
            except OSError:
                pass
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.proc = subprocess.Popen(
                [sys.executable, "-u", str(MAIN_PATH)],
                cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=flags,
            )
        except OSError as error:
            messagebox.showerror("Cannot start", f"Could not start main.py:\n{error}")
            self.proc = None
            return
        self.started_at = time.time()
        self.status_var.set("Saved. Controller started.")

    def stop_controller(self):
        if self.controller_running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def poll(self):
        while True:
            try:
                action = self._ui_actions.get_nowait()
            except queue.Empty:
                break
            if action == "show":
                self._show_window()
            elif action == "exit":
                self.on_close()
        if self.proc is not None and self.proc.poll() is not None:
            quick_exit = time.time() - self.started_at < 5
            self.proc = None
            if quick_exit:
                messagebox.showwarning(
                    "Controller exited",
                    "The controller stopped right after starting.\n\n"
                    "File logging is disabled, so no diagnostic output was saved. "
                    "Run main.py from a terminal if you need to inspect startup errors.",
                )
        running = self.controller_running()
        self.run_var.set("Controller: running" if running else "Controller: stopped")
        self.start_btn.config(text="Save & restart" if running else "Save & start")
        self.stop_btn.state(["!disabled"] if running else ["disabled"])
        self.root.after(500, self.poll)

    def _make_tray_image(self):
        """Create a simple app icon for the Windows notification area."""
        image = Image.new("RGBA", (64, 64), (36, 41, 51, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((7, 7, 57, 57), radius=12, fill=(65, 135, 230, 255))
        draw.ellipse((19, 19, 45, 45), fill=(255, 255, 255, 255))
        draw.ellipse((26, 26, 38, 38), fill=(65, 135, 230, 255))
        return image

    def _ensure_tray_icon(self):
        if not TRAY_AVAILABLE:
            messagebox.showerror(
                "Tray support unavailable",
                "Minimize-to-tray needs pystray and Pillow. Install them with:\n\n"
                "python -m pip install pystray pillow",
                parent=self.root,
            )
            return False
        if self.tray_icon is not None:
            return True

        menu = pystray.Menu(
            pystray.MenuItem("Show Kursor settings", self._tray_show, default=True),
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self.tray_icon = pystray.Icon(
            "KursorSettings", self._make_tray_image(), "Kursor settings", menu
        )
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()
        return True

    def minimize_to_tray(self):
        """Hide the settings window while keeping the controller alive."""
        if self._ensure_tray_icon():
            self.root.withdraw()
            self.status_var.set("Kursor settings is running in the system tray.")

    def on_minimize(self, event=None):
        if event is not None and event.widget is self.root:
            try:
                if self.root.state() == "iconic":
                    self.minimize_to_tray()
            except tk.TclError:
                pass

    def _tray_show(self, icon=None, item=None):
        self._ui_actions.put("show")

    def _show_window(self):
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _tray_exit(self, icon=None, item=None):
        self._ui_actions.put("exit")

    def _stop_tray_icon(self):
        icon = self.tray_icon
        self.tray_icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def on_close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel(
                "Unsaved changes", "Save your changes before closing?"
            )
            if answer is None:
                return
            if answer and not self.save():
                return
        if self.controller_running():
            answer = messagebox.askyesnocancel(
                "Controller running",
                "Stop the controller too?\n\n"
                "Yes = stop it    No = leave it running",
            )
            if answer is None:
                return
            if answer:
                self.stop_controller()
        self._stop_tray_icon()
        self._allow_destroy = True
        self.root.destroy()


def main():
    if sys.platform == "win32":
        try:
            ctypes_windll = __import__("ctypes").windll
            ctypes_windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    SettingsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
