import sys
import os
from typing import List, Dict, Union
from dataclasses import dataclass

# Ensure we can import msl_parser correctly
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.append(project_root)

from MusaX.tools.msl_parser import (
    MMLEvent, Note, Rest, SetOctave, OctaveUp, OctaveDown, SetLength,
    SetVolume, SetInstrument, SetTempo, SetGateTime, SetPortamento,
    VolumeFade, Detune, PhaseDelay, Chorus, GoTo, Restart, Instrument,
    Label, LoopStart, LoopEnd, FXBlockStart, FXBlockEnd
)

class MSLCompiler:
    # --- Bytecode Mapping ---
    CMD_PORTA    = 0xF2
    CMD_FADE     = 0xF3
    CMD_CHORUS   = 0xF4
    CMD_DETUNE   = 0xF5
    CMD_PHASE    = 0xF6
    CMD_GOTO     = 0xF7
    CMD_LOOP_E   = 0xF8
    CMD_LOOP_S   = 0xF9
    CMD_INST     = 0xFA
    CMD_GATE     = 0xFB
    CMD_VOLUME   = 0xFC
    CMD_TEMPO    = 0xFD
    CMD_RESTART  = 0xFE
    CMD_REST     = 0xFF

    def __init__(self):
        self.bytecode: List[int] = []
        self.labels: Dict[str, int] = {}
        self.instruments: Dict[int, List[int]] = {}
        self.fx_definitions: Dict[str, Dict] = {}
        self.current_offset = 0

    def _add_byte(self, b: int):
        self.bytecode.append(b & 0xFF)
        self.current_offset += 1

    def _add_word(self, w: int):
        # Little-endian (Z80 standard)
        self._add_byte(w & 0xFF)
        self._add_byte((w >> 8) & 0xFF)

    def _compile_instrument(self, inst: Instrument) -> List[int]:
        """Compiles an Instrument event into a 16-byte record."""
        record = [0] * 16
        # ADSR (4 bytes)
        for i in range(4):
            record[i] = inst.adsr[i]
        # LFO (5 bytes: dest, wave, speed_amp, delay)
        # Note: spec says speed and amp are combined into one byte.
        # Let's assume speed is high nibble, amp is low nibble for now.
        record[4] = inst.lfo[0] # Dest
        record[5] = inst.lfo[1] # Wave
        record[6] = (inst.lfo[2] << 4) | (inst.lfo[3] & 0x0F) # Speed/Amp
        record[7] = inst.lfo[4] # Delay
        # Flags (1 byte)
        record[8] = inst.flags
        return record

    def compile(self, events: List[MMLEvent], base_addr: int = 0) -> Dict[str, Union[List[int], Dict[int, List[int]], Dict[str, int], Dict[str, Dict]]]:
        self.bytecode = []
        self.labels = {}
        self.instruments = {}
        self.fx_definitions = {}
        self.current_offset = 0

        # --- Pass 1: Label Resolution, Loop Pairing & Instruments ---
        temp_offset = 0
        loop_stack = [] # Stores (LoopStart_id)
        start_to_count = {}
        
        current_fx_name = None
        for event in events:
            if isinstance(event, Label):
                name = event.name
                if current_fx_name:
                    name = f"{current_fx_name}_{name}"
                self.labels[name] = base_addr + temp_offset
                if current_fx_name:
                    self.fx_definitions[current_fx_name]["labels"].append(name)
            elif isinstance(event, Note):
                temp_offset += 3 # Note (1b) + Duration (2b)
            elif isinstance(event, Rest):
                temp_offset += 3 # CMD_REST (1b) + Duration (2b)
            elif isinstance(event, SetVolume):
                temp_offset += 2 # CMD_VOLUME (1b) + Val (1b)
            elif isinstance(event, SetInstrument):
                temp_offset += 2 # CMD_INST (1b) + Val (1b)
            elif isinstance(event, SetTempo):
                temp_offset += 3 # CMD_TEMPO (1b) + Val (2b)
            elif isinstance(event, SetGateTime):
                temp_offset += 2 # CMD_GATE (1b) + Val (1b)
            elif isinstance(event, SetPortamento):
                temp_offset += 2 # CMD_PORTA (1b) + Val (1b)
            elif isinstance(event, VolumeFade):
                temp_offset += 3 # CMD_FADE (1b) + Target (1b) + Step (1b)
            elif isinstance(event, Detune):
                temp_offset += 2 # CMD_DETUNE (1b) + Val (1b)
            elif isinstance(event, PhaseDelay):
                temp_offset += 2 # CMD_PHASE (1b) + Val (1b)
            elif isinstance(event, Chorus):
                temp_offset += 3 # CMD_CHORUS (1b) + Phase (1b) + Detune (1b)
            elif isinstance(event, GoTo):
                temp_offset += 3 # CMD_GOTO (1b) + Addr (2b)
            elif isinstance(event, Restart):
                temp_offset += 3 # CMD_RESTART (1b) + Addr (2b)
            elif isinstance(event, LoopStart):
                temp_offset += 2 # CMD_LOOP_S (1b) + Count (1b)
                loop_stack.append(id(event))
            elif isinstance(event, LoopEnd):
                temp_offset += 1 # CMD_LOOP_E (1b)
                if loop_stack:
                    s_id = loop_stack.pop()
                    start_to_count[s_id] = event.count
            elif isinstance(event, Instrument):
                self.instruments[event.id] = self._compile_instrument(event)
            elif isinstance(event, FXBlockStart):
                current_fx_name = event.name
                self.fx_definitions[current_fx_name] = {"start_addr": base_addr + temp_offset, "labels": []}
            elif isinstance(event, FXBlockEnd):
                current_fx_name = None
        
        # --- Pass 2: Bytecode Generation ---
        current_fx_name = None
        for event in events:
            if isinstance(event, FXBlockStart):
                current_fx_name = event.name
                continue
            if isinstance(event, FXBlockEnd):
                current_fx_name = None
                continue

            if isinstance(event, Note):
                self._add_byte(event.pitch_val)
                self._add_word(event.duration_ticks)
            elif isinstance(event, Rest):
                self._add_byte(self.CMD_REST)
                self._add_word(event.duration_ticks)
            elif isinstance(event, SetVolume):
                self._add_byte(self.CMD_VOLUME)
                self._add_byte(event.volume)
            elif isinstance(event, SetInstrument):
                self._add_byte(self.CMD_INST)
                self._add_byte(event.instrument_id)
            elif isinstance(event, SetTempo):
                self._add_byte(self.CMD_TEMPO)
                self._add_word(event.bpm_step)
            elif isinstance(event, SetGateTime):
                self._add_byte(self.CMD_GATE)
                self._add_byte(event.gate_time)
            elif isinstance(event, SetPortamento):
                self._add_byte(self.CMD_PORTA)
                self._add_byte(event.speed)
            elif isinstance(event, VolumeFade):
                self._add_byte(self.CMD_FADE)
                self._add_byte(event.target)
                self._add_byte(event.step)
            elif isinstance(event, Detune):
                self._add_byte(self.CMD_DETUNE)
                self._add_byte(event.cents)
            elif isinstance(event, PhaseDelay):
                self._add_byte(self.CMD_PHASE)
                self._add_byte(event.delay)
            elif isinstance(event, Chorus):
                self._add_byte(self.CMD_CHORUS)
                self._add_byte(event.phase)
                self._add_byte(event.detune)
            elif isinstance(event, GoTo):
                self._add_byte(self.CMD_GOTO)
                target = event.label
                if current_fx_name and f"{current_fx_name}_{target}" in self.labels:
                    target = f"{current_fx_name}_{target}"
                addr = self.labels.get(target, base_addr) # Default to start
                self._add_word(addr)
            elif isinstance(event, Restart):
                self._add_byte(self.CMD_RESTART)
                target = event.label
                if current_fx_name and f"{current_fx_name}_{target}" in self.labels:
                    target = f"{current_fx_name}_{target}"
                addr = self.labels.get(target, base_addr) # Default to start
                self._add_word(addr)
            elif isinstance(event, LoopStart):
                self._add_byte(self.CMD_LOOP_S)
                count = start_to_count.get(id(event), 2)
                self._add_byte(count)
            elif isinstance(event, LoopEnd):
                self._add_byte(self.CMD_LOOP_E)

        return {
            "bytecode": self.bytecode,
            "instruments": self.instruments,
            "labels": self.labels,
            "fx_definitions": self.fx_definitions
        }

    def to_z8a(self, bytecode: List[int]) -> str:
        """Converts bytecode to a formatted Z8A DEFB string."""
        lines = []
        for i in range(0, len(bytecode), 8):
            chunk = bytecode[i:i+8]
            hex_values = ", ".join([f"#{b:02X}" for b in chunk])
            lines.append(f"    DEFB {hex_values}")
        return "\n".join(lines)

if __name__ == "__main__":
    # Quick test
    import os
    import sys
    
    # Ensure we can import msl_parser
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Add project root to path
    project_root = os.path.dirname(current_dir)
    root_parent = os.path.dirname(project_root)
    if root_parent not in sys.path:
        sys.path.append(root_parent)
        
    try:
        from MusaX.tools.msl_parser import MSLParser
    except ImportError:
        # Fallback for different execution environments
        sys.path.append(current_dir)
        from msl_parser import MSLParser
    
    parser = MSLParser()
    compiler = MSLCompiler()
    
    mml = "L8 O4 C E G R4"
    print(f"Compiling MML: {mml}")
    events = parser.parse(mml)
    result = compiler.compile(events)
    
    print("\n--- Bytecode Output ---")
    z8a_out = compiler.to_z8a(result["bytecode"])
    if z8a_out:
        print(z8a_out)
    else:
        print("(Empty bytecode)")
    
    if result["instruments"]:
        print("\n--- Instruments ---")
        for idx, data in result["instruments"].items():
            hex_data = ", ".join([f"#{b:02X}" for b in data])
            print(f"Inst {idx}: DEFB {hex_data}")
