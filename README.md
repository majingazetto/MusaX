# MusaX

**MusaX** (Music & Sound for X) is a universal, high-precision sound driver and MML-like sequencer for retro systems. 

Born on the MSX but designed for any Z80-based (and beyond) machine, MusaX focuses on musical expression, rhythmic precision, and developer flexibility.

## Core Pillars

- **Musical Expression:** Built by musicians. Native support for triplets, complex polyrhythms, and syncopation.
- **Rhythmic Precision:** Uses a 16-bit Fixed-Point Accumulator to ensure perfect timing (Delta-Time) regardless of CPU clock or interrupt frequency (50/60Hz).
- **Agnostic Architecture:** The core transport and bytecode logic are separated from the physical hardware (PSG/AY-3-8910, etc.) and frequency tables.
- **Composer Friendly:** Write music using readable constants (`C4`, `LEN_Q`) in Z80 assembly files.
- **Developer Ready:** Includes a Python-based simulator to test, debug, and visualize your compositions before deploying to hardware.

## Architecture

- **Shadow Registers:** Prevents register collision and handles SFX priorities with a bitmask system.
- **Ghost Playback:** Music engine continues processing in the background during SFX, maintaining perfect synchronization.
- **Bytecode Stream:** Delta-timed events with support for Loops, Absolute Jumps, and Call/Return subroutines.
- **Advanced Modulation:** Support for per-frame Macros, LFOs (Vibrato/Tremolo), and software-emulated filters.

## Requirements

### Python Simulator (`tools/musax_sim.py`)
To run the real-time simulator, you need Python 3 and the PyAudio library. On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3-pyaudio portaudio19-dev
```

## Project Structure

- `src/`: Core assembly source code (Universal Z80).
- `tools/`: Python-based simulator and debugging tools.
- `docs/`: Technical specifications and hardware frequency tables.
- `examples/`: Example songs and usage patterns.

## Getting Started

Check out the documentation in the `docs/` directory:
- [Technical Specification](docs/technical_spec.md): Architecture, timing, and core concepts.
- [CLI Hub Guide](docs/cli_hub.md): Using the unified `musax.py` developer tool.
- [Command Reference](docs/commands.md): Comprehensive guide to MusaX bytecode.
- [Simulator User Guide](docs/simulator.md): How to use `musax_sim.py` for real-time debugging.

To try the engine immediately, use the **CLI Hub** to play an example:
```bash
python3 tools/musax.py play examples/msl/song_demo.msl
```

Or run the chorus demonstration directly from Z8A:
```bash
python3 tools/musax.py play examples/z8a/chorus_test.Z8A
```

---
Developed with passion for retro computing and musical excellence.
