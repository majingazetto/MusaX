# MusaX Technical Specification (v1.9)

MusaX is a high-precision, Z80-based sound driver and sequencer designed for retro systems. It uses a unified 16-bit timing transport to decouple musical resolution from the underlying hardware interrupt frequency.

## 1. Core Architecture
- **Streams:** 6 independent audio streams (3 Music + 3 FX).
- **Channels:** 3 physical PSG/hardware channels (A, B, C).
- **Priority System:** "Winner-takes-all" on a per-channel basis. Active FX streams override Music streams on the same physical channel.
- **Timing:** 16-bit fixed-point accumulator per stream (8-bit fraction).
- **Base Resolution:** 768 ticks per Quarter Note (Negra).
- **Synthesis (v1.9):** Per-note ADSR envelope and LFO modulator (vibrato/tremolo) driven by a per-source instrument table.

## 2. Timing Standards (768-tick)
The 768-tick resolution allows for perfect integer division of standard musical divisions and triplets:
- `LEN_W` (Whole/Redonda): 3072
- `LEN_H` (Half/Blanca): 1536
- `LEN_Q` (Quarter/Negra): 768
- `LEN_E` (Eighth/Corchea): 384
- `LEN_S` (Sixteenth/Semicorchea): 192
- `LEN_T` (Thirty-second/Fusa): 96
- `LEN_ET` (Eighth Triplet): 256
- `LEN_QT` (Quarter Triplet): 512
- `LEN_HT` (Half Triplet): 1024
- `LEN_QD` (Dotted Quarter): 1152
- `LEN_ED` (Dotted Eighth): 576
- `LEN_HD` (Dotted Half): 2304
- `LEN_WD` (Dotted Whole): 4608
- `LEN_QDD` (Double Dotted Quarter): 1344
- `LEN_EDD` (Double Dotted Eighth): 672

## 3. MusaX-ML (MSL) Features
The MSL compiler (v1.1) supports advanced rhythmic and metadata features.

### Metadata Tags
MSL files can include descriptive headers that are reflected in the generated assembly:
- `@TITLE "Song Title"`: Used for descriptive labels and headers.
- `@AUTHOR "Name"`: Included in the generated file header.
- `@DESC "Description"`: Included in the generated file header.

### Advanced Rhythmic Notation
- **Multiple Dots:** Append `.` for dotted (1.5x), `..` for double-dotted (1.75x), etc.
- **Triplets:** Append `t` to a note (e.g., `C8t`) or a group (e.g., `{ C D E }t`) to apply a 2/3 duration factor.

## 4. Bytecode Structure
MusaX uses a stream-based bytecode format. Every event is either a **Note** or a **Command**.

### Note Format
`[NoteID (1 byte)], [Duration (2 bytes, DEFW)]`
- `NoteID`: 0-95 (C-0 to B-7). 255 (REST).
- `Duration`: 16-bit value in ticks. `REST 0` (FF 00 00) is an immediate STOP for the channel.

### Command Reference
| Command | Hex | Parameters | Description |
|---------|-----|------------|-------------|
| `CMD_RESTART` | `0xFE` | `Addr (DEFW)` | Loop back to address and reset loop counter. |
| `CMD_TEMPO` | `0xFD` | `BPM_STEP (DEFW)` | Set channel-specific transport speed. |
| `CMD_VOLUME` | `0xFC` | `Vol (0-15)` | Set channel volume. |
| `CMD_GATE` | `0xFB` | `Val` | Set gate time (0-255). Triggers ADSR Release at the gate point. |
| `CMD_INST` | `0xFA` | `ID` | Select instrument from the active source's pointer table. |
| `CMD_LOOP_S` | `0xF9` | `Count` | Start a loop block. |
| `CMD_LOOP_E` | `0xF8` | `Modifier` | End a loop block. Modifier `t` applies triplet factor to the body. |
| `CMD_GOTO` | `0xF7` | `Addr (DEFW)` | Unconditional jump to address. |
| `CMD_PHASE` | `0xF6` | `Val` | Sub-tick delay (0-255). Shifts event timing. |
| `CMD_DETUNE` | `0xF5` | `Val (signed)` | Fine pitch offset in cents. |
| `CMD_CHORUS` | `0xF4` | `Phase, Detune` | Combined command for PSG Chorus effect. |
| `CMD_FADE` | `0xF3` | `Target, Step` | Per-channel volume fade (0-255). |
| `CMD_PORTA` | `0xF2` | `Speed` | Chromatic staircase (frames/semitone). |
| `CMD_CALL` | `0xF1` | `Addr (DEFW)` | Call a subroutine phrase. |
| `CMD_RET` | `0xF0` | None | Return from a subroutine phrase. |

## 5. Header Formats (v1.9)

### Music Header — 15 bytes

```
HDR:
    DEFB    TYPE_SONG       ; #80 — identifies this as a song header (1 byte)
    DEFW    BPM_A, PTR_A    ; Channel A: tempo step + entry point (4 bytes)
    DEFW    BPM_B, PTR_B    ; Channel B (4 bytes)
    DEFW    BPM_C, PTR_C    ; Channel C (4 bytes)
    DEFW    PTR_INST_TBL    ; Instrument table pointer; 0 = engine defaults (2 bytes)
```

Total: **15 bytes** (1 + 4 + 4 + 4 + 2).

### FX Header — 9 bytes

```
HDR_FX:
    DEFB    TYPE_FX         ; #81 — identifies this as an FX header (1 byte)
    DEFW    PTR_A           ; Channel A entry point; 0 = channel unused (2 bytes)
    DEFW    PTR_B           ; Channel B (2 bytes)
    DEFW    PTR_C           ; Channel C (2 bytes)
    DEFW    PTR_INST_TBL    ; Instrument table pointer; 0 = engine defaults (2 bytes)
```

