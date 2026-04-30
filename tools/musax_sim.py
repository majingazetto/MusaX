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
        self.log_file = None
        if debug_log:
            self.log_file = open(debug_log, "w")
            self.log_file.write(f"--- MusaX Trace Log ---\n")

        self.symbols = {
            "REST": 255, "LEN_Q": 768, "LEN_H": 1536, "LEN_E": 384, "LEN_S": 192,
            "LEN_W": 3072, "LEN_ET": 256, "LEN_QT": 512
        }
        self.commands = {
            "CMD_TEMPO": 0xFD, "CMD_VOLUME": 0xFC, "CMD_GATE": 0xFB,
            "CMD_INST": 0xFA, "CMD_LOOP_S": 0xF9, "CMD_LOOP_E": 0xF8,
            "CMD_GOTO": 0xF7, "CMD_RESTART": 0xFE, "CMD_PHASE": 0xF6,
            "CMD_DETUNE": 0xF5, "CMD_CHORUS": 0xF4
        }
        self.init_notes()
        self.symbols.update(self.commands)

        self.channel_labels = {}  # stream_name -> [(offset, label)]

        self.physical_channels = [{"sample_idx": 0} for _ in range(MAX_CHANNELS)]
        self.channels = []
        for i in range(MAX_STREAMS):
            is_fx = i >= 3
            self.channels.append({
                "active": False, "note_val": 255, "freq": 0.0,
                "vol": 15, "cur_vol": 0, "inst": 0, "inst_pc": 0,
                "stream": [], "stream_base": 0, "stream_name": "",
                "pc": 0, "wait": 0, "sample_idx": 0,
                "note_name": "---", "loop_count": 0, "loop_ticks": 0,
                "loop_stack": [],
                "bpm_step": 0x2400 if is_fx else 0x0600, # FX default to 168 BPM
                "accumulator": 0, "detune": 0
            })

        self.instruments = {
            0: [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
            1: [15, 15, 14, 14, 13, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
        }

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

    def eval_expr(self, expr):
        try:
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

    def load_z8a(self, filename, is_fx_only=False):
        if not os.path.exists(filename): print(f"Error: {filename} not found"); sys.exit(1)
        
        base_path = os.path.dirname(filename)
        lines = self.read_with_includes(os.path.basename(filename), base_path)
        
        # Pass 1: Gather EQU symbols
        for _ in range(5):
            for line in lines:
                m = re.match(r"^(\w+)\s+EQU\s+(.+)$", line)
                if m: self.symbols[m.group(1)] = self.eval_expr(m.group(2))

        # Pass 2: Map global and local labels
        global_labels = {} 
        curr_global = None
        # Start base_addr high for FX if merging
        base_addr = 0x8000 if is_fx_only else 0x1000
        current_offset = 0
        
        # Pre-scan for stream sizes to avoid overlaps
        stream_sizes = {}
        scan_global = None
        scan_offset = 0
        for line in lines:
            m = re.match(r"^([\w.]+):$", line)
            if m:
                label = m.group(1)
                if not label.startswith("."):
                    if scan_global: stream_sizes[scan_global] = scan_offset
                    scan_global = label
                    scan_offset = 0
                continue
            if scan_global and (line.startswith("DEFB") or line.startswith("DEFW")):
                parts = [p.strip() for p in re.split(r",", line[4:].strip())]
                scan_offset += len(parts) * (2 if line.startswith("DEFW") else 1)
        if scan_global: stream_sizes[scan_global] = scan_offset

        local_stream_bases = {}
        for line in lines:
            m = re.match(r"^([\w.]+):$", line)
            if m:
                label = m.group(1)
                if label.startswith("."):
                    if curr_global:
                        full_name = curr_global + label
                        self.symbols[full_name] = local_stream_bases[curr_global] + current_offset
                        self.channel_labels.setdefault(curr_global, []).append((current_offset, label))
                else:
                    if curr_global:
                        base_addr += stream_sizes[curr_global] + 16 # Add small padding
                    curr_global = label
                    local_stream_bases[curr_global] = base_addr
                    self.symbols[curr_global] = base_addr
                    global_labels[curr_global] = []
                    current_offset = 0
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
        
        # Merge local stream_bases into the global one
        for name, addr in local_stream_bases.items():
            self.stream_bases[name] = addr

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

        if not is_fx_only:
            # Pass 4: Assign Music Streams
            music_hdr_name = next((k for k in self.all_streams if "HDR" in k and "FX" not in k), None)
            if music_hdr_name and len(self.all_streams[music_hdr_name]) >= 12:
                hdr_bytes = self.all_streams[music_hdr_name]
                for i in range(3):
                    # Each channel has [BPM (2b), PTR (2b)]
                    bpm = hdr_bytes[i*4] + (hdr_bytes[i*4 + 1] << 8)
                    ptr = hdr_bytes[i*4 + 2] + (hdr_bytes[i*4 + 3] << 8)
                    
                    ch = self.channels[i]
                    ch["bpm_step"] = bpm
                    if ptr != 0:
                        s_name = next((name for name, addr in self.stream_bases.items() if addr == ptr), None)
                        if s_name:
                            ch["stream"] = self.all_streams[s_name]
                            ch["stream_base"] = self.stream_bases[s_name]
                            ch["stream_name"] = s_name
                            ch["active"] = True
            
        # Pass 5: FX Logic (either from main file or dedicated FX file)
        # Structure: DEFW PTR_FXHDR, PRIORITY
        fx_table_name = next((k for k in global_labels if "FX_TABLE" in k), None)
        if fx_table_name:
            table_bytes = self.all_streams[fx_table_name]
            # Clear library if it's a dedicated FX file to avoid duplicates or keep both? 
            # Let's keep both for now, but usually it's one or the other.
            for i in range(0, len(table_bytes), 4):
                if i + 3 >= len(table_bytes): break
                ptr = table_bytes[i] + (table_bytes[i+1] << 8)
                if ptr == 0: break
                priority = table_bytes[i+2] + (table_bytes[i+3] << 8)
                
                hdr_name = next((name for name, addr in self.stream_bases.items() if addr == ptr), None)
                if hdr_name and hdr_name in self.all_streams:
                    hdr_bytes = self.all_streams[hdr_name]
                    fx_data = []
                    for j in range(3):
                        if j*4 + 3 < len(hdr_bytes):
                            bpm = hdr_bytes[j*4] + (hdr_bytes[j*4+1] << 8)
                            ptr_fx = hdr_bytes[j*4+2] + (hdr_bytes[j*4+3] << 8)
                            fx_data.append({"bpm": bpm, "ptr": ptr_fx})
                        else:
                            # Fallback for old 6-byte headers if encountered (though we should update all)
                            if j*2 + 1 < len(hdr_bytes):
                                ptr_fx = hdr_bytes[j*2] + (hdr_bytes[j*2+1] << 8)
                                fx_data.append({"bpm": 0x2400, "ptr": ptr_fx})
                            else:
                                fx_data.append({"bpm": 0x2400, "ptr": 0})
                    self.fx_library.append({"data": fx_data, "priority": priority, "name": hdr_name})

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
        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | REQ_FX START: {req['name']} (P:{self.current_fx_priority})\n")
        
        for i in range(3):
            fx_item = req["data"][i]
            ptr = fx_item["ptr"]
            bpm = fx_item["bpm"]
            ch = self.channels[i+3]
            
            if ptr == 0:
                ch["active"] = False
            else:
                # Find the global stream that contains this pointer
                best_match = None
                best_base = -1
                for name, base in self.stream_bases.items():
                    if name in self.all_streams:
                        stream_len = len(self.all_streams[name])
                        if base <= ptr < base + stream_len:
                            if base > best_base:
                                best_match = name
                                best_base = base
                
                if best_match:
                    ch["stream"] = self.all_streams[best_match]
                    ch["stream_base"] = best_base
                    ch["stream_name"] = best_match
                    ch["pc"] = ptr - best_base
                    ch["wait"] = 0
                    ch["loop_ticks"] = 0
                    ch["loop_stack"] = []
                    ch["bpm_step"] = bpm # Use BPM from header
                    ch["active"] = True
                else:
                    ch["active"] = False

    def _reset_channels(self):
        self.active_mask = 0
        self.finished_mask = 0
        self.global_loops = 0
        self.sfx_mask = 0
        self.current_fx_priority = 0
        for i, ch in enumerate(self.channels):
            ch["pc"] = 0; ch["wait"] = 0; ch["loop_count"] = 0; ch["loop_stack"] = []; ch["loop_ticks"] = 0
            ch["accumulator"] = 0
            if i >= 3: ch["bpm_step"] = 0x2400 # FX default
            if ch["stream"]:
                ch["active"] = True
                if i < 3: self.active_mask |= (1 << i)
            else:
                ch["active"] = False

    def process_events(self, ch_idx):
        ch = self.channels[ch_idx]
        if not ch["active"]: return
        safety_counter = 0
        while ch["active"] and ch["wait"] <= 0:
            safety_counter += 1
            if safety_counter > 2000:
                print(f"Error: Infinite loop detected in CH:{ch_idx} (PC:{ch['pc']:03X}). Check wait times.")
                ch["active"] = False
                break
            
            if ch["pc"] >= len(ch["stream"]): 
                ch["active"] = False
                if ch_idx < 3: self.finished_mask |= (1 << ch_idx)
                if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{ch['pc']:03X} | END OF STREAM\n")
                continue
            
            old_pc = ch["pc"]
            cmd = ch["stream"][ch["pc"]]; ch["pc"] += 1
            
            if cmd == 0xFF:  # REST [Len (DEFW)]
                ch["note_val"] = 255; ch["freq"] = 0.0
                if ch["pc"] + 1 < len(ch["stream"]):
                    wait_val = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                    if wait_val == 0: # Zero wait = STOP
                        ch["active"] = False
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{old_pc:03X} | STOP via REST 0\n")
                        continue
                    else:
                        ch["wait"] = wait_val
                    if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{ch_idx} | PC:{old_pc:03X} | REST len:{ch['wait']}\n")
                else: ch["active"] = False
            elif cmd >= 0xF5:
                if cmd == 0xFC: # VOLUME [Val]
                    ch["vol"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                elif cmd == 0xFA: # INST [ID]
                    ch["inst"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
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
                    ch["pc"] += 1 
                elif cmd == 0xF7: # GOTO [Addr (DEFW)]
                    if ch["pc"] + 1 < len(ch["stream"]):
                        addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                        # Find the global stream that contains this address
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
                            ch["pc"] = 0
                    else:
                        ch["pc"] = 0
                elif cmd == 0xFE: # RESTART [Addr (DEFW)]
                    if ch_idx < 3: 
                        self.finished_mask |= (1 << ch_idx)
                        ch["loop_ticks"] = 0
                        if ch["pc"] + 1 < len(ch["stream"]):
                            addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                            # Find the global stream that contains this address
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
                                ch["pc"] = 0
                        else:
                            ch["pc"] = 0
                    else: # FX should NOT restart unless explicitly coded. RESTART in FX = STOP
                        ch["active"] = False
                    
                    if ch_idx < 3 and (self.finished_mask & self.active_mask) == self.active_mask:
                        self.global_loops += 1
                        self.finished_mask = 0
                elif cmd == 0xF6: # PHASE [Val]
                    val = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    ch["accumulator"] = (ch["accumulator"] + val) & 0xFFFF
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
                    ch["accumulator"] = (ch["accumulator"] + phase) & 0xFFFF
                    ch["detune"] = detune
                    if ch["note_val"] != 255:
                        ch["freq"] = self.note_to_freq(ch["note_val"], ch["detune"])
            else:
                ch["note_val"] = cmd; ch["freq"] = self.note_to_freq(cmd, ch["detune"]); ch["inst_pc"] = 0; ch["sample_idx"] = 0
                notes = ["C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-"]
                ch["note_name"] = f"{notes[cmd % 12]}{cmd // 12}"
                if ch["pc"] + 1 < len(ch["stream"]):
                    ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                else: ch["active"] = False

    def update(self):
        # Global frame counter
        self.total_ticks += 1 
        
        for i, ch in enumerate(self.channels):
            ch["accumulator"] += ch["bpm_step"]
            while ch["accumulator"] >= 256:
                ch["accumulator"] -= 256
                if ch["active"]:
                    if ch["wait"] > 0: ch["wait"] -= 1
                    ch["loop_ticks"] += 1
                self.process_events(i)
        
        self.sfx_mask = 0
        for i in range(MAX_CHANNELS):
            if self.channels[i+3]["active"]:
                self.sfx_mask |= (1 << i)
        
        if self.sfx_mask == 0:
            self.current_fx_priority = 0

        for ch in self.channels:
            if not ch["active"] or ch["note_val"] == 255: ch["cur_vol"] = 0; continue
            env = self.instruments.get(ch["inst"], [15])
            ch["cur_vol"] = (env[min(ch["inst_pc"], len(env) - 1)] * ch["vol"]) // 15; ch["inst_pc"] += 1

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
        W = 105
        elapsed = time.time() - getattr(self, "start_time", time.time())
        m, s = divmod(elapsed, 60)
        
        # Beat blink logic (using Channel A for global visual sync)
        beat_active = (self.channels[0]["loop_ticks"] // BASE_TICK) % 2 == 0
        bpm_style = "\033[1;32;5m" if beat_active else "\033[1;32m"

        sys.stdout.write("\033[H")
        sys.stdout.write(
            f"\033[1;44;37m MusaX Simulator v1.5 \033[0m"
            f" 60Hz | T:{self.total_ticks:>7} | {int(m)}:{s:04.1f} | SFX:{self.sfx_mask:03b} | \033[1;33mP:{self.current_fx_priority:>2}\033[0m | Loops:{self.global_loops}\r\n"
        )
        sys.stdout.write("\033[94m━\033[0m" * W + "\r\n")

        # Table Header - Precisely aligned
        # Indices: CH:2, STATE:6, NOTE:14, WAIT:20, VOLUME:27, BEAT:51, LABEL:58, LOOPS:67, PC:81
        sys.stdout.write("\033[1m  CH  STATE   NOTE  WAIT   VOLUME / ENVELOPE       BPM   LABEL     LOOPS          PC   HEX SNIP\033[0m\r\n")
        sys.stdout.write("  " + "\033[90m─\033[0m" * (W-2) + "\r\n")

        for i in range(MAX_CHANNELS):
            for ch_idx in [i, i+3]:
                ch = self.channels[ch_idx]
                is_fx = ch_idx >= 3
                audible = is_fx if ch["active"] else (ch["active"] and not self.channels[i+3]["active"])
                
                audible_marker = "\033[1;33m▶\033[0m" if audible else " "
                ch_name = f"\033[1m{'FX' if is_fx else 'MU'}{chr(65 + i)}\033[0m"
                
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
                hex_snip = "\033[90m" + " ".join(
                    f"{ch['stream'][ch['pc']+j]:02X}" if ch['pc'] + j < len(ch['stream']) else "--"
                    for j in range(4)
                ) + "\033[0m"

                if ch["active"]:
                    bpm_n  = f"{self._bpm(ch):>3}"
                    label  = f"\033[36m{self._current_label(ch)[:8]:8}\033[0m"
                    linfo  = f"\033[35m{self._loop_info(ch)[:12]:12}\033[0m"
                else:
                    bpm_n = "   "; label = " " * 8; linfo = " " * 12

                # Row construction with precise visible padding
                row = f"{audible_marker} {ch_name} [{status_color}{status_text}\033[0m]   "
                row += f"{note}   "
                row += f"{wait_str}   "
                row += f"{self._pad(bar, 24)}"
                row += f"{bpm_n}    "
                row += f"{self._pad(label, 9)}"
                row += f"{self._pad(linfo, 14)}"
                row += f"{pc}  "
                row += f"{hex_snip}\r\n"
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
        sys.stdout.write(" \033[1m[1-9]\033[0m Trigger FX | \033[1m[SPACE]\033[0m Reset | \033[1m[q/Esc]\033[0m Quit\r\n")
        sys.stdout.flush()

    def render_audio(self, loops=0, duration_limit=60):
        if loops > 0:
            print(f"[*] Rendering {loops} loop(s)...")
        else:
            print(f"[*] Rendering ({duration_limit}s limit)...")

        self.total_ticks = 0
        self.accumulator = 0
        self._reset_channels()

        all_samples = []
        max_samples = int(SAMPLE_RATE * duration_limit)

        while True:
            if not any(ch["active"] for ch in self.channels) and loops == 0:
                break

            self.update()

            for _ in range(SAMPLES_PER_INT):
                mixed = 0
                for i in range(MAX_CHANNELS):
                    fx_ch = self.channels[i + 3]
                    music_ch = self.channels[i]
                    src = fx_ch if fx_ch["active"] else music_ch
                    
                    if src["active"] and src["note_val"] != 255 and src["freq"] > 0:
                        period = SAMPLE_RATE / src["freq"]
                        p_ch = self.physical_channels[i]
                        p_ch["sample_idx"] += 1
                        amp = src["cur_vol"] * 1000
                        mixed += amp if (p_ch["sample_idx"] % period) < (period / 2) else -amp
                all_samples.append(max(-32768, min(32767, int(mixed))))

            if loops > 0 and self.global_loops >= loops:
                break
            if loops == 0 and len(all_samples) >= max_samples:
                break

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
        """Generates samples for exactly one interrupt frame (1/60th sec)"""
        self.update()
        samples = []
        for _ in range(SAMPLES_PER_INT):
            mixed = 0
            for i in range(MAX_CHANNELS):
                # Channel i (0=A, 1=B, 2=C)
                # Check if FX is active (stream i + 3)
                fx_ch = self.channels[i + 3]
                music_ch = self.channels[i]
                
                # Priority: FX > Music (Winner takes all)
                src = fx_ch if fx_ch["active"] else music_ch
                
                if src["active"] and src["note_val"] != 255 and src["freq"] > 0:
                    period = SAMPLE_RATE / src["freq"]
                    # Use physical channel sample_idx for phase continuity
                    p_ch = self.physical_channels[i]
                    p_ch["sample_idx"] += 1
                    amp = src["cur_vol"] * 1000
                    mixed += amp if (p_ch["sample_idx"] % period) < (period / 2) else -amp
                
                # Update SFXMSK bit for dashboard/logic
                if fx_ch["active"]:
                    self.sfx_mask |= (1 << i)
                else:
                    self.sfx_mask &= ~(1 << i)

            samples.append(max(-32768, min(32767, int(mixed))))
        return samples

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
                            self.accumulator = 0
                            self.start_time = time.time()
                            frames = 0
                        elif '1' <= key <= '9':
                            # Trigger FX from library (1-indexed for user convenience)
                            self.musax_req_fx(int(key) - 1)

                if loops > 0 and self.global_loops >= loops:
                    break
                
                # Generate and play frame
                samples = self.generate_frame_samples()
                byte_data = struct.pack(f'<{len(samples)}h', *samples)
                if HAS_PYAUDIO:
                    stream.write(byte_data)
                
                if frames % 2 == 0: self.draw()
                frames += 1
                
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
