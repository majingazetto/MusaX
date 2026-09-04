# MusaX Python Tools Reference

This document provides a detailed specification and reference guide for all the Python scripts (`.py` files) in the [MusaX/tools/](file:///Users/armandoperezabad/Code/brew/MSX/MusaX/tools) directory.

---

## 1. CLI Hub (`musax.py`)

The main entry point for the MusaX command-line toolchain. It unifies compilation, simulation, and inspection capabilities into a single utility.

* **Modules Imported:** `argparse`, `sys`, `os`, `subprocess`.
* **Execution Modes:**
  * `build`: Invokes the compiler pipeline to parse an `.msl` file and output a `.Z8A` assembly file.
  * `play`: Runs the software simulator on a `.Z8A` or `.msl` file (if an `.msl` file is passed, it compiles it to a temporary directory first).
  * `info`: Reads the metadata header of the target song (`@TITLE`, `@AUTHOR`, `@DESC`) and displays song duration, instrument lists, and active channels.
  * `import`: Converts MuseScore (`.mscz` / `.mscx`) scores into `.msl` files.
* **Usage:**
  ```bash
  python3 tools/musax.py [build | play | info | import] [arguments]
  ```

---

## 2. Integrated Development Environment TUI (`msl_editor.py`)

A full-screen, text-based TUI editor built on `prompt_toolkit`. It provides an integrated terminal workflow for composing and debugging music in MSL.

* **Key Panels:**
  * **Text Editor Panel:** Full-width editing space with custom syntax highlighting for notes, commands, loops, and labels.
  * **Error Panel (Ctrl+E / F12):** Fixed-height (5 lines) panel at the bottom. Lists compilation errors. Pressing `Enter` on an error automatically jumps to the corresponding line in the text area.
  * **Status Bar:** Displays cursor coordinates (line/col), active instrument bank, build status (`[OK]` or error count), and VI mode indicator.
  * **Instrument Editor Form (F6):** A multi-panel instrument manager. Navigates slots `0-15`. Allows editing ADSR envelope parameters, LFO configuration, and FLAGS. Generates live ASCII preview graphs for ADSR envelopes and LFO waveforms.
* **TUI Keyboard Mapping:**
  * `F2 / Ctrl+S`: Save file.
  * `F3 / Ctrl+O`: Switch to directory/file picker.
  * `F4`: View read-only compiled Z8A assembly code.
  * `F5`: Toggle Vi/Normal editing mode.
  * `F6`: Switch between main editor and instrument panel.
  * `F9 / Ctrl+B`: Compile and check for errors.
  * `F10 / Ctrl+R`: Compile and execute simulator (suspends editor).

---

## 3. High-Precision Simulator (`musax_sim.py`)

A sample-accurate, software-based emulator of the Z80 MusaX sound driver. It allows playing and debugging music without uploading to hardware.

* **Engine Characteristics:**
  * **Audio Pipeline:** Synthesizes sound waves (Square wave PSG channels, White Noise, envelope scaling) and plays them via `pyaudio` or `sounddevice`.
  * **Sample-Accurate Timing:** Simulates ticks at the sample level instead of quantizing to 60Hz retrace frames, ensuring precise phase-delays (`CMD_PHASE`) and slide interpolations.
  * **Export Mode:** Renders audio output to disk using the `--export` option (saves to `.wav`, or `.mp3` if `lame` is available).
* **Live Visualizer Fields:**
  * **Global Ticks (T):** Accumulator.
  * **ADSR Monitor:** Tracks phase (`ATTACK`, `DECAY`, `SUSTAIN`, `RELEASE`) and envelope height (`0-255`).
  * **BPM Metric:** Displays computed real-time channel speed.
  * **Bytecode Snippets (HEX SNIP):** Displays disassembly of active instructions.
  * **Real-time volume visualizers:** Audio peak output bars.

---

## 4. Compilation Pipeline (`msl_compiler.py`, `msl_parser.py`, `msl_codegen.py`)

These three modules form the MSL compiler. They translate the text-based musical description language (MSL) into structured Z80 assembly source code.

```
+------------+     +----------------+     +-----------------+     +-------------+
|  .msl file | --> | msl_compiler.b | --> |  msl_parser.py  | --> |  AST nodes  |
+------------+     +----------------+     +-----------------+     +-------------+
                                                                         |
                                                                         v
+------------+                            +-----------------+     +-------------+
|  .Z8A file | <------------------------- | msl_codegen.py  | <-- | Code Gen    |
+------------+                            +-----------------+     +-------------+
```

### A. Compiler Orchestrator (`msl_compiler.py`)
* Coordinates file inclusion (`@BANK`).
* Resolves instrument priority (song-defined inline instruments override external bank files).
* Controls file input/output streams and error formatting.

### B. Grammar Lexer & Parser (`msl_parser.py`)
* Implements the parser that identifies notes (`A-G`), accidentals (`+`, `#`, `-`, `b`), duration modifiers (dots, double-dots, triplets), loops (`{ ... }N`), command parameters, and metadata tags.
* Outputs Abstract Syntax Tree (AST) node structures.

### C. Z80 Code Generator (`msl_codegen.py`)
* Formats Z80 directives and data blocks compatible with `sjasmplus`.
* Translates musical structures into standard MusaX bytecode chunks.
* Implements namespaces (`@MODULE` / `@NAMESPACE`) and sets up header definitions (`TYPE_SONG`, `TYPE_FX`).

---

## 5. Build Automation Playlist Builder (`generate_playlist.py`)

A utility script executed by the Jukebox Makefile during compilation. It decouples the jukebox program from hardcoded playlists.

* **Scan Behavior:** Reads the `SRC/TESTS` subdirectory to search for `.msl` files.
* **Compilation Trigger:** Executes `musax.py build` sequentially for each discovered MSL file, outputting `.Z8A` intermediates to the `TMP/` folder.
* **Playlist Assembly:** Builds `TMP/PLAYLIST.Z8A`, containing:
  * Compile-time includes for each compiled song.
  * `SONGTBL` & `FXTBL` pointer tables referencing song labels, text colors, and clean names.
  * Constants `SONGCOUNT` and `FXCOUNT`.

---

## 6. Decompiler & Parser (`psglog2msl.py`)

A reverse-engineering utility to convert register capture dumps into editable MSL files.

* **Decompilation Pipeline:** Parses register writes logs (typically from openMSX emulation capturing PSG registers `0-13` at 50/60Hz).
* **Detection Algorithms:**
  * Groups register states into PSG note intervals.
  * Detects PSG period boundaries and fits them to MSL notes.
  * Captures volume envelopes and estimates ADSR/Gate values.
* **Play Verification:** Runs a command-line test player (`--play`) that renders raw PSG log events through system audio to verify dump integrity before decompilation.

---

## 7. Legacy Wrapper (`msl2z8a.py`)

A deprecated, backward-compatibility CLI script that maps simple arguments directly to the core compilation classes inside `msl_compiler.py`. It is retained for legacy builds but has been replaced by the unified CLI hub (`musax.py`).

---

## 8. Global CLI Installer (`install_wrappers.sh`)

Installs standalone CLI wrappers for `musax`, `msl_editor`, `musax_sim`, `psglog2msl`, `msl2z8a`, and `mscz2msl` into `~/.local/bin/`.

* **Generated Wrappers:**
  * `~/.local/bin/musax`: Launches `MusaX/tools/musax.py` with forwarded arguments.
  * `~/.local/bin/msl_editor`: Launches `MusaX/tools/msl_editor.py` with forwarded arguments.
  * `~/.local/bin/musax_sim`: Launches `MusaX/tools/musax_sim.py` with forwarded arguments.
  * `~/.local/bin/psglog2msl`: Launches `MusaX/tools/psglog2msl.py` with forwarded arguments.
  * `~/.local/bin/msl2z8a`: Launches `MusaX/tools/msl2z8a.py` with forwarded arguments.
  * `~/.local/bin/mscz2msl`: Launches `MusaX/tools/mscz2msl.py` with forwarded arguments.
* **Usage:**
  ```bash
  ./tools/install_wrappers.sh
  ```

---

## 9. MuseScore Score Importer (`mscz2msl.py`)

A standalone converter that translates MuseScore scores (`.mscz` compressed archives and `.mscx` XML files) directly into MusaX Sound Language (`.msl`) source.

* **Key Capabilities:**
  * **Pure Python Standard Library:** Uses `zipfile` and `xml.etree.ElementTree` with zero third-party package dependencies.
  * **MuseScore 3.x & 4.x Compatible:** Automatically detects internal score XML and layout hierarchy across major versions.
  * **Enharmonic Fidelity:** Preserves composer-intended accidentals (`Eb` vs `D#`, `Bb` vs `A#`) via TPC (Tonal Pitch Class) mapping.
  * **768-Tick Timing:** Native support for binary durations (`whole` down to `64th`), dotted lengths, and triplet subdivisions (`8t`, `4t`, `16t`).
  * **Tie Fusion:** Automatically fuses notes tied across measure barlines into unified compound durations.
  * **3-Channel PSG Assignment:** Map arbitrary staves/voices to `CH_A`, `CH_B`, and `CH_C` with monophonic chord resolution (`top` or `bottom`).
  * **Measure-by-Measure Formatting:** Emits aligned `// [Bar XX]` comments with 4-measure system dividers, or multi-measure lines separated by `|` via `--compact`.
  * **Automatic Phrase Subroutines:** Detects score repeats (`<startRepeat/>` / `<endRepeat>`) and extracts them into MusaX `PHRASE(NAME) { ... }` subroutines invoked via `@CALL(NAME)`. Reduces compiled Z80 bytecode footprint by 40–50% while preserving sample-accurate channel synchronization.
* **Usage:**
  ```bash
  mscz2msl song.mscz                      # Convert to song.msl (phrases enabled by default)
  mscz2msl song.mscz --info               # Inspect tracks, tempo, repeats, and measure counts
  mscz2msl song.mscz -a 1 -b 2 -c 4       # Explicit staff-to-channel routing
  mscz2msl song.mscz --transpose 12       # Transpose by semitones
  mscz2msl song.mscz --compact            # Format 4 measures per line separated by '|'
  mscz2msl song.mscz --repeats unroll     # Unroll repeats linearly instead of using PHRASEs
  mscz2msl song.mscz --play               # Convert and immediate simulation audition
  ```