Total: **9 bytes** (1 + 2 + 2 + 2 + 2). FX headers carry no BPM fields; each FX channel uses its own default tempo (168 BPM).

### FX Table

Each registered effect in `FX_TABLE` is a `DEFW PTR_HDR, PRIORITY` pair, terminated by `DEFW 0, 0`. A higher `PRIORITY` value overrides a lower-priority active FX on the same channel.

### Instrument Table

`PTR_INST_TBL` points to a table of 16 `DEFW` pointers (one per instrument slot, IDs 0–15). Each non-zero entry points to a 16-byte instrument record. Slots not used are `DEFW 0`. When `PTR_INST_TBL == 0`, the engine uses its built-in 5-instrument default table.

## 5. Instrument System (v1.9)

### Indirection Chain
```
Header.PTR_INST  ─►  INST_TBL[N*2]  ─►  16-byte instrument record
```

`CMD_INST N` reads the pointer at `[PTR_INST + N*2]` and resolves the 16-byte record. The Z80 cost of resolution is `ADD HL,HL` (×2), one indirect load (`LD E,(HL); INC HL; LD D,(HL)`), and a copy of 16 bytes into channel-state RAM.

### 16-byte Instrument Record
| Offset | Bytes | Field | Range | Description |
|--------|-------|-------|-------|-------------|
| 0 | 1 | `ATT` | 0–255 | Attack rate — added to envelope accumulator per frame until ≥ 255. |
| 1 | 1 | `DEC` | 0–255 | Decay rate — subtracted per frame until ≤ `SUS`. |
| 2 | 1 | `SUS` | 0–255 | Sustain level — envelope is held at this value. |
| 3 | 1 | `REL` | 0–255 | Release rate — subtracted per frame after gate, until ≤ 0. |
| 4 | 1 | `LFODEST` | 0–2 | LFO destination: `0`=off, `1`=pitch (vibrato), `2`=volume (tremolo). |
| 5 | 1 | `LFOWAVE` | 0–2 | LFO waveform: `0`=triangle, `1`=sawtooth, `2`=square. |
| 6 | 1 | `LFOPARS` | — | Packed byte: **high nibble = speed (0–15)**, **low nibble = amplitude (0–15)**. |
| 7 | 1 | `LFODELAY` | 0–255 | Frames before LFO begins after note-on. |
| 8 | 1 | `FLAGS` | 0 | Reserved for future flags. Always 0. |
| 9–15 | 7 | `RES` | 0 | Reserved — zero-fill. |

> **LFOPARS packing:** The MSL `@INST` block accepts `speed` and `amp` as separate integers (0–15 each). The compiler packs them into `LFOPARS` as `(speed << 4) | (amp & 0x0F)`. Values outside 0–15 will corrupt the adjacent nibble.

### ADSR State Machine
| State | Code | Behavior |
|-------|------|----------|
| IDLE | 0 | Output silent. Awaits a note. |
| ATTACK | 1 | `acc += ATT` per frame until `>= 255`, then -> DECAY. |
| DECAY | 2 | `acc -= DEC` per frame until `<= SUS`, then -> SUSTAIN. |
| SUSTAIN | 3 | `acc` held at SUS until release is triggered. |
| RELEASE | 4 | `acc -= REL` per frame until `<= 0`, then -> IDLE. |

Release is triggered by `CMD_GATE` (when the gated portion of the note has elapsed) or by external request.

The four phases can be visualized as follows:

```text
      Amplitude
          ^
          |
      1.0 +      / \
          |     /   \
          |    /     \
Sustain Level +---/-------\_________
          |  /         |         \
          | /          |          \
      0.0 +------------+-----------+-----> Time
              A        D         S         R
```

### LFO Engine
- Phase counter `0-255` advances by `speed` units per frame.
- Wave output is signed `[-127, +127]`, scaled by amplitude `(0-15)/15`.
- Vibrato (`LFODEST=1`): output applied as cents offset on top of `CMD_DETUNE`.
- Tremolo (`LFODEST=2`): output added to the ADSR-scaled volume.
- LFO is gated by ADSR: it only updates while `adsr_state != IDLE`.

### Default Instruments (PTR_INST == 0)

Five built-in instruments are available when no custom table is defined:

| ID | Name | ATT | DEC | SUS | REL | LFO |
|----|------|-----|-----|-----|-----|-----|
| 0 | Linear Decay | 255 | 16 | 0 | 1 | — |
| 1 | Plucky | 255 | 10 | 200 | 20 | — |
| 2 | Smooth Lead | 10 | 5 | 255 | 10 | Pitch, triangle, speed=8, amp=4, delay=20 |
| 3 | Full Sustain (Organ) | 255 | 0 | 255 | 0 | — |
| 4 | Ambient Pad | 5 | 10 | 150 | 5 | Volume, triangle, speed=6, amp=8 |

## 6. High-Precision Simulator
The Python simulator (`musax_sim.py`) implements a **sample-accurate** rendering engine.
- **Transport:** Advanced per-audio-sample to eliminate 60Hz quantization.
- **Phase Rendering:** `CMD_PHASE` delays are rendered by shifting event execution by precise sample counts.
- **Instrument resolution** uses a flat byte-addressed memory map built once after the load pass; `resolve_instrument(table_ptr, id)` returns the cached 16-byte blob (or the default fallback) and stores it on the channel for ADSR/LFO consumption.

## 7. Documentation Maintenance
This specification and the accompanying `commands.md` and `simulator.md` MUST be updated whenever:
- A new bytecode command is added or an existing one is modified.
- The internal timing or transport logic changes.
- The simulator adds new features, interactive controls, or log formats.
- Architectural decisions (priority systems, header formats, instrument layout) are refactored.
