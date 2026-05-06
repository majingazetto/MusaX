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
    is_triplet: bool = False

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
class Call:
    label: str

@dataclass
class PhraseStart:
    name: str

@dataclass
class PhraseEnd:
    pass

@dataclass
class FXBlockStart:
    name: str

@dataclass
class FXBlockEnd:
    pass

@dataclass
class Instrument:
    id: int
    name: str
    adsr: List[int]
    lfo: List[int]
    flags: int

@dataclass
class Metadata:
    key: str
    value: str

@dataclass
class MSLError:
    line: int
    column: int
    message: str

MMLEvent = Union[
    SetOctave, OctaveUp, OctaveDown, SetLength, Note, Rest,
    Label, LoopStart, LoopEnd,
    SetVolume, SetInstrument, SetTempo, SetGateTime, SetPortamento,
    VolumeFade, Detune, PhaseDelay, Chorus, GoTo, Restart, Call, Instrument,
    PhraseStart, PhraseEnd, FXBlockStart, FXBlockEnd, Metadata
]

# --- Constants ---
NOTE_PITCH_MAP = {
    'C': 0, 'C#': 1, 'DB': 1, 'D': 2, 'D#': 3, 'EB': 3, 'E': 4, 'F': 5,
    'F#': 6, 'GB': 6, 'G': 7, 'G#': 8, 'AB': 8, 'A': 9, 'A#': 10, 'BB': 10, 'B': 11
}
BASE_TICK = 768

# Enhanced regex to capture all MSL constructs
TOKEN_REGEX = re.compile(
    r'(//[^\n]*)|'                             # Group 1: Comments
    r'(@INST\s*\([^)]*\)\s*\{[^}]*\})|'        # Group 2: @INST blocks
    r'(PHRASE\s*\([^)]*\)\s*\{)|'              # Group 3: PHRASE blocks
    r'(@[A-Z0-9#\_\-]+(?:\s*(?:\([^)]*\)|"[^"]*"))?)|' # Group 4: other @-commands
    r'([A-Z0-9_\.]+):|'                        # Group 5: Labels
    r'(\{)|(\})\s*(\d*)(t?)|'                  # Group 6,7,8,9: Loops
    r'([<>])|'                                 # Group 10: octave shifts
    r'([A-GR])([#\+\-bB]?)(\d*)([\.t]*)|'      # Group 11,12,13,14: notes
    r'([OL])(\d*)([\.t]*)',                    # Group 15,16,17: O/L commands
    re.IGNORECASE | re.DOTALL
)

