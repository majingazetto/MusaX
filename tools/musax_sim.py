#!/usr/bin/env python3
import sys
import os
import time
import struct
import subprocess
import re
import argparse
import math
import wave
import tempfile
import termios
import tty
import select
import contextlib

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

@contextlib.contextmanager
def suppress_stderr():
    """Redirects stderr to devnull at the FD level to catch C library noise."""
    stderr_fd = sys.stderr.fileno()
    with os.fdopen(os.dup(stderr_fd), 'w') as old_stderr:
        with open(os.devnull, 'w') as devnull:
            sys.stderr.flush()
            os.dup2(devnull.fileno(), stderr_fd)
        try:
            yield
        finally:
            sys.stderr.flush()
            os.dup2(old_stderr.fileno(), stderr_fd)

try:
    import sounddevice as sd
    import numpy as np
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

# --- MUSAX CONSTANTS ---
BASE_TICK = 768
MAX_CHANNELS = 3
MAX_STREAMS = 6  # 3 Music + 3 FX
SAMPLE_RATE = 44100
INTERRUPT_FREQ = 60
SAMPLES_PER_INT = SAMPLE_RATE // INTERRUPT_FREQ
# Headroom per channel at full amplitude (vol=15 on AY curve = 1.0).
CH_AMP_MAX = 32767 // MAX_CHANNELS

# AY-3-8910 empirical volume curve (index 0-15 → normalized amplitude 0..1).
# Source: psglog2msl.py (May 2026)
_AY_VOL = [
    0.000, 0.013, 0.019, 0.027, 0.038, 0.054, 0.076, 0.107,
    0.152, 0.214, 0.303, 0.428, 0.605, 0.856, 1.000, 1.000,
]


