# MusaX Technical Specification (v1.7)

MusaX is a high-precision, Z80-based sound driver and sequencer designed for retro systems. It uses a unified 16-bit timing transport to decouple musical resolution from the underlying hardware interrupt frequency.

## 1. Core Architecture
- **Streams:** 6 independent audio streams (3 Music + 3 FX).
- **Channels:** 3 physical PSG/hardware channels (A, B, C).
- **Priority System:** "Winner-takes-all" on a per-channel basis. Active FX streams override Music streams on the same physical channel.
- **Timing:** 16-bit fixed-point accumulator per stream (8-bit fraction).
- **Base Resolution:** 768 ticks per Quarter Note (Negra).

## 2. Timing Standards (768-tick)
The 768-tick resolution allows for perfect integer division of standard musical divisions and triplets:
- `LEN_W` (Whole/Redonda): 3072
- `LEN_H` (Half/Blanca): 1536
- `LEN_Q` (Quarter/Negra): 768
- `LEN_E` (Eighth/Corchea): 384
- `LEN_S` (Sixteenth/Semicorchea): 192
- `LEN_ET` (Eighth Triplet): 256
- `LEN_QT` (Quarter Triplet): 512

## 3. Bytecode Structure
MusaX uses a stream-based bytecode format. Every event is either a **Note** or a **Command**.

### Note Format
`[NoteID (1 byte)], [Duration (2 bytes, DEFW)]`
- `NoteID`: 0-95 (C-0 to B-7). 255 (REST).
- `Duration`: 16-bit value in ticks.

### Command Reference
| Command | Hex | Parameters | Description |
|---------|-----|------------|-------------|
| `CMD_RESTART` | `0xFE` | `Addr (DEFW)` | Loop back to address and reset loop counter. |
| `CMD_TEMPO` | `0xFD` | `BPM_STEP (DEFW)` | Set channel-specific transport speed. |
| `CMD_VOLUME` | `0xFC` | `Vol (0-15)` | Set channel volume. |
| `CMD_GATE` | `0xFB` | `Val` | Set gate time (0-255). |
| `CMD_INST` | `0xFA` | `ID` | Select instrument envelope. |
| `CMD_LOOP_S` | `0xF9` | `Count` | Start a loop block. |
| `CMD_LOOP_E` | `0xF8` | | End a loop block (repeat if count > 0). |
| `CMD_GOTO` | `0xF7` | `Addr (DEFW)` | Unconditional jump to address. |
| `CMD_PHASE` | `0xF6` | `Val` | Sub-tick delay (0-255). Shifts event timing. |
| `CMD_DETUNE` | `0xF5` | `Val (signed)` | Fine pitch offset in cents. |
| `CMD_CHORUS` | `0xF4` | `Phase, Detune` | Combined command for PSG Chorus effect. |
| `CMD_FADE` | `0xF3` | `Target, Step` | Per-channel volume fade (0-255). |

## 4. Header Formats

### Music Header (12 bytes)
`[BPM_A (DEFW)], [PTR_A (DEFW)], [BPM_B (DEFW)], [PTR_B (DEFW)], [BPM_C (DEFW)], [PTR_C (DEFW)]`

### FX Header (Header-less Streams)
FX are triggered by requesting a stream address. FX default to `#2400` (~168 BPM) for fast execution unless a `CMD_TEMPO` is specified within the stream.

## 5. High-Precision Simulator (v1.7)
The Python simulator (`musax_sim.py`) implements a **sample-accurate** rendering engine. 
- **Transport:** Advanced per-audio-sample to eliminate 60Hz quantization.
- **Phase Rendering:** `CMD_PHASE` delays are rendered by shifting event execution by precise sample counts.
## 6. Documentation Maintenance
This specification and the accompanying `commands.md` and `simulator.md` MUST be updated whenever:
- A new bytecode command is added or an existing one is modified.
- The internal timing or transport logic changes.
- The simulator adds new features, interactive controls, or log formats.
- Architectural decisions (like priority systems) are refactored.

