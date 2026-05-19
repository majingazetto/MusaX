# MusaX-ML Language Reference (MSL v1.1)

MSL (MusaX Sound Language) is a text-based music description language inspired by classic MML (Music Macro Language). A single `.msl` file defines the complete song: instruments, sound effects, and up to three simultaneous PSG channels.

---

## 1. Comments

Line comments begin with `//`. Everything from `//` to the end of the line is ignored.

```msl
// This is a comment
CH_A:
    O4 L8 C D E F  // inline comment
```

---

## 2. Notes and Rests

### 2.1 Note Names

Seven note letters: `A B C D E F G` (case-insensitive).

### 2.2 Accidentals

Append directly to the note letter, before the duration number:

| Form | Meaning |
|------|---------|
| `C#` or `C+` | C sharp |
| `Cb` or `C-` or `CB` | C flat |

Enharmonic equivalents (both accepted):

| Sharp | Flat | Pitch |
|-------|------|-------|
| `C#` | `Db` | C♯ / D♭ |
| `D#` | `Eb` | D♯ / E♭ |
| `F#` | `Gb` | F♯ / G♭ |
| `G#` | `Ab` | G♯ / A♭ |
| `A#` | `Bb` | A♯ / B♭ |

### 2.3 Duration

A duration number follows the note (or accidental). The number is a standard musical division of a whole note:

| Number | Name | Ticks |
|--------|------|-------|
| `1` | Whole (Redonda) | 3072 |
| `2` | Half (Blanca) | 1536 |
| `4` | Quarter (Negra) | 768 |
| `8` | Eighth (Corchea) | 384 |
| `16` | Sixteenth (Semicorchea) | 192 |
| `32` | Thirty-second (Fusa) | 96 |

If no duration is given, the current **default length** (set by `L`) is used.

```msl
L4          // set default to quarter
C D E F     // four quarter notes
C8 D8 E4    // two eighths + one quarter (explicit)
```

### 2.4 Dotted Duration

Append `.` after the duration number to multiply by 1.5. Double dots (`..`) multiply by 1.75.

```msl
C4.    // dotted quarter: 768 × 1.5 = 1152 ticks
C4..   // double-dotted quarter: 768 × 1.75 = 1344 ticks
C8.    // dotted eighth: 384 × 1.5 = 576 ticks
```

Dots can also be applied to a bare note using the default length:

```msl
L4.    // default = dotted quarter
C D E  // all dotted quarters
```

### 2.5 Triplets

Append `t` to the duration number to apply a ×2/3 factor (triplet timing):

```msl
C8t D8t E8t    // three eighth triplets, each 256 ticks (= 384 × 2/3)
C4t D4t E4t    // three quarter triplets, each 512 ticks
```

Dots and triplets cannot be combined on a single note.

### 2.6 Rest

`R` followed by an optional duration. If omitted, the default length is used.

```msl
R4     // quarter rest
R8     // eighth rest
R      // rest for the current default length
```

**Special: `R0` — channel stop.** A rest with duration 0 immediately halts the channel. Used to end FX blocks cleanly:

```msl
@FX(JUMP) {
    CH_A: O5 L16 C E G >C R0   // plays arpeggio then stops
}
```

---

## 3. Default Length — `L`

`L` sets the default note/rest duration for all subsequent bare notes in the current channel.

```msl
L8          // default = eighth
C D E F     // all eighths
L4          // default = quarter
G A         // all quarters
```

`L` also accepts dots, which become the default modifier:

```msl
L4.         // default = dotted quarter (1152 ticks each)
C D E       // all dotted quarters
```

---

## 4. Octave Control

| Command | Effect |
|---------|--------|
| `O4` | Set current octave to 4 (range 0–7) |
| `>` | Step up one octave |
| `<` | Step down one octave |

Default octave at song start: **4**.

```msl
O4 C        // C4
> C         // C5
< < C       // C3
O2 G        // G2
```

---

## 5. Loops

Surround a block of notes with `{` and `}N` to repeat it N times:

```msl
{ C D E F }4    // plays C D E F four times
```

**Without a count**, the block repeats **2 times** (default):

