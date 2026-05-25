from msl_parser import MMLEvent, Note, SetVolume, SetInstrument, SetTempo, SetGateTime, SetPortamento, VolumeFade, Detune, PhaseDelay, Chorus, GoTo, Restart, Rest, SetOctave, OctaveUp, OctaveDown, SetLength, MSLParser, Instrument

# --- Mappings for Code Generation ---

NOTE_NAMES = ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"]

TICKS_TO_LEN_MAP = {
    768 * 4: "LENW",
    768 * 2: "LENH",
    768: "LENQ",
    int(768 / 2): "LENE",
    int(768 / 4): "LENS",
    int(768 / 8): "LENT",
    256: "LENET",
    128: "LENST",
    int(768 + 768 / 2): "LENQD",
    int(768 / 2 + 768 / 4): "LENED",
}

def pitch_to_note_name(pitch_val: int) -> str:
    if pitch_val < 0 or pitch_val > 95:
        return "INVALID_PITCH"
    octave = pitch_val // 12
    note_index = pitch_val % 12
    return f"{NOTE_NAMES[note_index]}{octave}"


class CodeGenerator:
    def __init__(self):
        self.z80_code = ""

    def _generate_note(self, event: Note):
        full_note_name = pitch_to_note_name(event.pitch_val)
        duration_label = TICKS_TO_LEN_MAP.get(event.duration_ticks, str(event.duration_ticks))

        self.z80_code += f"    DEFB    {full_note_name}\n"
        self.z80_code += f"    DEFW    {duration_label}\n"

    def _generate_rest(self, event: Rest):
        duration_label = TICKS_TO_LEN_MAP.get(event.duration_ticks, str(event.duration_ticks))
        self.z80_code += f"    DEFB    REST\n"
        self.z80_code += f"    DEFW    {duration_label}\n"

    def _generate_set_volume(self, event: SetVolume):
        self.z80_code += f"    DEFB    CVOLUME, {event.volume}\n"

    def _generate_set_instrument(self, event: SetInstrument):
        self.z80_code += f"    DEFB    CINST, {event.instrument_id}\n"

    def _generate_set_tempo(self, event: SetTempo):
        self.z80_code += f"    DEFB    CTEMPO\n"
        self.z80_code += f"    DEFW    #{event.bpm_step:04X}\n"

    def _generate_goto(self, event: GoTo):
        self.z80_code += f"    DEFB    CGOTO\n"
        self.z80_code += f"    DEFW    {event.label}\n"

    def _generate_restart(self, event: Restart):
        self.z80_code += f"    DEFB    CRESTART\n"
        self.z80_code += f"    DEFW    {event.label}\n"

    def _generate_set_gate_time(self, event: SetGateTime):
        self.z80_code += f"    DEFB    CGATE, {event.gate_time}\n"

    def _generate_set_portamento(self, event: SetPortamento):
        self.z80_code += f"    DEFB    CPORTA, {event.speed}\n"

    def _generate_volume_fade(self, event: VolumeFade):
        self.z80_code += f"    DEFB    CFADE, {event.target}, {event.step}\n"

    def _generate_detune(self, event: Detune):
        self.z80_code += f"    DEFB    CDETUNE, {event.cents}\n"

    def _generate_phase_delay(self, event: PhaseDelay):
        self.z80_code += f"    DEFB    CPHASE, {event.delay}\n"

    def _generate_chorus(self, event: Chorus):
        self.z80_code += f"    DEFB    CCHORUS, {event.phase}, {event.detune}\n"

    def generate(self, events: List[MMLEvent]) -> str:
        self.z80_code = ""
        for event in events:
            if isinstance(event, Note):
                self._generate_note(event)
            elif isinstance(event, Rest):
                self._generate_rest(event)
            elif isinstance(event, SetVolume):
                self._generate_set_volume(event)
            elif isinstance(event, SetInstrument):
                self._generate_set_instrument(event)
            elif isinstance(event, SetTempo):
                self._generate_set_tempo(event)
            elif isinstance(event, GoTo):
                self._generate_goto(event)
            elif isinstance(event, Restart):
                self._generate_restart(event)
            elif isinstance(event, SetGateTime):
                self._generate_set_gate_time(event)
            elif isinstance(event, SetPortamento):
                self._generate_set_portamento(event)
            elif isinstance(event, VolumeFade):
                self._generate_volume_fade(event)
            elif isinstance(event, Detune):
                self._generate_detune(event)
            elif isinstance(event, PhaseDelay):
                self._generate_phase_delay(event)
            elif isinstance(event, Chorus):
                self._generate_chorus(event)
            # ... more event types to be added
        return self.z80_code

    def generate_instruments(self, instruments: List[Instrument]) -> str:
        if not instruments:
            return ""

        # Sort instruments by id
        instruments.sort(key=lambda i: i.id)

        code = "; --- Instrument pointer table ---\n"
        code += "INST_TBL:\n"
        for inst in instruments:
            code += f"    DEFW    INS_{inst.name.upper()}\n"
        code += "\n"

        code += "; --- Instrument records (16 bytes each) ---\n"
        for inst in instruments:
            code += f"INS_{inst.name.upper()}:\n"
            # ADSR
            code += f"    DEFB    {inst.adsr[0]}, {inst.adsr[1]}, {inst.adsr[2]}, {inst.adsr[3]}\n"
            # LFO
            code += f"    DEFB    {inst.lfo[0]}, {inst.lfo[1]}, #{inst.lfo[2]:02X}, #{inst.lfo[3]:02X}, {inst.lfo[4]}\n"
            # FLAGS and reserved
            code += f"    DEFB    {inst.flags}, 0, 0, 0, 0, 0, 0\n"
        
        return code

def main():
    parser = MSLParser()
    mml_source = "L4 C @G128 @P10 @F(0,1) @D-5 @PH3 @CH(10,-5)"
    events = parser.parse(mml_source)
    
    generator = CodeGenerator()
    z80_code = generator.generate(events)
    
    print("--- Generated Z80 Code ---")
    print(z80_code)

if __name__ == "__main__":
    main()