class MusaXSim:
    def __init__(self, silent=False, debug_log=None):
        self.sfx_mask = 0  # Bitmask for active FX on channels A, B, C
        self.current_fx_priority = 0
        self.fx_library = [] # List of {"pointers": [A, B, C], "priority": P}
        self.silent = silent
        self.total_ticks = 0
        self.playing = False
        self.global_loops = 0
        self.active_mask = 0
        self.finished_mask = 0
        self.paused = False
        self.debug_step = False
        self.last_event_ch = -1
        self.history = []  # List of state snapshots
        self.max_history = 100
        self.log_file = None
        if debug_log:
            self.log_file = open(debug_log, "w")
            self.log_file.write(f"--- MusaX Trace Log ---\n")

        self.symbols = {
            "REST": 255, "LEN_Q": 768, "LEN_H": 1536, "LEN_E": 384, "LEN_S": 192,
            "LEN_W": 3072, "LEN_ET": 256, "LEN_QT": 512,
            "TYPE_SONG": 0x80, "TYPE_FX": 0x81
        }
        self.commands = {
            "CMD_TEMPO": 0xFD, "CMD_VOLUME": 0xFC, "CMD_GATE": 0xFB,
            "CMD_INST": 0xFA, "CMD_LOOP_S": 0xF9, "CMD_LOOP_E": 0xF8,
            "CMD_GOTO": 0xF7, "CMD_RESTART": 0xFE, "CMD_PHASE": 0xF6,
            "CMD_DETUNE": 0xF5, "CMD_CHORUS": 0xF4, "CMD_FADE": 0xF3,
            "CMD_PORTA": 0xF2, "CMD_CALL": 0xF1, "CMD_RET": 0xF0
        }
        self.init_notes()
        self.symbols.update(self.commands)

        self.channel_labels = {}  # stream_name -> [(offset, label)]

        # Physical oscillators state
        self.osc_phase = [0.0] * MAX_CHANNELS
        
        self.channels = []
        for i in range(MAX_STREAMS):
            is_fx = i >= 3
            self.channels.append({
                "active": False, "note_val": 255, "freq": 0.0,
                "vol": 15, "cur_vol": 0, "inst": 0,
                "inst_data": [255, 16, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], # default #0: Linear Decay
                "stream": [], "stream_base": 0, "stream_name": "",
                "pc": 0, "wait": 0,
                "note_name": "---", "loop_count": 0, "loop_ticks": 0,
                "loop_stack": [], "call_stack": [],
                "bpm_step": 0x2400 if is_fx else 0x0600, # FX default to 168 BPM
                "accumulator": 0, "detune": 0, "gate": 255, "total_wait": 0,
                "fade_vol": 255.0, "fade_target": 255.0, "fade_step": 0.0,
                "muted": False, "porta_speed": 0, "target_freq": 0.0,
                "target_note": 255, "porta_timer": 0,
                "adsr_state": 0, "adsr_acc": 0.0, "lfo_phase": 0.0, "lfo_delay_ctr": 0
            })

        # Default instrument table — used when source's PTR_INST == 0.
        # 16-byte record:
        #   [0:Att 1:Dec 2:Sus 3:Rel] ADSR
        #   [4:LFODest 5:LFOWave 6:LFOSpeed 7:LFOAmp 8:LFODelay] LFO
        #   [9:Flags 10..15:Reserved]
        # LFOSpeed: 0-255 (phase increment per frame; cycle = 256/speed frames).
        # LFODest: 0=off, 1=pitch (vibrato), 2=volume (tremolo).
        # LFOWave: 0=triangle, 1=saw, 2=square.
        self.default_instruments = [
            [255, 16,   0,   1, 0, 0,  0,  0,  0, 0, 0,0,0,0,0,0], # 0: Linear Decay
            [255, 10, 200,  20, 0, 0,  0,  0,  0, 0, 0,0,0,0,0,0], # 1: Plucky
            [ 10,  5, 255,  10, 1, 0,  8,  4, 20, 0, 0,0,0,0,0,0], # 2: Smooth Lead w/ Vibrato (8=2Hz, 4=±34 cents, delay=20fr)
            [255,  0, 255,   0, 0, 0,  0,  0,  0, 0, 0,0,0,0,0,0], # 3: Full Sustain (Organ)
            [  5, 10, 150,   5, 2, 0,  6,  8,  0, 0, 0,0,0,0,0,0], # 4: Ambient Pad w/ Tremolo (6=1.4Hz, 8=±68 units)
        ]
        # Per-source instrument table pointers (0 = use defaults)
        self.music_inst_ptr = 0
        self.fx_inst_ptr = 0
        # Flat byte-addressed memory built after Pass 3 (for instrument deref)
        self.flat_memory = {}

    def init_notes(self):
        notes = ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"]
        flats = ["C", "Df", "D", "Ef", "E", "F", "Gf", "G", "Af", "A", "Bf", "B"]
        span  = ["Do", "Dos", "Re", "Res", "Mi", "Fa", "Fas", "Sol", "Sols", "La", "Las", "Si"]
        sflat = ["Do", "Reb", "Re", "Mib", "Mi", "Fa", "Solb", "Sol", "Lab", "La", "Sib", "Si"]
        for oct in range(8):
            for i in range(12):
                val = oct * 12 + i
                for n in [notes[i], notes[i].replace("s", "#"), flats[i], span[i], sflat[i]]:
                    if n: self.symbols[f"{n}{oct}"] = val
                self.symbols[f"Rb{oct}"] = oct * 12 + 1

    def note_to_freq(self, note_val, detune=0):
        if note_val == 255: return 0.0
        # detune is in cents (1/100th of a semitone)
        return 440.0 * (2.0 ** (((note_val + 12 + detune/100.0) - 69.0) / 12.0))

    def _read_word(self, addr):
        if addr == 0: return 0
        return self.flat_memory.get(addr, 0) | (self.flat_memory.get(addr + 1, 0) << 8)

    def _read_byte(self, addr):
        return self.flat_memory.get(addr, 0)

    def resolve_instrument(self, table_ptr, inst_id):
        """Resolve CMD_INST id through a pointer-table at table_ptr.
        Returns a 16-byte list. Falls back to defaults when table_ptr==0
        or the dereferenced address is invalid."""
        if table_ptr == 0:
            if 0 <= inst_id < len(self.default_instruments):
                return list(self.default_instruments[inst_id])
            return list(self.default_instruments[0])
        rec_addr = self._read_word(table_ptr + inst_id * 2)
        if rec_addr == 0 or rec_addr not in self.flat_memory:
            return list(self.default_instruments[0])
        return [self._read_byte(rec_addr + i) for i in range(16)]

    def eval_expr(self, expr):
        try:
            # Strip sjasmplus global-scope @ prefix (e.g. @TYPE_SONG → TYPE_SONG)
            expr = re.sub(r'@(\w)', r'\1', expr)

            # Handle Sjasmplus low/high byte operators
            expr = re.sub(r"([^\w])<(\w+)", r"\1(\2 & 0xFF)", expr)
            if expr.startswith("<"): expr = re.sub(r"^<(\w+)", r"(\1 & 0xFF)", expr)
            expr = re.sub(r"([^\w])>(\w+)", r"\1((\2 >> 8) & 0xFF)", expr)
            if expr.startswith(">"): expr = re.sub(r"^>(\w+)", r"((\1 >> 8) & 0xFF)", expr)

            # Only replace # if it's followed by hex and NOT preceded by a note letter (A-G)
            expr = re.sub(r"(?<![A-Ga-g])#([0-9A-Fa-f]+)", r"0x\1", expr)
            for _ in range(5):
                sorted_syms = sorted(self.symbols.keys(), key=len, reverse=True)
                for sym in sorted_syms:
                    if sym in expr:
                        # Use a regex that handles potential leading dots in symbols
                        pattern = rf"\b{re.escape(sym)}\b"
                        if sym.startswith("."): 
                            pattern = rf"(?<![\w.]){re.escape(sym)}\b"
                        expr = re.sub(pattern, str(self.symbols[sym]), expr)
            clean_expr = expr.replace(" ", "")
            if not re.match(r"^[0-9a-fA-Fx+\-*/%()&| \t.><]+$", clean_expr): 
                return 0
            return int(eval(expr))
        except Exception:
            return 0

    def _preload_base_constants(self):
        """Load EQU symbols from musax_const.Z8A into self.symbols (idempotent)."""
        if getattr(self, '_base_constants_loaded', False):
            return
        self._base_constants_loaded = True
        const_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'musax_const.Z8A')
        if not os.path.exists(const_file):
            return
        const_lines = self.read_with_includes(os.path.basename(const_file), os.path.dirname(const_file))
        for _ in range(5):
            for line in const_lines:
                m = re.match(r"^(\w+)\s+EQU\s+(.+)$", line)
                if m:
                    self.symbols[m.group(1)] = self.eval_expr(m.group(2))

    def load_z8a(self, filename, is_fx_only=False):
        if not os.path.exists(filename): print(f"Error: {filename} not found"); sys.exit(1)

        # Ensure base constants (note pitches, durations, commands) are always available,
        # even when the Z8A was generated in MODULE mode and has no INCLUDE line.
        self._preload_base_constants()

        base_path = os.path.dirname(filename)
        lines = self.read_with_includes(os.path.basename(filename), base_path)

        # Pass 1: Gather EQU symbols
        for _ in range(5):
            for line in lines:
                m = re.match(r"^(\w+)\s+EQU\s+(.+)$", line)
                if m: self.symbols[m.group(1)] = self.eval_expr(m.group(2))

        # Pass 2: Map global and local labels
        # Track MODULE context so labels inside a MODULE are registered under both
        # their bare name and their fully-qualified "MODULE.label" name.
        global_labels = {}
        curr_global   = None
        curr_module   = None   # active sjasmplus MODULE name, or None
        # Start base_addr high for FX if merging
        base_addr = 0x8000 if is_fx_only else 0x1000
        current_offset = 0

        local_stream_bases = {}
        for line in lines:
            # MODULE / ENDMODULE directives
            mod_m = re.match(r'^MODULE\s+(\w+)$', line, re.IGNORECASE)
            if mod_m:
                curr_module = mod_m.group(1)
                continue
            if re.match(r'^ENDMODULE\b', line, re.IGNORECASE):
                curr_module = None
                continue

            m = re.match(r"^([\w.]+):$", line)
            if m:
                label = m.group(1)
                if label.startswith("."):
                    if curr_global:
                        full_name = curr_global + label
                        self.symbols[full_name] = base_addr + current_offset
                        self.channel_labels.setdefault(curr_global, []).append((current_offset, label))
                else:
                    curr_global = label
                    addr = base_addr + current_offset
                    local_stream_bases[curr_global] = addr
                    self.symbols[curr_global] = addr
                    # Also register the module-qualified name so cross-file DEFW references work
                    if curr_module:
                        self.symbols[f'{curr_module}.{label}'] = addr
                    global_labels[curr_global] = []
                continue

            # Inline label+directive (sjasmplus style, no colon):  LABEL   DEFB/DEFW   data
            inline_m = re.match(r"^([\w.]+)\s+(DEFB|DEFW)\s+(.+)$", line, re.IGNORECASE)
            if inline_m:
                label     = inline_m.group(1)
                directive = inline_m.group(2).upper()
                rest      = inline_m.group(3)
                if label.startswith("."):
                    if curr_global:
                        full_name = curr_global + label
                        self.symbols[full_name] = base_addr + current_offset
                        self.channel_labels.setdefault(curr_global, []).append((current_offset, label))
                else:
                    curr_global = label
                    addr = base_addr + current_offset
                    local_stream_bases[curr_global] = addr
                    self.symbols[curr_global] = addr
                    if curr_module:
                        self.symbols[f'{curr_module}.{label}'] = addr
                    global_labels[curr_global] = []
                if curr_global:
                    is_word = (directive == 'DEFW')
                    for p in [p.strip() for p in re.split(r",", rest.strip())]:
                        if p.startswith("."): p = curr_global + p
                        global_labels[curr_global].append((p, is_word))
                        current_offset += 2 if is_word else 1
                continue

            if curr_global and (line.startswith("DEFB") or line.startswith("DEFW")):
                is_word = line.startswith("DEFW")
                parts = [p.strip() for p in re.split(r",", line[4:].strip())]
                for p in parts:
                    if p.startswith("."): p = curr_global + p
                    global_labels[curr_global].append((p, is_word))
                    current_offset += 2 if is_word else 1

        # Pass 3: Resolve streams
        if not hasattr(self, "all_streams"): self.all_streams = {}
        if not hasattr(self, "stream_bases"): self.stream_bases = {}
        
        for s_name, byte_data in global_labels.items():
            bytes_out = []
            for expr, is_word in byte_data:
                val = self.eval_expr(expr)
                if is_word:
                    bytes_out.extend([val & 0xFF, (val >> 8) & 0xFF])
                else:
                    bytes_out.append(val & 0xFF)
            self.all_streams[s_name] = bytes_out
            self.stream_bases[s_name] = local_stream_bases[s_name]
            base = local_stream_bases[s_name]
            for i, b in enumerate(bytes_out):
                self.flat_memory[base + i] = b

        if not is_fx_only:
            # Pass 4: Assign Music Streams
            # Build master_stream view for absolute addressing
            max_addr = max(self.flat_memory.keys()) if self.flat_memory else 0
            master_stream = [self.flat_memory.get(i, 0) for i in range(max_addr + 1)]

            # Find Music Header — primary: TYPE_SONG byte; fallback: label name heuristic
            type_song = self.symbols.get("TYPE_SONG", 0x80)
            music_hdr_name = next((k for k in self.all_streams if self.all_streams[k] and self.all_streams[k][0] == type_song), None)
            if not music_hdr_name:
                # Matches HDR_xxx (standalone) and HEADER / MODULE.HEADER (module mode)
                music_hdr_name = next(
                    (k for k in self.all_streams
                     if ("HDR" in k.upper() or k.upper().endswith("HEADER")) and "FX" not in k.upper()),
                    None
                )
            
            if music_hdr_name:
                hdr_bytes = self.all_streams[music_hdr_name]
                has_sign = (hdr_bytes[0] == type_song)
                offset = 1 if has_sign else 0
                
                if len(hdr_bytes) >= (offset + 12):
                    for i in range(3):
                        bpm = hdr_bytes[offset + i*4] + (hdr_bytes[offset + i*4 + 1] << 8)
                        ptr = hdr_bytes[offset + i*4 + 2] + (hdr_bytes[offset + i*4 + 3] << 8)
                        ch = self.channels[i]
                        ch["bpm_step"] = bpm
                        if ptr != 0 and ptr < len(master_stream):
                            ch["stream"] = master_stream
                            ch["stream_base"] = 0 
                            ch["pc"] = ptr
                            ch["initial_pc"] = ptr
                            ch["stream_name"] = next((name for name, addr in self.stream_bases.items() if addr == ptr), f"PTR_{ptr:04X}")
                            ch["active"] = True
                    
                    inst_off = offset + 12
                    if len(hdr_bytes) >= inst_off + 2:
                        self.music_inst_ptr = hdr_bytes[inst_off] + (hdr_bytes[inst_off + 1] << 8)
            
        # Pass 5: FX Logic
        fx_table_name = next((k for k in global_labels if "FX_TABLE" in k), None)
        if fx_table_name:
            table_bytes = self.all_streams[fx_table_name]
            for i in range(0, len(table_bytes), 4):
                if i + 3 >= len(table_bytes): break
                ptr = table_bytes[i] + (table_bytes[i+1] << 8)
                if ptr == 0: break
                priority = table_bytes[i+2] + (table_bytes[i+3] << 8)
                
                hdr_name = next((name for name, addr in self.stream_bases.items() if addr == ptr), None)
                if hdr_name and hdr_name in self.all_streams:
                    hdr_bytes = self.all_streams[hdr_name]
                    type_fx = self.symbols.get("TYPE_FX", 0x81)
                    has_sign = (hdr_bytes[0] == type_fx)
                    offset = 1 if has_sign else 0
                    
                    fx_data = []
                    # Standard FX header is 3 pointers (6 bytes) or 3 [bpm, ptr] pairs (12 bytes)
                    # We'll check the signature or length to decide
                    is_extended = (len(hdr_bytes) >= (offset + 12))
                    
                    for j in range(3):
                        if is_extended:
                            bpm = hdr_bytes[offset + j*4] + (hdr_bytes[offset + j*4+1] << 8)
                            ptr_fx = hdr_bytes[offset + j*4+2] + (hdr_bytes[offset + j*4+3] << 8)
                            fx_data.append({"bpm": bpm, "ptr": ptr_fx})
                        else:
                            if offset + j*2 + 1 < len(hdr_bytes):
                                ptr_fx = hdr_bytes[offset + j*2] + (hdr_bytes[offset + j*2+1] << 8)
                                fx_data.append({"bpm": 0x2400, "ptr": ptr_fx})
                            else:
                                fx_data.append({"bpm": 0x2400, "ptr": 0})
                    
                    # v1.9: Instrument pointer
                    inst_off = offset + (12 if is_extended else 6)
                    if len(hdr_bytes) >= inst_off + 2:
                        inst_ptr = hdr_bytes[inst_off] + (hdr_bytes[inst_off+1] << 8)
                    else:
                        inst_ptr = 0
                    self.fx_library.append({"data": fx_data, "priority": priority, "name": hdr_name, "inst_ptr": inst_ptr})

    def read_with_includes(self, fname, base_path):
        candidates = [
            os.path.join(base_path, fname),
            fname,
            os.path.join(base_path, "..", "src", fname),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", fname),
        ]
        full_path = next((p for p in candidates if os.path.exists(p)), None)
        if full_path is None: return []
        with open(full_path, "r") as f:
            lines = f.readlines()
        processed = []
        for line in lines:
            line = line.split(";")[0].strip()
            if not line: continue
            inc_match = re.match(r"^INCLUDE\s+[\"\']?([\w\.\-]+)[\"\']?", line, re.I)
            if inc_match:
                inc_file = inc_match.group(1)
                processed.extend(self.read_with_includes(inc_file, os.path.dirname(full_path)))
            else:
                processed.append(line)
        return processed

    def musax_req_fx(self, fx_idx):
        if fx_idx < 0 or fx_idx >= len(self.fx_library): return
        req = self.fx_library[fx_idx]
        
        # Priority Logic: If an FX is already playing, check if new priority >= current
        any_fx_active = any(self.channels[i+3]["active"] for i in range(3))
        if any_fx_active and req["priority"] < self.current_fx_priority:
            if self.log_file: self.log_file.write(f"T:{self.total_ticks} | REQ_FX IGNORED: {req['name']} (P:{req['priority']} < {self.current_fx_priority})\n")
            return

        # Load new FX
        self.current_fx_priority = req["priority"]
        self.fx_inst_ptr = req.get("inst_ptr", 0)
        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | REQ_FX START: {req['name']} (P:{self.current_fx_priority})\n")
        
        for i in range(3):
            fx_item = req["data"][i]
            ptr = fx_item["ptr"]
            bpm = fx_item["bpm"]
            ch = self.channels[i+3]
            
            if ptr == 0:
                ch["active"] = False
            else:
                max_addr = max(self.flat_memory.keys()) if self.flat_memory else 0
                if ptr <= max_addr:
                    master_stream = [self.flat_memory.get(j, 0) for j in range(max_addr + 1)]
                    ch["stream"] = master_stream
                    ch["stream_base"] = 0
                    ch["stream_name"] = next((name for name, addr in self.stream_bases.items() if addr == ptr), f"PTR_{ptr:04X}")
                    ch["pc"] = ptr
                    ch["initial_pc"] = ptr
                    ch["wait"] = 0
                    ch["loop_ticks"] = 0
                    ch["loop_stack"] = []
                    ch["bpm_step"] = bpm # Use BPM from header
                    ch["active"] = True
                    ch["adsr_state"] = 0
                    ch["adsr_acc"] = 0.0
                    ch["inst"] = 0
                    ch["inst_data"] = self.resolve_instrument(self.fx_inst_ptr, 0)
                else:
                    ch["active"] = False

    def _reset_channels(self):
        self.active_mask = 0
        self.finished_mask = 0
        self.global_loops = 0
        self.sfx_mask = 0
        self.current_fx_priority = 0
        for i, ch in enumerate(self.channels):
            ch["pc"] = ch.get("initial_pc", 0); ch["wait"] = 0; ch["loop_count"] = 0; ch["loop_stack"] = []; ch["loop_ticks"] = 0
            ch["accumulator"] = 0
            if i >= 3: ch["bpm_step"] = 0x2400 # FX default
            if ch["stream"]:
                ch["active"] = True
                if i < 3: self.active_mask |= (1 << i)
            else:
                ch["active"] = False

    def jump_to(self, ch, addr):
        """Helper to jump to an absolute address, resolving the correct stream."""
        # v2.0: If the channel is using the master_stream (stream_base == 0),
        # just update the absolute PC.
        if ch.get("stream_base") == 0 and ch.get("stream"):
            if 0 <= addr < len(ch["stream"]):
                ch["pc"] = addr
                return

        best_match = None
        best_base = -1
        for name, base in self.stream_bases.items():
            if name in self.all_streams:
                stream_len = len(self.all_streams[name])
                if base <= addr < base + stream_len:
                    if base > best_base:
                        best_match = name
                        best_base = base
        if best_match:
            ch["stream"] = self.all_streams[best_match]
            ch["stream_base"] = best_base
            ch["stream_name"] = best_match
            ch["pc"] = addr - best_base
        else:
            # Fallback: if we can't find a fragmented stream but addr is in flat_memory,
            # switch this channel to master_stream mode.
            max_addr = max(self.flat_memory.keys()) if self.flat_memory else 0
            if 0 <= addr <= max_addr:
                ch["stream"] = [self.flat_memory.get(i, 0) for i in range(max_addr + 1)]
                ch["stream_base"] = 0
                ch["pc"] = addr
                ch["stream_name"] = next((name for name, b in self.stream_bases.items() if b == addr), f"PTR_{addr:04X}")
            else:
                ch["pc"] = 0

    def process_events(self, ch_idx):
        ch = self.channels[ch_idx]
        if not ch["active"]: return
        safety_counter = 0
        while ch["active"] and ch["wait"] <= 0:
            self.last_event_ch = ch_idx
            self.debug_step = False
            safety_counter += 1
            if safety_counter > 2000:
                print(f"Error: Infinite loop detected in CH:{ch_idx} (PC:{ch['pc']:03X}). Check wait times.")
                ch["active"] = False
                break
            
            if ch["pc"] >= len(ch["stream"]): 
                # End of stream, check call stack
                if ch["call_stack"]:
                    ret = ch["call_stack"].pop()
                    ch["stream"] = ret["stream"]
                    ch["stream_base"] = ret["base"]
                    ch["stream_name"] = ret["name"]
                    ch["pc"] = ret["pc"]
                    continue
                
                ch["active"] = False
                if ch_idx < 3: self.finished_mask |= (1 << ch_idx)
                if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{ch['pc']:03X} | END OF STREAM\n")
                continue
            
            old_pc = ch["pc"]
            cmd = ch["stream"][ch["pc"]]; ch["pc"] += 1
            
            if cmd == 0xFF:  # REST [Len (DEFW)]
                ch["note_val"] = 255; ch["freq"] = 0.0; ch["total_wait"] = 0; ch["gated_logged"] = False
                if ch["pc"] + 1 < len(ch["stream"]):
                    wait_val = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                    if wait_val == 0: # REST 0 = immediate STOP (deactivate channel).
                        # Use CMD_GATE before the final note if you want a release tail.
                        ch["active"] = False
                        ch["adsr_state"] = 0
                        ch["adsr_acc"] = 0.0
                        if ch_idx < 3: self.finished_mask |= (1 << ch_idx)
                        if self.log_file:
                            self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{old_pc:03X} | STOP via REST 0\n")
                            self.log_file.flush()
                        continue
                    else:
                        ch["wait"] = wait_val
                    if self.log_file: 
                        self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{old_pc:03X} | REST len:{ch['wait']}\n")
                        self.log_file.flush()
                else: ch["active"] = False
            elif cmd >= 0xF0 and cmd != 0xFF:
                if cmd == 0xFC: # VOLUME [Val]
                    ch["vol"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                elif cmd == 0xFA: # INST [ID]
                    inst_id = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    ch["inst"] = inst_id
                    table_ptr = self.fx_inst_ptr if ch_idx >= 3 else self.music_inst_ptr
                    ch["inst_data"] = self.resolve_instrument(table_ptr, inst_id)
                elif cmd == 0xFD: # TEMPO [Val]
                    if ch["pc"] + 1 < len(ch["stream"]):
                        ch["bpm_step"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8); ch["pc"] += 2
                elif cmd == 0xF9: # LOOP_S [Count]
                    count = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    ch["loop_stack"].append({"pc": ch["pc"], "count": count, "total": count})
                elif cmd == 0xF8: # LOOP_E
                    if ch["loop_stack"]:
                        ch["loop_stack"][-1]["count"] -= 1
                        if ch["loop_stack"][-1]["count"] > 0:
                            ch["pc"] = ch["loop_stack"][-1]["pc"]
                        else:
                            ch["loop_stack"].pop()
                elif cmd == 0xFB: # GATE [Val]
                    ch["gate"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                elif cmd == 0xF7: # GOTO [Addr (DEFW)]
                    if ch["pc"] + 1 < len(ch["stream"]):
                        addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                        self.jump_to(ch, addr)
                elif cmd == 0xF1: # CALL [Addr (DEFW)]
                    if ch["pc"] + 1 < len(ch["stream"]):
                        addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                        ch["pc"] += 2
                        # Push return address (absolute)
                        ch["call_stack"].append({
                            "stream": ch["stream"], "base": ch["stream_base"], 
                            "name": ch["stream_name"], "pc": ch["pc"]
                        })
                        self.jump_to(ch, addr)
                elif cmd == 0xF0: # RET
                    if ch["call_stack"]:
                        ret = ch["call_stack"].pop()
                        ch["stream"] = ret["stream"]
                        ch["stream_base"] = ret["base"]
                        ch["stream_name"] = ret["name"]
                        ch["pc"] = ret["pc"]
                elif cmd == 0xFE: # RESTART [Addr (DEFW)]
                    if ch_idx < 3: 
                        self.finished_mask |= (1 << ch_idx)
                        ch["loop_ticks"] = 0
                        if ch["pc"] + 1 < len(ch["stream"]):
                            addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                            self.jump_to(ch, addr)
                        else:
                            ch["pc"] = 0
                    else: # FX should NOT restart unless explicitly coded. RESTART in FX = STOP
                        ch["active"] = False
                    
                    if ch_idx < 3 and (self.finished_mask & self.active_mask) == self.active_mask:
                        self.global_loops += 1
                        self.finished_mask = 0
                elif cmd == 0xF6: # PHASE [Val]
                    val = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    # Phase Shift is a fractional timing delay (sub-tick)
                    ch["accumulator"] -= val
                    while ch["accumulator"] < 0:
                        ch["accumulator"] += 256
                        ch["wait"] += 1
                elif cmd == 0xF5: # DETUNE [Val]
                    val = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    if val > 127: val -= 256
                    ch["detune"] = val
                    if ch["note_val"] != 255:
                        ch["freq"] = self.note_to_freq(ch["note_val"], ch["detune"])
                elif cmd == 0xF4: # CHORUS [Phase, Detune]
                    phase = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    detune = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    if detune > 127: detune -= 256
                    # Apply Phase Delay
                    ch["accumulator"] -= phase
                    while ch["accumulator"] < 0:
                        ch["accumulator"] += 256
                        ch["wait"] += 1
                    ch["detune"] = detune
                    if ch["note_val"] != 255:
                        ch["freq"] = self.note_to_freq(ch["note_val"], ch["detune"])
                elif cmd == 0xF3: # FADE [Target, Step]
                    target = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    step = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    if step == 255:
                        ch["fade_vol"] = float(target)
                        ch["fade_target"] = float(target)
                    else:
                        ch["fade_target"] = float(target)
                        ch["fade_step"] = float(step)
                elif cmd == 0xF2: # PORTA [Speed]
                    ch["porta_speed"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
            else:
                ch["target_note"] = cmd
                # Chromatic Portamento: If speed is 0 or first note, snap immediately
                if ch["porta_speed"] == 0 or ch["note_val"] == 255:
                    ch["note_val"] = cmd
                    ch["freq"] = self.note_to_freq(cmd, ch["detune"])
                    # Trigger ADSR Attack
                    ch["adsr_state"] = 1 # ATTACK
                    ch["adsr_acc"] = 0.0
                    
                    # Initialize LFO from cached instrument
                    ch["lfo_delay_ctr"] = ch["inst_data"][8]
                    ch["lfo_phase"] = 0.0
                
                # Reset timer for the new slide
                ch["porta_timer"] = 0
                
                ch["sample_idx"] = 0
                ch["gated_logged"] = False
                notes = ["C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-"]
                ch["note_name"] = f"{notes[cmd % 12]}{cmd // 12}"
                if ch["pc"] + 1 < len(ch["stream"]):
                    ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                    ch["total_wait"] = ch["wait"]
                    if self.log_file: 
                        self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{old_pc:03X} | NOTE: {ch['note_name']}, wait:{ch['wait']}\n")
                        self.log_file.flush()
                else: ch["active"] = False

    def update(self):
        """Update per-frame state (envelopes, priority, status)"""
        self.total_ticks += 1
        self.sfx_mask = 0
        for i in range(MAX_CHANNELS):
            if self.channels[i+3]["active"]:
                self.sfx_mask |= (1 << i)
        
        if self.sfx_mask == 0:
            self.current_fx_priority = 0
            self.fx_inst_ptr = 0

        for ch in self.channels:
            ch["cur_freq_mod"] = ch["freq"]
            inst = ch["inst_data"]

            # --- 1. ADSR STATE MACHINE ---
            if ch["adsr_state"] == 1: # ATTACK
                ch["adsr_acc"] += inst[0]
                if ch["adsr_acc"] >= 255.0:
                    ch["adsr_acc"] = 255.0
                    ch["adsr_state"] = 2 # Pass to DECAY
            elif ch["adsr_state"] == 2: # DECAY
                ch["adsr_acc"] -= inst[1]
                if ch["adsr_acc"] <= inst[2]: # Sustain Level
                    ch["adsr_acc"] = float(inst[2])
                    ch["adsr_state"] = 3 # Pass to SUSTAIN
            elif ch["adsr_state"] == 4: # RELEASE
                ch["adsr_acc"] -= inst[3]
                if ch["adsr_acc"] <= 0.0:
                    ch["adsr_acc"] = 0.0
                    ch["adsr_state"] = 0 # IDLE

            # --- 2. LFO ENGINE ---
            # Phase is a 0..255 unsigned counter, advanced by `speed` units/frame.
            # Wave outputs a signed value in [-127, +127], scaled by amp (0..15).
            lfo_val = 0.0
            if ch["adsr_state"] != 0:
                if ch["lfo_delay_ctr"] > 0:
                    ch["lfo_delay_ctr"] -= 1
                else:
                    lfo_speed = inst[6]
                    lfo_amp   = inst[7]

                    ch["lfo_phase"] = (ch["lfo_phase"] + lfo_speed) % 256.0
                    p = ch["lfo_phase"]
                    wave_type = inst[5]
                    if wave_type == 0:   # Triangle: 0 -> +127 -> 0 -> -127 -> 0
                        if p < 128:
                            wave = p * 2 if p < 64 else 255 - p * 2
                        else:
                            q = p - 128
                            wave = -(q * 2 if q < 64 else 255 - q * 2)
                    elif wave_type == 1: # Saw: ramps -127 -> +127
                        wave = p - 128
                    elif wave_type == 2: # Square: -127 / +127
                        wave = -127 if p < 128 else 127
                    else:                # Sine
                        wave = int(127 * math.sin(2 * math.pi * p / 256))
                    lfo_val = (wave * lfo_amp) / 15.0

            # --- 3. APPLY MODULATIONS ---
            final_vol_scale = ch["adsr_acc"]
            final_freq = ch["freq"]

            lfo_dest = inst[4]
            if lfo_dest == 1: # PITCH (Vibrato) — lfo_val in cents
                final_freq = self.note_to_freq(ch["note_val"], ch["detune"] + lfo_val)
            elif lfo_dest == 2: # VOLUME (Tremolo)
                final_vol_scale = max(0.0, min(255.0, final_vol_scale + lfo_val))

            ch["cur_freq_mod"] = final_freq # Used for mixing

            # Handle Chromatic PORTA logic (60Hz update)
            if ch["active"] and ch["porta_speed"] > 0 and ch["note_val"] != 255 and ch["target_note"] != 255:
                if ch["note_val"] != ch["target_note"]:
                    ch["porta_timer"] += 1
                    if ch["porta_timer"] >= ch["porta_speed"]:
                        ch["porta_timer"] = 0
                        if ch["note_val"] < ch["target_note"]:
                            ch["note_val"] += 1
                        else:
                            ch["note_val"] -= 1
                        ch["freq"] = self.note_to_freq(ch["note_val"], ch["detune"])
                        # Update note_name for visual feedback
                        notes_list = ["C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-"]
                        ch["note_name"] = f"{notes_list[ch['note_val'] % 12]}{ch['note_val'] // 12}"

            # Handle FADE logic (60Hz update)
            if ch["fade_vol"] < ch["fade_target"]:
                ch["fade_vol"] = min(ch["fade_target"], ch["fade_vol"] + ch["fade_step"])
            elif ch["fade_vol"] > ch["fade_target"]:
                ch["fade_vol"] = max(ch["fade_target"], ch["fade_vol"] - ch["fade_step"])

            if not ch["active"] or ch["note_val"] == 255: 
                ch["cur_vol"] = 0
                continue
            
            # SCALE VOLUME: (Channel Vol * Fade Vol * ADSR Acc)
            ch["cur_vol"] = int((ch["vol"] * (ch["fade_vol"] / 255.0) * (final_vol_scale / 255.0)))
            if ch["muted"]: ch["cur_vol"] = 0

    def _vis_len(self, s):
        """Returns the visible length of a string, excluding ANSI escape codes."""
        return len(re.sub(r'\033\[[0-9;]*m', '', s))

    def _pad(self, s, width):
        """Pads a string to a specific visible width."""
        return s + " " * max(0, width - self._vis_len(s))

    def _current_label(self, ch):
        labels = self.channel_labels.get(ch.get("stream_name", ""), [])
        result = ""
        for offset, label in labels:
            if offset <= ch["pc"]:
                result = label
        return result

    def _loop_info(self, ch):
        if not ch["loop_stack"]:
            return ""
        parts = []
        for e in ch["loop_stack"]:
            total = e.get("total", e["count"])
            cur = total - e["count"] + 1
            parts.append(f"{cur}/{total}")
        return " ".join(f"[{p}]" for p in parts)

    def _bpm(self, ch):
        return int(3600 * ch["bpm_step"] / (BASE_TICK * 256))

    def draw(self):
        W = 115
        elapsed = time.time() - getattr(self, "start_time", time.time())
        m, s = divmod(elapsed, 60)
        
        # Beat blink logic (using Channel A for global visual sync)
        beat_active = (self.channels[0]["loop_ticks"] // BASE_TICK) % 2 == 0
        bpm_style = "\033[1;32;5m" if beat_active else "\033[1;32m"

        sys.stdout.write("\033[H")
        sys.stdout.write(
            f"\033[1;44;37m MusaX Simulator v1.9 \033[0m"
            f" 60Hz | T:{self.total_ticks:>7} | {int(m)}:{s:04.1f} | SFX:{self.sfx_mask:03b} | \033[1;33mP:{self.current_fx_priority:>2}\033[0m | Loops:{self.global_loops} {'\033[1;31m[PAUSED]\033[0m' if self.paused else ''}\r\n"
        )
        sys.stdout.write("\033[94m━\033[0m" * W + "\r\n")

        # Column layout (visible chars, SEP=2):
        # [11] CH+STATE  [4] I:N  [5] NOTE  [6] WAIT  [17] VU  [6] FADE  [5] BPM  [5] SLD  [9] ADSR  [10] LABEL  [12] LOOPS  [11] PC+FRAC  [11] HEX
        sys.stdout.write("\033[1m  CH  STATE  I   NOTE WAIT  VOL/ENV          FADE  BPM  SLD  ADSR     LABEL     LOOPS       PC  FRAC  HEX\033[0m\r\n")
        sys.stdout.write("  " + "\033[90m─\033[0m" * (W-2) + "\r\n")

        for i in range(MAX_CHANNELS):
            for ch_idx in [i, i+3]:
                ch = self.channels[ch_idx]
                is_fx = ch_idx >= 3
                audible = is_fx if ch["active"] else (ch["active"] and not self.channels[i+3]["active"])
                
                # Highlight if this channel was the last one to process an event
                if self.paused and ch_idx == self.last_event_ch:
                    audible_marker = "\033[1;33m▶\033[0m"
                    ch_name = f"\033[1;33m{'FX' if is_fx else 'MU'}{chr(65 + i)}\033[0m"
                else:
                    audible_marker = "\033[1;33m▶\033[0m" if audible else " "
                    ch_name = f"\033[1m{'FX' if is_fx else 'MU'}{chr(65 + i)}\033[0m"
                
                if ch["muted"]:
                    status_color = "\033[1;31m"
                    status_text = "MUT"
                else:
                    status_color = "\033[1;32m" if ch["active"] else "\033[2;90m"
                    status_text = "ON " if ch["active"] else "OFF"
                
                note_color = "\033[1;37m" if audible else "\033[90m"
                note     = f"{note_color}{ch['note_name']:3}\033[0m" if ch["active"] else "\033[90m---\033[0m"
                wait_str = f"{ch['wait']:4}" if ch["active"] else "    "
                
                # Visual volume bar
                if audible:
                    v = ch["cur_vol"]
                    bar = "\033[1;32m" + "█" * min(v, 8) + "\033[1;33m" + "█" * max(0, min(v-8, 4)) + "\033[1;31m" + "█" * max(0, v-12) + "\033[0m"
                    bar = self._pad(bar, 15)
                else:
                    bar = "\033[90m" + "▒" * ch["cur_vol"] + " " * (15 - ch["cur_vol"]) + "\033[0m"
                
                pc       = f"{ch['pc']:03X}"
                frac     = f"\033[90m.{int(ch['accumulator']):03}\033[0m"
                hex_snip = "\033[90m" + " ".join(
                    f"{ch['stream'][ch['pc']+j]:02X}" if ch['pc'] + j < len(ch['stream']) else "--"
                    for j in range(4)
                ) + "\033[0m"

                if ch["active"]:
                    bpm_n     = f"{self._bpm(ch):>3}"
                    fade_n    = int(ch["fade_vol"] * 100 / 255)
                    fade_str  = f"{fade_n:>3}%"
                    porta_str = f"{ch['porta_speed']:>3}" if ch['porta_speed'] > 0 else "   "
                    label     = f"\033[36m{self._current_label(ch)[:8]:8}\033[0m"
                    linfo     = f"\033[35m{self._loop_info(ch)[:10]:10}\033[0m"
                    inst_str  = f"\033[33m{ch['inst']:>2}\033[0m"
                else:
                    bpm_n = "   "; fade_str = "    "; porta_str = "   "
                    label = " " * 8; linfo = " " * 10; inst_str = "  "

                adsr_info = f"{['---', 'ATT', 'DEC', 'SUS', 'REL'][ch['adsr_state']]} {int(ch['adsr_acc']):>3}"

                # Uniform 2-space separator between every column.
                # Visible widths: [11] state [4] inst [5] note [6] wait [17] vu [6] fade [5] bpm [5] sld [9] adsr [10] label [12] loops [11] pc+frac [11] hex
                row  = f"{audible_marker} {ch_name} [{status_color}{status_text}\033[0m]"  # 11
                row += f"  {inst_str}"                        # 2+2  = 4
                row += f"  {note}"                            # 2+3  = 5
                row += f"  {wait_str}"                        # 2+4  = 6
                row += f"  {self._pad(bar, 15)}"              # 2+15 = 17
                row += f"  {fade_str}"                        # 2+4  = 6
                row += f"  {bpm_n}"                           # 2+3  = 5
                row += f"  {porta_str}"                       # 2+3  = 5
                row += f"  {adsr_info}"                       # 2+7  = 9
                row += f"  {self._pad(label, 8)}"             # 2+8  = 10
                row += f"  {self._pad(linfo, 10)}"            # 2+10 = 12
                row += f"  {pc} {frac}"                       # 2+3+1+4 = 10
                row += f"  {hex_snip}\r\n"                    # 2+11 = 13
                sys.stdout.write(row)

            if i < 2: sys.stdout.write("  " + "\033[2;90m┄\033[0m" * (W-4) + "\r\n")

        sys.stdout.write("\033[94m━\033[0m" * W + "\r\n")
        
        # Enhanced FX Library View
        sys.stdout.write(" \033[1;37;44m FX LIBRARY \033[0m\r\n")
        
        for i in range(0, len(self.fx_library), 3):
            fx_line = " "
            for j in range(3):
                idx = i + j
                if idx >= len(self.fx_library):
                    break
                
                fx = self.fx_library[idx]
                active_fx = (self.current_fx_priority == fx['priority'] and any(self.channels[k+3]["active"] for k in range(3)))

                color = "\033[1;33m" if active_fx else "\033[90m"
                sel_mark = "\033[1;33m→\033[0m" if active_fx else " "

                # Channel info string [ABC]
                ch_info = "".join(f"\033[1;37m{chr(65+k)}\033[0m" if fx['data'][k]['ptr'] > 0 else "\033[90m-\033[0m" for k in range(3))                
                # Construct entry
                entry = f"{sel_mark}\033[1m[{idx+1:>2}]\033[0m {color}{fx['name'][:12]:12}\033[0m \033[2m(P:{fx['priority']:2})\033[0m [{ch_info}]"
                fx_line += self._pad(entry, 34)
            
            sys.stdout.write(fx_line + "\r\n")
        
        sys.stdout.write("\033[94m━\033[0m" * W + "\r\n")
        sys.stdout.write(" \033[1m[1-9]\033[0m Trigger FX | \033[1m[SPACE]\033[0m Reset | \033[1m[p]\033[0m Pause | \033[1m[n]\033[0m Next | \033[1m[b]\033[0m Back | \033[1m[q/Esc]\033[0m Quit\r\n")
        sys.stdout.write(" \033[1m[a/s/d]\033[0m Mute MU A/B/C | \033[1m[f/g/h]\033[0m Mute FX A/B/C\r\n")
        sys.stdout.flush()

    def render_audio(self, loops=0, duration_limit=60):
        if loops > 0:
            print(f"[*] Rendering {loops} loop(s)...")
        else:
            print(f"[*] Rendering ({duration_limit}s limit)...")

        self.total_ticks = 0
        self._reset_channels()
        self.silent = True # Don't draw while rendering
        self.start_time = time.time()

        all_samples = []
        max_samples = int(SAMPLE_RATE * duration_limit)

        while True:
            # Check for completion
            if not any(ch["active"] for ch in self.channels):
                print(f"[DEBUG] No active channels. Active mask: {self.active_mask}")
                break
            
            if loops > 0 and self.global_loops >= loops:
                print(f"[DEBUG] Global loops reached: {self.global_loops}")
                break
            
            if len(all_samples) >= max_samples:
                break

            # Generate one frame (1/60th sec) of samples
            frame_samples = self.generate_frame_samples()
            if not frame_samples: # Should not happen unless error
                break
                
            all_samples.extend(frame_samples)

        return all_samples

    def _write_wav(self, path, samples):
        with wave.open(path, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(SAMPLE_RATE)
            wav.writeframesraw(struct.pack(f'<{len(samples)}h', *samples))

    def export(self, output_path, time_limit=30, loops=0):
        samples = self.render_audio(loops=loops, duration_limit=time_limit)
        duration = len(samples) / SAMPLE_RATE

        if output_path.lower().endswith('.mp3'):
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                wav_path = tmp.name
            try:
                self._write_wav(wav_path, samples)
                result = subprocess.run(['lame', '-q', '2', wav_path, output_path],
                                        capture_output=True)
                if result.returncode != 0:
                    print("[!] lame failed or not found. Install with: brew install lame")
                    return
            finally:
                if os.path.exists(wav_path): os.unlink(wav_path)
        else:
            self._write_wav(output_path, samples)

        print(f"[*] Exported {duration:.1f}s -> {output_path}")

    def generate_frame_samples(self):
        """Generates samples for exactly one interrupt frame with sub-frame precision"""
        was_debug = self.debug_step
        self.update() # Update envelopes/status once per frame
        frame_samples = []
        
        # Distribute bpm_step across samples for high-precision timing
        for s in range(SAMPLES_PER_INT):
            event_this_sample = False
            for i in range(MAX_STREAMS):
                ch = self.channels[i]
                # Increment proportionally (bpm_step is per 1/60th sec)
                ch["accumulator"] += ch["bpm_step"] / SAMPLES_PER_INT
                while ch["accumulator"] >= 256:
                    ch["accumulator"] -= 256
                    if ch["active"]:
                        if ch["wait"] > 0: ch["wait"] -= 1
                        ch["loop_ticks"] += 1
                    
                    # Before processing an event, save a snapshot if we haven't this sample
                    if ch["active"] and ch["wait"] <= 0:
                        if not event_this_sample:
                            self.push_history()
                            event_this_sample = True
                        self.process_events(i)
            
            # Mix current sample
            mixed = 0
            for i in range(MAX_CHANNELS):
                fx_ch = self.channels[i + 3]
                music_ch = self.channels[i]
                src = fx_ch if fx_ch["active"] else music_ch
                
                if src["active"] and src["note_val"] != 255 and src["freq"] > 0:
                    amp = int(_AY_VOL[min(15, src["cur_vol"])] * CH_AMP_MAX)

                    # Apply GATE silencing (Trigger Release)
                    if src["gate"] < 255 and src["total_wait"] > 0:
                        played_ticks = src["total_wait"] - src["wait"]
                        if played_ticks * 256 >= src["total_wait"] * src["gate"]:
                            if not src.get("gated_logged", False):
                                if self.log_file:
                                    self.log_file.write(f"T:{self.total_ticks} | CH:{i} | GATED (gate:{src['gate']}) -> RELEASE\n")
                                    self.log_file.flush()
                                src["gated_logged"] = True
                            # Trigger Release phase
                            if src["adsr_state"] != 4 and src["adsr_state"] != 0:
                                src["adsr_state"] = 4

                    freq = src.get("cur_freq_mod", src["freq"])
                    if freq <= 0: continue
                    
                    # High-fidelity synthesis using floating phase
                    period_s = SAMPLE_RATE / freq
                    self.osc_phase[i] += 1.0
                    if self.osc_phase[i] >= period_s:
                        self.osc_phase[i] -= period_s
                    
                    tone_out = 1 if self.osc_phase[i] < period_s / 2 else 0
                    mixed += amp if tone_out else -amp

                # Dynamic mask update for dashboard
                if fx_ch["active"]: self.sfx_mask |= (1 << i)
                else: self.sfx_mask &= ~(1 << i)

            frame_samples.append(max(-32768, min(32767, int(mixed))))
            
            # If we were in debug_step mode and an event just cleared it, stop here
            if was_debug and self.debug_step == False:
                break
            
        return frame_samples

    def render_static_samples(self, num_samples):
        """Generates audio samples based on CURRENT state without advancing time/logic"""
        samples = []
        for _ in range(num_samples):
            mixed = 0
            for i in range(MAX_CHANNELS):
                fx_ch = self.channels[i + 3]
                music_ch = self.channels[i]
                src = fx_ch if fx_ch["active"] else music_ch
                
                if src["active"] and src["note_val"] != 255 and src["freq"] > 0:
                    amp = int(_AY_VOL[min(15, src["cur_vol"])] * CH_AMP_MAX)
                    freq = src.get("cur_freq_mod", src["freq"])
                    if freq <= 0: continue
                    period_s = SAMPLE_RATE / freq
                    self.osc_phase[i] += 1.0
                    if self.osc_phase[i] >= period_s:
                        self.osc_phase[i] -= period_s
                    tone_out = 1 if self.osc_phase[i] < period_s / 2 else 0
                    mixed += amp if tone_out else -amp
            samples.append(max(-32768, min(32767, int(mixed))))
        return samples

    def save_state(self):
        """Returns a deep copy of the engine state"""
        import copy
        state = {
            "total_ticks": self.total_ticks,
            "sfx_mask": self.sfx_mask,
            "current_fx_priority": self.current_fx_priority,
            "global_loops": self.global_loops,
            "active_mask": self.active_mask,
            "finished_mask": self.finished_mask,
            "last_event_ch": self.last_event_ch,
            "channels": copy.deepcopy(self.channels),
            "osc_phase": copy.deepcopy(self.osc_phase),
            "start_time_offset": time.time() - getattr(self, "start_time", time.time())
        }
        return state

    def load_state(self, state):
        """Restores the engine state from a snapshot"""
        self.total_ticks = state["total_ticks"]
        self.sfx_mask = state["sfx_mask"]
        self.current_fx_priority = state["current_fx_priority"]
        self.global_loops = state["global_loops"]
        self.active_mask = state["active_mask"]
        self.finished_mask = state["finished_mask"]
        self.last_event_ch = state["last_event_ch"]
        self.channels = state["channels"]
        self.osc_phase = state["osc_phase"]
        self.start_time = time.time() - state["start_time_offset"]

    def push_history(self):
        """Saves current state to history buffer"""
        self.history.append(self.save_state())
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def run(self, loops=0):
        if not HAS_PYAUDIO and not HAS_SOUNDDEVICE:
            print("[!] Error: No real-time audio library found (pyaudio or sounddevice).")
            print("[*] Falling back to pre-rendered mode via afplay/aplay...")
            # Fallback to old behavior if no libs available
            samples = self.render_audio(loops=loops)
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                temp_name = tmp.name
            try:
                self._write_wav(temp_name, samples)
                cmd = ['afplay', temp_name] if sys.platform == 'darwin' else ['aplay', '-q', '-c', '1', temp_name]
                play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._reset_channels()
                self.start_time = time.time()
                while play_proc.poll() is None:
                    time.sleep(0.1)
            finally:
                if os.path.exists(temp_name): os.remove(temp_name)
            return

        self._reset_channels()
        os.system('clear')
        self.start_time = time.time()
        frames = 0
        
        # Audio stream setup (PyAudio preferred)
        pa = None
        stream = None
        if HAS_PYAUDIO:
            with suppress_stderr():
                pa = pyaudio.PyAudio()
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, output=True, frames_per_buffer=SAMPLES_PER_INT)
        elif HAS_SOUNDDEVICE:
            # sounddevice usage would go here if needed
            pass

        # Setup non-blocking keyboard input
        fd = sys.stdin.fileno()
        is_tty = os.isatty(fd)
        if is_tty:
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        
        try:
            while True:
                # Check for input
                if is_tty:
                    while select.select([fd], [], [], 0)[0]:
                        key = os.read(fd, 1).decode(errors='ignore')
                        if key in ['q', 'Q', '\x1b']: # Quit
                            return
                        elif key == ' ': # SPACE: Retrigger
                            self._reset_channels()
                            self.total_ticks = 0
                            self.start_time = time.time()
                            self.history = []
                            frames = 0
                        elif key in ['p', 'P']: # Pause
                            self.paused = not self.paused
                            if self.paused: self.draw()
                        elif key in ['n', 'N'] and self.paused: # Next event
                            self.debug_step = True
                        elif key in ['b', 'B'] and self.paused: # Back event
                            if self.history:
                                self.load_state(self.history.pop())
                                # Render static samples to make it audible WITHOUT advancing logic
                                samples = self.render_static_samples(SAMPLES_PER_INT)
                                byte_data = struct.pack(f'<{len(samples)}h', *samples)
                                if HAS_PYAUDIO:
                                    stream.write(byte_data)
                                self.draw()
                        elif '1' <= key <= '9':
                            # Trigger FX from library (1-indexed for user convenience)
                            self.musax_req_fx(int(key) - 1)
                        elif key in ['a', 's', 'd', 'f', 'g', 'h']:
                            mapping = {'a': 0, 's': 1, 'd': 2, 'f': 3, 'g': 4, 'h': 5}
                            idx = mapping[key]
                            self.channels[idx]["muted"] = not self.channels[idx]["muted"]
                            self.draw()

                if loops > 0 and self.global_loops >= loops:
                    break
                
                if not self.paused or self.debug_step:
                    # Generate and play frame (or just samples until next event if debug_step)
                    samples = self.generate_frame_samples()
                    byte_data = struct.pack(f'<{len(samples)}h', *samples)
                    if HAS_PYAUDIO:
                        stream.write(byte_data)
                    
                    if frames % 2 == 0: self.draw()
                    frames += 1
                else:
                    time.sleep(0.01) # Low CPU while paused
                
                # Sync timing (optional for streaming, but good for dashboard)
                # Note: stream.write is usually blocking until buffer is ready
        finally:
            if stream:
                if HAS_PYAUDIO:
                    stream.stop_stream()
                    stream.close()
                    pa.terminate()
            if is_tty:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description='MusaX Simulator/Exporter')
    p.add_argument("file", help="Source .Z8A file (Music)")
    p.add_argument("fx_file", nargs='?', help="Optional secondary .Z8A file (FX)")
    p.add_argument("--export", "-e", metavar="OUTPUT", nargs='?', const='',
                   help="Export to .wav or .mp3; omit filename to use song name")
    p.add_argument("--time", "-t", type=float, default=30,
                   help="Duration in seconds for export (default: 30)")
    p.add_argument("--loops", "-l", type=int, default=0,
                   help="Number of loops for export/play (default: 0 = infinite/limit)")
    p.add_argument("--debug-log", type=str, help="Output file for execution trace")
    args = p.parse_args()

    sim = MusaXSim(debug_log=args.debug_log)
    sim.load_z8a(args.file)
    if args.fx_file:
        sim.load_z8a(args.fx_file, is_fx_only=True)
    
    if args.export is not None:
        if args.export:
            output = args.export
        else:
            output = os.path.splitext(os.path.basename(args.file))[0] + '.wav'
        sim.export(output, time_limit=args.time, loops=args.loops)
    else:
        sim.run(loops=args.loops)
    
    if sim.log_file:
        sim.log_file.close()
        print(f"[*] Trace log saved to {args.debug_log}")
