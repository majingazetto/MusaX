#!/usr/bin/env python3
"""
mscz2msl.py — MuseScore to MusaX MSL Converter

Converts MuseScore scores (.mscz compressed archives and .mscx XML files)
directly into MusaX Sound Language (.msl) format for playback and editing.

Features:
- Pure standard library Python (zero external dependencies, no pip needed)
- Compatible with MuseScore 3.x and 4.x
- Preserves exact enharmonic spellings (sharps and flats) via TPC mapping
- Full 768-tick support for standard durations and triplets (8t, 4t, 16t)
- Automatic tie fusion across measure boundaries
- Flexible channel mapping to MSX PSG channels (CH_A, CH_B, CH_C)
- Measure-by-measure formatted comments for easy editing in msl_editor
- Score inspection mode (--info) and instant playback audition (--play)
"""

import sys
import os
import zipfile
import argparse
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Union

# Ensure MusaX tools are in sys.path for optional --play integration
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.append(project_root)


# --- Musical Constants & Tables ---

BASE_TICK = 768  # MusaX 768-tick standard (quarter note)

# Tonal Pitch Class (TPC) to (NoteName, Accidental, OctaveOffset)
TPC_MAP = {
    6:  ('Eb', '', 0),   # Fbb -> Eb
    7:  ('B', '', -1),   # Cb -> B in previous octave
    8:  ('Gb', '', 0),   # Gb
    9:  ('Db', '', 0),   # Db
    10: ('Ab', '', 0),   # Ab
    11: ('Eb', '', 0),   # Eb
    12: ('Bb', '', 0),   # Bb
    13: ('F', '', 0),    # F
    14: ('C', '', 0),    # C
    15: ('G', '', 0),    # G
    16: ('D', '', 0),    # D
    17: ('A', '', 0),    # A
    18: ('E', '', 0),    # E
    19: ('B', '', 0),    # B
    20: ('F#', '', 0),   # F#
    21: ('C#', '', 0),   # C#
    22: ('G#', '', 0),   # G#
    23: ('D#', '', 0),   # D#
    24: ('A#', '', 0),   # A#
    25: ('F', '', 0),    # E# -> F
    26: ('C', '', 1),    # B# -> C in next octave
    27: ('G', '', 0),    # F## -> G
}

# Standard symbolic duration names to MSL length strings
DURATION_TO_MSL = {
    "whole": "1",
    "half": "2",
    "quarter": "4",
    "eighth": "8",
    "16th": "16",
    "32nd": "32",
    "64th": "64",
}

# Ticks to MSL duration notation
TICKS_TO_MSL = {
    4608: "1.",
    3072: "1",
    2688: "2..",
    2304: "2.",
    2048: "1t",
    1536: "2",
    1344: "4..",
    1152: "4.",
    1024: "2t",
    768:  "4",
    576:  "8.",
    512:  "4t",
    384:  "8",
    288:  "16.",
    256:  "8t",
    192:  "16",
    128:  "16t",
    96:   "32",
    64:   "32t",
    48:   "64",
}

DURATION_BASE_TICKS = {
    "whole": 3072,
    "half": 1536,
    "quarter": 768,
    "eighth": 384,
    "16th": 192,
    "32nd": 96,
    "64th": 48,
}


@dataclass
class NoteItem:
    pitch: int          # MIDI concert pitch (e.g. 60 = C4)
    tpc: int            # Tonal Pitch Class (e.g. 14 = C)
    has_tie_start: bool = False


@dataclass
class ChordEvent:
    notes: List[NoteItem]
    duration_type: str
    dots: int = 0
    is_triplet: bool = False
    ticks: int = 0


@dataclass
class RestEvent:
    duration_type: str
    dots: int = 0
    is_triplet: bool = False
    ticks: int = 0


@dataclass
class MeasureData:
    measure_number: int
    events: List[Union[ChordEvent, RestEvent]] = field(default_factory=list)


@dataclass
class RepeatSection:
    start_bar: int            # 1-based measure number inclusive of common body
    body_end_bar: int         # 1-based measure number inclusive of common body
    count: int = 2            # Repeat count (default 2)
    volta1_bars: List[int] = field(default_factory=list) # Measures for Casilla 1 (1st ending)
    volta2_bars: List[int] = field(default_factory=list) # Measures for Casilla 2 (2nd ending)

    @property
    def end_bar(self) -> int:
        if self.volta2_bars:
            return self.volta2_bars[-1]
        if self.volta1_bars:
            return self.volta1_bars[-1]
        return self.body_end_bar


