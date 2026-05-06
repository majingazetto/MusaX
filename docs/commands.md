# MusaX Command Reference

This document provides detailed information on all bytecode commands available in the MusaX sound engine (v1.9).

## Flow Control

### CMD_GOTO (0xF7)
- **Parameters:** `Address (DEFW)`
- **Description:** Performs an unconditional jump to the specified memory address.
- **Usage:** Used for repeating sections or linking melodies.

### CMD_RESTART (0xFE)
- **Parameters:** `Address (DEFW)`
- **Description:** Jumps to the address and marks the end of a global loop. In the simulator, this increments the "Loops" counter.
- **Usage:** Typically placed at the very end of a song loop.

### CMD_LOOP_S (0xF9) / CMD_LOOP_E (0xF8)
- **Parameters (S):** `Count (1 byte)`
- **Parameters (E):** None
- **Description:** Defines a loop block. The code between `LOOP_S` and `LOOP_E` will repeat `Count` times.
- **Note:** MusaX supports nested loops via an internal stack.

### CMD_CALL (0xF1) / CMD_RET (0xF0)
- **Parameters (CALL):** `Address (DEFW)`
- **Parameters (RET):** None
- **Description:** Performs a subroutine call. `CALL` pushes the return address onto the stack and jumps to the destination. `RET` pops the address and returns.
- **Note:** Useful for reusable musical phrases (motifs) across different parts of a song. Supports nesting.

## Audio & Modulation

### CMD_VOLUME (0xFC)
- **Parameters:** `Volume (0-15)`
- **Description:** Sets the base volume for the channel. This value is multiplied by the instrument envelope.

### CMD_GATE (0xFB)
- **Parameters:** `Gate (0-255)`
- **Description:** Sets the articulation/gate time for subsequent notes.
- **Math:** 0-254 represents a fraction of the note's duration. 255 represents 100% (legato).
- **Behavior (v1.9):** When the gated portion of the note has elapsed, the channel transitions to ADSR `RELEASE`. The instrument's `REL` rate then fades the envelope to silence.
- **Usage:** Lower values (e.g., 32-64) create a staccato effect with a natural release tail.

### CMD_INST (0xFA)
- **Parameters:** `InstrumentID (1 byte)`
- **Description:** Selects the instrument for the channel.
- **Resolution (v1.9):** The active source's `PTR_INST` (from its 14-byte header) points to a table of `DEFW` pointers. The engine reads the pointer at `[PTR_INST + ID*2]` and copies the 16-byte instrument record into channel state for ADSR/LFO use.
- **Defaults:** When `PTR_INST == 0`, the engine uses a built-in 4-instrument table (`Plucky`, `Vibrato Lead`, `Organ`, `Tremolo Pad`). See `technical_spec.md §5` for the full record layout.

### CMD_DETUNE (0xF5)
- **Parameters:** `Cents (1 byte, signed)`
- **Description:** Applies a fine-grained pitch offset. 
- **Range:** -128 to 127 cents.
- **Usage:** Essential for creating "fat" sounds when used across multiple channels.

### CMD_PHASE (0xF6)
- **Parameters:** `Delay (1 byte, 0-255)`
- **Description:** Applies a sub-tick timing delay.
- **Math:** A value of 128 results in a delay of exactly half a transport tick.
- **Usage:** Used to create phase offsets for chorus effects or "swing" feels.

### CMD_CHORUS (0xF4)
- **Parameters:** `Phase, Detune`
- **Description:** A macro command that applies both `CMD_PHASE` and `CMD_DETUNE` simultaneously.
- **Usage:** Standard way to set up a "wet" chorus channel relative to a "dry" lead channel.

### CMD_FADE (0xF3)
- **Parameters:** `Target (0-255), Step (0-255)`
- **Description:** Gradually adjusts the channel's master volume toward `Target`.
- **Logic:** Every 60Hz frame, the engine adds or subtracts `Step` from the current fade volume until `Target` is reached.
- **Usage:** Use `Step=255` for immediate volume changes, or small values (1-5) for smooth musical fades.

### CMD_PORTA (0xF2)
- **Parameters:** `Speed (0-255)`
- **Description:** Enables discrete chromatic pitch sliding (staircase) between notes.
- **Logic:** 
    - `0`: Portamento OFF (Notes snap immediately).
    - `>0`: Portamento ON. `Speed` defines the number of 60Hz frames to wait before stepping to the next semitone.
- **Articulation:** Slides are played **Legato** (the instrument envelope is NOT re-triggered during the steps).
- **Usage:** Used for harp-like or trombone-style chromatic runs.

## Transport

### CMD_TEMPO (0xFD)
- **Parameters:** `BPM_STEP (DEFW)`
- **Description:** Sets the transport speed for the current channel.
- **Formula:** `BPM_STEP = (BPM * BASE_TICK * 256) / 3600` (for 60Hz systems).
- **Note:** MusaX supports different tempos per channel, allowing for complex polyrhythms.
