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

# --- MUSAX CONSTANTS ---
BASE_TICK = 256
MAX_CHANNELS = 3
SAMPLE_RATE = 44100
INTERRUPT_FREQ = 60
SAMPLES_PER_INT = SAMPLE_RATE // INTERRUPT_FREQ


class MusaXSim:
    def __init__(self, filename=None, silent=False):
        self.bpm_step = 0x0200
        self.accumulator = 0
        self.silent = silent
        self.total_ticks = 0
        self.playing = False
        self.global_loops = 0
        self.active_mask = 0
        self.finished_mask = 0

        self.symbols = {
            "REST": 255, "LEN_Q": 256, "LEN_H": 512, "LEN_E": 128, "LEN_S": 64,
            "LEN_W": 1024, "LEN_ET": 85, "LEN_QT": 170
        }
        self.commands = {
            "CMD_TEMPO": 0xFD, "CMD_VOLUME": 0xFC, "CMD_GATE": 0xFB,
            "CMD_INST": 0xFA, "CMD_LOOP_S": 0xF9, "CMD_LOOP_E": 0xF8,
            "CMD_GOTO": 0xF7, "CMD_RESTART": 0xFE
        }
        self.init_notes()
        self.symbols.update(self.commands)

        self.channels = []
        for _ in range(MAX_CHANNELS):
            self.channels.append({
                "active": False, "note_val": 255, "freq": 0.0,
                "vol": 15, "cur_vol": 0, "inst": 0, "inst_pc": 0,
                "stream": [], "stream_base": 0, "pc": 0, "wait": 0, "sample_idx": 0,
                "note_name": "---", "loop_count": 0,
                "loop_stack": [] # Stores (return_pc, count)
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
            if not re.match(r"^[0-9a-fA-Fx+\-*/%() \t.]+$", clean_expr): return 0
            return int(eval(expr))
        except Exception: return 0

    def load_z8a(self, filename):
        if not os.path.exists(filename): print(f"Error: {filename} not found"); sys.exit(1)
        
        def read_with_includes(fname, base_path):
            full_path = os.path.join(base_path, fname)
            if not os.path.exists(full_path):
                full_path = fname
                if not os.path.exists(full_path): return []
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
                self.channels[i]["active"] = True

        hdr = next((k for k in final_streams if "HDR" in k), None)
        if hdr and len(final_streams[hdr]) >= 2: 
            self.bpm_step = final_streams[hdr][0] + (final_streams[hdr][1] << 8)

    def _reset_channels(self):
        self.active_mask = 0
        self.finished_mask = 0
        self.global_loops = 0
        for i, ch in enumerate(self.channels):
            ch["pc"] = 0; ch["wait"] = 0; ch["loop_count"] = 0; ch["loop_stack"] = []
            if ch["stream"]:
                ch["active"] = True
                self.active_mask |= (1 << i)
            else:
                ch["active"] = False

    def process_events(self):
        for i in range(MAX_CHANNELS):
            ch = self.channels[i]
            if not ch["active"]: continue
            while ch["active"] and ch["wait"] <= 0:
                if ch["pc"] >= len(ch["stream"]): 
                    ch["active"] = False
                    self.finished_mask |= (1 << i)
                    break
                cmd = ch["stream"][ch["pc"]]; ch["pc"] += 1
                
                if cmd == 0xFF:  # REST [Len (DEFW)]
                    ch["note_val"] = 255; ch["freq"] = 0.0
                    if ch["pc"] + 1 < len(ch["stream"]):
                        ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                    else: ch["active"] = False
                elif cmd >= 0xF7:
                    if cmd == 0xFC: # VOLUME [Val]
                        ch["vol"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    elif cmd == 0xFA: # INST [ID]
                        ch["inst"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    elif cmd == 0xFD: # TEMPO [Val]
                        if ch["pc"] + 1 < len(ch["stream"]):
                            self.bpm_step = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8); ch["pc"] += 2
                    elif cmd == 0xF9: # LOOP_S [Count]
                        count = ch["stream"][ch["pc"]]; ch["pc"] += 1
                        ch["loop_stack"].append({"pc": ch["pc"], "count": count})
                    elif cmd == 0xF8: # LOOP_E
                        if ch["loop_stack"]:
                            ch["loop_stack"][-1]["count"] -= 1
                            if ch["loop_stack"][-1]["count"] > 0:
                                ch["pc"] = ch["loop_stack"][-1]["pc"]
                            else:
                                ch["loop_stack"].pop()
                    elif cmd == 0xF7: # GOTO [Addr (DEFW)]
                        if ch["pc"] + 1 < len(ch["stream"]):
                            addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                            ch["pc"] = addr - ch["stream_base"]
                        else:
                            ch["pc"] = 0
                    elif cmd == 0xFE: # RESTART [Addr (DEFW)]
                        self.finished_mask |= (1 << i)
                        if ch["pc"] + 1 < len(ch["stream"]):
                            addr = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8)
                            ch["pc"] = addr - ch["stream_base"]
                        else:
                            ch["pc"] = 0
                        
                        if (self.finished_mask & self.active_mask) == self.active_mask:
                            self.global_loops += 1
                            self.finished_mask = 0
                else:
                    ch["note_val"] = cmd; ch["freq"] = self.note_to_freq(cmd); ch["inst_pc"] = 0; ch["sample_idx"] = 0
                    notes = ["C-", "Cs", "D-", "Ds", "E-", "F-", "Fs", "G-", "Gs", "A-", "As", "B-"]
                    ch["note_name"] = f"{notes[cmd % 12]}{cmd // 12}"
                    if ch["pc"] + 1 < len(ch["stream"]):
                        ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"] + 1] << 8); ch["pc"] += 2
                    else: ch["active"] = False

    def update(self):
        self.accumulator += self.bpm_step
        while self.accumulator >= BASE_TICK:
            self.accumulator -= BASE_TICK; self.total_ticks += 1
            for ch in self.channels:
                if ch["active"] and ch["wait"] > 0: ch["wait"] -= 1
            self.process_events()
        for ch in self.channels:
            if not ch["active"] or ch["note_val"] == 255: ch["cur_vol"] = 0; continue
            env = self.instruments.get(ch["inst"], [15])
            ch["cur_vol"] = (env[min(ch["inst_pc"], len(env) - 1)] * ch["vol"]) // 15; ch["inst_pc"] += 1

    def draw(self):
        sys.stdout.write("\033[H")
        sys.stdout.write(f"\033[1;36m MusaX Sim v1.0 \033[0m | 60Hz | Ticks:{self.total_ticks} | Loops:{self.global_loops}\r\n")
        sys.stdout.write("-" * 75 + "\r\n")
        for i, ch in enumerate(self.channels):
            status = "\033[32mON \033[0m" if ch["active"] else "\033[31mOFF\033[0m"
            bar = "█" * ch['cur_vol']
            sys.stdout.write(f" CH {chr(65 + i)}: [{status}] | {ch['note_name']:4} | Vol:{ch['vol']:2} | Out:{ch['cur_vol']:2} {bar:15} | PC:{ch['pc']:3}\r\n")
        sys.stdout.write("-" * 75 + "\r\n [q/Esc] Quit\r\n")
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
            if loops > 0:
                if self.global_loops >= loops:
                    break
            else:
                if len(all_samples) >= max_samples:
                    break
                if not any(ch["active"] for ch in self.channels):
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

    def run(self, loops=0):
        # We pre-render the entire requested duration to a temp WAV for perfect playback
        samples = self.render_audio(loops=loops)

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            temp_name = tmp.name
        try:
            self._write_wav(temp_name, samples)
            cmd = ['afplay', temp_name] if sys.platform == 'darwin' else ['aplay', '-q', '-c', '1', temp_name]
            play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.total_ticks = 0
            self.accumulator = 0
            self._reset_channels()

            os.system('clear')
            start_time = time.time()
            frames = 0
            
            # Setup non-blocking keyboard input if in a TTY
            fd = sys.stdin.fileno()
            is_tty = os.isatty(fd)
            if is_tty:
                old_settings = termios.tcgetattr(fd)
                tty.setcbreak(fd)
            
            try:
                while play_proc.poll() is None:
                    # Check for exit keys (q, Q, or Esc)
                    if is_tty:
                        while select.select([fd], [], [], 0)[0]:
                            key = os.read(fd, 1).decode(errors='ignore')
                            if key in ['q', 'Q', '\x1b']:
                                play_proc.terminate()
                                # Drain buffer
                                while select.select([fd], [], [], 0)[0]: os.read(fd, 1)
                                return

                    if loops > 0 and self.global_loops >= loops:
                        play_proc.terminate()
                        break
                    
                    self.update()
                    if frames % 2 == 0: self.draw()
                    frames += 1
                    
                    # Timing sync
                    wait_t = start_time + (frames / INTERRUPT_FREQ) - time.time()
                    if wait_t > 0: time.sleep(wait_t)
            finally:
                if is_tty:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                
        except KeyboardInterrupt:
            if 'play_proc' in locals(): play_proc.terminate()
        finally:
            if os.path.exists(temp_name): os.remove(temp_name)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description='MusaX Simulator/Exporter')
    p.add_argument("file", help="Source .Z8A file")
    p.add_argument("--export", "-e", metavar="OUTPUT", nargs='?', const='',
                   help="Export to .wav or .mp3; omit filename to use song name")
    p.add_argument("--time", "-t", type=float, default=30,
                   help="Duration in seconds for export (default: 30)")
    p.add_argument("--loops", "-l", type=int, default=0,
                   help="Number of loops for export/play (default: 0 = infinite/limit)")
    args = p.parse_args()

    sim = MusaXSim(args.file)
    if args.export is not None:
        if args.export:
            output = args.export
        else:
            output = os.path.splitext(os.path.basename(args.file))[0] + '.wav'
        sim.export(output, time_limit=args.time, loops=args.loops)
    else:
        sim.run(loops=args.loops)