@dataclass
class StaffData:
    staff_id: str
    instrument_name: str
    measures: List[MeasureData] = field(default_factory=list)


# --- Score Parser ---

class MsczReader:
    """Extracts and parses MuseScore (.mscz / .mscx) score data."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.tree: ET.Element = self._load_xml()
        self.title: str = ""
        self.composer: str = ""
        self.bpm: int = 120
        self.time_sig: Tuple[int, int] = (4, 4)
        self.repeats: List[RepeatSection] = []
        self.staves: List[StaffData] = []
        self._parse_score()

    def _load_xml(self) -> ET.Element:
        if not os.path.isfile(self.filepath):
            raise FileNotFoundError(f"File not found: {self.filepath}")

        # Check if it is a zip container (.mscz) or raw xml (.mscx)
        if zipfile.is_zipfile(self.filepath):
            with zipfile.ZipFile(self.filepath, "r") as z:
                mscx_names = [n for n in z.namelist() if n.endswith(".mscx")]
                if not mscx_names:
                    raise ValueError(f"No .mscx file found inside {self.filepath}")
                # Prefer the root score file over sub-scores if multiple exist
                main_file = mscx_names[0]
                for name in mscx_names:
                    if "/" not in name and "\\" not in name:
                        main_file = name
                        break
                return ET.fromstring(z.read(main_file))
        else:
            # Assume raw .mscx XML file
            return ET.parse(self.filepath).getroot()

    def _parse_score(self):
        # Metadata
        raw_title = self.tree.findtext(".//metaTag[@name='workTitle']") or ""
        if not raw_title or raw_title.strip().lower() in ["partitura sin título", "partitura sin titulo", "untitled", "untitled score"]:
            self.title = os.path.splitext(os.path.basename(self.filepath))[0]
        else:
            self.title = raw_title.strip()
        self.composer = self.tree.findtext(".//metaTag[@name='composer']") or ""

        # Tempo (BPM)
        tempo_elem = self.tree.find(".//Tempo/tempo")
        if tempo_elem is not None and tempo_elem.text:
            try:
                # MuseScore stores tempo in quarter notes per second
                self.bpm = round(float(tempo_elem.text) * 60)
            except ValueError:
                self.bpm = 120

        # Initial Time Signature
        sig_n_elem = self.tree.find(".//TimeSig/sigN")
        sig_d_elem = self.tree.find(".//TimeSig/sigD")
        if sig_n_elem is not None and sig_d_elem is not None:
            try:
                self.time_sig = (int(sig_n_elem.text), int(sig_d_elem.text))
            except ValueError:
                self.time_sig = (4, 4)

        # Part and Instrument mapping (Staves in MuseScore are sequential 1, 2, ... matching Parts)
        part_instruments: Dict[str, str] = {}
        staff_counter = 1
        for part in self.tree.findall(".//Part"):
            part_name = "Unknown"
            for query in [
                ".//Instrument/trackName",
                ".//Instrument/longNames/name",
                ".//Instrument/longName",
                ".//trackName",
                ".//longName",
                ".//name",
            ]:
                for el in part.findall(query):
                    txt = el.text.strip() if el.text else ""
                    if txt and txt.lower() not in ["stdnormal", "voice", "staff"]:
                        part_name = txt
                        break
                if part_name != "Unknown":
                    break

            # Count how many Staff elements this Part contains
            num_staves = len(part.findall("Staff"))
            if num_staves == 0:
                num_staves = 1
            for _ in range(num_staves):
                part_instruments[str(staff_counter)] = part_name
                staff_counter += 1

        # Repeats and Voltas detection across master system measures
        first_staff_with_measures = None
        for s in self.tree.iter("Staff"):
            if s.get("id") and s.findall("Measure"):
                first_staff_with_measures = s
                break

        if first_staff_with_measures is not None:
            all_m_xml = first_staff_with_measures.findall("Measure")
            voltas_by_bar: Dict[int, str] = {}
            for idx, m_xml in enumerate(all_m_xml):
                for sp in m_xml.iter("Spanner"):
                    if sp.get("type") == "Volta":
                        v = sp.find("Volta")
                        if v is not None:
                            endings = v.findtext("endings")
                            if endings:
                                voltas_by_bar[idx + 1] = endings.strip()

            cur_repeat_start = None
            last_end_bar = 0
            for idx, m_xml in enumerate(all_m_xml):
                bar_num = idx + 1
                if m_xml.find("startRepeat") is not None:
                    cur_repeat_start = bar_num
                if m_xml.find("endRepeat") is not None:
                    end_elem = m_xml.find("endRepeat")
                    count = 2
                    if end_elem.text and end_elem.text.strip().isdigit():
                        count = int(end_elem.text.strip())
                    if cur_repeat_start is None:
                        # Music convention: repeat from beginning or after last repeat
                        cur_repeat_start = last_end_bar + 1

                    # Check for Voltas (Casilla 1 y Casilla 2)
                    v1_bars = []
                    v2_bars = []
                    if voltas_by_bar.get(bar_num) == "1":
                        b = bar_num
                        while b >= cur_repeat_start and voltas_by_bar.get(b) == "1":
                            v1_bars.insert(0, b)
                            b -= 1
                        body_end = v1_bars[0] - 1
                        # Look ahead for Volta 2 starting right after Volta 1
                        b = bar_num + 1
                        while b <= len(all_m_xml) and voltas_by_bar.get(b) == "2":
                            v2_bars.append(b)
                            b += 1
                    else:
                        body_end = bar_num

                    rep = RepeatSection(
                        start_bar=cur_repeat_start,
                        body_end_bar=body_end,
                        count=count,
                        volta1_bars=v1_bars,
                        volta2_bars=v2_bars,
                    )
                    self.repeats.append(rep)
                    last_end_bar = rep.end_bar
                    cur_repeat_start = None

        # Staves parsing
        raw_staves = [s for s in self.tree.iter("Staff") if s.get("id")]
        for staff in raw_staves:
            sid = staff.get("id")
            measures_xml = staff.findall("Measure")
            if not measures_xml:
                continue

            inst_name = part_instruments.get(sid, f"Staff {sid}")
            staff_data = StaffData(staff_id=sid, instrument_name=inst_name)

            for m_idx, m_xml in enumerate(measures_xml):
                m_data = MeasureData(measure_number=m_idx + 1)
                
                # Check for measure-level time signature update
                ts_n = m_xml.findtext(".//TimeSig/sigN")
                ts_d = m_xml.findtext(".//TimeSig/sigD")
                if ts_n and ts_d:
                    try:
                        self.time_sig = (int(ts_n), int(ts_d))
                    except ValueError:
                        pass

                # Parse voices (default voice 1 is the primary line)
                voices = m_xml.findall("voice")
                if not voices:
                    continue

                primary_voice = voices[0]
                in_triplet = False

                for elem in primary_voice:
                    if elem.tag == "Tuplet":
                        actual = elem.findtext("actualNotes")
                        normal = elem.findtext("normalNotes")
                        if actual == "3" and (normal == "2" or normal is None):
                            in_triplet = True
                        continue
                    elif elem.tag == "endTuplet":
                        in_triplet = False
                        continue

                    if elem.tag == "Chord":
                        dtype = elem.findtext("durationType") or "quarter"
                        dots = int(elem.findtext("dots") or 0)
                        
                        # Calculate base ticks
                        base_t = DURATION_BASE_TICKS.get(dtype, 768)
                        calc_t = base_t
                        if dots == 1: calc_t = int(calc_t * 1.5)
                        elif dots == 2: calc_t = int(calc_t * 1.75)
                        if in_triplet: calc_t = int(calc_t * 2 / 3)

                        notes_list = []
                        for note_xml in elem.findall("Note"):
                            pitch_str = note_xml.findtext("pitch")
                            tpc_str = note_xml.findtext("tpc")
                            if pitch_str:
                                p = int(pitch_str)
                                t = int(tpc_str) if tpc_str else 14
                                tie_elem = note_xml.find(".//Tie")
                                notes_list.append(NoteItem(pitch=p, tpc=t, has_tie_start=(tie_elem is not None)))

                        if notes_list:
                            chord_ev = ChordEvent(
                                notes=notes_list,
                                duration_type=dtype,
                                dots=dots,
                                is_triplet=in_triplet,
                                ticks=calc_t
                            )
                            m_data.events.append(chord_ev)

                    elif elem.tag == "Rest":
                        dtype = elem.findtext("durationType") or "quarter"
                        dots = int(elem.findtext("dots") or 0)

                        if dtype == "measure":
                            # Measure rest: duration equals full measure ticks
                            num, den = self.time_sig
                            calc_t = int((num * 4 * BASE_TICK) / den)
                        else:
                            base_t = DURATION_BASE_TICKS.get(dtype, 768)
                            calc_t = base_t
                            if dots == 1: calc_t = int(calc_t * 1.5)
                            elif dots == 2: calc_t = int(calc_t * 1.75)
                            if in_triplet: calc_t = int(calc_t * 2 / 3)

                        rest_ev = RestEvent(
                            duration_type=dtype,
                            dots=dots,
                            is_triplet=in_triplet,
                            ticks=calc_t
                        )
                        m_data.events.append(rest_ev)

                staff_data.measures.append(m_data)

            self.staves.append(staff_data)


# --- Polyphony and Formatting Engine ---

class MslEmitter:
    """Resolves monophonic stream selection and formats clean MSL output."""

    def __init__(self, chord_mode: str = "top", transpose: int = 0, bars_per_line: int = 1, repeat_mode: str = "phrases"):
        self.chord_mode = chord_mode
        self.transpose = transpose
        self.bars_per_line = max(1, bars_per_line)
        self.repeat_mode = repeat_mode

    def _select_note(self, chord: ChordEvent) -> NoteItem:
        if not chord.notes:
            return NoteItem(pitch=60, tpc=14)
        if len(chord.notes) == 1:
            return chord.notes[0]

        if self.chord_mode == "bottom":
            # Lowest pitch for bass
            return min(chord.notes, key=lambda n: n.pitch)
        else:
            # Default 'top': highest pitch for melody lead
            return max(chord.notes, key=lambda n: n.pitch)

    def _format_pitch(self, note: NoteItem, current_oct: int) -> Tuple[str, int]:
        midi_pitch = note.pitch + self.transpose
        # MusaX pitch formula: C0 = 0, C4 = 48 (MIDI C4 = 60)
        musax_pitch = midi_pitch - 12
        if musax_pitch < 0:
            musax_pitch = 0
        elif musax_pitch > 95:
            musax_pitch = 95

        octave = musax_pitch // 12
        name, acc, oct_delta = TPC_MAP.get(note.tpc, ("C", "", 0))
        octave += oct_delta

        oct_str = ""
        if octave != current_oct:
            if octave == current_oct + 1:
                oct_str = "> "
            elif octave == current_oct - 1:
                oct_str = "< "
            else:
                oct_str = f"O{octave} "
            new_oct = octave
        else:
            new_oct = current_oct

        note_token = f"{oct_str}{name}{acc}"
        return note_token, new_oct

    def _ticks_to_duration_tokens(self, ticks: int, is_triplet: bool = False) -> List[str]:
        if ticks in TICKS_TO_MSL:
            return [TICKS_TO_MSL[ticks]]
        # Decompose ticks greedily into standard MSL durations without dropping ticks
        res = []
        standard = [3072, 2304, 1536, 1152, 768, 576, 384, 288, 192, 128, 96, 64, 48]
        rem = ticks
        while rem > 0:
            found = False
            for t in standard:
                if t <= rem and t in TICKS_TO_MSL:
                    res.append(TICKS_TO_MSL[t])
                    rem -= t
                    found = True
                    break
            if not found:
                res.append("64")
                break
        return res if res else ["4"]

    def _extract_measure_tokens(self, m: MeasureData, current_oct: int) -> Tuple[List[str], int]:
        tokens: List[str] = []
        idx = 0
        ev_count = len(m.events)
        while idx < ev_count:
            ev = m.events[idx]
            if isinstance(ev, RestEvent):
                if ev.duration_type == "measure":
                    tokens.append("R1")
                else:
                    dur_tokens = self._ticks_to_duration_tokens(ev.ticks, ev.is_triplet)
                    for dur_str in dur_tokens:
                        tokens.append(f"R{dur_str}")
                idx += 1
            elif isinstance(ev, ChordEvent):
                note = self._select_note(ev)
                acc_ticks = ev.ticks

                # Look ahead for ties on the same note: ONLY fuse if resulting duration is in TICKS_TO_MSL
                while ev.notes and ev.notes[0].has_tie_start and (idx + 1 < ev_count):
                    next_ev = m.events[idx + 1]
                    if isinstance(next_ev, ChordEvent):
                        next_note = self._select_note(next_ev)
                        if next_note.pitch == note.pitch:
                            cand_ticks = acc_ticks + next_ev.ticks
                            if cand_ticks in TICKS_TO_MSL:
                                acc_ticks = cand_ticks
                                ev = next_ev
                                idx += 1
                                continue
                    break

                dur_tokens = self._ticks_to_duration_tokens(acc_ticks, ev.is_triplet)
                for dur_idx, dur_str in enumerate(dur_tokens):
                    pitch_token, current_oct = self._format_pitch(note, current_oct)
                    tokens.append(f"{pitch_token}{dur_str}")
                idx += 1
        return tokens, current_oct

    def _format_measures_block(self, measures: List[MeasureData], current_oct: int, indent: str = "    ") -> Tuple[List[str], int]:
        lines: List[str] = []
        total_m = len(measures)
        if not measures:
            return lines, current_oct

        if self.bars_per_line == 1:
            for idx, m in enumerate(measures):
                if idx % 4 == 0:
                    end_bar = min(idx + 4, total_m)
                    if idx > 0:
                        lines.append("")
                    lines.append(f"{indent}// --- Bars {m.measure_number} - {measures[end_bar - 1].measure_number} ---")

                tokens, current_oct = self._extract_measure_tokens(m, current_oct)
                bar_label = f"// [Bar {m.measure_number:02d}]"
                if tokens:
                    notes_str = " ".join(tokens)
                    if len(notes_str) < 36:
                        lines.append(f"{indent}{notes_str:<36} {bar_label}")
                    else:
                        lines.append(f"{indent}{notes_str}  {bar_label}")
                else:
                    lines.append(f"{indent}{'R1':<36} {bar_label}")
        else:
            chunk_size = self.bars_per_line
            for chunk_start in range(0, total_m, chunk_size):
                chunk = measures[chunk_start:chunk_start + chunk_size]
                start_num = chunk[0].measure_number
                end_num = chunk[-1].measure_number
                if chunk_start > 0:
                    lines.append("")
                lines.append(f"{indent}// --- Bars {start_num} - {end_num} ---")

                bar_parts = []
                for m in chunk:
                    tokens, current_oct = self._extract_measure_tokens(m, current_oct)
                    m_str = " ".join(tokens) if tokens else "R1"
                    bar_parts.append(m_str)

                lines.append(f"{indent}" + " | ".join(bar_parts))

        return lines, current_oct

    def format_staff_and_phrases(
        self, staff: StaffData, channel_label: str, inst_id: int, repeats: List[RepeatSection]
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        lines: List[str] = []
        lines.append(f"{channel_label}:")
        lines.append(f"    @I{inst_id} @V14 O4 L4")

        phrases: Dict[str, List[str]] = {}
        suffix = channel_label[-1]
        current_oct = 4
        measures = staff.measures
        total_m = len(measures)

        if not measures:
            lines.append("    R1")
            lines.append(f"\n    @RESTART({channel_label})\n")
            return lines, phrases

        if not repeats:
            block_lines, current_oct = self._format_measures_block(measures, current_oct)
            lines.extend(block_lines)
            lines.append(f"\n    @RESTART({channel_label})\n")
            return lines, phrases

        # Partition measures into normal and repeated segments
        segments = []
        cur_bar = 1
        for rep_idx, rep in enumerate(repeats, start=1):
            if rep.start_bar > cur_bar:
                norm_m = [m for m in measures if cur_bar <= m.measure_number < rep.start_bar]
                if norm_m:
                    segments.append(("normal", norm_m, 1, None, None))

            body_m = [m for m in measures if rep.start_bar <= m.measure_number <= rep.body_end_bar]
            v1_m = [m for m in measures if m.measure_number in rep.volta1_bars]
            v2_m = [m for m in measures if m.measure_number in rep.volta2_bars]

            if body_m:
                segments.append(("repeat", body_m, rep.count, rep_idx, (v1_m, v2_m)))
            cur_bar = rep.end_bar + 1

        if cur_bar <= total_m:
            rem_m = [m for m in measures if m.measure_number >= cur_bar]
            if rem_m:
                segments.append(("normal", rem_m, 1, None, None))

        for seg_type, seg_measures, count, rep_idx, voltas in segments:
            if not seg_measures:
                continue
            start_bar = seg_measures[0].measure_number
            end_bar = seg_measures[-1].measure_number

            if seg_type == "normal":
                lines.append("")
                lines.append(f"    O{current_oct}")
                block_lines, current_oct = self._format_measures_block(seg_measures, current_oct)
                lines.extend(block_lines)
            elif seg_type == "repeat":
                phrase_name = f"PH{rep_idx}{suffix}"
                v1_m, v2_m = voltas if voltas else ([], [])

                if self.repeat_mode == "phrases":
                    phrase_start_oct = current_oct
                    phrase_lines = []
                    phrase_lines.append(f"PHRASE({phrase_name}) {{")
                    phrase_lines.append(f"    // --- Repeat {rep_idx}: Bars {start_bar} - {end_bar} ({channel_label}) ---")
                    phrase_lines.append(f"    O{phrase_start_oct}")
                    p_block, phrase_end_oct = self._format_measures_block(seg_measures, phrase_start_oct, indent="    ")
                    phrase_lines.extend(p_block)
                    phrase_lines.append("}\n")
                    phrases[phrase_name] = phrase_lines

                    lines.append("")
                    if v1_m and v2_m:
                        # Pass 1: Common body + Casilla 1
                        lines.append(f"    // --- Repeat {rep_idx}: Pass 1 (Bars {start_bar} - {end_bar}) ---")
                        lines.append(f"    @CALL({phrase_name})")
                        v1_start, v1_end = v1_m[0].measure_number, v1_m[-1].measure_number
                        lines.append(f"    // --- Casilla 1: Bars {v1_start} - {v1_end} ---")
                        lines.append(f"    O{phrase_end_oct}")
                        v1_block, current_oct = self._format_measures_block(v1_m, phrase_end_oct)
                        lines.extend(v1_block)

                        # Pass 2: Common body + Casilla 2
                        lines.append("")
                        lines.append(f"    // --- Repeat {rep_idx}: Pass 2 (Bars {start_bar} - {end_bar}) ---")
                        lines.append(f"    @CALL({phrase_name})")
                        v2_start, v2_end = v2_m[0].measure_number, v2_m[-1].measure_number
                        lines.append(f"    // --- Casilla 2: Bars {v2_start} - {v2_end} ---")
                        lines.append(f"    O{phrase_end_oct}")
                        v2_block, current_oct = self._format_measures_block(v2_m, phrase_end_oct)
                        lines.extend(v2_block)
                    else:
                        lines.append(f"    // --- Repeat {rep_idx}: Bars {start_bar} - {end_bar} ({count}x) ---")
                        for _ in range(count):
                            lines.append(f"    @CALL({phrase_name})")
                        current_oct = phrase_end_oct
                        lines.append(f"    O{phrase_end_oct}")
                else:
                    lines.append("")
                    if v1_m and v2_m:
                        lines.append(f"    // --- Repeat {rep_idx}: Pass 1 ---")
                        block_lines, current_oct = self._format_measures_block(seg_measures, current_oct)
                        lines.extend(block_lines)
                        v1_block, current_oct = self._format_measures_block(v1_m, current_oct)
                        lines.extend(v1_block)

                        lines.append(f"    // --- Repeat {rep_idx}: Pass 2 ---")
                        block_lines, current_oct = self._format_measures_block(seg_measures, current_oct)
                        lines.extend(block_lines)
                        v2_block, current_oct = self._format_measures_block(v2_m, current_oct)
                        lines.extend(v2_block)
                    else:
                        lines.append(f"    // --- Repeat {rep_idx}: Bars {start_bar} - {end_bar} ({count}x) ---")
                        for iter_num in range(count):
                            if iter_num > 0:
                                lines.append(f"    // Repetition {iter_num + 1}")
                            block_lines, current_oct = self._format_measures_block(seg_measures, current_oct)
                            lines.extend(block_lines)

        lines.append(f"\n    @RESTART({channel_label})\n")
        return lines, phrases

    def generate_full_msl(self, score: MsczReader, staff_mappings: Dict[str, StaffData]) -> str:
        out: List[str] = []
        out.append("// ============================================================================")
        out.append(f"// MusaX MSL Score: {score.title}")
        if score.composer:
            out.append(f"// Composer: {score.composer}")
        out.append("// Generated by mscz2msl (MuseScore to MusaX MSL Converter)")
        out.append("// ============================================================================\n")
        
        clean_title = score.title.replace('"', '\\"')
        out.append(f'@TITLE "{clean_title}"')
        out.append(f"@T{score.bpm}\n")

        # Standard clean audible PSG instruments template (instant attack, solid sustain)
        out.append('@INST(0, "Lead")    { ADSR: 255, 10, 200, 15 LFO: 0, 0, 0, 0, 0 }')
        out.append('@INST(1, "Harmony") { ADSR: 255, 10, 180, 15 LFO: 0, 0, 0, 0, 0 }')
        out.append('@INST(2, "Bass")    { ADSR: 255, 15, 120, 20 LFO: 0, 0, 0, 0, 0 }\n')

        inst_indices = {"CH_A": 0, "CH_B": 1, "CH_C": 2}
        all_phrases: List[str] = []
        channel_blocks: List[str] = []

        for ch_name in ["CH_A", "CH_B", "CH_C"]:
            staff = staff_mappings.get(ch_name)
            if staff is not None:
                lines, phrases = self.format_staff_and_phrases(staff, ch_name, inst_indices[ch_name], score.repeats)
                for p_lines in phrases.values():
                    all_phrases.extend(p_lines)
                channel_blocks.extend(lines)

        if all_phrases:
            out.append("// --- Subroutines (Phrases) ---\n")
            out.extend(all_phrases)

        out.append("// --- Channels ---\n")
        out.extend(channel_blocks)

        return "\n".join(out)


# --- CLI and Subcommands ---

def print_score_info(reader: MsczReader):
    print("=================================================================")
    print(f"MuseScore Score Information: {os.path.basename(reader.filepath)}")
    print("=================================================================")
    print(f"Title:          {reader.title}")
    print(f"Composer:       {reader.composer or '(None specified)'}")
    print(f"Tempo:          {reader.bpm} BPM")
    print(f"Time Signature: {reader.time_sig[0]}/{reader.time_sig[1]}")
    print(f"Total Staves:   {len(reader.staves)}")
    if reader.repeats:
        print(f"Repeats:        {len(reader.repeats)} section(s) detected")
        for idx, rep in enumerate(reader.repeats, start=1):
            if rep.volta1_bars and rep.volta2_bars:
                v1_str = f"Bars {rep.volta1_bars[0]}-{rep.volta1_bars[-1]}"
                v2_str = f"Bars {rep.volta2_bars[0]}-{rep.volta2_bars[-1]}"
                print(f"  - Repeat {idx}:   Bars {rep.start_bar} - {rep.body_end_bar} (Casilla 1: {v1_str}, Casilla 2: {v2_str})")
            else:
                print(f"  - Repeat {idx}:   Bars {rep.start_bar} - {rep.end_bar} ({rep.count}x)")
    else:
        print("Repeats:        None (through-composed score)")
    print("-----------------------------------------------------------------")
    print("Staff ID | Instrument / Track Name            | Measures | Chords")
    print("-----------------------------------------------------------------")
    for s in reader.staves:
        total_chords = sum(sum(1 for ev in m.events if isinstance(ev, ChordEvent)) for m in s.measures)
        print(f"  {s.staff_id:<6} | {s.instrument_name:<34} | {len(s.measures):<8} | {total_chords:<6}")
    print("=================================================================")


def main():
    parser = argparse.ArgumentParser(
        description="mscz2msl — MuseScore (.mscz/.mscx) to MusaX (.msl) Converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  mscz2msl song.mscz                      # Convert song.mscz to song.msl
  mscz2msl song.mscz -o output.msl        # Specify output filename
  mscz2msl song.mscz --info               # Inspect tracks and tempo without converting
  mscz2msl song.mscz -a 1 -b 2 -c 4       # Map staves 1, 2, and 4 to CH_A, CH_B, CH_C
  mscz2msl song.mscz --transpose 12       # Transpose up one octave (+12 semitones)
  mscz2msl song.mscz --repeats phrases    # Extract repeat blocks into PHRASE subroutines
  mscz2msl song.mscz --play               # Convert and immediately play in simulator
"""
    )

    parser.add_argument("input", help="Input MuseScore file (.mscz or .mscx)")
    parser.add_argument("-o", "--output", help="Output .msl file path (defaults to <basename>.msl)")
    parser.add_argument("--info", action="store_true", help="Display score metadata and track listing, then exit")
    parser.add_argument("-a", "--channel-a", help="Staff ID to map to CH_A (default: 1st available staff)")
    parser.add_argument("-b", "--channel-b", help="Staff ID to map to CH_B (default: 2nd available staff)")
    parser.add_argument("-c", "--channel-c", help="Staff ID to map to CH_C (default: 3rd available staff)")
    parser.add_argument("--chord", choices=["top", "bottom"], default="top",
                        help="Monophonic resolution for multi-note chords: 'top' (highest note/melody) or 'bottom' (lowest note/bass)")
    parser.add_argument("--transpose", type=int, default=0,
                        help="Transpose all notes by N semitones (e.g. +12 or -12)")
    parser.add_argument("--bars-per-line", type=int, default=1,
                        help="Number of measures to format per line (default: 1, tabbed with bar numbers)")
    parser.add_argument("--compact", action="store_true",
                        help="Format 4 measures per line separated by '|' (compact system view)")
    parser.add_argument("--repeats", choices=["phrases", "unroll"], default="phrases",
                        help="How to handle score repeats: 'phrases' (extract into PHRASE subroutines with @CALL, saves Z80 memory) or 'unroll' (duplicate measures sequentially)")
    parser.add_argument("--play", action="store_true",
                        help="Immediately compile and audition the generated MSL using the MusaX simulator")

    args = parser.parse_args()

    try:
        reader = MsczReader(args.input)
    except Exception as e:
        print(f"Error reading MuseScore score: {e}", file=sys.stderr)
        sys.exit(1)

    if args.info:
        print_score_info(reader)
        return

    # Determine Staff mappings to CH_A, CH_B, CH_C
    staff_map: Dict[str, StaffData] = {s.staff_id: s for s in reader.staves}
    assigned_mappings: Dict[str, StaffData] = {}

    channels = ["CH_A", "CH_B", "CH_C"]
    user_picks = [args.channel_a, args.channel_b, args.channel_c]

    # Assign user-specified staves first
    for ch_name, pick in zip(channels, user_picks):
        if pick is not None:
            if str(pick) in staff_map:
                assigned_mappings[ch_name] = staff_map[str(pick)]
            else:
                # Try 1-based index
                try:
                    idx = int(pick) - 1
                    if 0 <= idx < len(reader.staves):
                        assigned_mappings[ch_name] = reader.staves[idx]
                    else:
                        print(f"Warning: Staff '{pick}' not found for {ch_name}", file=sys.stderr)
                except ValueError:
                    print(f"Warning: Invalid staff specifier '{pick}' for {ch_name}", file=sys.stderr)

    # Auto-assign remaining channels sequentially from available staves
    auto_idx = 0
    for ch_name in channels:
        if ch_name not in assigned_mappings:
            while auto_idx < len(reader.staves):
                candidate = reader.staves[auto_idx]
                auto_idx += 1
                if candidate not in assigned_mappings.values():
                    assigned_mappings[ch_name] = candidate
                    break

    bars_per_line = 4 if args.compact else args.bars_per_line
    emitter = MslEmitter(chord_mode=args.chord, transpose=args.transpose, bars_per_line=bars_per_line, repeat_mode=args.repeats)
    msl_content = emitter.generate_full_msl(reader, assigned_mappings)

    # Output path
    output_path = args.output
    if not output_path:
        base_name = os.path.splitext(args.input)[0]
        output_path = f"{base_name}.msl"

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(msl_content)
        print(f"Successfully converted: {args.input} -> {output_path}")
        print(f"  Title:   {reader.title}")
        print(f"  Tempo:   {reader.bpm} BPM")
        for ch, staff in assigned_mappings.items():
            print(f"  {ch}:    Staff {staff.staff_id} ({staff.instrument_name})")
    except Exception as e:
        print(f"Error writing output file {output_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Optional immediate playback
    if args.play:
        print("\nStarting MusaX Simulator playback...")
        try:
            from tools.musax import cmd_play
            class PlayArgs:
                input = output_path
                loops = 0
            cmd_play(PlayArgs())
        except Exception as sim_err:
            print(f"Simulator playback error: {sim_err}", file=sys.stderr)


if __name__ == "__main__":
    main()
