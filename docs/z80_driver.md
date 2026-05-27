# MusaX Z80 Sound Driver Integration Guide (v1.9)

This document provides a comprehensive guide for integrating the **MusaX Z80 Sound Driver** into MSX and other Z80-based ROM/RAM projects. It details the file architecture, memory map, ROM/RAM placement constraints, Z80 assembly standards, and performance characteristics of the engine.

---

## 1. Overview

MusaX is a 6-stream, high-precision Z80 sound driver designed for musical expression and concurrent SFX playback. It handles:
- **3 Music Streams** (Channels A, B, C mapped to physical PSG channels 0..2)
- **3 SFX Streams** (Channels A, B, C mapped to physical PSG channels 3..5)
- **Winner-Takes-All SFX Priority System**: Active SFX streams override music streams on the same PSG channel.
- **Ghost Playback**: Music streams continue to be processed in the background when overwritten by SFX to keep tempo and sync intact.
- **16.8 Fixed-Point Timing**: Decouples ticks from hardware refresh rates, allowing sub-tick precision and micro-timing.
- **Envelope (ADSR) & Modulation (LFO)**: Processed per-channel at 60Hz.

---

## 2. File Architecture

The core engine is divided into four files in the `MusaX/src/driver/` directory:

1. **`CONST.Z8A`**: Unified constants containing note indices, durations (in 768-tick base), command bytecode values, and channel/instrument record offsets.
2. **`VARS.Z8A`**: RAM allocations for variables and channel states.
3. **`TABLES.Z8A`**: Pitch frequency tables for the PSG (MSX at 3.57 MHz), LFO wavetables, ADSR/LFO scaling, and precomputed lookup tables (vibrato multipliers, fade, and volume tables).
4. **`MUSAX.Z8A`**: The primary assembly module containing the update loop, parser, sound synthesis envelopes, LFO, and physical PSG register committer.

---

## 3. Memory Map & RAM Allocation

The RAM footprint of MusaX variables is exactly **268 bytes** (under v1.9). This memory block is defined between `MUSVARS` and `MUSEVARS` inside `VARS.Z8A`.

### Core Memory Buffers

- **`CHANNELS`** (`DEFS CHSIZE * 6` = 192 bytes): Stores the state of the 6 active streams. Each channel state is 32 bytes (`CHSIZE`).
- **`PSGMUS`** (`DEFS 14`): Shadow registers containing the PSG values computed by the music sequencer.
- **`PSGSFX`** (`DEFS 14`): Shadow registers containing the PSG values computed by the SFX sequencer.
- **`PSGREG`** (`DEFS 14`): Merged registers containing the finalized values from `PSGMUS` and `PSGSFX` according to the active `SFXMSK`.
- **`PSGOLD`** (`DEFS 14`): Previous physical register state, used to perform delta-writes to the hardware PSG to minimize register-writing overhead.
- **`SFXMSK`** (`DEFB 0`): Active SFX mask (bits 0, 1, 2 represent channels A, B, C). If set, the SFX shadow register overrides the music shadow.

---

## 4. ROM/RAM Integration Guide

For external ROM projects (like *Ghostly Manor* or *Wizard of Wor*), the engine must be integrated using the preprocessor define `_MUSAX_INTEGRATION_`. This allows placing the driver code, tables, and variables in their respective ROM and RAM banks.

### Integration Steps

1. Define `_MUSAX_INTEGRATION_` in your main codebase.
2. Explicitly include `CONST.Z8A` in your constants section.
3. Explicitly include `VARS.Z8A` within your RAM segment (between `#C000` and `#FFFF` on MSX).
4. Include `TABLES.Z8A` and `MUSAX.Z8A` within your ROM segment.

### Assembly Example (sjasmplus)

```assembly
; --- CONSTANTS SEGMENT ---
            DEFINE  _MUSAX_INTEGRATION_
            INCLUDE "MusaX/src/driver/CONST.Z8A"

; --- RAM SEGMENT ---
            ORG     #C000
            
            ; Your game variables
            
            ; Include MusaX RAM variables
            INCLUDE "MusaX/src/driver/VARS.Z8A"

; --- ROM SEGMENT ---
            ORG     #4000
            
            ; Your game code
            
            ; Include MusaX tables (must be page-aligned to 256 bytes)
            ALIGN   256
            INCLUDE "MusaX/src/driver/TABLES.Z8A"
            
            ; Include MusaX driver code
            INCLUDE "MusaX/src/driver/MUSAX.Z8A"
```

---

## 5. API Reference

The Z80 driver exports the following subroutines. All entry points preserve the registers unless stated otherwise.

### `MUSINIT`
- **Description**: Initializes the sound engine. It clears all engine variables, resets the channel indexes, sets the delta-write shadow buffer (`PSGOLD`) to `#FF` (forcing a complete rewrite on the next frame), reads the current hardware Mixer (R7) configuration to preserve bits 6-7 (I/O configuration used for keyboard/joystick scanning), and silences the PSG.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: None