```msl
{ C E G }       // plays C E G twice
```

**Triplet loops** — append `t` after the count to apply a ×2/3 factor to every note and rest in the body. The count and triplet can be combined:

```msl
{ C8 E8 G8 }3t  // three iterations, each note scaled to 8t (256 ticks)
```

**Nesting** is supported. The engine uses an internal stack.

```msl
{ { C D }2 E F }4
```

---

## 6. Song Structure

### 6.1 Labels

Any sequence of uppercase letters, digits, underscores, and dots followed by `:` defines a label. Labels are jump targets for `@GOTO`, `@RESTART`, and `@CALL`.

```msl
MAIN_LOOP:
    O4 C E G
    @RESTART(MAIN_LOOP)
```

Local labels inside FX blocks are automatically scoped to that block by the compiler.

### 6.2 Channel Sections

The three PSG channels are declared with the reserved labels `CH_A:`, `CH_B:`, and `CH_C:`. The compiler uses these to populate the song header.

```msl
CH_A:
    @I0 @V14 @T120
    O4 L8 C D E F G A B >C
    @RESTART(CH_A)

CH_B:
    @I1 @V10
    O3 L4 C E G
    @RESTART(CH_B)

CH_C:
    @I2 @V12
    O2 L4 C R C R
    @RESTART(CH_C)
```

Channels that should remain silent can simply be omitted; their header pointer will be `0`.

### 6.3 Phrases — Reusable Subroutines

`PHRASE(NAME) { ... }` defines a named subroutine. The compiler emits a `CMD_RET` at the closing brace. Call it with `@CALL(NAME)` from any channel.

```msl
PHRASE(ARPUP) {
    O4 L16 C E G >C
}

CH_A:
    SONG_A:
        @CALL(ARPUP)
        @CALL(ARPUP)
        @RESTART(SONG_A)
```

- Phrases are **global** — callable from any channel.
- Phrases can contain labels; the compiler prefixes them with the phrase name to avoid collisions.
- Nesting (`@CALL` inside a `PHRASE`) is supported; the engine uses a call stack.

### 6.4 Sound Effects — `@FX`

`@FX(NAME) { ... }` defines a sound effect block. Each block may contain one or more of `CH_A:`, `CH_B:`, `CH_C:` sub-sections for multi-channel effects. End each channel's stream with `R0`.

```msl
// Single-channel FX
@FX(COIN) {
    CH_A: @I0 @V15 O5 L16 E >G R0
}

// Multi-channel FX
@FX(FANFARE) {
    CH_A: @I0 @V15 O4 L8 G G G >C R0
    CH_B: @I0 @V12 O4 L8 E E E  G R0
}
```

FX are triggered at runtime by the simulator's `[1]–[9]` keys. Priority is set in the generated FX table (default `10`). A higher-priority FX overrides a lower-priority one on the same channel; music plays silently ("ghost playback") underneath and resumes in sync when the FX ends.

---

## 7. Commands (@-commands)

All commands begin with `@` and appear inline in the note stream. They take effect immediately for the channel in which they appear.

### 7.1 Volume — `@V`

```msl
@V N        // N = 0–15
```

Sets channel master volume. The ADSR envelope scales this value per note.

```msl
@V15        // full volume
@V8         // half volume
@V0         // silent (useful for ghost tracking)
```

### 7.2 Instrument — `@I`

```msl
@I N        // N = instrument ID (0–15)
```

Selects an instrument from the active instrument table. When `@INST` blocks are defined in the file, ID refers to those. Without custom instruments, selects from the engine default table (IDs 0–4).

### 7.3 Tempo — `@T`

```msl
@T BPM          // decimal BPM (e.g. @T120)
@T#XXXX         // raw BPM_STEP in hex (e.g. @T#0600)
```

Sets channel transport speed. Each channel can have an independent tempo, enabling polyrhythms.

Formula (for 60 Hz systems): `BPM_STEP = (BPM × 768 × 256) / 3600`

Common values:

| BPM | BPM_STEP (hex) |
|-----|----------------|
| 60  | `#0300` |
| 90  | `#0480` |
| 120 | `#0600` |
| 150 | `#0780` |
| 180 | `#0900` |

