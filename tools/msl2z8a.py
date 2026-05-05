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
    # We'll first find BASE_TICK
    base_tick = 768
    with open(const_file, 'r') as f:
        content = f.read()
        match = re.search(r'^\s*BASE_TICK\s+EQU\s+(\d+)', content, re.MULTILINE | re.IGNORECASE)
        if match:
            base_tick = int(match.group(1))

    # Re-read to process all constants
    const_list = []
    with open(const_file, 'r') as f:
        for line in f:
            # Match EQU lines: NAME EQU VALUE (allow spaces in value)
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

    # Apply constants with priority: 
    # 1. Notes (scientific)
    # 2. Others
    # 3. LEN_ (highest priority for durations)
    
    # First pass: all non-LEN constants
    for name, val in const_list:
        if name.startswith('LEN_'): continue
        if 0 <= val <= 95: # Note range
            if val not in val_to_name or ("#" not in name and "S" not in name):
                if len(name) <= 3:
                    val_to_name[val] = name
        else:
            if val not in val_to_name:
                val_to_name[val] = name
    
    # Second pass: LEN_ constants (they overwrite)
    for name, val in const_list:
        if name.startswith('LEN_'):
            val_to_name[val] = name
                        
    # Re-enforce some specific command names if they were overwritten by aliases
    priority_cmds = {
        0xFE: "CMD_RESTART", 0xFD: "CMD_TEMPO", 0xFC: "CMD_VOLUME", 
        0xFB: "CMD_GATE", 0xFA: "CMD_INST", 0xF9: "CMD_LOOP_S", 
        0xF8: "CMD_LOOP_E", 0xF7: "CMD_GOTO", 0xF6: "CMD_PHASE", 
        0xF5: "CMD_DETUNE", 0xF4: "CMD_CHORUS", 0xF3: "CMD_FADE", 
        0xF2: "CMD_PORTA", 255: "REST", 0x80: "TYPE_SONG", 0x81: "TYPE_FX"
    }
    val_to_name.update(priority_cmds)
    return val_to_name

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
    # We use base_addr=0 so labels are offsets
    result = compiler.compile(events, base_addr=0)
    
    bytecode = result["bytecode"]
    labels = result["labels"]
    instruments = result["instruments"]
    fx_defs = result.get("fx_definitions", {})
    metadata = result.get("metadata", {})
    
    # Song name priority: CLI arg > @TITLE > Filename
    display_name = song_name or metadata.get("TITLE") or os.path.splitext(os.path.basename(input_file))[0]
    author = metadata.get("AUTHOR", "")
    description = metadata.get("DESC", "")

    # Heuristic for default tempo
    # Look for a SetTempo event in the events
    initial_tempo = "#0600"
    from MusaX.tools.msl_parser import SetTempo
    for ev in events:
        if isinstance(ev, SetTempo):
            initial_tempo = f"#{ev.bpm_step:04X}"
            break

    with open(output_file, 'w') as f:
        f.write(f"; ************************************************************************\n")
        f.write(f"; * {os.path.basename(output_file)} - Generated from {os.path.basename(input_file)}\n")
        f.write(f"; * Title: {display_name}\n")
        if author: f.write(f"; * Author: {author}\n")
        if description: f.write(f"; * Description: {description}\n")
        f.write(f"; * MusaX-ML Compiler v1.1\n")
        f.write(f"; ************************************************************************\n\n")
        
        f.write('INCLUDE "musax_const.Z8A"\n\n')
        
        # Safe label name (no spaces)
        safe_name = "".join([c if c.isalnum() or c == '_' else '_' for c in display_name.upper()])
        
        # --- FX Table (if any) ---
        if fx_defs:
            f.write("; --- FX Table ---\n")
            f.write(f"FX_TABLE_{safe_name}:\n")
            for name in fx_defs:
                f.write(f"    DEFW HDR_{name}, 10\n")
            f.write("    DEFW 0, 0\n\n")

            for name, defn in fx_defs.items():
                f.write(f"; --- FX: {name} ---\n")
                f.write(f"HDR_{name}:\n")
                f.write("    DEFB TYPE_FX\n")
                
                block_labels = defn["labels"]
                ptr_a = next((l for l in block_labels if "CH_A" in l.upper() or "CHA" in l.upper()), "0")
                ptr_b = next((l for l in block_labels if "CH_B" in l.upper() or "CHB" in l.upper()), "0")
                ptr_c = next((l for l in block_labels if "CH_C" in l.upper() or "CHC" in l.upper()), "0")
                f.write(f"    DEFW {ptr_a}, {ptr_b}, {ptr_c}\n")
                f.write(f"    DEFW {'INST_TABLE_' + safe_name if instruments else '0'}\n\n")

        # --- Song Header ---
        song_labels = [l for l in labels if l.upper() in ["CH_A", "CHA", "CH_B", "CHB", "CH_C", "CHC"]]
        fx_label_set = set()
        for d in fx_defs.values():
            fx_label_set.update(d["labels"])
        
        has_song_labels = any(l for l in song_labels if l not in fx_label_set)
        
        if has_song_labels or (not fx_defs and bytecode):
            f.write("; --- Song Header ---\n")
            f.write(f"HDR_{safe_name}:\n")
            f.write("    DEFB TYPE_SONG\n")
            
            entry_a = next((l for l in song_labels if l not in fx_label_set and ("CH_A" in l.upper() or "CHA" in l.upper())), None)
            entry_b = next((l for l in song_labels if l not in fx_label_set and ("CH_B" in l.upper() or "CHB" in l.upper())), None)
            entry_c = next((l for l in song_labels if l not in fx_label_set and ("CH_C" in l.upper() or "CHC" in l.upper())), None)
            
            if not any([entry_a, entry_b, entry_c]) and not fx_defs:
                entry_a = "STREAM_START"

            f.write(f"    DEFW {initial_tempo}, {entry_a or '0'}\n")
            f.write(f"    DEFW {initial_tempo}, {entry_b or '0'}\n")
            f.write(f"    DEFW {initial_tempo}, {entry_c or '0'}\n")
            f.write(f"    DEFW {'INST_TABLE_' + safe_name if instruments else '0'}\n\n")
        
        if instruments:
            f.write("; --- Instrument Table ---\n")
            f.write(f"INST_TABLE_{safe_name}:\n")
            for i in range(16):
                if i in instruments:
                    f.write(f"    DEFW INST_{safe_name}_{i}\n")
                else:
                    f.write("    DEFW 0\n")
            f.write("\n")
            for i, data in sorted(instruments.items()):
                f.write(f"INST_{safe_name}_{i}:\n")
                hex_vals = ", ".join([f"#{b:02X}" for b in data])
                f.write(f"    DEFB {hex_vals}\n")
            f.write("\n")
            
        f.write("; --- Bytecode Streams ---\n")
        offset_to_labels = {}
        for name, addr in labels.items():
            offset_to_labels.setdefault(addr, []).append(name)
        
        i = 0
        while i < len(bytecode):
            if i in offset_to_labels:
                for name in offset_to_labels[i]:
                    f.write(f"{name}:\n")
            
            b = bytecode[i]
            
            # Identify command or note
            if b in val_to_const:
                cmd_name = val_to_const[b]
                
                # Check if it's a command that takes arguments
                # From musax_const: 
                # 3-byte cmds: CMD_TEMPO, CMD_GOTO, CMD_RESTART, REST, CMD_FADE, CMD_CHORUS
                # 2-byte cmds: CMD_VOLUME, CMD_GATE, CMD_INST, CMD_LOOP_S, CMD_PHASE, CMD_DETUNE, CMD_PORTA
                # Note: Notes are also 3 bytes (Note + 2 bytes Duration)
                
                if cmd_name in ["CMD_TEMPO", "CMD_GOTO", "CMD_RESTART", "REST"] or (0 <= b <= 95):
                    if i + 2 < len(bytecode):
                        w_val = bytecode[i+1] | (bytecode[i+2] << 8)
                        duration_str = val_to_const.get(w_val, f"#{w_val:04X}")
                        
                        if cmd_name in ["CMD_GOTO", "CMD_RESTART"]:
                            # Find label for this address
                            target_label = next((l for l, a in labels.items() if a == w_val), duration_str)
                            f.write(f"    DEFB {cmd_name}\n    DEFW {target_label}\n")
                        else:
                            f.write(f"    DEFB {cmd_name}\n    DEFW {duration_str}\n")
                        i += 3
                    else:
                        f.write(f"    DEFB {cmd_name}\n")
                        i += 1
                elif cmd_name in ["CMD_FADE", "CMD_CHORUS"]:
                    if i + 2 < len(bytecode):
                        v1, v2 = bytecode[i+1], bytecode[i+2]
                        f.write(f"    DEFB {cmd_name}, #{v1:02X}, #{v2:02X}\n")
                        i += 3
                    else:
                        f.write(f"    DEFB {cmd_name}\n")
                        i += 1
                elif cmd_name in ["CMD_VOLUME", "CMD_GATE", "CMD_INST", "CMD_LOOP_S", "CMD_PHASE", "CMD_DETUNE", "CMD_PORTA"]:
                    if i + 1 < len(bytecode):
                        v = bytecode[i+1]
                        f.write(f"    DEFB {cmd_name}, #{v:02X}\n")
                        i += 2
                    else:
                        f.write(f"    DEFB {cmd_name}\n")
                        i += 1
                else:
                    # Single byte command or constant (e.g. CMD_LOOP_E)
                    f.write(f"    DEFB {cmd_name}\n")
                    i += 1
            else:
                # Unknown byte, just write it
                f.write(f"    DEFB #{b:02X}\n")
                i += 1

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

