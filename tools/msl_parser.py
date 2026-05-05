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

MMLEvent = Union[
    SetOctave, OctaveUp, OctaveDown, SetLength, Note, Rest,
    SetVolume, SetInstrument, SetTempo, SetGateTime, SetPortamento,
    VolumeFade, Detune, PhaseDelay, Chorus, GoTo, Restart
]

# --- Constants ---
NOTE_PITCH_MAP = {
    'C': 0, 'C#': 1, 'DB': 1, 'D': 2, 'D#': 3, 'EB': 3, 'E': 4, 'F': 5,
    'F#': 6, 'GB': 6, 'G': 7, 'G#': 8, 'AB': 8, 'A': 9, 'A#': 10, 'BB': 10, 'B': 11
}
BASE_TICK = 768

# This enhanced regex now also captures octave up/down characters and @-commands.
TOKEN_REGEX = re.compile(
    r'(@[A-Z0-9#\(\)\,\-\_]+)|'
    r'([<>])|'
    r'([A-GR])([#\+\-bB]?)(\d*)(\.?)|'
    r'([OL])(\d+)',
    re.IGNORECASE
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

    def _parse_at_command(self, command_str: str):
        command_str = command_str[1:] # Remove @
        
        # Simple commands like @V15, @I3
        match = re.match(r'([VIGPD])(\-?\d+)', command_str, re.IGNORECASE)
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
        match = re.match(r'T#([0-9A-F]+)', command_str, re.IGNORECASE)
        if match:
            val_str = match.groups()[0]
            val = int(val_str, 16)
            self.events.append(SetTempo(val))
            return
            
        # Phase delay @PH12
        match = re.match(r'PH(\d+)', command_str, re.IGNORECASE)
        if match:
            val = int(match.groups()[0])
            self.events.append(PhaseDelay(val))
            return

        # Commands with two args like @F(1,1), @CH(10,-5)
        match = re.match(r'([FCH]{1,2})\((\d+),(\-?\d+)\)', command_str, re.IGNORECASE)
        if match:
            cmd, val1_str, val2_str = match.groups()
            val1, val2 = int(val1_str), int(val2_str)
            cmd = cmd.upper()
            if cmd == 'F': self.events.append(VolumeFade(val1, val2))
            elif cmd == 'CH': self.events.append(Chorus(val1, val2))
            return
            
        # GOTO/RESTART commands
        match = re.match(r'(GOTO|RESTART)\((.+)\)', command_str, re.IGNORECASE)
        if match:
            cmd, label = match.groups()
            cmd = cmd.upper()
            if cmd == 'GOTO': self.events.append(GoTo(label))
            elif cmd == 'RESTART': self.events.append(Restart(label))
            return

    def _parse_token(self, match):
        at_command, octave_shift, note, alteration, length_str, dot, command, cmd_val = match.groups()

        if at_command:
            self._parse_at_command(at_command)

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
        print(f"--- Starting MML Parse ---")
        print(f"Input: \"{mml_string}\"")
        self.source = mml_string.strip()
        self.state = ParserState()
        self.events = []

        for match in TOKEN_REGEX.finditer(self.source):
            self._parse_token(match)
            
        print("--- Parse Complete ---")
        return self.events

def main():
    parser = MSLParser()
    mml_source = "L8 O4 C E G > C < G E C R4 @V15 @I3 @T#0600 @F(1,1) @CH(10,-5) @GOTO(TEST_LABEL) @RESTART(SONG_LOOP)" 
    
    parsed_events = parser.parse(mml_source)
    
    print("\n--- Parsed Events ---")
    if not parsed_events:
        print("(No events parsed)")
    else:
        for event in parsed_events:
            print(event)

if __name__ == "__main__":
    main()
