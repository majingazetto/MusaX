#!/bin/bash
# Script para arrancar el MusaX Web Editor
# Detectamos el directorio donde está este script para que las rutas sean relativas a él
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo "Arrancando el servidor del editor MusaX desde $DIR..."
python3 "$DIR/web_editor/server.py"
