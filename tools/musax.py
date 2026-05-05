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
    
    args = parser.parse_args()
    
    if args.command == "build":
        cmd_build(args)
    elif args.command == "play":
        cmd_play(args)
    elif args.command == "info":
        cmd_info(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
