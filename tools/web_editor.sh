#!/bin/bash
# Script para arrancar el MusaX Web Editor
# Detectamos el directorio donde está este script para que las rutas sean relativas a él
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Verificar dependencias
python3 -c "import flask" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: Flask no está instalado."
    echo "Por favor, ejecuta el script de configuración: ./MusaX/setup.sh"
    exit 1
fi

echo "Arrancando el servidor del editor MusaX desde $DIR..."
python3 "$DIR/web_editor/server.py"
