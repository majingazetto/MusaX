# MusaX Editor Specification

TUI-based editor for the MusaX sound engine. Uses MSL (MusaX Sound Language), a custom MML dialect.

## 1. Core Philosophy & Technology Stack

- **Paradigm:** MML editor, not a tracker. Compositional fluidity over rigid grid-based entry.
- **Technology:** TUI, full-screen terminal application using `prompt_toolkit`.
- **Style:** Borland-inspired layout. Colors configurable in the future.
- **Integration:** Tightly integrated with `msl_compiler.py` and `musax_sim.py`.

## 2. Modes

The editor has two full-screen modes that switch completely (no split panels):

1. **Main Editor** — MSL text editor.
2. **Instrument Editor** — Form-based instrument manager (F6).

---

## 3. Main Editor Layout

```
┌─ MusaX v1.9 ─ song.msl [modified] ─────────────────────────────┐
│                                                                  │
│  (MSL text area — full width, channels A/B/C in same file)      │
│                                                                  │
├─ Errors (3) ────────────────────────────────────────────────────┤
│  Line 12: Unknown command @X                                     │
│▸ Line 15: Expected duration after note C                        │
│  Line 23: Undefined label SONG_LOOP                             │
├─────────────────────────────────────────────────────────────────┤
│  Ln 12  Col 4  │  BANK: banco.msxi  │  [OK]  [VI]              │
├─────────────────────────────────────────────────────────────────┤
│ F2 Save  F3 Open  F4 Z8A  F5 VI  F6 Instr  F9 Build  F10 Play │
└─────────────────────────────────────────────────────────────────┘
```

### Text Area
- Single MSL file. Channels A, B, C coexist in the same file as classic MML.
- Syntax highlighting for notes, `@` commands, `@INST` blocks, labels, loops, comments.

### Error Panel
- **Height:** 5 lines, fixed.
- **Behaviour:** Opens automatically when compilation has errors. Closes automatically on clean compile.
- **Toggle:** `Ctrl+E` or `F12`.
- **Navigation:** `↑↓` to select an error, `Enter` to jump to that line in the text area.

### Status Bar
- Current line and column.
- Active bank filename (from `@BANK` directive, if present).
- Last build result: `[OK]` or `[N error(s)]` (clears when the file is modified).
- VI mode indicator: `[VI]` / `[NORMAL]` (only shown when VI mode is active).

### F-key Bar
```
F2 Save  F3 Open  F4 Z8A  F5 VI  F6 Instr  F9 Build  F10 Play  Ctrl+T Theme  Ctrl+Q Quit
```

### Keyboard Map
```
F2 / Ctrl+S   Save current file
F3 / Ctrl+O   Open file (directory picker — ↑↓ navigate, Enter open/descend, Esc cancel)
F4            Toggle Z8A view (read-only assembled output; Esc or F4 to return)
F5            Toggle Vi mode (only active in Main Editor mode)
F6            Switch to Instrument Editor (not yet implemented)
F9 / Ctrl+B   Build — compiles MSL, shows errors in panel
F10 / Ctrl+R  Build + Play — compiles, then suspends editor and runs simulator
Ctrl+E / F12  Toggle error panel (↑↓ to select, Enter to jump to line)
Ctrl+N        New file (clears buffer)
Ctrl+T        Cycle colour theme (retrobox → borland → …)
Ctrl+Q        Quit
```
> Note: F9–F12 may be intercepted by some terminals. Use the Ctrl aliases
> for reliable cross-terminal operation.

### File Picker (F3 / Ctrl+O)
Switches the editor to a full-screen file-picker mode. The title bar shows the current directory.

```
┌─ MusaX v1.9 ─ Open File ─ /home/user/songs ────────────────────┐
│  ../                                                             │
│  demos/                                                          │
│▸ korobeiniki.msl                                                │
│  synthesis_test.msl                                             │
│  higedeck.msl                                                   │
├─────────────────────────────────────────────────────────────────┤
│  3/5  [Enter] open  [Esc] cancel                                │
└─────────────────────────────────────────────────────────────────┘
```

- Entries are sorted: `../` first, then subdirectories, then `.msl` files.
- `Enter` on a directory descends into it; `Enter` on a file loads it and returns to the editor.
- `Esc` cancels without loading.
- Opening a file clears the `[OK]`/error indicator immediately.

