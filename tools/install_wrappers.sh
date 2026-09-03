#!/bin/bash
# install_wrappers.sh — Installs MusaX CLI wrappers into ~/.local/bin
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"

# 1. msl_editor wrapper
cat << EOF > "$BIN_DIR/msl_editor"
#!/bin/bash
exec python3 "$SCRIPT_DIR/msl_editor.py" "\$@"
EOF
chmod +x "$BIN_DIR/msl_editor"
echo "Installed: $BIN_DIR/msl_editor -> $SCRIPT_DIR/msl_editor.py"

# 2. musax CLI hub wrapper
cat << EOF > "$BIN_DIR/musax"
#!/bin/bash
exec python3 "$SCRIPT_DIR/musax.py" "\$@"
EOF
chmod +x "$BIN_DIR/musax"
echo "Installed: $BIN_DIR/musax -> $SCRIPT_DIR/musax.py"

echo "MusaX CLI wrappers successfully installed in $BIN_DIR"
