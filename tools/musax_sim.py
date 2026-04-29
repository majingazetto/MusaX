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
        
        self.symbols = {
            "REST": 255, "LEN_Q": 256, "LEN_H": 512, "LEN_E": 128, "LEN_S": 64, 
            "LEN_W": 1024, "LEN_ET": 85, "LEN_QT": 170
        }
        self.commands = {
            "CMD_TEMPO": 0xFD, "CMD_VOLUME": 0xFC, "CMD_GATE": 0xFB, 
            "CMD_INST": 0xFA, "CMD_LOOP_S": 0xF9, "CMD_LOOP_E": 0xF8, 
            "CMD_GOTO": 0xF7
        }
        self.init_notes()
        self.symbols.update(self.commands)

        self.channels = []
        for _ in range(MAX_CHANNELS):
            self.channels.append({
                "active": False, "note_val": 255, "freq": 0.0,
                "vol": 15, "cur_vol": 0, "inst": 0, "inst_pc": 0,
                "stream": [], "pc": 0, "wait": 0, "sample_idx": 0, "note_name": "---"
            })
            
        self.instruments = {
            0: [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
            1: [15, 15, 14, 14, 13, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
        }

        if filename: self.load_z8a(filename)

    def init_notes(self):
        notes = ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"]
        flats = ["C", "Df", "D", "Ef", "E", "F", "Gf", "G", "Af", "A", "Bf", "B"]
        span = ["Do", "Dos", "Re", "Res", "Mi", "Fa", "Fas", "Sol", "Sols", "La", "Las", "Si"]
        sflat = ["Do", "Reb", "Re", "Mib", "Mi", "Fa", "Solb", "Sol", "Lab", "La", "Sib", "Si"]
        for oct in range(8):
            for i in range(12):
                val = oct * 12 + i
                for n in [notes[i], notes[i].replace("s","#"), flats[i], span[i], sflat[i]]:
                    if n: self.symbols[f"{n}{oct}"] = val
                self.symbols[f"Rb{oct}"] = oct * 12 + 1 # Rb alias

    def note_to_freq(self, note_val):
        if note_val == 255: return 0.0
        return 440.0 * (2.0 ** (((note_val + 12) - 69.0) / 12.0))

    def eval_expr(self, expr):
        try:
            expr = re.sub(r"#([0-9A-Fa-f]+)", r"0x\1", expr)
            # Iterative resolve
            for _ in range(5):
                sorted_syms = sorted(self.symbols.keys(), key=len, reverse=True)
                for sym in sorted_syms:
                    if sym in expr:
                        expr = re.sub(rf"\b{re.escape(sym)}\b", str(self.symbols[sym]), expr)
            clean_expr = expr.replace(" ", "")
            if not re.match(r"^[0-9x+\-*/%() \t.]+$", clean_expr): return 0
            return int(eval(expr))
        except Exception: return 0

    def load_z8a(self, filename):
        if not os.path.exists(filename): print(f"Error: {filename} not found"); sys.exit(1)
        with open(filename, "r") as f: lines = [l.split(";")[0].strip() for l in f.readlines()]
        labels = {}
        for line in lines:
            if not line: continue
            m = re.match(r"^(\w+):$", line)
            if m: labels[m.group(1)] = []; self.symbols[m.group(1)] = 0
            m = re.match(r"^(\w+)\s+EQU\s+(.+)$", line)
            if m: self.symbols[m.group(1)] = 0
            
        for _ in range(5):
            for line in lines:
                m = re.match(r"^(\w+)\s+EQU\s+(.+)$", line)
                if m: self.symbols[m.group(1)] = self.eval_expr(m.group(2))

        curr = None
        for line in lines:
            if not line: continue
            m = re.match(r"^(\w+):$", line)
            if m: curr = m.group(1); continue
            if curr and (line.startswith("DEFB") or line.startswith("DEFW")):
                parts = re.split(r",", line[4:].strip())
                for p in parts:
                    val = self.eval_expr(p.strip())
                    if line.startswith("DEFW"): labels[curr].extend([val & 0xFF, (val >> 8) & 0xFF])
                    else: labels[curr].append(val & 0xFF)
                    
        for i, ch_id in enumerate(["CHA", "CHB", "CHC"]):
            match = next((k for k in labels if k.endswith(ch_id) and not k.endswith("LP")), None)
            if not match: match = next((k for k in labels if ch_id in k and not k.endswith("LP")), None)
            if match: self.channels[i]["stream"] = labels[match]; self.channels[i]["active"] = True
        
        hdr = next((k for k in labels if "HDR" in k), None)
        if hdr and len(labels[hdr]) >= 2: self.bpm_step = labels[hdr][0] + (labels[hdr][1] << 8)

    def process_events(self):
        for i in range(MAX_CHANNELS):
            ch = self.channels[i]
            if not ch["active"]: continue
            while ch["active"] and ch["wait"] <= 0:
                if ch["pc"] >= len(ch["stream"]): ch["active"] = False; break
                cmd = ch["stream"][ch["pc"]]; ch["pc"] += 1
                if cmd >= 0xF7:
                    if cmd == 0xFC: ch["vol"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    elif cmd == 0xFA: ch["inst"] = ch["stream"][ch["pc"]]; ch["pc"] += 1
                    elif cmd == 0xF7: ch["pc"] = 0
                else:
                    ch["note_val"] = cmd; ch["freq"] = self.note_to_freq(cmd); ch["inst_pc"] = 0; ch["sample_idx"] = 0
                    notes = ["C-","Cs","D-","Ds","E-","F-","Fs","G-","Gs","A-","As","B-"]
                    ch["note_name"] = f"{notes[cmd%12]}{cmd//12}"
                    if ch["pc"] + 1 < len(ch["stream"]):
                        ch["wait"] = ch["stream"][ch["pc"]] + (ch["stream"][ch["pc"]+1] << 8); ch["pc"] += 2
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
            ch["cur_vol"] = (env[min(ch["inst_pc"], len(env)-1)] * ch["vol"]) // 15; ch["inst_pc"] += 1

    def draw(self):
        sys.stdout.write("\033[H")
        print(f"\033[1;36m MusaX Sim v0.9 (Fidelity) \033[0m | 60Hz | Ticks:{self.total_ticks}")
        print("-" * 75)
        for i, ch in enumerate(self.channels):
            status = "\033[32mON \033[0m" if ch["active"] else "\033[31mOFF\033[0m"
            bar = "█" * ch['cur_vol']
            print(f" CH {chr(65+i)}: [{status}] | {ch['note_name']:4} | Vol:{ch['vol']:2} | Out:{ch['cur_vol']:2} {bar:15} | PC:{ch['pc']:3}")
        print("-" * 75 + "\n [Ctrl+C] Quit")

    def render_audio(self, duration_limit=60):
        print(f"[*] Rendering audio ({duration_limit}s limit)...")
        all_samples = []
        max_samples = int(SAMPLE_RATE * duration_limit)
        
        # Reset state for rendering
        self.total_ticks = 0
        self.accumulator = 0
        for ch in self.channels: ch["pc"] = 0; ch["wait"] = 0; ch["active"] = True if ch["stream"] else False
        
        while len(all_samples) < max_samples:
            self.update()
            
            # Check if any channel is still active
            if not any(ch["active"] for ch in self.channels): break
            
            for _ in range(SAMPLES_PER_INT):
                mixed = 0
                for ch in self.channels:
                    if not ch["active"] or ch["note_val"] == 255 or ch["freq"] == 0: continue
                    period = SAMPLE_RATE / ch["freq"]
                    ch["sample_idx"] += 1
                    amp = (ch["cur_vol"] * 1000)
                    mixed += amp if (ch["sample_idx"] % period) < (period / 2) else -amp
                all_samples.append(max(-32768, min(32767, int(mixed))))
        
        return all_samples

    def run(self):
        samples = self.render_audio()
        
        # Save to temp WAV
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_name = tmp.name
        
        try:
            with wave.open(temp_name, "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(SAMPLE_RATE)
                for s in samples: wav.writeframesraw(struct.pack('<h', s))
            
            # Play in background
            print(f"[*] Playing...")
            cmd = ['aplay', '-q', '-c', '1', temp_name]
            if sys.platform == "darwin": cmd = ['afplay', temp_name]
            play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Simulated Dashboard (synchronized to real time)
            self.total_ticks = 0
            self.accumulator = 0
            for ch in self.channels: ch["pc"] = 0; ch["wait"] = 0; ch["active"] = True if ch["stream"] else False
            
            os.system('clear')
            start_time = time.time()
            frames = 0
            while play_proc.poll() is None:
                self.update()
                if frames % 2 == 0: self.draw()
                frames += 1
                wait_t = start_time + (frames / INTERRUPT_FREQ) - time.time()
                if wait_t > 0: time.sleep(wait_t)
                
        finally:
            if os.path.exists(temp_name): os.remove(temp_name)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file"); args = p.parse_args()
    MusaXSim(args.file).run()
