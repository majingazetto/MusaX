#	*************************************************************************
#	* MUSAX MINIMAL TEST ROM BUILD PIPELINE                                 *
#	*************************************************************************

.PHONY: build run clean

# --- DIRECTORIES
SRC_DIR     = SRC
TMP_DIR     = TMP
ROM_DIR     = ROM
MUSAX_DIR   = ../..

# --- TOOLS
ASM         = sjasmplus
PYTHON      = python3
MUSAX_COMP  = $(PYTHON) $(MUSAX_DIR)/tools/musax.py

# --- SOURCE AND OUTPUT FILES
ROM_OUTPUT  = $(ROM_DIR)/JUKEBOX.ROM
SRC_FILE    = $(SRC_DIR)/JUKEBOX.Z8A

# Test files lists
MSL_TESTS   = $(wildcard $(SRC_DIR)/TESTS/*.msl)
MOG_MSL     = $(MUSAX_DIR)/examples/msl/shalomcastle.msl

# --- INCLUDES AND EMULATION
EMULATOR    = m1 ntsc
SJASM_OPTS  = --lst=$(TMP_DIR)/test.lst --sym=$(TMP_DIR)/test.sym --outprefix=$(TMP_DIR)/ -I $(TMP_DIR)
MSXTOOLS_DIR = $(MUSAX_DIR)/../msxtools
PNG2TMS     = $(PYTHON) $(MSXTOOLS_DIR)/scripts/png2tms-sr1-pat.py

# --- DEFAULT TARGET
all: build

# --- CREATE DIRECTORIES
$(ROM_DIR) $(TMP_DIR):
	@mkdir -p $@

# --- GENERATE PLAYLIST AND COMPILE ALL SONGS
$(TMP_DIR)/PLAYLIST.Z8A: $(MOG_MSL) $(MSL_TESTS) | $(TMP_DIR)
	@echo "Generating playlist and compiling songs..."
	@$(PYTHON) $(MUSAX_DIR)/tools/generate_playlist.py

# --- EXTRACT VRAM PATTERNS FROM TILESET PNG
$(TMP_DIR)/VRAM.BIN: RES/TILESET.PNG | $(TMP_DIR)
	@echo "Extracting VRAM patterns from $<..."
	@$(PNG2TMS) $< $(TMP_DIR)/TILESET
	@mv $(TMP_DIR)/TILESET.PAT $@

# --- ASSEMBLE ROM BINARY
$(ROM_OUTPUT): $(SRC_FILE) $(TMP_DIR)/PLAYLIST.Z8A $(TMP_DIR)/VRAM.BIN | $(ROM_DIR)
	@echo "Assembling Minimal Test ROM..."
	@$(ASM) $(SJASM_OPTS) $(SRC_FILE)
	@cp $(TMP_DIR)/JUKEBOX.raw $(ROM_OUTPUT)

# --- BUILD
build: $(ROM_OUTPUT)

# --- RUN IN EMULATOR
run: build
	@echo "Launching Test ROM in emulator..."
	@$(EMULATOR) $(ROM_OUTPUT)

# --- CLEAN
clean:
	@echo "Cleaning intermediate files..."
	@rm -rf $(TMP_DIR) $(ROM_DIR)

