#!/bin/bash
# install_wrappers.sh — Installs MusaX CLI wrappers into ~/.local/bin
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"

tools=(
    "musax:musax.py"
    "msl_editor:msl_editor.py"
    "musax_sim:musax_sim.py"
    "psglog2msl:psglog2msl.py"
    "msl2z8a:msl2z8a.py"
    "mscz2msl:mscz2msl.py"
)

echo "Installing MusaX CLI wrappers to $BIN_DIR..."

for entry in "${tools[@]}"; do
    wrapper="${entry%%:*}"
    script="${entry#*:}"
    script_path="$SCRIPT_DIR/$script"
    wrapper_path="$BIN_DIR/$wrapper"

    if [ -f "$script_path" ]; then
        echo "Creating wrapper: $wrapper_path -> $script_path"
        cat << EOF > "$wrapper_path"
#!/bin/bash
exec python3 "$script_path" "\$@"
EOF
        chmod +x "$wrapper_path"
        chmod +x "$script_path"
        echo "  Successfully installed $wrapper"
    else
        echo "Warning: Script not found: $script_path"
    fi
done

echo "MusaX CLI wrappers successfully installed in $BIN_DIR"