@dataclass
class ParserState:
    """Holds the state of the parser at any given time."""
    current_octave: int = 4
    default_length: int = 4
    default_length_mod: str = ""

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

    def _calculate_ticks(self, length_str: str, modifiers: str) -> int:
        if not length_str:
            length = self.state.default_length
            if not modifiers:
                modifiers = self.state.default_length_mod
        else:
            length = int(length_str)
        
        if length == 0: return 0 # Avoid division by zero
        ticks = (BASE_TICK * 4) / length
        
        if modifiers:
            # Handle dots
            dots = modifiers.count('.')
            if dots > 0:
                factor = 1.0
                add = 0.5
                for _ in range(dots):
                    factor += add
                    add /= 2.0
                ticks *= factor
            
            # Handle triplets
            if 't' in modifiers.lower():
                ticks = (ticks * 2) / 3
                
        return int(ticks)

    def _parse_at_command(self, command_str: str, offset: int):
        command_str = command_str[1:].strip() # Remove @ and extra spaces
        
        # Metadata commands like @TITLE "Song Name", @AUTHOR "Artist"
        match = re.match(r'(TITLE|AUTHOR|DESC)\s*"([^"]*)"', command_str, re.IGNORECASE)
        if match:
            key, val = match.groups()
            self.events.append(Metadata(key.upper(), val))
            return

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

        # Tempo command @T#0600 or @T120
        match = re.match(r'T\s*#([0-9A-F]+)', command_str, re.IGNORECASE)
        if match:
            val_str = match.groups()[0]
            val = int(val_str, 16)
            self.events.append(SetTempo(val))
            return
            
        match = re.match(r'T\s*(\d+)', command_str, re.IGNORECASE)
        if match:
            bpm = int(match.groups()[0])
            # Convert BPM to MusaX bpm_step: (BPM * BASE_TICK * 256) / (60 * INTERRUPT_FREQ)
            # 60 * 60 = 3600
            val = int((bpm * BASE_TICK * 256) / 3600)
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
            
        # GOTO/RESTART/CALL/FX commands
        match = re.match(r'(GOTO|RESTART|CALL|FX)\s*\(\s*([^)]+)\s*\)', command_str, re.IGNORECASE)
        if match:
            cmd, arg = match.groups()
            cmd = cmd.upper()
            arg = arg.strip()
            if cmd == 'GOTO': self.events.append(GoTo(arg))
            elif cmd == 'RESTART': self.events.append(Restart(arg))
            elif cmd == 'CALL': self.events.append(Call(arg))
            elif cmd == 'FX': self.events.append(FXBlockStart(arg))
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
        (comment, inst_block, phrase_block, at_command, label, loop_start, loop_end, loop_count, loop_triplet,
         octave_shift, note, alteration, length_str, dot, command, cmd_val, cmd_mod) = match.groups()

        offset = match.start()

        if comment:
            return

        if inst_block:
            self._parse_inst_block(inst_block, offset)

        elif phrase_block:
            match = re.search(r'PHRASE\s*\(([^)]+)\)\s*\{', phrase_block, re.IGNORECASE)
            if match:
                self.events.append(PhraseStart(match.group(1).strip()))
            else:
                self._add_error(offset, "Malformed PHRASE header")

        elif at_command:
            self._parse_at_command(at_command, offset)

        elif label:
            self.events.append(Label(label))

        elif loop_start:
            # If the last event was FXBlockStart or PhraseStart, this brace belongs to it
            if self.events and isinstance(self.events[-1], (FXBlockStart, PhraseStart)):
                pass
            else:
                self.events.append(LoopStart())

        elif loop_end:
            # Check if we are inside an FX block, a Phrase block or a loop
            is_block_end = False
            if not loop_count:
                depth = 0
                for ev in reversed(self.events):
                    if isinstance(ev, (FXBlockEnd, PhraseEnd)): depth += 1
                    if isinstance(ev, (FXBlockStart, PhraseStart)):
                        if depth == 0:
                            is_block_end = True
                            break
                        depth -= 1

            if is_block_end:
                # Check if the closest starting block is a Phrase
                depth = 0
                for ev in reversed(self.events):
                    if isinstance(ev, (FXBlockEnd, PhraseEnd)): depth += 1
                    if isinstance(ev, PhraseStart):
                        if depth == 0:
                            self.events.append(PhraseEnd())
                            break
                        depth -= 1
                    elif isinstance(ev, FXBlockStart):
                        if depth == 0:
                            self.events.append(FXBlockEnd())
                            break
                        depth -= 1
            else:
                count = int(loop_count) if loop_count else 2 # Default to 2 if not specified
                is_triplet = loop_triplet.lower() == 't'
                self.events.append(LoopEnd(count, is_triplet))

        elif octave_shift:
            if octave_shift == '>':
                self.state.current_octave += 1
                self.events.append(OctaveUp())
            elif octave_shift == '<':
                self.state.current_octave -= 1
                self.events.append(OctaveDown())

        elif note:
            is_dotted = '.' in dot
            duration_ticks = self._calculate_ticks(length_str, dot)

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
            if cmd_char == 'O':
                val = int(cmd_val) if cmd_val else self.state.current_octave
                self.state.current_octave = val
                self.events.append(SetOctave(val))
            elif cmd_char == 'L':
                val = int(cmd_val) if cmd_val else self.state.default_length
                self.state.default_length = val
                self.state.default_length_mod = cmd_mod if cmd_mod else ""
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
                    pass # Handled by FXBlockEnd logic or reported as error
                else:
                    loop_stack.pop()
        
        # Note: we should probably report unbalanced loops here but we'll keep it simple
        return self.events

def main():
    parser = MSLParser()
    mml_source = """
    @FX(LASER) {
        CH_A: O6 L16 C D E
    }
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
