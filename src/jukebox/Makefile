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
Z8A_TESTS   = $(patsubst $(SRC_DIR)/TESTS/%.msl,$(TMP_DIR)/%.Z8A,$(MSL_TESTS))
MOG_MSL     = $(MUSAX_DIR)/examples/msl/shalomcastle.msl
MOG_Z8A     = $(TMP_DIR)/shalomcastle.Z8A

ALL_Z8A     = $(MOG_Z8A) $(Z8A_TESTS)

# --- INCLUDES AND EMULATION
EMULATOR    = m1 ntsc
SJASM_OPTS  = --lst=$(TMP_DIR)/test.lst --sym=$(TMP_DIR)/test.sym --outprefix=$(TMP_DIR)/

# --- DEFAULT TARGET
all: build

# --- CREATE DIRECTORIES
$(ROM_DIR) $(TMP_DIR):
	@mkdir -p $@

# --- COMPILE MSL SONG TO Z8A
$(TMP_DIR)/%.Z8A: $(SRC_DIR)/TESTS/%.msl | $(TMP_DIR)
	@echo "Compiling test $< to Z8A..."
	@$(MUSAX_COMP) build $< -o $@

$(MOG_Z8A): $(MOG_MSL) | $(TMP_DIR)
	@echo "Compiling song $< to Z8A..."
	@$(MUSAX_COMP) build $< -o $@

# --- ASSEMBLE ROM BINARY
$(ROM_OUTPUT): $(SRC_FILE) $(ALL_Z8A) | $(ROM_DIR)
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
