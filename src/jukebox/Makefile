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
ROM_OUTPUT  = $(ROM_DIR)/test.rom
SRC_FILE    = $(SRC_DIR)/test.Z8A
SONG_MSL    = $(MUSAX_DIR)/examples/msl/korobeiniki.msl
SONG_Z8A    = $(TMP_DIR)/korobeiniki.Z8A

# --- INCLUDES AND EMULATION
EMULATOR    = m1 ntsc
SJASM_OPTS  = --lst=$(TMP_DIR)/test.lst --sym=$(TMP_DIR)/test.sym --outprefix=$(TMP_DIR)/

# --- DEFAULT TARGET
all: build

# --- CREATE DIRECTORIES
$(ROM_DIR) $(TMP_DIR):
	@mkdir -p $@

# --- COMPILE MSL SONG TO Z8A
$(SONG_Z8A): $(SONG_MSL) | $(TMP_DIR)
	@echo "Compiling song to Z8A..."
	@$(MUSAX_COMP) build $< -o $@

# --- ASSEMBLE ROM BINARY
$(ROM_OUTPUT): $(SRC_FILE) $(SONG_Z8A) | $(ROM_DIR)
	@echo "Assembling Minimal Test ROM..."
	@$(ASM) $(SJASM_OPTS) $(SRC_FILE)
	@cp $(TMP_DIR)/test.raw $(ROM_OUTPUT)

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
