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
    VolumeFade, Detune, PhaseDelay, Chorus, GoTo, Restart, Call, Instrument,
    Label, LoopStart, LoopEnd, PhraseStart, PhraseEnd, FXBlockStart, FXBlockEnd, Metadata
)

class MSLCompiler:
    # --- Bytecode Mapping ---
    CMD_RET      = 0xF0
    CMD_CALL     = 0xF1
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
        self.metadata: Dict[str, str] = {}
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
        record[4] = inst.lfo[0]        # LFO Dest
        record[5] = inst.lfo[1]        # LFO Wave
        record[6] = inst.lfo[2] & 0xFF # LFO Speed (0-255)
        record[7] = inst.lfo[3] & 0x0F # LFO Amp   (0-15)
        record[8] = inst.lfo[4]        # LFO Delay
        record[9] = inst.flags
        return record

    def compile(self, events: List[MMLEvent], base_addr: int = 0) -> Dict[str, Union[List[int], Dict[int, List[int]], Dict[str, int], Dict[str, Dict], Dict[str, str]]]:
        self.bytecode = []
        self.labels = {}
        self.instruments = {}
        self.fx_definitions = {}
        self.metadata = {}
        self.current_offset = 0

        # --- Pass 1: Label Resolution, Loop Pairing & Instruments & Metadata ---
        # Note: We need to pre-expand loops that are triplets to get correct offsets
        expanded_events = []
        loop_stack = [] # List of (start_index_in_expanded)
        
        i = 0
        while i < len(events):
            ev = events[i]
            if isinstance(ev, LoopStart):
                loop_stack.append(len(expanded_events))
                i += 1
            elif isinstance(ev, LoopEnd):
                if loop_stack:
                    start_idx = loop_stack.pop()
                    loop_body = expanded_events[start_idx:]
                    count = ev.count
                    
                    if ev.is_triplet:
                        # Triplet adjustment: scale each note/rest in the body
                        for j in range(len(loop_body)):
                            item = loop_body[j]
                            if isinstance(item, Note):
                                loop_body[j] = Note(item.pitch_val, int((item.duration_ticks * 2) / 3))
                            elif isinstance(item, Rest):
                                loop_body[j] = Rest(int((item.duration_ticks * 2) / 3))
                    
                    # Add remaining repetitions (1st repetition already in expanded_events)
                    for _ in range(count - 1):
                        expanded_events.extend(loop_body)
                i += 1
            else:
                expanded_events.append(ev)
                i += 1
        
        # Now use expanded_events for bytecode generation
        temp_offset = 0
        current_fx_name = None
        current_phrase_name = None
        for event in expanded_events:
            if isinstance(event, Label):
                name = event.name
                if current_fx_name:
                    name = f"{current_fx_name}_{name}"
                elif current_phrase_name:
                    name = f"{current_phrase_name}_{name}"
                self.labels[name] = base_addr + temp_offset
                if current_fx_name:
                    self.fx_definitions[current_fx_name]["labels"].append(name)
            elif isinstance(event, Metadata):
                self.metadata[event.key] = event.value
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
            elif isinstance(event, Call):
                temp_offset += 3 # CMD_CALL (1b) + Addr (2b)
            elif isinstance(event, Instrument):
                self.instruments[event.id] = self._compile_instrument(event)
            elif isinstance(event, FXBlockStart):
                current_fx_name = event.name
                self.fx_definitions[current_fx_name] = {"start_addr": base_addr + temp_offset, "labels": []}
            elif isinstance(event, FXBlockEnd):
                current_fx_name = None
            elif isinstance(event, PhraseStart):
                current_phrase_name = event.name
                self.labels[current_phrase_name] = base_addr + temp_offset
            elif isinstance(event, PhraseEnd):
                current_phrase_name = None
                temp_offset += 1 # CMD_RET (1b)
        
        # --- Pass 2: Bytecode Generation ---
        current_fx_name = None
        current_phrase_name = None
        for event in expanded_events:
            if isinstance(event, FXBlockStart):
                current_fx_name = event.name
                continue
            if isinstance(event, FXBlockEnd):
                current_fx_name = None
                continue
            if isinstance(event, PhraseStart):
                current_phrase_name = event.name
                continue
            if isinstance(event, PhraseEnd):
                self._add_byte(self.CMD_RET)
                current_phrase_name = None
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
            elif isinstance(event, Call):
                self._add_byte(self.CMD_CALL)
                target = event.label
                # Phrases are global, so no need for prefixing usually, 
                # but let's check for scoped labels if they are ever supported within phrases.
                if current_phrase_name and f"{current_phrase_name}_{target}" in self.labels:
                    target = f"{current_phrase_name}_{target}"
                addr = self.labels.get(target, base_addr)
                self._add_word(addr)

        return {
            "bytecode": self.bytecode,
            "instruments": self.instruments,
            "labels": self.labels,
            "fx_definitions": self.fx_definitions,
            "metadata": self.metadata
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
