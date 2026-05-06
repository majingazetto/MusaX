from flask import Flask, render_template, request, jsonify
import sys
import os
import subprocess
import tempfile
import re

# Añadir la raíz del proyecto al path para importar los módulos de MusaX
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.append(project_root)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/play', methods=['POST'])
def play():
    data = request.json
    global_data = data.get('global', '')
    ch_a = data.get('cha', '')
    ch_b = data.get('chb', '')
    ch_c = data.get('chc', '')
    
    # Recomponer el archivo MSL
    full_msl = [global_data]
    if ch_a.strip(): full_msl.append("\nCH_A:\n" + ch_a)
    if ch_b.strip(): full_msl.append("\nCH_B:\n" + ch_b)
    if ch_c.strip(): full_msl.append("\nCH_C:\n" + ch_c)
    
    msl_source = "\n".join(full_msl)
    
    try:
        # Guardar en temporal y lanzar simulador
        with tempfile.NamedTemporaryFile(suffix='.msl', mode='w', delete=False) as tmp:
            tmp.write(msl_source)
            tmp_msl = tmp.name
        
        # Lanzar el simulador de MusaX
        # Usamos el musax.py CLI que ya integra el compilador y el simulador
        cmd = f"python3 MusaX/tools/musax.py play {tmp_msl}"
        
        if sys.platform == 'darwin':
            # En macOS abrimos una terminal nueva para ver el dashboard del simulador
            with tempfile.NamedTemporaryFile(suffix='.sh', mode='w', delete=False) as sh:
                sh.write(f"#!/bin/bash\n{cmd}\n")
                sh_name = sh.name
            os.chmod(sh_name, 0o755)
            subprocess.Popen(['open', '-a', 'Terminal', sh_name])
        else:
            subprocess.Popen(['x-terminal-emulator', '-e', f'bash -c "{cmd}"'])
            
        return jsonify({"status": "success", "message": "Simulator launched"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/load', methods=['POST'])
def load():
    filename = request.json.get('filename')
    if not filename or not os.path.exists(filename):
        return jsonify({"status": "error", "message": "File not found"})
    
    try:
        with open(filename, 'r') as f:
            content = f.read()
        
        # Lógica de splitting por canales
        parts = re.split(r'(CH_[ABC]:)', content)
        global_content = parts[0].strip()
        ch_a, ch_b, ch_c = "", "", ""
        
        for i in range(1, len(parts), 2):
            marker = parts[i]
            data = parts[i+1].strip() if i+1 < len(parts) else ""
            if "CH_A" in marker: ch_a = data
            elif "CH_B" in marker: ch_b = data
            elif "CH_C" in marker: ch_c = data
            
        return jsonify({
            "status": "success",
            "global": global_content,
            "cha": ch_a,
            "chb": ch_b,
            "chc": ch_c
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    print("MusaX Web Editor arrancando en http://127.0.0.1:5001")
    app.run(debug=True, port=5001)
