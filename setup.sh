#!/bin/bash
# MusaX setup — checks and installs all required dependencies.
# To add a new dep: append an entry to PYTHON_DEPS or APT_DEPS below.

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${BOLD}→${NC} $*"; }

OS="$(uname)"
ERRORS=0

# ─── Python packages ──────────────────────────────────────────────────────────
# Format: "import_name:pip_name:description"
PYTHON_DEPS=(
    "prompt_toolkit:prompt_toolkit:TUI editor"
    "pyaudio:pyaudio:audio simulator playback"
)

# ─── System packages (Linux/apt only) ────────────────────────────────────────
# Installed before pip so C extensions can build (e.g. portaudio → pyaudio).
APT_DEPS=(
    "portaudio19-dev:C headers required to build pyaudio"
)

# ─────────────────────────────────────────────────────────────────────────────

check_python() {
    echo -e "\n${BOLD}Python${NC}"
    if command -v python3 &>/dev/null; then
        ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)"
    else
        fail "python3 not found — install Python 3.8+"
        ERRORS=$((ERRORS + 1))
    fi
}

install_apt_deps() {
    [ "$OS" != "Linux" ] && return
    command -v apt-get &>/dev/null || { warn "apt-get not found — skipping system packages"; return; }

    echo -e "\n${BOLD}System packages (apt)${NC}"
    local missing=()
    for entry in "${APT_DEPS[@]}"; do
        pkg="${entry%%:*}"; desc="${entry#*:}"
        if dpkg -s "$pkg" &>/dev/null 2>&1; then
            ok "$pkg — $desc"
        else
            warn "$pkg missing — $desc"
            missing+=("$pkg")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        info "sudo apt-get install -y ${missing[*]}"
        sudo apt-get install -y "${missing[@]}"
    fi
}

install_brew_deps() {
    [ "$OS" != "Darwin" ] && return

    echo -e "\n${BOLD}System packages (brew)${NC}"
    if ! command -v brew &>/dev/null; then
        fail "Homebrew not found — install from https://brew.sh"; ERRORS=$((ERRORS + 1)); return
    fi
    if brew list portaudio &>/dev/null 2>&1; then
        ok "portaudio — C headers required to build pyaudio"
    else
        info "brew install portaudio"
        brew install portaudio
    fi
}

install_python_deps() {
    echo -e "\n${BOLD}Python packages${NC}"
    local missing=()
    for entry in "${PYTHON_DEPS[@]}"; do
        import_name="${entry%%:*}"; rest="${entry#*:}"
        pip_name="${rest%%:*}"; desc="${rest#*:}"
        if python3 -c "import ${import_name}" &>/dev/null; then
            ok "${pip_name} — ${desc}"
        else
            warn "${pip_name} missing — ${desc}"
            missing+=("$pip_name")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        info "pip install ${missing[*]}"
        python3 -m pip install "${missing[@]}" --break-system-packages 2>/dev/null \
            || python3 -m pip install "${missing[@]}" \
            || { fail "pip install failed — try manually: pip3 install ${missing[*]}"; ERRORS=$((ERRORS + 1)); }
    fi
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo -e "${BOLD}MusaX dependency check${NC} (OS: $OS)"

check_python
install_apt_deps
install_brew_deps
install_python_deps

echo ""
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}${BOLD}All dependencies satisfied.${NC}"
else
    echo -e "${RED}${BOLD}$ERRORS error(s) found — check output above.${NC}"
    exit 1
fi
