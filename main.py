#!/usr/bin/env python3
"""FluidVoice Linux — push-to-talk dictation via faster-whisper (CUDA)."""

import json
import os
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from pynput import keyboard

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
                print(f"[fluidvoice] Loading faster-whisper '{MODEL_NAME}' on {DEVICE} ...", flush=True)
                if DEVICE == "cuda":
                    try:
                        _model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
                        print("[fluidvoice] Model loaded on GPU (float16)", flush=True)
                    except Exception as e:
                        print(f"[fluidvoice] CUDA failed ({e}), falling back to CPU int8", flush=True)
                        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
                        print("[fluidvoice] Model loaded on CPU (int8)", flush=True)
                else:
                    _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
                    print("[fluidvoice] Model loaded on CPU (int8)", flush=True)
    return _model

# ---------------------------------------------------------------------------
# Recording state
# ---------------------------------------------------------------------------

_recording = False
_audio_chunks: list[np.ndarray] = []
_record_lock = threading.Lock()

def _audio_callback(indata, frames, time, status):
    if status:
        print(f"[fluidvoice] sounddevice: {status}", flush=True)
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
    print(f"[fluidvoice] Typing: {text!r}", flush=True)

    if TYPING_METHOD == "clipboard":
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)
    else:
        subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text], check=True)

# ---------------------------------------------------------------------------
# Hotkey handling
# ---------------------------------------------------------------------------

_hotkey_held = False
_transcribe_thread: threading.Thread | None = None

HOTKEY_MAP = {
    "left_ctrl":  keyboard.Key.ctrl_l,
    "right_ctrl": keyboard.Key.ctrl_r,
    "left_alt":   keyboard.Key.alt_l,
    "right_alt":  keyboard.Key.alt_r,
}

TARGET_KEY = HOTKEY_MAP.get(config.get("hotkey", "left_ctrl"), keyboard.Key.ctrl_l)

def on_press(key):
    global _hotkey_held
    if key == TARGET_KEY and not _hotkey_held:
        _hotkey_held = True
        start_recording()

def on_release(key):
    global _hotkey_held, _transcribe_thread
    if key == TARGET_KEY and _hotkey_held:
        _hotkey_held = False
        audio = stop_recording()

        def _do_transcribe():
            if audio is None or len(audio) == 0:
                print("[fluidvoice] No audio captured.", flush=True)
                return
            text = transcribe(audio)
            if text:
                type_text(text)
            else:
                print("[fluidvoice] Empty transcript.", flush=True)

        _transcribe_thread = threading.Thread(target=_do_transcribe, daemon=True)
        _transcribe_thread.start()

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

        icon_ready = _make_icon("#22c55e")
        icon_rec   = _make_icon("#ef4444")

        tray = pystray.Icon(
            "fluidvoice",
            icon_ready,
            "FluidVoice — ready",
            menu=pystray.Menu(
                pystray.MenuItem("Quit", lambda: (tray.stop(), os._exit(0)))
            ),
        )

        def _press_tray(key):
            on_press(key)
            if key == TARGET_KEY:
                tray.icon  = icon_rec
                tray.title = "FluidVoice — recording"

        def _release_tray(key):
            on_release(key)
            if key == TARGET_KEY:
                tray.icon  = icon_ready
                tray.title = "FluidVoice — ready"

        tray._press_fn   = _press_tray
        tray._release_fn = _release_tray

        threading.Thread(target=tray.run, daemon=True).start()
        return tray
    except Exception as e:
        print(f"[fluidvoice] Tray icon unavailable: {e}", flush=True)
        return None

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 50, flush=True)
    print("FluidVoice Linux", flush=True)
    print(f"  Model   : faster-whisper/{MODEL_NAME}", flush=True)
    print(f"  Device  : {DEVICE}", flush=True)
    print(f"  Hotkey  : {config.get('hotkey', 'left_ctrl')}", flush=True)
    print(f"  Typing  : {TYPING_METHOD}", flush=True)
    print("=" * 50, flush=True)

    import ctranslate2
    n = ctranslate2.get_cuda_device_count()
    if n > 0:
        print(f"[fluidvoice] CUDA ready — {n} device(s)", flush=True)
    else:
        print("[fluidvoice] WARNING: CUDA not available, will run on CPU", flush=True)

    # Preload model in background
    threading.Thread(target=get_model, daemon=True).start()

    tray = _try_start_tray()
    press_fn   = getattr(tray, "_press_fn",   None) if tray else None
    release_fn = getattr(tray, "_release_fn", None) if tray else None

    print(f"\nHold {config.get('hotkey', 'left_ctrl')} to record. Ctrl+C to quit.\n", flush=True)

    with keyboard.Listener(
        on_press   = press_fn   or on_press,
        on_release = release_fn or on_release,
    ) as listener:
        listener.join()


if __name__ == "__main__":
    main()
