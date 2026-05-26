# Direct VDP Port Access Implementation Plan

## Goal Description
Replace MSX BIOS-dependent VRAM read/write access calls with highly optimized, direct I/O port routines extracted from the `WOR` project (`VRAM.Z8A`). This achieves maximum rendering speed and eliminates BIOS overhead while remaining standard-compliant by dynamically reading the VDP ports from BIOS locations `#0006` and `#0007` at startup.

Specifically, we will:
1. Define VDP port shadow variables (`VDPWDATA`, `VDPWCTRL`, `VDPRDATA`, `VDPRCTRL`) in RAM.
2. Initialize these port variables at startup using `SCRINIT` by reading locations `#0006` and `#0007`.
3. Prohibit BIOS `LDIRVM` (`#005C`) and `CLS` (`#00C3`) calls.
4. Implement optimized Z80 subroutines for:
   - `LDIRVM` (direct `OUTI` block loop transfer to VRAM).
   - `FILVRM` (direct VRAM filling).
5. Replace startup screen clearing (`CALL CLS`) with a direct name table clear using `FILVRM`.
6. Retain `ENASCR` (`#0044`) as a BIOS call as requested.

## User Review Required
No major architectural risks. Direct hardware access via BIOS-provided ports is standard for games on MSX.

## Proposed Changes

### MusaX Jukebox ROM

#### [MODIFY] [JUKEBOX.Z8A](file:///home/arman/Code/brew/MSX/MusaX/src/jukebox/SRC/JUKEBOX.Z8A)

- **System Constants:**
  - Remove `LDIRVM EQU #005C`
  - Remove `CLS EQU #00C3`

- **RAM Variables Section:**
  - Append the following variables (1 byte each) before `SCRBUFF`:
    - `VDPWDATA`
    - `VDPWCTRL`
    - `VDPRDATA`
    - `VDPRCTRL`

- **Startup Sequence (`ROMINIT`):**
  - Right after stacking pointer setup, call `SCRINIT` to initialize the VDP port variables.
  - Replace `CALL CLS` at line 88 with a direct clear call:
    ```assembly
    LD      HL,#1800
    LD      BC,768
    LD      A,#20
    CALL    FILVRM
    ```

- **New VDP/VRAM Subroutines:**
  - Implement `SCRINIT`, `FILVRM`, and `LDIRVM` in `JUKEBOX.Z8A` (e.g. right before or after other drawing subroutines).
  - All local labels in these routines will use the standard dot (`.`) prefix (like `.LOOP`, `.LOW`, `.HIGH`) to adhere to repository conventions.

```assembly
; - SCRINIT ----------------------------
; - INITIALIZE VDP PORT VARIABLES FROM BIOS
; - IN: NONE
; - OUT: NONE
; - CLOBBERS: A
; -

SCRINIT         LD      A,(#0007)
                LD      (VDPWDATA),A
                INC     A
                LD      (VDPWCTRL),A
                LD      A,(#0006)
                LD      (VDPRDATA),A
                INC     A
                LD      (VDPRCTRL),A
                RET


; - FILVRM -----------------------------
; - FILL VRAM WITH A CONSTANT VALUE
; - IN: HL = VRAM ADDRESS, BC = COUNT, A = VALUE
; - OUT: NONE
; - CLOBBERS: A, BC, DE, HL
; -

FILVRM          PUSH    BC
                LD      B,A
                LD      A,(VDPWCTRL)
                LD      C,A
                OUT     (C),L
                SET     6,H
                OUT     (C),H
                POP     DE
                LD      A,(VDPWDATA)
                LD      C,A
.LOOP           OUT     (C),B
                DEC     DE
                LD      A,D
                OR      E
                JP      NZ,.LOOP
                RET


; - LDIRVM -----------------------------
; - TRANSFER RAM DATA TO VRAM USING OUTI LOOPS
; - IN: HL = RAM SOURCE, DE = VRAM DEST, BC = COUNT
; - OUT: NONE
; - CLOBBERS: A, BC, DE, HL
; -

LDIRVM          PUSH    BC
                LD      A,(VDPWCTRL)
                LD      C,A
                OUT     (C),E
                SET     6,D
                OUT     (C),D
                POP     BC
                LD      D,B
                LD      B,C
                LD      A,(VDPWDATA)
                LD      C,A
                LD      A,B
                AND     A
                LD      A,D 
                JP      Z,.LOOP
.LOW            OUTI
                JP      NZ,.LOW
                AND     A
                RET     Z
.LOOP           LD      B,0
.HIGH           OUTI    
                JP      NZ,.HIGH
                DEC     A
                JP      NZ,.LOOP
                RET
```

## Verification Plan

### Automated Tests
- Run `make clean && make build` to verify compiling with zero errors.

### Manual Verification
- Run the compiled ROM to confirm the screen displays correctly with no visual corruption.
- Verify the 2-second splash screen still displays correctly on boot.