`@T` placed before any `CH_X:` label sets the initial tempo for all channels in the generated header.

### 7.4 Gate — `@G`

```msl
@G N        // N = 0–255
```

Sets articulation (gate time). Controls when the ADSR Release phase begins within a note:
- `255` — legato: Release begins only when the next note starts.
- `128` — 50%: Release begins halfway through the note duration.
- `32–64` — staccato: short tone, prominent release tail.
- `0` — immediate release.

### 7.5 Portamento — `@P`

```msl
@P N        // N = 0–255
```

Chromatic pitch slide between successive notes.
- `0` — portamento off (notes snap immediately).
- `>0` — number of 60 Hz frames per semitone step.

Slides are played **legato**: the envelope is not re-triggered during the glide. This produces a trombone-like effect for ascending runs and a harp-like effect for descending runs.

### 7.6 Detune — `@D`

```msl
@D N        // N = -128 to 127 (signed, in cents)
```

Fine pitch offset. 100 cents = 1 semitone. Useful for:
- Creating "fat" chords when two channels play the same note at `@D-5` and `@D+5`.
- Applying a fixed pitch correction.

### 7.7 Phase Delay — `@PH`

```msl
@PH N       // N = 0–255
```

Sub-tick timing delay. A value of 128 shifts event timing by half a tick. Used together with `@CH` to stagger channels for a chorus effect.

### 7.8 Volume Fade — `@F`

```msl
@F(target, step)    // both 0–255
```

Gradually slides the channel's master volume toward `target`. Every 60 Hz frame, `step` is added or subtracted.
- `@F(0, 3)` — fade out slowly.
- `@F(255, 255)` — snap to full volume immediately.
- `@F(128, 1)` — slow fade to half volume.

### 7.9 Chorus — `@CH`

```msl
@CH(phase, detune)
```

Convenience macro that applies `@PH` and `@D` simultaneously. Use on a second channel alongside a "dry" lead:

```msl
// Channel A: dry lead
CH_A: @I0 @V14 O4 L8 C E G

// Channel B: wet chorus copy
CH_B: @I0 @V10 @CH(32, -7) O4 L8 C E G
```

### 7.10 Control Flow

| Command | Bytecode | Description |
|---------|----------|-------------|
| `@GOTO(LABEL)` | CMD_GOTO | Unconditional jump to label |
| `@RESTART(LABEL)` | CMD_RESTART | Jump + increment the simulator's loop counter |
| `@CALL(LABEL)` | CMD_CALL | Push return address, jump to phrase |

`@RESTART` is the idiomatic way to loop a song section — use it at the end of each channel's main sequence. `@GOTO` is for internal branching without incrementing the loop count.

---

## 8. Instrument Definitions — `@INST`

Instruments can be defined inline in the `.msl` file. They are placed at the top, outside any channel section. Definitions are collected by the compiler and emitted as a 16-byte table in the Z8A output.

```msl
@INST(0, "LeadPulse") {
    ADSR: att, dec, sus, rel
    LFO:  dest, wave, speed, amp, delay
    FLAGS: 0
}
```

### ADSR Fields (0–255 each)

| Field | Description |
|-------|-------------|
| `att` | Attack rate — added to envelope accumulator per frame until ≥ 255 |
| `dec` | Decay rate — subtracted per frame until ≤ sustain level |
| `sus` | Sustain level — envelope is held here |
| `rel` | Release rate — subtracted per frame after gate, until ≤ 0 |

Fast attack = high att value (255 = instant). Short decay = high dec value.

### LFO Fields

| Field | Range | Description |
|-------|-------|-------------|
| `dest` | 0–2 | `0`=off, `1`=Pitch (vibrato), `2`=Volume (tremolo) |
| `wave` | 0–3 | `0`=Triangle, `1`=Sawtooth, `2`=Square, `3`=Sine |
| `speed` | 0–255 | Phase advance per frame. Cycle = 256/speed frames at 60 Hz. Speed=32 → 7.5 Hz, speed=64 → 15 Hz. |
| `amp` | 0–15 | LFO depth. Pitch: peak offset = amp×127/15 cents (≈8.5 cents/step). |
| `delay` | 0–255 | Frames before LFO begins after note-on |