### `MUSPLAY`
- **Description**: Starts playing a song module. It sets the active playing status, resets song timers, initializes the 3 music channels (0..2), and registers the song's custom instrument pointer table.
- **Inputs**:
  - `HL` = Address of the Song Header
  - `A` = Song loop limit (`0` = infinite loops)
- **Outputs**: None
- **Clobbers**: None

### `MUSSTOP`
- **Description**: Instantly stops song playback and silences all channels.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: None

### `MUSPAUS`
- **Description**: Toggles the playback pause state for music streams. If the music is currently playing, it silences the music shadow registers (`PSGMUS`) and commits the change, leaving active sound effects (SFX) running. It pauses the music sequencer updates while allowing SFX updates (streams 3..5) to continue processing. If the music is paused, it resumes music playback from the exact position on the next frame update.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: AF, BC, DE, HL

### `MUSUPDAT`
- **Description**: Updates the sequencer and synthesizers. This routine processes all 6 audio streams (portamento, ADSR envelopes, LFO modulations, and bytecode events) and updates the global volume fade. Finally, it calls `MUSMERGE` to build the new `PSGREG` shadow buffer.
- **Timing**: MUST be called once per vertical blank interrupt (VBLANK).
- **Execution Safety**: Do NOT call `MUSCOMM` directly inside `MUSUPDAT` if you want to avoid VDP CPU access jitter. Call it in your critical interrupt routine.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: None

### `MUSMERGE`
- **Description**: Merges `PSGMUS` and `PSGSFX` shadow registers into the unified `PSGREG` buffer. If `SFXMSK` is zero, a fast copy path is executed via unrolled `LDI` operations. If SFX is active, it blends registers on a per-channel basis and preserves R7 bits 6-7.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: None

### `MUSCOMM`
- **Description**: Commits the contents of `PSGREG` to the physical PSG hardware registers using delta-writes. It compares each register in `PSGREG` with `PSGOLD`. If they differ, it writes the register to ports `PSGADR` (`#A0`) and `PSGWR` (`#A1`), then caches the new value in `PSGOLD`.
- **Interrupt Safety**: Fully interrupt-safe. It preserves all registers so it can be called from asynchronous interrupt service routines.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: None

### `FXPLAY`
- **Description**: Plays a sound effect. It checks if the requested SFX has equal or higher priority than the currently active SFX. If so, it updates the active priority and initializes channels 3..5 with the SFX streams.
- **Inputs**:
  - `A` = Sound Effect ID (0-based index in `FX_TABLE`)
- **Outputs**: None
- **Clobbers**: None

### `MUSMUT`
- **Description**: Instantly silences the three shadow buffers (`PSGMUS`, `PSGSFX`, `PSGREG`) and preserves Mixer R7 configuration.
- **Inputs**: None
- **Outputs**: None
- **Clobbers**: None

### `MUSFADE`
- **Description**: Initiates a global volume fade.
- **Inputs**:
  - `A` = Target volume (`0`..`255`)
  - `H` = Fade speed (`1`..`255`, where `255` = snap immediately to target)
- **Outputs**: None
- **Clobbers**: None

---

## 6. Channel State Structure (32 bytes)

Each channel state occupies exactly 32 bytes (`CHSIZE`). The variable offsets are defined in `CONST.Z8A`:

| Constant | Offset | Size | Description |
|---|---|---|---|
| `CHPC` | 0 | 2 bytes | Program Counter (bytecode pointer) |
| `CHWAIT` | 2 | 2 bytes | Wait ticks integer accumulator |
| `CHFRAC` | 4 | 1 byte | Wait ticks fractional transport accumulator |
| `CHBPM` | 5 | 2 bytes | Channel BPM step (8.8 fixed-point) |
| `CHVOL` | 7 | 1 byte | Current volume (0..15) |
| `CHGATE` | 8 | 1 byte | Gate time multiplier (0..255) |
| `CHINST` | 9 | 2 bytes | Current instrument record address |
| `CHLCOUNT`| 11 | 1 byte | Loop counter (finite repeats) |
| `CHLADDR` | 12 | 2 bytes | Loop start address |
| `CHADSRS` | 14 | 1 byte | ADSR State (0 = IDLE, 1 = ATT, 2 = DEC, 3 = SUS, 4 = REL) |
| `CHADSRA` | 15 | 1 byte | ADSR envelope accumulator (0..255) |
| `CHLFOP`  | 16 | 1 byte | LFO Phase counter (0..255) |
| `CHLFOD`  | 17 | 1 byte | LFO Delay counter |
| `CHINSID` | 18 | 1 byte | Cached instrument ID (#FF = none) |
| `CHLFOVAL`| 19 | 1 byte | Active LFO value (signed cents/vol offset) |
| `CHNOTE`  | 20 | 1 byte | Current note index (0..95, 255 = REST) |
| `CHFADVOL`| 21 | 1 byte | Channel fade volume multiplier (0..255) |
| `CHFADTRG`| 22 | 1 byte | Channel fade target volume (0..255) |
| `CHFADSPD`| 23 | 1 byte | Channel fade speed/step (0 = inactive) |
| `CHRET`   | 24 | 2 bytes | Subroutine return PC |
| `CHDETUNE`| 26 | 1 byte | Fine detune offset (signed cents) |
| `CHPORTAS`| 27 | 1 byte | Portamento speed (frames per semitone, 0 = off) |
| `CHPORTAT`| 28 | 1 byte | Portamento timer |
| `CHPORTAN`| 29 | 1 byte | Portamento target note |
| `CHIDX`   | 30 | 1 byte | Stream channel index (0..5) |
| `CHLDEST` | 31 | 1 byte | Active LFO destination copy |

---

## 7. PSG Audio Register Mapping

MusaX is mapped to write to the physical PSG (AY-3-8910 / YM2149) through ports `#A0` (Address select), `#A1` (Write data), and `#A2` (Read data).
For systems with different port address mapping (like Sega Master System, MSX clones, or other custom Z80 boards), the ports are defined as constants in `CONST.Z8A`:

- `PSGADR EQU #A0`
- `PSGWR  EQU #A1`
- `PSGRD  EQU #A2`

All writes to these ports must route through `MUSCOMM`.

---

## 8. Z80 Development Constraints

When modifying or expanding the driver, several strict architecture rules must be followed:

1. **Upper-Case Standard**: All mnemonics, registers, labels, and comments must be in UPPERCASE.
2. **Label Limitations**: Labels must be 8 characters or fewer, containing no underscores and no colons. Local labels must be prefixed with a dot (e.g. `.INITCH`).
3. **No Lowercase in Comments**: Comments must be in UPPERCASE, with exceptions allowed only for note flat notations (e.g., `Cb`).
4. **Immediate Register Indexing**: In Z80, `LD (IX+offset), immediate` is illegal. You must load the value to a register first:
   ```assembly
   ; ILLEGAL
   LD (IX + CHGATE), 255
   
   ; CORRECT
   LD A, 255
   LD (IX + CHGATE), A
   ```
5. **No 16-bit Indirect Loads (except HL)**: Instruction `LD (DE), L` or `LD (DE), H` is illegal. Write 16-bit values through register `A` or use stack operations.
6. **Bit Shift Operators**: Do not double a register using `ADD B,B`. Sjasmplus compiles this as `ADD A,B` twice without throwing a warning. Use `SLA B` instead.
7. **Far Jumps**: For conditionals branching to targets out of range, replace `JR` with `JP`.

---

## 9. CPU Cycle Optimizations & Performance

MusaX uses precomputed tables to minimize CPU usage, saving over **7,100 T-states per frame** compared to linear implementations.

- **Fast Multiplier**: The 8-bit multiplier `MUL8` is replaced with a precomputed quarter-square lookup table `SQR_TBL` (512 bytes aligned to 256 bytes) using `MUL8_HIGH`, completing in ~108 T-states (3x faster than shift-and-add).
- **LFO Inactive Fast-Exit**: The channel state caches `CHLDEST` (offset 31). If LFO is inactive, `UPDLFO` exits immediately in 28 T-states.
- **Fast VBL VRAM Dump**: The jukebox visual buffer `SCRBUFF` utilizes an inline unrolled `OUTI` loop in the interrupt handler (`IRQINT`), copying 768 bytes in ~14,160 T-states (safely fitting within NTSC's VBLANK window).
- **Direct Pointer Lookups**: Branchless mapping tables (`VOL_PTR_TBL`, `PER_PTR_TBL`, `MIX_PTR_TBL`) map channel indices directly to shadow address registers without dynamic offset logic.

---

## 10. Sample Interrupt Service Routine (ISR)

To ensure jitter-free PSG execution, separate channel processing (`MUSUPDAT`) from register commit (`MUSCOMM`) in your interrupt handler. 

> [!NOTE]
> If you are hooking into the MSX BIOS vertical retrace hook **`H.TIMI`** (`#FD9F`), register preservation (`PUSH`/`POP`) is completely redundant because the BIOS already saves and restores all CPU registers before calling the hook.

```assembly
; --- MSX INTERRUPT HANDLER HOOK (VBLANK) ---
; REGISTERED AT H.TIMI (#FD9F)
; NOTE: NO PUSH/POP NEEDED AS BIOS PRESERVES ALL REGISTERS BEFORE H.TIMI CALL.
VBLANK_HOOK:
            ; 1. INSTANTLY WRITE BUFFERED REGISTERS TO PSG
            ; THIS REMOVES JITTER AND TIMING GLITCHES
            CALL    MUSCOMM
            
            ; 2. (OPTIONAL) VRAM BLOCK DUMPS HERE
            ; e.g. COPY GRAPHICAL DASHBOARDS OUT OF VBLANK WINDOW
            
            RET

; --- MAIN GAME LOOP ---
GAME_LOOP:
            ; Wait for VBLANK synchronization
            CALL    WAIT_VBLANK
            
            ; Execute game logic, physics, and input scanning
            CALL    UPDATE_GAME
            
            ; 3. UPDATE SEQUENCE DATA FOR THE NEXT FRAME
            ; THIS CAN RUN WITH INTERRUPTS ENABLED (NO DI/EI REQUIRED)
            CALL    MUSUPDAT
            
            JP      GAME_LOOP
```
