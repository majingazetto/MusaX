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

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

try:
    import sounddevice as sd
    import numpy as np
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

# --- MUSAX CONSTANTS ---
BASE_TICK = 768
MAX_CHANNELS = 3
SAMPLE_RATE = 44100
INTERRUPT_FREQ = 60
SAMPLES_PER_INT = SAMPLE_RATE // INTERRUPT_FREQ


class MusaXSim:
    def __init__(self, filename=None, silent=False, debug_log=None):
        self.bpm_step = 0x0600
        self.accumulator = 0
        self.silent = silent
        self.total_ticks = 0
        self.playing = False
        self.global_loops = 0
        self.active_mask = 0
        self.finished_mask = 0
        self.log_file = None
        if debug_log:
            self.log_file = open(debug_log, "w")
            self.log_file.write(f"--- MusaX Trace Log: {filename} ---\n")

        self.symbols = {
            "REST": 255, "LEN_Q": 768, "LEN_H": 1536, "LEN_E": 384, "LEN_S": 192,
            "LEN_W": 3072, "LEN_ET": 256, "LEN_QT": 512
        }
        self.commands = {
            "CMD_TEMPO": 0xFD, "CMD_VOLUME": 0xFC, "CMD_GATE": 0xFB,
            "CMD_INST": 0xFA, "CMD_LOOP_S": 0xF9, "CMD_LOOP_E": 0xF8,
            "CMD_GOTO": 0xF7, "CMD_RESTART": 0xFE
        }
        self.init_notes()
        self.symbols.update(self.commands)

        self.channel_labels = {}  # stream_name -> [(offset, label)]

        self.channels = []
        for _ in range(MAX_CHANNELS):
            self.channels.append({
                "active": False, "note_val": 255, "freq": 0.0,
                "vol": 15, "cur_vol": 0, "inst": 0, "inst_pc": 0,
                "stream": [], "stream_base": 0, "stream_name": "",
                "pc": 0, "wait": 0, "sample_idx": 0,
                "note_name": "---", "loop_count": 0, "loop_ticks": 0,
                "loop_stack": []
            })

        self.instruments = {
            0: [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
            1: [15, 15, 14, 14, 13, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
        }

        if filename: self.load_z8a(filename)

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

    def note_to_freq(self, note_val):
        if note_val == 255: return 0.0
        return 440.0 * (2.0 ** (((note_val + 12) - 69.0) / 12.0))

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

    def load_z8a(self, filename):
        if not os.path.exists(filename): print(f"Error: {filename} not found"); sys.exit(1)
        
        def read_with_includes(fname, base_path):
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
                    processed.extend(read_with_includes(inc_file, os.path.dirname(full_path)))
                else:
                    processed.append(line)
            return processed

        lines = read_with_includes(os.path.basename(filename), os.path.dirname(filename))
        
        # Pass 1: Gather EQU symbols
        for _ in range(5):
            for line in lines:
                m = re.match(r"^(\w+)\s+EQU\s+(.+)$", line)
                if m: self.symbols[m.group(1)] = self.eval_expr(m.group(2))

        # Pass 2: Map global and local labels to unique addresses
        global_labels = {} # name -> [(expr, is_word), ...]
        stream_bases = {} # name -> base address
        curr_global = None
        base_addr = 0x1000
        current_offset = 0
        
        for line in lines:
            m = re.match(r"^([\w.]+):$", line)
            if m:
                label = m.group(1)
                if label.startswith("."):
                    if curr_global:
                        self.symbols[curr_global + label] = stream_bases[curr_global] + current_offset
                        self.channel_labels.setdefault(curr_global, []).append((current_offset, label))
                else:
                    curr_global = label
                    stream_bases[curr_global] = base_addr
                    self.symbols[curr_global] = base_addr
                    global_labels[curr_global] = []
                    current_offset = 0
                    base_addr += 0x1000 
                continue
            
            if curr_global and (line.startswith("DEFB") or line.startswith("DEFW")):
                is_word = line.startswith("DEFW")
                parts = [p.strip() for p in re.split(r",", line[4:].strip())]
                for p in parts:
                    # Prefix local labels in data with current global scope
                    if p.startswith("."): p = curr_global + p
                    global_labels[curr_global].append((p, is_word))
                    current_offset += 2 if is_word else 1

        # Pass 3: Resolve all streams into final byte lists
        final_streams = {}
        for s_name, byte_data in global_labels.items():
            bytes_out = []
            for expr, is_word in byte_data:
                val = self.eval_expr(expr)
                if is_word:
                    bytes_out.extend([val & 0xFF, (val >> 8) & 0xFF])
                else:
                    bytes_out.append(val & 0xFF)
            final_streams[s_name] = bytes_out

        # Pass 4: Assign streams to channels
        for i, ch_id in enumerate(["CHA", "CHB", "CHC"]):
            match = next((k for k in final_streams if k.endswith(ch_id) and not k.endswith("LP")), None)
            if not match: match = next((k for k in final_streams if ch_id in k and not k.endswith("LP")), None)
            if match:
                self.channels[i]["stream"] = final_streams[match]
                self.channels[i]["stream_base"] = stream_bases[match]
                self.channels[i]["stream_name"] = match
                self.channels[i]["active"] = True

        hdr = next((k for k in final_streams if "HDR" in k), None)
        if hdr and len(final_streams[hdr]) >= 2: 
            self.bpm_step = final_streams[hdr][0] + (final_streams[hdr][1] << 8)

    def _reset_channels(self):
        self.active_mask = 0
        self.finished_mask = 0
        self.global_loops = 0
        for i, ch in enumerate(self.channels):
            ch["pc"] = 0; ch["wait"] = 0; ch["loop_count"] = 0; ch["loop_stack"] = []; ch["loop_ticks"] = 0
            if ch["stream"]:
                ch["active"] = True
                self.active_mask |= (1 << i)
            else:
                ch["active"] = False

    def process_events(self):
        for i in range(MAX_CHANNELS):
            ch = self.channels[i]
            if not ch["active"]: continue
            safety_counter = 0
            while ch["active"] and ch["wait"] <= 0:
                safety_counter += 1
                if safety_counter > 2000:
                    print(f"Error: Infinite loop detected in CH:{i} (PC:{ch['pc']:03X}). Check wait times.")
                    ch["active"] = False
                    break
                
                if ch["pc"] >= len(ch["stream"]): 
                    ch["active"] = False
                    self.finished_mask |= (1 << i)
                    if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{ch['pc']:03X} | END OF STREAM\n")
                    break
                
                old_pc = ch["pc"]
                cmd = ch["stream"][ch["pc"]]; ch["pc"] += 1
                
                if cmd == 0xFF:  # REST [Len (DEFW)]
                    ch["note_val"] = 255; ch["freq"] = 0.0
                    if ch["pc"] + 1 < len(ch["stream"]):
                        ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | REST len:{ch['wait']}\n")
                    else: ch["active"] = False
                elif cmd >= 0xF7:
                    if cmd == 0xFC: # VOLUME [Val]
                        ch["vol"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | VOLUME val:{ch['vol']}\n")
                    elif cmd == 0xFA: # INST [ID]
                        ch["inst"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | INST id:{ch['inst']}\n")
                    elif cmd == 0xFD: # TEMPO [Val]
                        if ch["pc"] + 1 < len(ch["stream"]):
                            self.bpm_step = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8); ch["pc"] += 2
                            if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | TEMPO step:{self.bpm_step}\n")
                    elif cmd == 0xF9: # LOOP_S [Count]
                        count = ch["stream"][ch["pc"]]; ch["pc"] += 1
                        ch["loop_stack"].append({"pc": ch["pc"], "count": count, "total": count})
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | LOOP_S count:{count}\n")
                    elif cmd == 0xF8: # LOOP_E
                        if ch["loop_stack"]:
                            ch["loop_stack"][-1]["count"] -= 1
                            if ch["loop_stack"][-1]["count"] > 0:
                                ch["pc"] = ch["loop_stack"][-1]["pc"]
                                if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | LOOP_E repeat -> PC:{ch['pc']:03X}\n")
                            else:
                                ch["loop_stack"].pop()
                                if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | LOOP_E finished\n")
                    elif cmd == 0xFB: # GATE [Val]
                        ch["pc"] += 1  # consume parameter, gate not yet implemented
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | GATE (ignored)\n")
                    elif cmd == 0xF7: # GOTO [Addr (DEFW)]
                        if ch["pc"] + 1 < len(ch["stream"]):
                            addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                            ch["pc"] = addr - ch["stream_base"]
                            if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | GOTO -> PC:{ch['pc']:03X}\n")
                        else:
                            ch["pc"] = 0
                    elif cmd == 0xFE: # RESTART [Addr (DEFW)]
                        self.finished_mask |= (1 << i)
                        ch["loop_ticks"] = 0
                        if ch["pc"] + 1 < len(ch["stream"]):
                            addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                            ch["pc"] = addr - ch["stream_base"]
                            if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | RESTART -> PC:{ch['pc']:03X}\n")
                        else:
                            ch["pc"] = 0
                        
                        if (self.finished_mask & self.active_mask) == self.active_mask:
                            self.global_loops += 1
                            self.finished_mask = 0
                            if self.log_file: self.log_file.write(f"T:{self.total_ticks} | --- GLOBAL LOOP {self.global_loops} COMPLETED ---\n")
                else:
                    ch["note_val"] = cmd; ch["freq"] = self.note_to_freq(cmd); ch["inst_pc"] = 0; ch["sample_idx"] = 0
                    notes = ["C-", "Cs", "D-", "Ds", "E-", "F-", "Fs", "G-", "Gs", "A-", "As", "B-"]
                    ch["note_name"] = f"{notes[cmd % 12]}{cmd // 12}"
                    if ch["pc"] + 1 < len(ch["stream"]):
                        ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                        if self.log_file: self.log_file.write(f"T:{self.total_ticks} | CH:{i} | PC:{old_pc:03X} | NOTE {ch['note_name']} wait:{ch['wait']}\n")
                    else: ch["active"] = False

    def update(self):
        self.accumulator += self.bpm_step
        while self.accumulator >= 256:
            self.accumulator -= 256; self.total_ticks += 1
            for ch in self.channels:
                if ch["active"]:
                    if ch["wait"] > 0: ch["wait"] -= 1
                    ch["loop_ticks"] += 1
            self.process_events()
        for ch in self.channels:
            if not ch["active"] or ch["note_val"] == 255: ch["cur_vol"] = 0; continue
            env = self.instruments.get(ch["inst"], [15])
            ch["cur_vol"] = (env[min(ch["inst_pc"], len(env) - 1)] * ch["vol"]) // 15; ch["inst_pc"] += 1

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

    def _bpm(self):
        # Formula: 3600 * bpm_step / (BASE_TICK * 256)
        return int(3600 * self.bpm_step / (BASE_TICK * 256))

    def draw(self):
        W = 96
        elapsed = time.time() - getattr(self, "start_time", time.time())
        m, s = divmod(elapsed, 60)
        bpm = self._bpm()

        sys.stdout.write("\033[H")
        sys.stdout.write(
            f"\033[1;36m MusaX Sim v1.0 \033[0m"
            f" 60Hz | T:{self.total_ticks:>7} | {int(m)}:{s:04.1f} | BPM:{bpm:>3} | Loops:{self.global_loops}\r\n"
        )
        sys.stdout.write("─" * W + "\r\n")

        for i, ch in enumerate(self.channels):
            ch_name  = f"CH{chr(65 + i)}"
            status   = "\033[32mON \033[0m" if ch["active"] else "\033[31mOFF\033[0m"
            note     = ch["note_name"] if ch["active"] else "---"
            wait_str = f"W:{ch['wait']:4}" if ch["active"] else "     "
            bar      = ("█" * ch["cur_vol"]).ljust(13)
            pc       = ch["pc"]
            hex_snip = " ".join(
                f"{ch['stream'][pc+j]:02X}" if pc + j < len(ch["stream"]) else "--"
                for j in range(4)
            )

            if ch["active"]:
                # Bar is BASE_TICK * 4 (a whole note)
                bar_n  = ch["loop_ticks"] // (BASE_TICK * 4) + 1
                label  = self._current_label(ch)[:8].ljust(8)
                linfo  = self._loop_info(ch)[:13].ljust(13)
                extra  = f"B:{bar_n:<3}  {label}  {linfo}"
            else:
                extra  = " " * 28

            sys.stdout.write(
                f" {ch_name} [{status}] {note:3}  {wait_str}  {bar}  {extra}  PC:{pc:03X}  {hex_snip}\r\n"
            )

        sys.stdout.write("─" * W + "\r\n [q/Esc] Quit\r\n")
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
                for ch in self.channels:
                    if not ch["active"] or ch["note_val"] == 255 or ch["freq"] == 0: continue
                    period = SAMPLE_RATE / ch["freq"]
                    ch["sample_idx"] += 1
                    amp = ch["cur_vol"] * 1000
                    mixed += amp if (ch["sample_idx"] % period) < (period / 2) else -amp
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
            for ch in self.channels:
                if not ch["active"] or ch["note_val"] == 255 or ch["freq"] == 0: continue
                period = SAMPLE_RATE / ch["freq"]
                ch["sample_idx"] += 1
                amp = ch["cur_vol"] * 1000
                mixed += amp if (ch["sample_idx"] % period) < (period / 2) else -amp
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
    p.add_argument("file", help="Source .Z8A file")
    p.add_argument("--export", "-e", metavar="OUTPUT", nargs='?', const='',
                   help="Export to .wav or .mp3; omit filename to use song name")
    p.add_argument("--time", "-t", type=float, default=30,
                   help="Duration in seconds for export (default: 30)")
    p.add_argument("--loops", "-l", type=int, default=0,
                   help="Number of loops for export/play (default: 0 = infinite/limit)")
    p.add_argument("--debug-log", type=str, help="Output file for execution trace")
    args = p.parse_args()

    sim = MusaXSim(args.file, debug_log=args.debug_log)
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
