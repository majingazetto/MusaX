# Separate PSG Register Commit from Channel Processing in MusaX Z80

Separating the physical PSG (AY-3-8910) register updates from the frame update processing of each music and SFX channel. 

Currently, `MUSUPDAT` processes the 6 audio streams (3 music + 3 SFX), writes to their respective shadow buffers (`PSGMUS` and `PSGSFX`), and immediately calls `MUSCOMM` to write the registers to the hardware. 

To improve timing consistency, eliminate jitter, and align with the requested design:
1. `MUSUPDAT` will run in the main/game loop, processing channels and writing the merged register state into a new buffer, `PSGREG` (14 bytes).
2. The actual write to the physical PSG (via ports `#A0` and `#A1`) will be done by the game's interrupt handler (IRQ) calling `MUSCOMM`.
3. A new merge routine, `MUSMERGE`, will combine `PSGMUS` and `PSGSFX` into `PSGREG` using the `SFXMSK` active mask, while preserving bits 6-7 (I/O configuration) of the Mixer Control register (R7) from the music shadow.
4. We will replace the C-style `#INCLUDE` directives with native `sjasmplus` `INCLUDE` directives (without the `#` character).
5. We will introduce a constant `PSGSIZE EQU 14` in `CONST.Z8A` to avoid magic numbers.
6. We will optimize the loop in `MUSMUT` (which clears the shadow registers) by muting sound channels (bits 0-5 of R7 set to 1, all other registers set to 0) while preserving bits 6-7 of R7 to prevent keyboard/joystick scanning corruption.
7. We will update `MUSINIT` to read the physical PSG R7 register on start, preserve its bits 6-7, and initialize `PSGMUS + 7`, `PSGSFX + 7`, and `PSGREG + 7` with those bits.

---

## Proposed Changes

### MusaX Core Z80 Driver

We will update the constants, variable declarations, and engine logic in the `src/` directory.

#### [MODIFY] [CONST.Z8A](file:///Users/armandoperezabad/Code/brew/MSX/MusaX/src/CONST.Z8A)
- Add `PSGSIZE EQU 14` constant.

#### [MODIFY] [VARS.Z8A](file:///Users/armandoperezabad/Code/brew/MSX/MusaX/src/VARS.Z8A)
- Define a new buffer `PSGREG` using `PSGREG DEFS PSGSIZE` contiguous to `PSGMUS` and `PSGSFX` (which should also use `PSGSIZE` instead of the magic number 14).

#### [MODIFY] [MUSAX.Z8A](file:///Users/armandoperezabad/Code/brew/MSX/MusaX/src/MUSAX.Z8A)
- Change `#INCLUDE "CONST.Z8A"` to `INCLUDE "CONST.Z8A"` and `#INCLUDE "VARS.Z8A"` to `INCLUDE "VARS.Z8A"`.
- Update `MUSINIT` to:
  - Read the current physical PSG R7 register via port `#A2` (after writing 7 to `#A0`).
  - Clear all variables between `MUSVARS` and `MUSEVARS` via `LDIR`.
  - Re-merge the original R7 bits 6-7 with `#3F` (disabling all tone/noise channels) and store the result in `PSGMUS + 7`, `PSGSFX + 7`, and `PSGREG + 7`.
- Update `MUSMUT` to mute the 3 contiguous PSG register buffers (`PSGMUS`, `PSGSFX`, `PSGREG`) by setting R0-R6 and R8-R13 to 0, and setting bits 0-5 of R7 to 1 while preserving bits 6-7.
- Implement the `MUSMERGE` routine to merge `PSGMUS` and `PSGSFX` into `PSGREG` according to `SFXMSK` channel priority:
  - If a channel `i` is owned by SFX (`SFXMSK` bit `i` is 1), its period (R0-R5) and volume (R8-R10) come from `PSGSFX`. Otherwise, they come from `PSGMUS`.
  - If any SFX is active (`SFXMSK != 0`), the common registers (Noise Period R6, Envelope Period R11-R12, Envelope Shape R13) come from `PSGSFX`. Otherwise, they come from `PSGMUS`.
  - Mixer Control (R7) is merged bitwise: Tone (bits 0-2) and Noise (bits 3-5) are selected from `PSGSFX` (for active SFX channels) or `PSGMUS` (for active music channels). Bits 6-7 (I/O configuration) are preserved from the current shadow.
- Implement `MUSCOMM` to write the `PSGSIZE` bytes in `PSGREG` buffer to physical registers 0-13 using ports `#A0` and `#A1`.
- Update `MUSUPDAT` to call `MUSMERGE` instead of `MUSCOMM`.

---

## Verification Plan

### Automated Tests
- Test compilation directly using `sjasmplus`:
  ```bash
  sjasmplus MUSAX.Z8A
  ```
  This guarantees that our syntax is correct and compiles without errors using native `sjasmplus` tools.

### Manual Verification
- Verify the Z80 code structure and register mapping logic to ensure it behaves exactly as requested.
