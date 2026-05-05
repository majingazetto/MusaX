import re
from dataclasses import dataclass, field
from typing import List, Union

# --- Data Classes for MML Events ---
@dataclass
class SetOctave:
    octave: int

@dataclass
class OctaveUp:
    pass

@dataclass
class OctaveDown:
    pass

@dataclass
class SetLength:
    length: int

@dataclass
class Note:
    pitch_val: int
    duration_ticks: int

@dataclass
class Rest:
    duration_ticks: int

@dataclass
class Label:
    name: str

@dataclass
class LoopStart:
    pass

@dataclass
class LoopEnd:
    count: int

# --- MusaX Engine Command Classes ---
@dataclass
class SetVolume:
    volume: int

@dataclass
class SetInstrument:
    instrument_id: int

@dataclass
class SetTempo:
    bpm_step: int

@dataclass
class SetGateTime:
    gate_time: int

@dataclass
class SetPortamento:
    speed: int

@dataclass
class VolumeFade:
    target: int
    step: int

@dataclass
class Detune:
    cents: int

@dataclass
class PhaseDelay:
    delay: int

@dataclass
class Chorus:
    phase: int
    detune: int

@dataclass
class GoTo:
    label: str

@dataclass
class Restart:
    label: str

@dataclass
class Instrument:
    id: int
    name: str
    adsr: List[int]
    lfo: List[int]
    flags: int

@dataclass
class MSLError:
    line: int
    column: int
    message: str

MMLEvent = Union[
    SetOctave, OctaveUp, OctaveDown, SetLength, Note, Rest,
    Label, LoopStart, LoopEnd,
    SetVolume, SetInstrument, SetTempo, SetGateTime, SetPortamento,
    VolumeFade, Detune, PhaseDelay, Chorus, GoTo, Restart, Instrument
]

# --- Constants ---
NOTE_PITCH_MAP = {
    'C': 0, 'C#': 1, 'DB': 1, 'D': 2, 'D#': 3, 'EB': 3, 'E': 4, 'F': 5,
    'F#': 6, 'GB': 6, 'G': 7, 'G#': 8, 'AB': 8, 'A': 9, 'A#': 10, 'BB': 10, 'B': 11
}
BASE_TICK = 768

# Enhanced regex to capture all MSL constructs
TOKEN_REGEX = re.compile(
    r'(//.*)|'                                 # Group 1: Comments
    r'(@INST\s*\([^)]*\)\s*\{[^}]*\})|'        # Group 2: @INST blocks
    r'(@[A-Z0-9#\_\-]+(?:\s*\([^)]*\))?)|'      # Group 3: other @-commands (added -)
    r'([A-Z0-9_\.]+):|'                        # Group 4: Labels
    r'(\{)|(\})\s*(\d*)|'                      # Group 5,6,7: Loops
    r'([<>])|'                                 # Group 8: octave shifts
    r'([A-GR])([#\+\-bB]?)(\d*)(\.?)|'          # Group 9,10,11,12: notes
    r'([OL])(\d+)',                            # Group 13,14: O/L commands
    re.IGNORECASE | re.DOTALL
)

@dataclass
class ParserState:
    """Holds the state of the parser at any given time."""
    current_octave: int = 4
    default_length: int = 4

