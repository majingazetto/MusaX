#!/usr/bin/env python3
import sys
import os
import argparse

# Ensure we can import msl_parser and msl_compiler
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.append(project_root)

from MusaX.tools.msl_parser import MSLParser
from MusaX.tools.msl_compiler import MSLCompiler

def load_constants():
    """Loads constants from musax_const.Z8A and returns a mapping from value to name."""
    const_file = os.path.join(project_root, "MusaX", "src", "musax_const.Z8A")
    val_to_name = {}
    if not os.path.exists(const_file):
        return val_to_name

    import re
    base_tick = 768
    with open(const_file, 'r') as f:
        content = f.read()
        match = re.search(r'^\s*BASE_TICK\s+EQU\s+(\d+)', content, re.MULTILINE | re.IGNORECASE)
        if match:
            base_tick = int(match.group(1))

    const_list = []
    with open(const_file, 'r') as f:
        for line in f:
            match = re.match(r'^\s*([A-Z0-9#\_]+)\s+EQU\s+([^;\n]+)', line, re.IGNORECASE)
            if match:
                name, val_str = match.groups()
                name = name.strip()
                val_str = val_str.strip()
                if val_str.startswith('#'):
                    try: val = int(val_str[1:], 16)
                    except ValueError: continue
                elif val_str.isdigit():
                    val = int(val_str)
                elif 'BASE_TICK' in val_str.upper():
                    try:
                        expr = val_str.upper().replace('BASE_TICK', str(base_tick))
                        val = int(eval(expr))
                    except:
                        continue
                else:
                    continue
                const_list.append((name, val))

    for name, val in const_list:
        if name.startswith('LEN_'): continue
        if 0 <= val <= 95:
            if val not in val_to_name or ("#" not in name and "S" not in name):
                if len(name) <= 3:
                    val_to_name[val] = name
        else:
            if val not in val_to_name:
                val_to_name[val] = name

    for name, val in const_list:
        if name.startswith('LEN_'):
            val_to_name[val] = name

    priority_cmds = {
        0xFE: "CMD_RESTART", 0xFD: "CMD_TEMPO", 0xFC: "CMD_VOLUME",
        0xFB: "CMD_GATE",    0xFA: "CMD_INST",  0xF9: "CMD_LOOP_S",
        0xF8: "CMD_LOOP_E",  0xF7: "CMD_GOTO",  0xF6: "CMD_PHASE",
        0xF5: "CMD_DETUNE",  0xF4: "CMD_CHORUS",0xF3: "CMD_FADE",
        0xF2: "CMD_PORTA",   0xF1: "CMD_CALL",  0xF0: "CMD_RET",
        255: "REST", 0x80: "TYPE_SONG", 0x81: "TYPE_FX"
    }
    val_to_name.update(priority_cmds)
    return val_to_name


# ---------------------------------------------------------------------------
# Assembly line formatting  (label col=0, directive col=16, operands col=24)
# Matches the hand-written .Z8A style used across this workspace.
# ---------------------------------------------------------------------------

def _aline(lbl: str, directive: str, operands: str) -> str:
    """Label + directive + operands, column-aligned."""
    pad = max(16 - len(lbl), 1)
    return f'{lbl}{" " * pad}{directive:<8}{operands}\n'

def _line(directive: str, operands: str) -> str:
    """No-label directive line."""
    return f'{"":16}{directive:<8}{operands}\n'

def _g(sym: str, use_module: bool) -> str:
    """Prefix a constant symbol with @ for global scope inside a sjasmplus MODULE.
    Hex literals (#XX), plain numbers, and '0' are returned unchanged."""
    if not use_module or sym.startswith('#') or sym == '0' or sym.isdigit():
        return sym
    return f'@{sym}'


