#!/usr/bin/env python3
"""LinuxVoice — push-to-talk dictation via faster-whisper (CUDA)."""

import json
import math
import os
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
import selectors

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

config = load_config()

SAMPLE_RATE = config.get("sample_rate", 16000)
CHANNELS    = config.get("channels", 1)
DEVICE      = config.get("device", "cuda")
MODEL_NAME  = config.get("model", "base")
TYPING_METHOD = config.get("typing_method", "xdotool")

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

_model = None
_model_lock = threading.Lock()

def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                print(f"[linuxvoice] Loading faster-whisper '{MODEL_NAME}' on {DEVICE} ...", flush=True)
                if DEVICE == "cuda":
                    try:
                        _model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
                        print("[linuxvoice] Model loaded on GPU (float16)", flush=True)
                    except Exception as e:
                        print(f"[linuxvoice] CUDA failed ({e}), falling back to CPU int8", flush=True)
                        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
                        print("[linuxvoice] Model loaded on CPU (int8)", flush=True)
                else:
                    _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
                    print("[linuxvoice] Model loaded on CPU (int8)", flush=True)
    return _model

# ---------------------------------------------------------------------------
# Recording state
# ---------------------------------------------------------------------------

_recording = False
_audio_chunks: list[np.ndarray] = []
_record_lock = threading.Lock()

def _audio_callback(indata, frames, time, status):
    if status:
        print(f"[linuxvoice] sounddevice: {status}", flush=True)
    with _record_lock:
        if _recording:
            _audio_chunks.append(indata.copy())

_stream: sd.InputStream | None = None

def start_recording():
    global _recording, _audio_chunks, _stream
    with _record_lock:
        _recording = True
        _audio_chunks = []
    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        callback=_audio_callback,
    )
    _stream.start()
    print("[recording...]", flush=True)
    if _overlay:
        _overlay.show_recording()

def stop_recording() -> np.ndarray | None:
    global _recording, _stream
    with _record_lock:
        _recording = False
    if _stream is not None:
        _stream.stop()
        _stream.close()
        _stream = None
    with _record_lock:
        if not _audio_chunks:
            return None
        return np.concatenate(_audio_chunks, axis=0)

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe(audio: np.ndarray) -> str:
    print("[transcribing...]", flush=True)
    model = get_model()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name

    try:
        sf.write(tmp_path, audio, SAMPLE_RATE)
        segments, info = model.transcribe(tmp_path, beam_size=5, language="en")
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()
    finally:
        os.unlink(tmp_path)

# ---------------------------------------------------------------------------
# Typing output
# ---------------------------------------------------------------------------

def type_text(text: str):
    if not text:
        return
    print(f"[linuxvoice] Typing: {text!r}", flush=True)

    if TYPING_METHOD == "clipboard":
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)
    else:
        subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text], check=True)

# ---------------------------------------------------------------------------
# Hotkey handling (evdev — works on both X11 and Wayland)
# ---------------------------------------------------------------------------

try:
    from evdev import InputDevice, ecodes, list_devices as _list_devices
    _EVDEV_AVAILABLE = True
except ImportError:
    _EVDEV_AVAILABLE = False

_EVDEV_KEY_MAP = {
    "left_ctrl":  "KEY_LEFTCTRL",
    "right_ctrl": "KEY_RIGHTCTRL",
    "left_alt":   "KEY_LEFTALT",
    "right_alt":  "KEY_RIGHTALT",
}
_TARGET_EVCODE = None
if _EVDEV_AVAILABLE:
    import evdev.ecodes as _ec
    _TARGET_EVCODE = getattr(_ec, _EVDEV_KEY_MAP.get(
        config.get("hotkey", "left_ctrl"), "KEY_LEFTCTRL"))

_hotkey_held = False
_transcribe_thread: threading.Thread | None = None


def on_press():
    global _hotkey_held
    if not _hotkey_held:
        _hotkey_held = True
        start_recording()


def on_release():
    global _hotkey_held, _transcribe_thread
    if _hotkey_held:
        _hotkey_held = False
        audio = stop_recording()

        def _do_transcribe():
            if audio is None or len(audio) == 0:
                print("[linuxvoice] No audio captured.", flush=True)
                if _overlay:
                    _overlay.hide()
                return
            if _overlay:
                _overlay.show_transcribing()
            text = transcribe(audio)
            if _overlay:
                _overlay.hide()
            if text:
                type_text(text)
            else:
                print("[linuxvoice] Empty transcript.", flush=True)

        _transcribe_thread = threading.Thread(target=_do_transcribe, daemon=True)
        _transcribe_thread.start()


def _find_keyboards():
    keyboards = []
    for path in _list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if _ec.EV_KEY in caps and _ec.KEY_A in caps[_ec.EV_KEY]:
                keyboards.append(dev)
        except Exception:
            pass
    return keyboards


