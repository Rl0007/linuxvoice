# LinuxVoice

Press and hold **right_ctrl** to talk to linuxvoice.


https://github.com/user-attachments/assets/17112cdb-96bd-480c-8513-2d06cebf1f5a

Even though the faster-whisper is an Open Source model the results are surprisingly fast and accurate.


Push-to-talk dictation for Linux. Hold a key → speak → release → text is typed into whatever window you have focused.

- **Transcription:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper base, CPU int8 by default; set `"device": "cuda"` if you have CUDA libs)
- **Works on:** X11 and Wayland
- **Hotkey:** Right Ctrl (configurable)
- **Output:** `xdotool type` or clipboard paste

## Quick install

```bash
git clone <repo-url> ~/linuxvoice
cd ~/linuxvoice
chmod +x install.sh
./install.sh
```

The script will:
1. Install system packages (`xdotool`, `portaudio`, `python3-tkinter`, etc.)
2. Add your user to the `input` group (required for Wayland global hotkey detection)
3. `pip install --user` all Python dependencies
4. Install `linuxvoice` command to `/usr/local/bin`
5. Install and enable the systemd user service for autostart

## First run

Log out and back in (so the `input` group takes effect), then just run:

```bash
linuxvoice
```

If you don't want to re-login yet:

```bash
sg input -c "python3 ~/linuxvoice/main.py"
```

Hold **Left Ctrl** to record. Release to transcribe and type.

A small animated overlay appears at the bottom-center of your screen:
- **Red pulsing dot** → recording
- **Blue bouncing dots** → transcribing

## Autostart (background service)

```bash
systemctl --user start linuxvoice      # start now
systemctl --user status linuxvoice     # check status
journalctl --user -fu linuxvoice       # live logs
systemctl --user disable linuxvoice    # remove autostart
```

## Config (`config.json`)

| Key | Default | Options |
|-----|---------|---------|
| `model` | `"base"` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `device` | `"cpu"` | `"cpu"`, `"cuda"` |
| `hotkey` | `"left_ctrl"` | `left_ctrl`, `right_ctrl`, `left_alt`, `right_alt` |
| `typing_method` | `"xdotool"` | `"xdotool"`, `"clipboard"` |
| `sample_rate` | `16000` | — |
| `channels` | `1` | — |

Use `"typing_method": "clipboard"` if `xdotool type` misses characters (requires `xclip`).

## GPU acceleration

The app falls back to CPU if CUDA compute libraries are missing. To enable GPU:

```bash
# Check if CUDA libs are present
ldconfig -p | grep libcublas

# If missing, install them (Fedora example)
sudo dnf install cuda-cublas-12-6   # adjust version to match your driver
```

Then set `"device": "cuda"` in `config.json`.

## Manual dependency install

If the install script doesn't support your distro:

```bash
# System packages (Debian/Ubuntu)
sudo apt install xdotool xclip portaudio19-dev python3-tk python3-dev gcc
sudo usermod -aG input $USER

# Python packages
pip install faster-whisper sounddevice soundfile numpy evdev Pillow pystray
```