### Simulator Integration (F10)
The editor suspends itself and hands the terminal to `musax_sim.py` completely (the sim's interactive UI and dashboard remain fully functional). When the user exits the sim, the editor resumes exactly where it was.

---

## 4. Instrument Editor Layout (F6)

```
┌─ MusaX Instrument Editor ─────────────────────────────────────────┐
│  [BANK: mi_banco.msxi]              [SONG: cancion.msl]           │
│                                                                    │
│  0  Piano          [BANK]    │  Name:  Piano                      │
│  1  VibratoLead    [SONG]    │  ──────────────────────────────    │
│  2  BassDrum       [BANK]    │  ADSR   Att: 10  Dec:  5           │
│  3  SquareLead     [SONG]*   │         Sus:255  Rel: 10           │
│  4  (empty)                  │  ──────────────────────────────    │
│  ...                         │  LFO    Dest: Pitch  Wave: TRI     │
│  15 (empty)                  │         Speed:  2    Amp:   12     │
│                              │         Delay: 20                  │
│                              │  ──────────────────────────────    │
│                              │  FLAGS: 0                          │
├────────────────────────────────────────────────────────────────────┤
│  * = SONG overrides BANK instrument with same ID                  │
├────────────────────────────────────────────────────────────────────┤
│ F2 Save  F4 Copy→Bank  F5 Copy→Song  Del Delete  Esc/F6 Back     │
└────────────────────────────────────────────────────────────────────┘
```

### Instrument List (left panel)
- Always shows all 16 slots (IDs 0–15), including empty ones.
- Labels: `[BANK]` — from bank file only. `[SONG]` — defined inline in MSL. `[SONG]*` — defined in both; SONG overrides BANK (compiler emits a warning).

### Instrument Form (right panel)
- Fields: Name, ADSR (Att/Dec/Sus/Rel), LFO (Dest/Wave/Speed/Amp/Delay), FLAGS.
- `Tab` moves focus between list and form.
- `↑↓` navigates the list or form fields.

### Keyboard Map
```
F2          Save (bank file or MSL depending on selected instrument source)
F4          Copy selected instrument → Bank file
F5          Copy selected instrument → Song (inline @INST)
Del         Delete selected instrument from its source
Tab         Toggle focus: list ↔ form
↑ ↓         Navigate list / form fields
Esc / F6    Return to Main Editor
```

---

## 5. Instrument Bank File Format (`.msxi`)

Plain text, same `@INST(...)` syntax as MSL. No special header required.

```msl
// mi_banco.msxi
@INST(0, "Piano") {
    ADSR: 2, 8, 200, 15
    LFO:  0, 0, 0, 0, 0
    FLAGS: 0
}
@INST(1, "VibratoLead") {
    ADSR: 10, 5, 255, 10
    LFO:  1, 0, 2, 12, 20
    FLAGS: 0
}
```

---

## 6. Instrument Resolution Model

When a song uses both a bank and inline instruments, the priority is:

```
CLI --bank  <  @BANK directive in .msl  <  @INST inline in .msl
```

- The `@BANK` directive in the MSL file overrides the `--bank` CLI argument.
- Inline `@INST` blocks override any bank definition for the same ID.
- If an ID is defined in both the bank and inline, the compiler emits a warning and uses the inline definition.

### `@BANK` Directive
Declared at the top of the MSL file:
```msl
@BANK "mi_banco.msxi"
```
The editor reads this directive on file open and automatically loads the bank into the Instrument Editor.

---

## 7. MSL Language Reference

See `technical_spec.md` for the full MSL language specification.

### Quick Reference
- **Notes:** `C D E F G A B` with `+`/`#` (sharp) or `-`/`b` (flat).
- **Octave:** `O4`, `>` (up), `<` (down).
- **Duration:** `L8`, `C4`, `R4.` (dotted), `C8t` (triplet).
- **Commands:** `@V` (volume), `@I` (instrument), `@T` (tempo), `@G` (gate).
- **Flow:** `{ ... }4` (loop), `@RESTART(label)`, `@GOTO(label)`, `@CALL(name)`.
- **Bank:** `@BANK "file.msxi"`.

---

## 8. Workflow

1. Open or create an MSL file (`F3` / `Ctrl+N`).
2. Optionally declare `@BANK "banco.msxi"` at the top.
3. Define instruments inline with `@INST(...)` or manage them via `F6`.
4. Write music for channels A, B, C in the same file.
5. `F9` to compile and check for errors.
6. `F10` to compile and play — editor suspends, simulator runs interactively.
7. `F5` to stop simulation and return to the editor.
8. `F2` to save.