def _evdev_listen():
    keyboards = _find_keyboards()
    if not keyboards:
        print("[linuxvoice] ERROR: No keyboard devices found — are you in the 'input' group?", flush=True)
        return
    print(f"[linuxvoice] Listening on {len(keyboards)} keyboard device(s) via evdev", flush=True)
    sel = selectors.DefaultSelector()
    for kb in keyboards:
        try:
            sel.register(kb, selectors.EVENT_READ)
        except Exception:
            pass
    while True:
        try:
            for key, _ in sel.select(timeout=1.0):
                device = key.fileobj
                try:
                    for event in device.read():
                        if event.type == _ec.EV_KEY and event.code == _TARGET_EVCODE:
                            if event.value == 1:    # key down
                                on_press()
                            elif event.value == 0:  # key up
                                on_release()
                except OSError:
                    pass
        except Exception:
            pass

# ---------------------------------------------------------------------------
# On-screen recording overlay (tkinter)
# ---------------------------------------------------------------------------

_overlay = None

class RecordingOverlay:
    W, H = 300, 56

    def __init__(self):
        self._root = None
        self._canvas = None
        self._state = "hidden"
        self._step = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import tkinter as tk
        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-alpha", 0.88)
        root.configure(bg="#111111")

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - self.W) // 2
        y = sh - self.H - 48
        root.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self._canvas = tk.Canvas(root, width=self.W, height=self.H,
                                  bg="#111111", highlightthickness=0)
        self._canvas.pack()
        root.withdraw()
        self._tick()
        root.mainloop()

    def _tick(self):
        if self._root is None:
            return
        c = self._canvas
        c.delete("all")
        W, H = self.W, self.H

        if self._state == "recording":
            # Pulsing red dot
            pulse = 0.6 + 0.4 * abs(math.sin(self._step * 0.12))
            r = int(11 * pulse)
            cx, cy = 28, H // 2
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          fill="#ef4444", outline="")
            c.create_text(46, cy, text="Recording…", anchor="w",
                          fill="white", font=("Sans", 14, "bold"))
            self._step += 1

        elif self._state == "transcribing":
            # Three bouncing dots
            n = 3
            spacing = 16
            total = spacing * (n - 1)
            x0 = (W - total) // 2 - 30
            for i in range(n):
                phase = self._step * 0.18 + i * 1.0
                offset = int(6 * abs(math.sin(phase)))
                cx = x0 + i * spacing
                cy = H // 2 - offset
                c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                              fill="#60a5fa", outline="")
            c.create_text(x0 + total + 18, H // 2, text="Transcribing…",
                          anchor="w", fill="#93c5fd", font=("Sans", 13))
            self._step += 1

        self._root.after(45, self._tick)

    def show_recording(self):
        if self._root:
            self._root.after(0, self._do_show_recording)

    def _do_show_recording(self):
        self._state = "recording"
        self._step = 0
        self._root.deiconify()
        self._root.lift()

    def show_transcribing(self):
        if self._root:
            self._root.after(0, self._do_show_transcribing)

    def _do_show_transcribing(self):
        self._state = "transcribing"
        self._step = 0

    def hide(self):
        if self._root:
            self._root.after(0, self._do_hide)

    def _do_hide(self):
        self._state = "hidden"
        self._root.withdraw()


def _try_start_overlay():
    try:
        import tkinter  # noqa: F401 — just check availability
        ov = RecordingOverlay()
        print("[linuxvoice] On-screen overlay ready", flush=True)
        return ov
    except Exception as e:
        print(f"[linuxvoice] Overlay unavailable: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Optional tray icon
# ---------------------------------------------------------------------------

def _try_start_tray():
    try:
        import pystray
        from PIL import Image, ImageDraw

        def _make_icon(color: str) -> Image.Image:
            img = Image.new("RGB", (64, 64), color=color)
            d = ImageDraw.Draw(img)
            d.ellipse([16, 16, 48, 48], fill="white")
            return img

        tray = pystray.Icon(
            "linuxvoice",
            _make_icon("#22c55e"),
            "LinuxVoice — ready",
            menu=pystray.Menu(
                pystray.MenuItem("Quit", lambda: os._exit(0))
            ),
        )
        threading.Thread(target=tray.run, daemon=True).start()
    except Exception as e:
        print(f"[linuxvoice] Tray icon unavailable: {e}", flush=True)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 50, flush=True)
    print("LinuxVoice", flush=True)
    print(f"  Model   : faster-whisper/{MODEL_NAME}", flush=True)
    print(f"  Device  : {DEVICE}", flush=True)
    print(f"  Hotkey  : {config.get('hotkey', 'left_ctrl')}", flush=True)
    print(f"  Typing  : {TYPING_METHOD}", flush=True)
    print("=" * 50, flush=True)

    import ctranslate2
    n = ctranslate2.get_cuda_device_count()
    if n > 0:
        print(f"[linuxvoice] CUDA ready — {n} device(s)", flush=True)
    else:
        print("[linuxvoice] WARNING: CUDA not available, will run on CPU", flush=True)

    # Preload model in background
    threading.Thread(target=get_model, daemon=True).start()

    global _overlay
    _overlay = _try_start_overlay()

    _try_start_tray()

    print(f"\nHold {config.get('hotkey', 'left_ctrl')} to record. Ctrl+C to quit.\n", flush=True)

    if not _EVDEV_AVAILABLE:
        print("[linuxvoice] ERROR: evdev not installed. Run: pip install evdev", flush=True)
        return

    _evdev_listen()  # blocks until Ctrl+C


if __name__ == "__main__":
    main()