def msl2z8a(input_file, output_file=None, song_name=None):
    if not output_file:
        output_file = os.path.splitext(input_file)[0] + ".Z8A"

    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    val_to_const = load_constants()

    with open(input_file, 'r') as f:
        source = f.read()

    parser = MSLParser()
    events = parser.parse(source)

    if parser.errors:
        print(f"Errors found in {input_file}:")
        for err in parser.errors:
            print(f"  Line {err.line}, col {err.column}: {err.message}")
        return

    compiler = MSLCompiler()
    result = compiler.compile(events, base_addr=0)

    bytecode    = result["bytecode"]
    labels      = result["labels"]
    instruments = result["instruments"]
    fx_defs     = result.get("fx_definitions", {})
    metadata    = result.get("metadata", {})

    module_name = metadata.get('MODULE', '').strip().upper()
    use_module  = bool(module_name)

    display_name = song_name or metadata.get("TITLE") or os.path.splitext(os.path.basename(input_file))[0]
    author       = metadata.get("AUTHOR", "")
    description  = metadata.get("DESC", "")

    if use_module:
        safe_name = module_name
    else:
        safe_name = "".join([c if c.isalnum() or c == '_' else '_' for c in display_name.upper()])

    initial_tempo = "#0600"
    from MusaX.tools.msl_parser import SetTempo
    for ev in events:
        if isinstance(ev, SetTempo):
            initial_tempo = f"#{ev.bpm_step:04X}"
            break

    # In module mode: short generic labels; the MODULE name scopes them.
    hdr_label   = 'HEADER'   if use_module else f'HDR_{safe_name}'
    itbl_label  = 'INST_TABLE' if use_module else f'INST_TABLE_{safe_name}'
    inst_label  = (lambda i: f'INST_{i}') if use_module else (lambda i: f'INST_{safe_name}_{i}')
    fxtbl_label = 'FX_TABLE' if use_module else f'FX_TABLE_{safe_name}'

    with open(output_file, 'w') as f:

        # --- File header comment ---
        f.write(f"; {'*' * 72}\n")
        f.write(f"; * {os.path.basename(output_file)} - Generated from {os.path.basename(input_file)}\n")
        f.write(f"; * Title: {display_name}\n")
        if author:      f.write(f"; * Author: {author}\n")
        if description: f.write(f"; * Description: {description}\n")
        f.write(f"; * MusaX-ML Compiler v1.1\n")
        f.write(f"; {'*' * 72}\n\n")

        if use_module:
            f.write(_line('MODULE', module_name) + '\n')
        else:
            f.write('INCLUDE "musax_const.Z8A"\n\n')

        # --- FX Table ---
        if fx_defs:
            f.write("; --- FX Table ---\n\n")
            first = True
            for name in fx_defs:
                lbl = fxtbl_label if first else ''
                first = False
                f.write(_aline(lbl, 'DEFW', f'HDR_{name}, 10'))
            f.write(_line('DEFW', '0, 0'))
            f.write('\n')

            for name, defn in fx_defs.items():
                f.write(f"; --- FX: {name}\n\n")
                block_labels = defn["labels"]
                ptr_a = next((l for l in block_labels if "CH_A" in l.upper() or "CHA" in l.upper()), "0")
                ptr_b = next((l for l in block_labels if "CH_B" in l.upper() or "CHB" in l.upper()), "0")
                ptr_c = next((l for l in block_labels if "CH_C" in l.upper() or "CHC" in l.upper()), "0")
                f.write(_aline(f'HDR_{name}', 'DEFB', _g('TYPE_FX', use_module)))
                f.write(_line('DEFW', f'{ptr_a}, {ptr_b}, {ptr_c}'))
                f.write(_line('DEFW', itbl_label if instruments else '0'))
                f.write('\n')

        # --- Song Header ---
        song_labels  = [l for l in labels if l.upper() in ["CH_A", "CHA", "CH_B", "CHB", "CH_C", "CHC"]]
        fx_label_set = set()
        for d in fx_defs.values():
            fx_label_set.update(d["labels"])
        has_song_labels = any(l for l in song_labels if l not in fx_label_set)

        if has_song_labels or (not fx_defs and bytecode):
            f.write("; --- Song Header ---\n\n")
            entry_a = next((l for l in song_labels if l not in fx_label_set and ("CH_A" in l.upper() or "CHA" in l.upper())), None)
            entry_b = next((l for l in song_labels if l not in fx_label_set and ("CH_B" in l.upper() or "CHB" in l.upper())), None)
            entry_c = next((l for l in song_labels if l not in fx_label_set and ("CH_C" in l.upper() or "CHC" in l.upper())), None)
            if not any([entry_a, entry_b, entry_c]) and not fx_defs:
                entry_a = "STREAM_START"

            f.write(_aline(hdr_label,  'DEFB', _g('TYPE_SONG', use_module)))
            f.write(_line('DEFW', f'{initial_tempo}, {entry_a or "0"}'))
            f.write(_line('DEFW', f'{initial_tempo}, {entry_b or "0"}'))
            f.write(_line('DEFW', f'{initial_tempo}, {entry_c or "0"}'))
            f.write(_line('DEFW', itbl_label if instruments else '0'))
            f.write('\n')

        # --- Instrument Table ---
        if instruments:
            f.write("; --- Instrument Table ---\n\n")
            first = True
            for i in range(16):
                lbl = itbl_label if first else ''
                first = False
                ref = inst_label(i) if i in instruments else '0'
                f.write(_aline(lbl, 'DEFW', ref))
            f.write('\n')

            for i, data in sorted(instruments.items()):
                # Max 8 bytes per DEFB line (workspace convention)
                first_chunk = True
                for chunk_start in range(0, len(data), 8):
                    chunk = data[chunk_start:chunk_start + 8]
                    hex_vals = ', '.join(f'#{b:02X}' for b in chunk)
                    lbl = inst_label(i) if first_chunk else ''
                    first_chunk = False
                    f.write(_aline(lbl, 'DEFB', hex_vals))
            f.write('\n')

        # --- Bytecode Streams ---
        f.write("; --- Bytecode Streams ---\n\n")
        offset_to_labels = {}
        for name, addr in labels.items():
            offset_to_labels.setdefault(addr, []).append(name)

        i = 0
        while i < len(bytecode):
            labels_here = offset_to_labels.get(i, [])
            # Extra labels (all but the last) get their own lines with colon
            for name in labels_here[:-1]:
                f.write(f'{name}:\n')
            lbl = labels_here[-1] if labels_here else ''

            b = bytecode[i]

            if b in val_to_const:
                cmd_name = val_to_const[b]
                cmd_ref  = _g(cmd_name, use_module)

                if cmd_name in ["CMD_TEMPO", "CMD_GOTO", "CMD_RESTART", "CMD_CALL", "REST"] or (0 <= b <= 95):
                    if i + 2 < len(bytecode):
                        w_val   = bytecode[i+1] | (bytecode[i+2] << 8)
                        raw_dur = val_to_const.get(w_val, f'#{w_val:04X}')
                        f.write(_aline(lbl, 'DEFB', cmd_ref))
                        if cmd_name in ["CMD_GOTO", "CMD_RESTART", "CMD_CALL"]:
                            target = next((l for l, a in labels.items() if a == w_val), raw_dur)
                            f.write(_line('DEFW', target))
                        else:
                            f.write(_line('DEFW', _g(raw_dur, use_module)))
                        i += 3
                    else:
                        f.write(_aline(lbl, 'DEFB', cmd_ref))
                        i += 1

                elif cmd_name in ["CMD_FADE", "CMD_CHORUS"]:
                    if i + 2 < len(bytecode):
                        v1, v2 = bytecode[i+1], bytecode[i+2]
                        f.write(_aline(lbl, 'DEFB', f'{cmd_ref}, #{v1:02X}, #{v2:02X}'))
                        i += 3
                    else:
                        f.write(_aline(lbl, 'DEFB', cmd_ref))
                        i += 1

                elif cmd_name in ["CMD_VOLUME", "CMD_GATE", "CMD_INST", "CMD_LOOP_S",
                                  "CMD_PHASE", "CMD_DETUNE", "CMD_PORTA"]:
                    if i + 1 < len(bytecode):
                        v = bytecode[i+1]
                        f.write(_aline(lbl, 'DEFB', f'{cmd_ref}, #{v:02X}'))
                        i += 2
                    else:
                        f.write(_aline(lbl, 'DEFB', cmd_ref))
                        i += 1

                else:
                    f.write(_aline(lbl, 'DEFB', cmd_ref))
                    i += 1
            else:
                f.write(_aline(lbl, 'DEFB', f'#{b:02X}'))
                i += 1

        if use_module:
            f.write('\n' + _line('ENDMODULE', f' ; {module_name}'))

    print(f"Successfully compiled {input_file} -> {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MusaX MSL to Z8A Compiler")
    parser.add_argument("input", help="Input .MSL file")
    parser.add_argument("-o", "--output", help="Output .Z8A file")
    parser.add_argument("-s", "--song-name", help="Override song name for labels")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    msl2z8a(args.input, args.output, args.song_name)
