#!/usr/bin/env python3
import sys
import os
import argparse
import tempfile
import subprocess

# Ensure we can import modules from the project
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.append(project_root)

# Import MusaX core modules
try:
    from MusaX.tools.msl_parser import MSLParser
    from MusaX.tools.msl_compiler import MSLCompiler
    from MusaX.tools.msl2z8a import msl2z8a
    from MusaX.tools.musax_sim import MusaXSim
except ImportError:
    # Fallback for direct execution in tools dir
    from msl_parser import MSLParser
    from msl_compiler import MSLCompiler
    from msl2z8a import msl2z8a
    from musax_sim import MusaXSim

def cmd_build(args):
    """Compiles MSL to Z8A."""
    input_file = args.input
    output_file = args.output
    song_name = args.song_name
    
    msl2z8a(input_file, output_file, song_name)

def cmd_play(args):
    """Plays MSL or Z8A file using the simulator."""
    input_file = args.input
    
    if input_file.lower().endswith('.msl'):
        # Compile to temporary Z8A file
        with tempfile.NamedTemporaryFile(suffix='.Z8A', mode='w', delete=False) as tmp:
            tmp_name = tmp.name
        
        try:
            # We use msl2z8a to get the full formatted Z8A
            msl2z8a(input_file, tmp_name)
            
            # Run simulator
            sim = MusaXSim()
            sim.load_z8a(tmp_name)
            sim.run(loops=args.loops)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
    else:
        # Direct Z8A playback
        sim = MusaXSim()
        sim.load_z8a(input_file)
        sim.run(loops=args.loops)

def cmd_info(args):
    """Displays information about a music file."""
    input_file = args.input
    
    if input_file.lower().endswith('.msl'):
        with open(input_file, 'r') as f:
            source = f.read()
        parser = MSLParser()
        events = parser.parse(source)
        compiler = MSLCompiler()
        result = compiler.compile(events)
        metadata = result.get("metadata", {})
        
        print(f"--- MusaX File Info: {os.path.basename(input_file)} ---")
        print(f"Title:   {metadata.get('TITLE', 'N/A')}")
        print(f"Author:  {metadata.get('AUTHOR', 'N/A')}")
        print(f"Desc:    {metadata.get('DESC', 'N/A')}")
        print(f"Insts:   {len(result.get('instruments', {}))}")
        print(f"Labels:  {', '.join(result.get('labels', {}).keys())}")
        print(f"Size:    {len(result.get('bytecode', []))} bytes (bytecode)")
    else:
        print("Info command currently only supports .MSL files.")

def cmd_import(args):
    """Imports MuseScore (.mscz/.mscx) score into MSL."""
    try:
        from MusaX.tools.mscz2msl import MsczReader, MslEmitter, print_score_info
    except ImportError:
        from mscz2msl import MsczReader, MslEmitter, print_score_info

    try:
        reader = MsczReader(args.input)
    except Exception as e:
        print(f"Error reading MuseScore score: {e}", file=sys.stderr)
        sys.exit(1)

    if args.info:
        print_score_info(reader)
        return

    staff_map = {s.staff_id: s for s in reader.staves}
    assigned_mappings = {}
    channels = ["CH_A", "CH_B", "CH_C"]
    user_picks = [args.channel_a, args.channel_b, args.channel_c]

    for ch_name, pick in zip(channels, user_picks):
        if pick is not None:
            if str(pick) in staff_map:
                assigned_mappings[ch_name] = staff_map[str(pick)]
            else:
                try:
                    idx = int(pick) - 1
                    if 0 <= idx < len(reader.staves):
                        assigned_mappings[ch_name] = reader.staves[idx]
                except ValueError:
                    pass

    auto_idx = 0
    for ch_name in channels:
        if ch_name not in assigned_mappings:
            while auto_idx < len(reader.staves):
                candidate = reader.staves[auto_idx]
                auto_idx += 1
                if candidate not in assigned_mappings.values():
                    assigned_mappings[ch_name] = candidate
                    break

    bars_per_line = 4 if getattr(args, "compact", False) else getattr(args, "bars_per_line", 1)
    repeat_mode = getattr(args, "repeats", "phrases")
    emitter = MslEmitter(chord_mode=args.chord, transpose=args.transpose, bars_per_line=bars_per_line, repeat_mode=repeat_mode)
    msl_content = emitter.generate_full_msl(reader, assigned_mappings)

    output_path = args.output or f"{os.path.splitext(args.input)[0]}.msl"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(msl_content)
    print(f"Successfully converted: {args.input} -> {output_path}")

    if args.play:
        class PlayArgs:
            input = output_path
            loops = 0
        cmd_play(PlayArgs())

def main():
    parser = argparse.ArgumentParser(description="MusaX CLI Hub - Unified Developer Tool")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Build command
    p_build = subparsers.add_parser("build", help="Compile MSL to Z8A")
    p_build.add_argument("input", help="Input .MSL file")
    p_build.add_argument("-o", "--output", help="Output .Z8A file")
    p_build.add_argument("-s", "--song-name", help="Override song name for labels")
    
    # Play command
    p_play = subparsers.add_parser("play", help="Play MSL or Z8A file")
    p_play.add_argument("input", help="Input file (.MSL or .Z8A)")
    p_play.add_argument("-l", "--loops", type=int, default=0, help="Number of loops (0=infinite)")
    
    # Info command
    p_info = subparsers.add_parser("info", help="Show file information")
    p_info.add_argument("input", help="Input .MSL file")

    # Import command
    p_import = subparsers.add_parser("import", help="Import MuseScore (.mscz/.mscx) to MSL")
    p_import.add_argument("input", help="Input MuseScore file (.mscz or .mscx)")
    p_import.add_argument("-o", "--output", help="Output .msl file path")
    p_import.add_argument("--info", action="store_true", help="Display score metadata and track listing")
    p_import.add_argument("-a", "--channel-a", help="Staff ID to map to CH_A")
    p_import.add_argument("-b", "--channel-b", help="Staff ID to map to CH_B")
    p_import.add_argument("-c", "--channel-c", help="Staff ID to map to CH_C")
    p_import.add_argument("--chord", choices=["top", "bottom"], default="top", help="Chord resolution strategy")
    p_import.add_argument("--transpose", type=int, default=0, help="Semitone transposition")
    p_import.add_argument("--bars-per-line", type=int, default=1, help="Number of measures per line")
    p_import.add_argument("--compact", action="store_true", help="Format 4 measures per line separated by '|'")
    p_import.add_argument("--repeats", choices=["phrases", "unroll"], default="phrases", help="How to handle repeats: 'phrases' (default, subroutines) or 'unroll'")
    p_import.add_argument("--play", action="store_true", help="Play immediately after import")
    
    args = parser.parse_args()
    
    if args.command == "build":
        cmd_build(args)
    elif args.command == "play":
        cmd_play(args)
    elif args.command == "info":
        cmd_info(args)
    elif args.command == "import":
        cmd_import(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
