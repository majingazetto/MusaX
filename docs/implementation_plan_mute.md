# Jukebox Channel Muting Implementation Plan

## Goal Description
Implement the ability to toggle channel muting in the Z80 graphical Jukebox ROM. This feature should closely mirror the simulator's muting capabilities. Specifically, we will:
1. Define a 1-byte `MUTEMSK` flag variable to track which channels are muted.
2. Extend keyboard matrix polling in `POLLKEY` to detect mute toggle keystrokes.
3. Implement `APPLYMUTE` in the interrupt thread (`IRQINT`) to silence muted channels by overriding the merged physical registers in `PSGREG` (setting volume to 0 and disabling tones/noise in R7).
4. Update `DRAWUI` to render muted channels with empty volume bars, note `---`, and status `MUTE`.
5. Reorganize the bottom legend layout to display the new controls.

## User Review Required
We propose the following contiguous, conflict-free keys on a standard keyboard for toggling channel mutes:
- **`T` / `Y` / `U`** -> Toggle Music Channels A, B, C
- **`G` / `H` / `J`** -> Toggle FX Channels A, B, C

These keys are completely free, do not overlap with existing hotkeys, and are physically grouped on the keyboard.

## Proposed Changes

### MusaX Jukebox ROM

#### [MODIFY] [JUKEBOX.Z8A](file:///home/arman/Code/brew/MSX/MusaX/src/jukebox/SRC/JUKEBOX.Z8A)

- **Variables Section:**
  - Define `MUTEMSK` (1 byte) in the variables area.
  - Define `SCRDIRTY` if not already defined (already added in previous steps).
  
- **Interrupt Handler (`IRQINT`):**
  - Insert a call to `APPLYMUTE` right after `CALL MUSUPDAT` and before `CALL MUSCOMM`.
  
- **Mute Application Routine (`APPLYMUTE`):**
  - Compare `SFXMSK` (which specifies whether Music or FX is currently owning the physical PSG channel) and `MUTEMSK` for each of the 3 PSG channels (A, B, and C).
  - If the active source is muted:
    - Set physical volume to 0 (`PSGREG + 8/9/10`).
    - Disable the tone and noise generators in the mixer (`PSGREG + 7` bits 0-2 and 3-5).

- **Keyboard Polling (`POLLKEY`):**
  - In the Row 5 check (scanned at `#FBEA`), process bit 1 (`T`), bit 6 (`Y`), and bit 2 (`U`). On press:
    - Toggle `MUTEMSK` bits 0, 1, and 2 respectively.
    - Set `SCRDIRTY = 1` and call `CLRUI` to force a full redrawing of the visual states.
  - In the Row 3 check (scanned at `#FBE8`), process bit 4 (`G`), bit 5 (`H`), and bit 7 (`J`). On press:
    - Toggle `MUTEMSK` bits 3, 4, and 5 respectively.
    - Set `SCRDIRTY = 1` and call `CLRUI`.

- **UI Drawing (`DRAWUI`):**
  - Define `.SMUTE DEFB "MUTE", 0`.
  - In the channel drawing loop, check the channel's index bit in `MUTEMSK`. If set:
    - Set `TMPSTAT = 3` (Muted status code).
    - Set `TMPVOL = 0` (No volume visualizer bar).
    - Set `TMPNOTE = 255` (Prints `---`).
    - Set status string pointer to `.SMUTE` (`"MUTE"`).
    - Skip the standard music/SFX state logic and jump directly to `.CHSTATD`.

- **Legend Strings Reorganization:**
  - Move `d DETUNE` from the right column to the left column of Row 23.
  - Combine `f FADE OUT` and `i FADE IN` into `f/i FADE O/I` on Row 21 right.
  - Reorganize `STRKEY1` to `STRKEY4` as follows:
    - `STRKEY1`: `" keys: 1-6 PLAY   │ s STOP ALL   "`
    - `STRKEY2`: `"       c PORTA    │ f/i FADE O/I "`
    - `STRKEY3`: `"       e CHORUS   │ t,y,u MUT MU "`
    - `STRKEY4`: `"       d DETUNE   │ g,h,j MUT FX "`
  *(Note: `#80` represents the vertical divider column character `│`)*

## Verification Plan

### Automated Tests
- Run `make clean && make build` to verify compiling with zero errors.

### Manual Verification
- Check that pressing `T`, `Y`, or `U` mutes/unmutes Music channels A, B, or C instantly.
- Check that pressing `G`, `H`, or `J` mutes/unmutes FX channels A, B, or C.
- Verify that a muted channel displays status `MUTE`, volume bar is cleared, and note goes to `---`.
- Verify that muting does not stop the underlying playback sequencer, and unmuting restores the sound instantly.