### FLAGS

Reserved for future use. Always set to `0`.

### Instrument ID rules

- IDs 0–15 are valid.
- If an ID is defined both in a `.msxi` bank file and inline, the inline definition wins (compiler warning is emitted).
- At runtime, `@I N` selects the instrument at position N in the table built from these definitions.

### Example

```msl
@INST(0, "Plucky") {
    ADSR: 255, 10, 200, 20
    LFO:  0, 0, 0, 0, 0
    FLAGS: 0
}

@INST(1, "VibratoLead") {
    ADSR: 10, 5, 255, 10
    LFO:  1, 0, 10, 4, 20
    FLAGS: 0
}
```

---

## 9. File-Level Directives

### 9.1 Metadata

Placed at the top of the file. Reflected in the generated Z8A header comment and `musax.py info` output.

```msl
@TITLE "Tetris Theme"
@AUTHOR "Artist Name"
@DESC "Classic 3-channel chiptune"
```

### 9.2 Module / Namespace

```msl
@MODULE TETRIS
@NAMESPACE TETRIS    // alias for @MODULE
```

Wraps the entire generated Z8A in a `sjasmplus` `MODULE ... ENDMODULE` block. All labels become module-scoped (`TETRIS.HEADER`, `TETRIS.INST_0`, etc.). Use when embedding the song in a larger project that uses sjasmplus modules.

Without `@MODULE`, the compiler outputs `INCLUDE "musax_const.Z8A"` at the top and uses long scoped label names.

### 9.3 Bank File — `@BANK` (editor-only)

```msl
@BANK "instruments.msxi"
```

Declares an external instrument bank (`.msxi` file). **This directive is read only by the MSL editor (F6 Instrument Editor)**; it is not processed by the compiler. The bank file uses the same `@INST(...)` syntax as inline definitions.

---

## 10. Tick Reference

Base resolution: **768 ticks per quarter note**.

| Division | Ticks | With dot | Triplet |
|----------|-------|----------|---------|
| Whole | 3072 | 4608 | 2048 |
| Half | 1536 | 2304 | 1024 |
| Quarter | 768 | 1152 | 512 |
| Eighth | 384 | 576 | 256 |
| Sixteenth | 192 | 288 | 128 |
| Thirty-second | 96 | 144 | 64 |

Double-dotted: multiply the base by 1.75 (e.g. `4..` = 768 × 1.75 = 1344).

---

## 11. Complete Example

```msl
// ==========================================
// EXAMPLE.MSL — 3-channel demo
// ==========================================

@TITLE "Example Song"
@AUTHOR "Author"
@NAMESPACE EXAMPLE

// --- Instruments ---

@INST(0, "Lead") {
    ADSR: 255, 8, 200, 15
    LFO:  1, 0, 6, 3, 20
    FLAGS: 0
}

@INST(1, "Bass") {
    ADSR: 255, 20, 0, 10
    LFO:  0, 0, 0, 0, 0
    FLAGS: 0
}

// --- Subroutines ---

PHRASE(MOTIF) {
    O5 L8 C E G >C< G E C
}

// --- Global Tempo ---

@T140

// --- Channel A: Melody ---

CH_A:
    @I0 @V14
    SONG_A:
        @CALL(MOTIF)
        O5 L4 C R2
        @RESTART(SONG_A)

// --- Channel B: Harmony ---

CH_B:
    @I0 @V10 @D-7
    SONG_B:
        O4 L8 { G E }4
        O4 L4 E R2
        @RESTART(SONG_B)

// --- Channel C: Bass ---

CH_C:
    @I1 @V13
    SONG_C:
        O2 L4 C C G G
        @RESTART(SONG_C)

// --- Sound Effects ---

@FX(COIN) {
    CH_A: @I0 @V15 O5 L16 E >G R0
}

@FX(ALARM) {
    CH_A: @I0 @V15 O6 L8 { A E }4 R0
    CH_B: @I0 @V12 O5 L8 { F C }4 R0
}
```
