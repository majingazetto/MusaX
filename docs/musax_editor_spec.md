# MusaX-ML Editor Specification

This document outlines the design and specification for a TUI-based editor for the MusaX sound engine. The editor will use a custom Music Macro Language (MML) dialect, tentatively named "MusaX-ML", designed for fluid composition.

## 1. Core Philosophy & Technology Stack

- **Paradigm:** MML (Music Macro Language) editor, not a tracker. The goal is compositional fluidity over rigid grid-based entry.
- **Technology:** A TUI (Textual User Interface) application built in Python, likely using the `textual` framework.
- **Integration:** The editor will be tightly integrated with `musax_sim.py` to provide real-time audio preview.

## 2. Editor Layout (TUI)

The proposed layout consists of several interactive panes:
- **Channel Panes:** Three primary text-editing panes, one for each PSG channel (A, B, C). Users will write MusaX-ML directly into these panes.
- **Instrument Editor Pane:** A form-based pane for creating and modifying the 16-byte instrument records. This will use sliders, input boxes, and dropdown menus instead of requiring direct hex entry.
- **Status/Output Pane:** A read-only pane for displaying compiler messages, errors, and application status.

## 3. "MusaX-ML" Language Specification

### 3.1. Note & Time Syntax

- **Notes:** `C, D, E, F, G, A, B`. Case-insensitive.
- **Alterations:**
    - Sharps: `+` or `#`. Example: `C#`, `F+`.
    - Flats: `-` or `b`. Example: `Bb`, `E-`.
- **Octave:**
    - `O<num>`: Sets the current octave (e.g., `O4`).
    - `>`: Increase octave by 1.
    - `<`: Decrease octave by 1.
- **Duration:**
    - `L<num>`: Sets the default note duration (e.g., `L4` for quarter notes, `L8` for eighth notes).
    - A number directly after a note sets its specific duration. Example: `C4`, `E8`, `G16`.
    - A `.` after the duration number creates a dotted note (duration * 1.5). Example: `C4.`.
- **Rests (Silences):**
    - `R`. Follows the same duration rules as notes. Example: `R4`, `R8.`.

### 3.2. MusaX Engine Commands

MusaX-specific commands are prefixed with `@`.

- `@V<num>`: Sets channel volume (0-15).
- `@I<num>`: Sets the active instrument (by ID).
- `@T<hex>`: Sets the tempo via the `BPM_STEP` value. Must be a hex value. Example: `@T#0600`.
- `@G<num>`: Sets the note gate time (0-255).
- `@P<num>`: Sets the portamento speed.
- `@F(<target>,<step>)`: Initiates a volume fade.
- `@D<num>`: Applies a signed pitch detune in cents.
- `@PH<num>`: Applies a sub-tick phase delay.
- `@CH(<phase>,<detune>)`: A macro for the Chorus command.

### 3.3. Flow Control: Labels, Loops, and Jumps

To enable non-linear song structures, the following syntax will be supported:

- **Labels:** A name followed by a colon defines a jump destination.
  ```mml
  MAIN_LOOP:
    C D E F
  ```
- **Local Loops:** A block of MML surrounded by `{...}` and followed by a number will be repeated. The compiler translates this to `CMD_LOOP_S`/`CMD_LOOP_E`.
  ```mml
  { C E G }4 // Arpeggio repeats 4 times
  ```
- **Jumps:**
    - `@GOTO(label)`: Compiles to a `CMD_GOTO`. This is a technical, unconditional jump. Useful for one-off jumps to specific sections (e.g., an ending).
    - `@RESTART(label)`: Compiles to a `CMD_RESTART`. This is a logical jump that also signals the end of a main loop for inter-channel synchronization. This is the standard way to loop a song.

### 4. Example Song Structure

```mml
// --- Channel A ---
// An intro that plays only once
INTRO:
  O4 L8 @I1 @V15
  C E G <C

// The main loop of the song starts here
SONG_LOOP:
  O5 L4 @I2
  C C G G A A G2
  F F E E D D C2
  // ... more music ...

// At the end of the song, jump back to the main loop
@RESTART(SONG_LOOP)
```

## 5. Workflow

1.  User edits MML and instrument definitions in the TUI.
2.  On pressing a "Play" hotkey, the editor invokes the **MusaX-ML Compiler**.
3.  The compiler parses the MML from all channels and generates a valid `.Z8A` byte stream in memory or to a temporary file.
4.  The editor launches `musax_sim.py` with the compiled temporary file for instant audio preview.
5.  A "File -> Export" command generates the final, human-readable `.Z8A` file for inclusion in a project.