class MSLParser:
    def __init__(self):
        self.state = ParserState()
        self.events: List[MMLEvent] = []
        self.errors: List[MSLError] = []
        self.source = ""

    def _get_line_col(self, offset: int):
        line = self.source.count('\n', 0, offset) + 1
        last_newline = self.source.rfind('\n', 0, offset)
        col = offset - last_newline if last_newline != -1 else offset + 1
        return line, col

    def _add_error(self, offset: int, message: str):
        line, col = self._get_line_col(offset)
        self.errors.append(MSLError(line, col, message))

    def _calculate_ticks(self, length_str: str, is_dotted: bool) -> int:
        if not length_str:
            length = self.state.default_length
        else:
            length = int(length_str)
        
        if length == 0: return 0 # Avoid division by zero
        ticks = (BASE_TICK * 4) / length
        if is_dotted:
            ticks *= 1.5
        return int(ticks)

    def _parse_at_command(self, command_str: str, offset: int):
        command_str = command_str[1:].strip() # Remove @ and extra spaces
        
        # Simple commands like @V15, @I3, @G200, @P10, @D-5
        match = re.match(r'([VIGPD])\s*(\-?\d+)', command_str, re.IGNORECASE)
        if match:
            cmd, val_str = match.groups()
            val = int(val_str)
            cmd = cmd.upper()
            if cmd == 'V': self.events.append(SetVolume(val))
            elif cmd == 'I': self.events.append(SetInstrument(val))
            elif cmd == 'G': self.events.append(SetGateTime(val))
            elif cmd == 'P': self.events.append(SetPortamento(val))
            elif cmd == 'D': self.events.append(Detune(val))
            return

        # Tempo command @T#0600
        match = re.match(r'T\s*#([0-9A-F]+)', command_str, re.IGNORECASE)
        if match:
            val_str = match.groups()[0]
            val = int(val_str, 16)
            self.events.append(SetTempo(val))
            return
            
        # Phase delay @PH12
        match = re.match(r'PH\s*(\d+)', command_str, re.IGNORECASE)
        if match:
            val = int(match.groups()[0])
            self.events.append(PhaseDelay(val))
            return

        # Commands with two args like @F(1, 1), @CH(10, -5)
        match = re.match(r'([FCH]{1,2})\s*\(\s*(\-?\d+)\s*,\s*(\-?\d+)\s*\)', command_str, re.IGNORECASE)
        if match:
            cmd, val1_str, val2_str = match.groups()
            val1, val2 = int(val1_str), int(val2_str)
            cmd = cmd.upper()
            if cmd == 'F': self.events.append(VolumeFade(val1, val2))
            elif cmd == 'CH': self.events.append(Chorus(val1, val2))
            return
            
        # GOTO/RESTART commands
        match = re.match(r'(GOTO|RESTART)\s*\(\s*(.+)\s*\)', command_str, re.IGNORECASE)
        if match:
            cmd, label = match.groups()
            cmd = cmd.upper()
            label = label.strip()
            if cmd == 'GOTO': self.events.append(GoTo(label))
            elif cmd == 'RESTART': self.events.append(Restart(label))
            return

        self._add_error(offset, f"Unknown or malformed command: @{command_str}")

    def _parse_inst_block(self, inst_block: str, offset: int):
        # Parse id and name
        header_match = re.search(r'@INST\s*\((\d+)\s*,\s*"([^"]+)"\)', inst_block, re.IGNORECASE)
        if not header_match:
            self._add_error(offset, "Malformed @INST header. Expected @INST(id, \"name\")")
            return
        inst_id, inst_name = header_match.groups()
        inst_id = int(inst_id)

        # Parse properties
        adsr = [0,0,0,0]
        lfo = [0,0,0,0,0]
        flags = 0

        adsr_match = re.search(r'ADSR:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', inst_block, re.IGNORECASE)
        if adsr_match:
            adsr = [int(v) for v in adsr_match.groups()]
        else:
            self._add_error(offset, "Missing or malformed ADSR definition in instrument block")

        lfo_match = re.search(r'LFO:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', inst_block, re.IGNORECASE)
        if lfo_match:
            lfo = [int(v) for v in lfo_match.groups()]
        else:
            self._add_error(offset, "Missing or malformed LFO definition in instrument block")
            
        flags_match = re.search(r'FLAGS:\s*(\d+)', inst_block, re.IGNORECASE)
        if flags_match:
            flags = int(flags_match.groups()[0])

        self.events.append(Instrument(id=inst_id, name=inst_name, adsr=adsr, lfo=lfo, flags=flags))

    def _parse_token(self, match):
        (comment, inst_block, at_command, label, loop_start, loop_end, loop_count, 
         octave_shift, note, alteration, length_str, dot, command, cmd_val) = match.groups()
        
        offset = match.start()

        if comment:
            return

        if inst_block:
            self._parse_inst_block(inst_block, offset)
        
        elif at_command:
            self._parse_at_command(at_command, offset)

        elif label:
            self.events.append(Label(label))

        elif loop_start:
            self.events.append(LoopStart())

        elif loop_end:
            count = int(loop_count) if loop_count else 2 # Default to 2 if not specified
            self.events.append(LoopEnd(count))

        elif octave_shift:
            if octave_shift == '>':
                self.state.current_octave += 1
                self.events.append(OctaveUp())
            elif octave_shift == '<':
                self.state.current_octave -= 1
                self.events.append(OctaveDown())
        
        elif note:
            is_dotted = dot == '.'
            duration_ticks = self._calculate_ticks(length_str, is_dotted)
            
            if note.upper() == 'R':
                self.events.append(Rest(duration_ticks))
                return

            if alteration in ('+', '#'): pitch_name = note.upper() + '#'
            elif alteration in ('-', 'b', 'B'): pitch_name = note.upper() + 'B'
            else: pitch_name = note.upper()
            
            # Use a get with fallback for robustness
            base_pitch = NOTE_PITCH_MAP.get(pitch_name, NOTE_PITCH_MAP.get(note.upper()))
            
            pitch_val = base_pitch + (self.state.current_octave * 12)
            self.events.append(Note(pitch_val, duration_ticks))

        elif command:
            cmd_char = command.upper()
            val = int(cmd_val)
            if cmd_char == 'O':
                self.state.current_octave = val
                self.events.append(SetOctave(val))
            elif cmd_char == 'L':
                self.state.default_length = val
                self.events.append(SetLength(val))
        
    def parse(self, mml_string: str) -> List[MMLEvent]:
        self.source = mml_string
        self.state = ParserState()
        self.events = []
        self.errors = []

        last_pos = 0
        for match in TOKEN_REGEX.finditer(self.source):
            # Check for skipped "dead zones" (unrecognized text)
            skipped = self.source[last_pos:match.start()].strip()
            if skipped:
                # Basic check: ignore whitespace, but report other characters
                self._add_error(last_pos, f"Unrecognized token: '{skipped}'")
            
            self._parse_token(match)
            last_pos = match.end()
        
        # Final dead zone check
        remaining = self.source[last_pos:].strip()
        if remaining:
            self._add_error(last_pos, f"Unrecognized token: '{remaining}'")

        # Post-parse validation: check for unbalanced loops
        loop_stack = []
        for event in self.events:
            if isinstance(event, LoopStart):
                loop_stack.append(event)
            elif isinstance(event, LoopEnd):
                if not loop_stack:
                    # For LoopEnd, we don't have an easy offset here, 
                    # but we can improve this later.
                    self.errors.append(MSLError(0, 0, "Unbalanced loop: found '}' without '{'"))
                else:
                    loop_stack.pop()
        
        for _ in loop_stack:
            self.errors.append(MSLError(0, 0, "Unbalanced loop: missing '}'"))

        return self.events

def main():
    parser = MSLParser()
    mml_source = """
    @INST(0, "VibratoLead") {
        ADSR: 10, 5, 255, 10
        LFO: 1, 0, 2, 12, 20
        FLAGS: 0
    }
    L8 O4 C E G
    """
    
    parsed_events = parser.parse(mml_source)
    
    print("\n--- Parsed Events ---")
    if not parsed_events:
        print("(No events parsed)")
    else:
        for event in parsed_events:
            print(event)

if __name__ == "__main__":
    main()
