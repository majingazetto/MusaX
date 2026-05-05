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

MMLEvent = Union[SetOctave, OctaveUp, OctaveDown, SetLength, Note, Rest]

# --- Constants ---
NOTE_PITCH_MAP = {
    'C': 0, 'C#': 1, 'DB': 1, 'D': 2, 'D#': 3, 'EB': 3, 'E': 4, 'F': 5,
    'F#': 6, 'GB': 6, 'G': 7, 'G#': 8, 'AB': 8, 'A': 9, 'A#': 10, 'BB': 10, 'B': 11
}
BASE_TICK = 768

# This enhanced regex now also captures octave up/down characters.
TOKEN_REGEX = re.compile(
    r'([<>])|([A-GR])([#\+\-bB]?)(\d*)(\.?)|([OL])(\d+)',
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

    def _parse_token(self, match):
        octave_shift, note, alteration, length_str, dot, command, cmd_val = match.groups()

        if octave_shift:
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
    mml_source = "L8 O4 C E G > C < G E C R4" 
    
    parsed_events = parser.parse(mml_source)
    
    print("\n--- Parsed Events ---")
    if not parsed_events:
        print("(No events parsed)")
    else:
        for event in parsed_events:
            print(event)

if __name__ == "__main__":
    main()
