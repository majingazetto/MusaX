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

def msl2z8a(input_file, output_file=None):
    if not output_file:
        output_file = os.path.splitext(input_file)[0] + ".Z8A"
        
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

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
        f.write(f"; * MusaX-ML Compiler v1.0\n")
        f.write(f"; ************************************************************************\n\n")
        
        f.write('INCLUDE "musax_const.Z8A"\n\n')
        
        # --- FX Table (if any) ---
        if fx_defs:
            f.write("; --- FX Table ---\n")
            f.write("FX_TABLE:\n")
            for name in fx_defs:
                # Priority: search for priority in comments or metadata? 
                # For now, default to 10.
                f.write(f"    DEFW HDR_{name}, 10\n")
            f.write("    DEFW 0, 0\n\n")

            for name, defn in fx_defs.items():
                f.write(f"; --- FX: {name} ---\n")
                f.write(f"HDR_{name}:\n")
                f.write("    DEFB TYPE_FX\n")
                
                # Find entry points for this FX
                # Typically CH_A, CH_B, CH_C labels within the block
                block_labels = defn["labels"]
                ptr_a = next((l for l in block_labels if "CH_A" in l.upper() or "CHA" in l.upper()), "0")
                ptr_b = next((l for l in block_labels if "CH_B" in l.upper() or "CHB" in l.upper()), "0")
                ptr_c = next((l for l in block_labels if "CH_C" in l.upper() or "CHC" in l.upper()), "0")
                f.write(f"    DEFW {ptr_a}, {ptr_b}, {ptr_c}\n")
                f.write(f"    DEFW {'INST_TABLE' if instruments else '0'}\n\n")

        # --- Song Header (if not strictly an FX library) ---
        # A file is a song if it has CH_A/B/C labels OUTSIDE of FX blocks or no FX blocks at all
        song_labels = [l for l in labels if l.upper() in ["CH_A", "CHA", "CH_B", "CHB", "CH_C", "CHC"]]
        # Filter out labels that belong to FX blocks
        fx_label_set = set()
        for d in fx_defs.values():
            fx_label_set.update(d["labels"])
        
        has_song_labels = any(l for l in song_labels if l not in fx_label_set)
        
        if has_song_labels or (not fx_defs and bytecode):
            f.write("; --- Song Header ---\n")
            f.write("HDR_START:\n")
            f.write("    DEFB TYPE_SONG\n")
            
            entry_a = next((l for l in song_labels if l not in fx_label_set and ("CH_A" in l.upper() or "CHA" in l.upper())), None)
            entry_b = next((l for l in song_labels if l not in fx_label_set and ("CH_B" in l.upper() or "CHB" in l.upper())), None)
            entry_c = next((l for l in song_labels if l not in fx_label_set and ("CH_C" in l.upper() or "CHC" in l.upper())), None)
            
            if not any([entry_a, entry_b, entry_c]) and not fx_defs:
                # Fallback for label-less simple songs
                entry_a = "STREAM_START"
                if 0 not in [labels[l] for l in labels]:
                    # We'll handle this in the bytecode section by adding the label
                    pass

            f.write(f"    DEFW {initial_tempo}, {entry_a or '0'}\n")
            f.write(f"    DEFW {initial_tempo}, {entry_b or '0'}\n")
            f.write(f"    DEFW {initial_tempo}, {entry_c or '0'}\n")
            f.write(f"    DEFW {'INST_TABLE' if instruments else '0'}\n\n")
        
        if instruments:
            f.write("; --- Instrument Table ---\n")
            f.write("INST_TABLE:\n")
            for i in range(16):
                if i in instruments:
                    f.write(f"    DEFW INST_{i}\n")
                else:
                    f.write("    DEFW 0\n")
            f.write("\n")
            for i, data in sorted(instruments.items()):
                f.write(f"INST_{i}:\n")
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
            
            # Simple heuristic: try to read until next label or max 8 bytes
            chunk = []
            for j in range(8):
                curr_pos = i + j
                if curr_pos >= len(bytecode): break
                if j > 0 and curr_pos in offset_to_labels: break
                chunk.append(bytecode[curr_pos])
            
            if chunk:
                hex_vals = ", ".join([f"#{b:02X}" for b in chunk])
                f.write(f"    DEFB {hex_vals}\n")
                i += len(chunk)
            else:
                i += 1

    print(f"Successfully compiled {input_file} -> {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MusaX MSL to Z8A Compiler")
    parser.add_argument("input", help="Input .MSL file")
    parser.add_argument("-o", "--output", help="Output .Z8A file")
    
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
        
    args = parser.parse_args()
    msl2z8a(args.input, args.output)
