# FluidVoice Linux

Push-to-talk dictation for Linux using NVIDIA Parakeet TDT v3 (600M params).

**Hold Left Ctrl** → speak → **release** → text is typed into the focused window.

## Requirements

- NVIDIA GPU (CUDA)
- Python 3.10+
- `xdotool` (for typing output)
- `xclip` or `xdotool` available in PATH

## Setup

```bash
# Install system deps
sudo apt install xdotool xclip portaudio19-dev

# Install Python deps
pip install -r requirements.txt

# Run
python main.py
```

## Config

Edit `config.json` to change model, device, hotkey, or typing method.

## Autostart

```bash
# Copy service file
cp fluidvoice.service ~/.config/systemd/user/
systemctl --user enable --now fluidvoice
```
