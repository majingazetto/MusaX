# MusaX Simulator User Guide

The MusaX Simulator (`musax_sim.py`) is a real-time playback and debug tool for `.Z8A` music and sound effect files.

## Requirements
- Python 3.8+
- `pyaudio` or `sounddevice` (for real-time playback)
- `lame` (optional, for MP3 export)

## Usage
```bash
python3 musax_sim.py <music_file.Z8A> [fx_file.Z8A] [options]
```

### Positional Arguments
- `music_file`: The primary song file (Music streams A, B, C).
- `fx_file`: (Optional) A secondary file containing sound effects.

### Options
- `--export, -e [filename]`: Renders the song to a `.wav` or `.mp3` file. If no filename is provided, it uses the input filename.
- `--time, -t <seconds>`: Limit the duration of the export (default: 30s).
- `--loops, -l <count>`: Number of loops to render/play (default: 0 = infinite).
- `--debug-log <file>`: Generates a timestamped execution trace for debugging.

## Interactive Controls (Real-time mode)
- `[1-9]`: Trigger FX from the library (if `fx_file` is provided).
- `[SPACE]`: Reset/Restart playback from the beginning.
- `[p]`: Pause/Resume playback.
- `[n]`: Advance to next event (while paused).
- `[b]`: Step backward to previous event (while paused).
- `[a/s/d]`: Mute/Unmute Music channels A, B, and C.
- `[f/g/h]`: Mute/Unmute FX channels A, B, and C.
- `[q] or [ESC]`: Quit the simulator.

## Dashboard Overview
The dashboard provides a live view of the engine state:
- **T**: Global tick counter.
- **SFX**: Bitmask of active FX channels.
- **P**: Current FX priority.
- **Loops**: Total song loops completed.
- **CH**: Channel name (MUA/FXA, MUB/FXB, MUC/FXC).
- **STATE**: Channel status (`ON`, `OFF`, or `MUT` for muted).
- **WAIT**: Remaining ticks until the next event.
- **VOLUME**: Visual envelope monitor and scale.
- **FADE**: Current per-channel volume multiplier (0-100%).
- **BPM**: Calculated real-time tempo per channel.
- **SLIDE**: Current `CMD_PORTA` speed (frames/semitone).
- **ADSR**: ADSR phase and envelope accumulator (`---/ATT/DEC/SUS/REL` + 0-255).
- **PC**: Program counter (hex offset).
- **FRAC**: 8-bit accumulator fraction (`.XXX`).
- **HEX SNIP**: Live bytecode preview.

## Debugging with Trace Logs
The `--debug-log` output is essential for verifying timing:
- `T:500 | CH:0 | PC:01B | NOTE: C-4, wait:768`
- Trace logs in v1.7+ show sub-frame timing, meaning notes can trigger between 60Hz interrupts in the internal logic.
