# MusaX Command Reference

This document provides detailed information on all bytecode commands available in the MusaX sound engine (v1.7).

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

## Audio & Modulation

### CMD_VOLUME (0xFC)
- **Parameters:** `Volume (0-15)`
- **Description:** Sets the base volume for the channel. This value is multiplied by the instrument envelope.

### CMD_INST (0xFA)
- **Parameters:** `InstrumentID (1 byte)`
- **Description:** Selects the ADSR/Volume envelope for the channel.
- **Default Envelopes:**
  - `0`: Simple decay.
  - `1`: Sustained square.

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

## Transport

### CMD_TEMPO (0xFD)
- **Parameters:** `BPM_STEP (DEFW)`
- **Description:** Sets the transport speed for the current channel.
- **Formula:** `BPM_STEP = (BPM * BASE_TICK * 256) / 3600` (for 60Hz systems).
- **Note:** MusaX supports different tempos per channel, allowing for complex polyrhythms.
