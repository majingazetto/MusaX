#!/usr/bin/env python3
"""psglog2msl — Convert openMSX PSG capture to MusaX MSL notation.

Reads a .psg file (openMSX format: "PSG\\x1A" magic + reg/val pairs +
0xFF frame separator) and produces an annotated .msl file.

Usage:
    psglog2msl.py INPUT.psg [OUTPUT.msl] [options]
    psglog2msl.py INPUT.psg --info            # analysis only, no output
"""

import argparse
import math
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TPQN        = 768    # MusaX ticks per quarter note
SAMPLE_RATE = 44100  # audio output sample rate

# AY-3-8910 empirical volume curve (index 0-15 → normalized amplitude 0..1)
_AY_VOL = [
    0.000, 0.013, 0.019, 0.027, 0.038, 0.054, 0.076, 0.107,
    0.152, 0.214, 0.303, 0.428, 0.605, 0.856, 1.000, 1.000,
]

# (ticks, name) — MusaX standard note values
NOTE_TABLE = [
    (3072, 'L1'),
    (2304, 'L2.'),
    (1536, 'L2'),
    (1344, 'L4..'),
    (1152, 'L4.'),
    ( 768, 'L4'),
    ( 672, 'L8..'),
    ( 576, 'L8.'),
    ( 384, 'L8'),
    ( 288, 'L16.'),
    ( 192, 'L16'),
    (  96, 'L32'),
    (  48, 'L64'),
]
NOTE_TICKS = {n: t for t, n in NOTE_TABLE}

NOTE_NAMES  = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
ENHARMONICS = {'C#': 'Db', 'D#': 'Eb', 'F#': 'Gb', 'G#': 'Ab', 'A#': 'Bb'}

# Candidate BPMs for auto-detection
BPM_CANDIDATES = [
    50, 60, 66, 70, 75, 80, 84, 90, 96, 100, 105,
    108, 110, 112, 120, 125, 132, 140, 144, 150,
    160, 168, 176, 180, 192, 200, 210, 240,
]

# Quantization tolerance: fractions of a note value that we can round away
QUANT_TOLERANCE_TICKS = 40  # ~L64/1.2 — rounding within this is silent

# AY-3-8910 PSG hardware envelope shapes (R13)
ENVELOPE_SHAPES = {
    0x00: 'decay once (\\)',  0x04: 'decay once (\\)',
    0x08: 'decay once (\\)',  0x0C: 'decay once (\\)',
    0x09: 'decay-attack-..', 0x0A: 'decay \\|\\|',
    0x0B: 'decay\\-max',     0x0D: 'attack-decay-..',
    0x0E: 'attack /|/|',     0x0F: 'attack/-min',
}


# ---------------------------------------------------------------------------
# PSG file parser
# ---------------------------------------------------------------------------

def parse_psg(data: bytes) -> tuple[list[list[int]], float]:
    """Parse openMSX PSG binary.

    Returns (frames, fps_hint) where frames is a list of 16-reg snapshots.
    fps_hint is derived from file version if available, otherwise 0.
    """
    if not data[:3] == b'PSG':
        raise ValueError('Not an openMSX PSG file (missing PSG magic)')

    # Byte 3 = 0x1A.  Bytes 4-7: some versions encode fps or version.
    fps_hint = 0.0
    if len(data) >= 8:
        v = data[4]
        if v in (50, 60):
            fps_hint = float(v)

    pos = 8
    regs = [0] * 16
    frames: list[list[int]] = []

    while pos < len(data):
        b = data[pos]; pos += 1
        if b == 0xFF:
            frames.append(regs[:])
        elif b <= 15 and pos < len(data):
            regs[b] = data[pos]; pos += 1
        # 0xFE and others: skip (some variants use 0xFE for 50Hz tick)
        elif b == 0xFE:
            frames.append(regs[:])

    return frames, fps_hint


# ---------------------------------------------------------------------------
# Frame → musical data
# ---------------------------------------------------------------------------

def decode_frame(regs: list[int]) -> dict:
    """Extract per-channel musical state from a register snapshot."""
    mixer = regs[7]
    result = {}
    for ch, (lo, hi, ar) in enumerate([(0,1,8), (2,3,9), (4,5,10)]):
        period   = ((regs[hi] & 0x0F) << 8) | regs[lo]
        vol_byte = regs[ar]
        tone_on  = not bool(mixer & (1 << ch))
        noise_on = not bool(mixer & (1 << (ch + 3)))
        env_mode = bool(vol_byte & 0x10)
        vol      = vol_byte & 0x0F
        result[ch] = {
            'period':    period,
            'tone_on':   tone_on,
            'noise_on':  noise_on,
            'env_mode':  env_mode,
            'vol':       vol,
            'active':    tone_on and (vol > 0 or env_mode),
        }
    result['noise_period'] = regs[6] & 0x1F
    result['env_period']   = (regs[12] << 8) | regs[11]
    result['env_shape']    = regs[13] & 0x0F
    return result


def period_to_freq(period: int, clock: float) -> float:
    return clock / (16 * period) if period > 0 else 0.0


def freq_to_midi(freq: float) -> int | None:
    if freq <= 0:
        return None
    m = round(69 + 12 * math.log2(freq / 440.0))
    return m if 0 <= m <= 127 else None


def midi_to_name(m: int) -> str:
    return f'{NOTE_NAMES[m % 12]}{m // 12 - 1}'


# ---------------------------------------------------------------------------
# Note event extraction
# ---------------------------------------------------------------------------

def extract_events(frames: list[list[int]], ch: int, clock: float,
                   artifact_window: int = 2) -> list[tuple]:
    """Extract (note_or_none, start_frame, duration) events for one channel.

    note_or_none:
        int  → MIDI note number (tone active)
        None → silent / rest
        'N'  → noise-only (tone off, noise on)
    """
    raw = []
    for regs in frames:
        d = decode_frame(regs)
        cd = d[ch]
        if cd['noise_on'] and not cd['tone_on']:
            raw.append('N')
        elif cd['active']:
            freq = period_to_freq(cd['period'], clock)
            raw.append(freq_to_midi(freq))
        else:
            raw.append(None)

    # Suppress short vibrato/glitch artifacts (≤ artifact_window frames)
    merged = raw[:]
    for _ in range(artifact_window):
        for i in range(1, len(merged) - 1):
            if merged[i] != merged[i - 1] and merged[i] != merged[i + 1]:
                merged[i] = merged[i - 1]

    events: list[tuple] = []
    cur = merged[0]; start = 0
    for i in range(1, len(merged)):
        if merged[i] != cur:
            events.append((cur, start, i - start))
            cur = merged[i]; start = i
    events.append((cur, start, len(merged) - start))
    return events


# ---------------------------------------------------------------------------
# Volume envelope extraction (for --adsr)
# ---------------------------------------------------------------------------

def extract_vol_envelope(frames: list[list[int]], ch: int,
                         note_start: int, note_dur: int,
                         rest_dur: int = 0) -> list[int]:
    """Return per-frame volume (0-15) for the note + optional trailing rest."""
    ar = [8, 9, 10][ch]
    end = min(note_start + note_dur + rest_dur, len(frames))
    return [frames[i][ar] & 0x0F for i in range(note_start, end)]


def analyze_envelope(vols: list[int]) -> dict:
    """Estimate ATT/DEC/SUS/REL from a PSG volume sequence."""
    if not vols:
        return {'ATT': 255, 'DEC': 0, 'SUS': 255, 'REL': 0}

    peak_i  = max(range(len(vols)), key=lambda i: vols[i])
    peak    = vols[peak_i]

    # Attack: frames from 0 to peak
    attack_fr = peak_i + 1
    ATT = min(255, round(255 / attack_fr)) if attack_fr > 0 else 255

    # Find sustain level: stable plateau after peak (middle third of note)
    n = len(vols)
    mid_start = peak_i + max(1, n // 8)
    mid_end   = max(mid_start + 1, n * 3 // 4)
    plateau   = vols[mid_start:mid_end]
    SUS_raw   = round(sum(plateau) / len(plateau)) if plateau else peak
    SUS       = round(SUS_raw / 15 * 255)

    # Decay: frames from peak to sustain
    decay_drop = peak - SUS_raw
    if decay_drop > 0:
        decay_fr = mid_start - peak_i
        DEC = min(255, round((decay_drop / 15 * 255) / max(1, decay_fr)))
    else:
        DEC = 0

    # Release: frames from sustain to 0 (tail of vols)
    tail = vols[mid_end:]
    if tail and tail[0] > 0:
        first_zero = next((i for i, v in enumerate(tail) if v == 0), len(tail))
        if first_zero > 0:
            REL = min(255, round((SUS_raw / 15 * 255) / first_zero))
        else:
            REL = 255
    else:
        REL = 0

    return {'ATT': ATT, 'DEC': DEC, 'SUS': SUS, 'REL': REL}


# ---------------------------------------------------------------------------
# BPM auto-detection
# ---------------------------------------------------------------------------

def score_bpm(events_per_ch: list[list[tuple]], bpm: float,
              fps: float, min_dur: int = 3) -> float:
    """Score how well a BPM quantizes all note durations (higher = better)."""
    fpq = fps * 60.0 / bpm   # frames per quarter
    tpf = TPQN / fpq         # ticks per frame

    total = 0.0; count = 0
    for events in events_per_ch:
        for note, start, dur in events:
            if dur < min_dur:
                continue
            ticks = dur * tpf
            nearest_t, _ = min(NOTE_TABLE, key=lambda x: abs(x[0] - ticks))
            err = abs(ticks - nearest_t) / nearest_t
            total += max(0.0, 1.0 - err * 6)   # penalise >16% error heavily
            count += 1

    return total / count if count else 0.0


def detect_bpm(events_per_ch: list[list[tuple]], fps: float,
               forced_bpm: float | None = None) -> float:
    if forced_bpm:
        return forced_bpm
    best_bpm = 120.0; best_score = -1.0
    for bpm in BPM_CANDIDATES:
        s = score_bpm(events_per_ch, bpm, fps)
        if s > best_score:
            best_score, best_bpm = s, float(bpm)
    return best_bpm


# ---------------------------------------------------------------------------
# Duration quantizer — no L64 artifacts
# ---------------------------------------------------------------------------

def quantize_frames(dur_frames: int, tpf: float) -> list[str]:
    """Convert frame count to 1-2 MSL note-value tokens.

    Rounds aggressively — avoids L64 correction tokens for tiny errors.
    """
    ticks = round(dur_frames * tpf)
    if ticks <= 0:
        return ['L64']

    t1, n1 = min(NOTE_TABLE, key=lambda x: abs(x[0] - ticks))
    remainder = ticks - t1

    if abs(remainder) <= QUANT_TOLERANCE_TICKS:
        return [n1]

    # Try to express remainder as a second token
    t2, n2 = min(NOTE_TABLE, key=lambda x: abs(x[0] - remainder))
    if abs(remainder - t2) <= QUANT_TOLERANCE_TICKS and t2 < t1:
        return [n1, n2]

    return [n1]   # just round — small error


# ---------------------------------------------------------------------------
# Gate detection
# ---------------------------------------------------------------------------

def try_gate(note_dur: int, rest_dur: int, tpf: float) -> tuple[str, int] | None:
    """If note+rest total quantizes to a clean value, return (len_token, gate_val).

    gate_val is 0-255 (MusaX @GATE parameter).
    Returns None if not a clear staccato pattern.
    """
    total_ticks = round((note_dur + rest_dur) * tpf)
    note_ticks  = round(note_dur * tpf)

    t_total, n_total = min(NOTE_TABLE, key=lambda x: abs(x[0] - total_ticks))
    if abs(total_ticks - t_total) > QUANT_TOLERANCE_TICKS:
        return None                          # total doesn't land on a note value

    gate_ratio = note_dur / (note_dur + rest_dur)
    gate_val   = round(gate_ratio * 255)

    if 32 <= gate_val <= 223:               # meaningful gate (not just legato/mute)
        return n_total, gate_val
    return None


# ---------------------------------------------------------------------------
# MSL generator
# ---------------------------------------------------------------------------

class MslWriter:
    def __init__(self):
        self._tokens: list[str] = []
        self._cur_oct  = None
        self._cur_len  = None
        self._col      = 4           # indent

    def _emit(self, tok: str):
        self._tokens.append(tok)

    def note_or_rest(self, midi: int | None, dur_tokens: list[str], *, comment: str = ''):
        """Emit a note or rest with its duration token(s)."""
        if midi is None:
            note_chr = 'R'
            oct_needed = False
        else:
            note_chr   = NOTE_NAMES[midi % 12]
            new_oct    = midi // 12 - 1
            oct_needed = (new_oct != self._cur_oct)
            if oct_needed:
                self._emit(f'O{new_oct}')
                self._cur_oct = new_oct

        for i, tok in enumerate(dur_tokens):
            if tok != self._cur_len:
                self._emit(tok)
                self._cur_len = tok
            self._emit(note_chr if i == 0 else ('R' if midi is None else note_chr))

        if comment:
            self._emit(f'// {comment}')

    def cmd(self, text: str):
        """Emit a raw command token (e.g. @GATE 128)."""
        self._emit(text)

    def comment(self, text: str):
        self._emit(f'// {text}')

    def blank(self):
        self._emit('')

    def render(self, tokens_per_line: int = 8, indent: int = 4) -> list[str]:
        """Format tokens into indented lines."""
        pad = ' ' * indent
        lines = []
        i = 0
        while i < len(self._tokens):
            tok = self._tokens[i]
            if tok == '':
                lines.append('')
                i += 1
                continue
            if tok.startswith('//'):
                lines.append(pad + tok)
                i += 1
                continue
            # Accumulate a line of regular tokens
            chunk = []
            while i < len(self._tokens) and len(chunk) < tokens_per_line:
                t = self._tokens[i]
                if t == '' or t.startswith('//'):
                    break
                chunk.append(t)
                i += 1
            if chunk:
                lines.append(pad + '  '.join(chunk))
        return lines


# ---------------------------------------------------------------------------
# Noise / FX summary
# ---------------------------------------------------------------------------

def noise_summary(frames: list[list[int]], ch: int,
                  start_frame: int) -> list[str]:
    """Return comment lines describing noise sections for a channel."""
    lines = []
    in_noise = False; ns_start = 0; ns_period = 0; ns_vols = []
    ar = [8, 9, 10][ch]

    for i, regs in enumerate(frames):
        d = decode_frame(regs)
        cd = d[ch]
        is_noise = cd['noise_on'] and not cd['tone_on'] and (cd['vol'] > 0 or cd['env_mode'])
        if is_noise and not in_noise:
            in_noise = True; ns_start = i
            ns_period = regs[6] & 0x1F
            ns_vols = []
        if in_noise:
            ns_vols.append(cd['vol'])
        if not is_noise and in_noise:
            in_noise = False
            dur = i - ns_start
            avg_vol = round(sum(ns_vols) / len(ns_vols)) if ns_vols else 0
            lines.append(f'// [NOISE] fr{ns_start}-{i} ({dur}fr) '
                         f'period={ns_period} vol≈{avg_vol}/15')
    return lines


# ---------------------------------------------------------------------------
# Loop point detection
# ---------------------------------------------------------------------------

def detect_loop(frames: list[list[int]], min_len: int = 30,
                search_end: int | None = None) -> int | None:
    """Find the frame where the song loops back to (register state repeat).

    Compares register state of last `min_len` frames against earlier positions.
    Returns the start frame of the loop, or None.
    """
    if search_end is None:
        search_end = len(frames)

    tail_len = min(min_len, search_end // 4)
    if tail_len < 5:
        return None

    tail = [tuple(f[:13]) for f in frames[search_end - tail_len:search_end]]

    for anchor in range(search_end // 2, search_end - tail_len * 2):
        candidate = [tuple(frames[anchor + j][:13]) for j in range(tail_len)]
        if candidate == tail:
            return anchor

    return None


# ---------------------------------------------------------------------------
# FX vs song auto-detection
# ---------------------------------------------------------------------------

def is_likely_fx(frames: list[list[int]], fps: float, clock: float) -> bool:
    """Heuristic: does this PSG capture look more like a sound effect than a song?

    Criteria that suggest FX:
      - Duration < 3 seconds
      - Heavy hardware envelope usage (> 30 % of frames)
      - Only 1 melodic channel active
    """
    if len(frames) / fps < 3.0:
        return True

    env_frames = sum(1 for r in frames if any(r[8 + c] & 0x10 for c in range(3)))
    if env_frames / max(1, len(frames)) > 0.30:
        return True

    active_melodic = sum(
        1 for ch in range(3)
        if any(e[0] is not None and e[0] != 'N'
               for e in extract_events(frames, ch, clock))
    )
    return active_melodic <= 1


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def psg_to_msl(frames: list[list[int]], fps: float, clock: float,
               bpm: float, channels: str,
               gate_mode: bool, adsr_mode: bool,
               start_frame: int, end_frame: int,
               loop_frame: int | None,
               title: str, author: str, desc: str,
               fx_mode: bool = False, fx_name: str = 'FX',
               verbose: bool = False) -> str:
    """Convert decoded PSG frames to MSL text."""

    # Trim to requested range
    frames = frames[start_frame:end_frame]

    fpq = fps * 60.0 / bpm
    tpf = TPQN / fpq   # ticks per frame

    if verbose:
        print(f'  BPM={bpm}  fps={fps}  frames/quarter={fpq:.2f}  ticks/frame={tpf:.2f}',
              file=sys.stderr)

    # ---- ADSR instrument analysis ----
    inst_suggestions: dict[int, dict] = {}
    if adsr_mode:
        all_adsr: list[dict] = []
        for ch in range(3):
            if str(ch) not in ''.join(str(ord(c) - ord('A')) for c in channels):
                pass
            events = extract_events(frames, ch, clock)
            for j, (note, s, d) in enumerate(events):
                if note is None or note == 'N':
                    continue
                rest_d = events[j+1][2] if j+1 < len(events) and events[j+1][0] is None else 0
                vols = extract_vol_envelope(frames, ch, s, d, min(rest_d, 20))
                adsr = analyze_envelope(vols)
                all_adsr.append(adsr)
        if all_adsr:
            avg_att = round(sum(a['ATT'] for a in all_adsr) / len(all_adsr))
            avg_dec = round(sum(a['DEC'] for a in all_adsr) / len(all_adsr))
            avg_sus = round(sum(a['SUS'] for a in all_adsr) / len(all_adsr))
            avg_rel = round(sum(a['REL'] for a in all_adsr) / len(all_adsr))
            inst_suggestions[0] = {'ATT': avg_att, 'DEC': avg_dec,
                                   'SUS': avg_sus, 'REL': avg_rel}
            if verbose:
                print(f'  ADSR suggestion: ATT={avg_att} DEC={avg_dec} '
                      f'SUS={avg_sus} REL={avg_rel}', file=sys.stderr)

    # ---- Header ----
    out_lines: list[str] = []

    if title:  out_lines.append(f'@TITLE  "{title}"')
    if author: out_lines.append(f'@AUTHOR "{author}"')
    if desc:   out_lines.append(f'@DESC   "{desc}"')
    if title or author or desc:
        out_lines.append('')

    if adsr_mode and inst_suggestions:
        ad = inst_suggestions[0]
        out_lines += [
            f'@INST(0, "Lead") {{',
            f'    ADSR: {ad["ATT"]}, {ad["DEC"]}, {ad["SUS"]}, {ad["REL"]}',
            f'    LFO:  0, 0, 0, 0, 0',
            f'    FLAGS: 0',
            f'}}',
            '',
        ]
    else:
        out_lines += [
            '@INST(0, "Lead") {',
            '    ADSR: 255, 10, 200, 20',
            '    LFO:  0, 0, 0, 0, 0',
            '    FLAGS: 0',
            '}',
            '',
            '@INST(1, "Staccato") {',
            '    ADSR: 255, 20, 0, 30',
            '    LFO:  0, 0, 0, 0, 0',
            '    FLAGS: 0',
            '}',
            '',
        ]

    # ---- Channels ----
    ch_names = {'A': 0, 'B': 1, 'C': 2}

    if fx_mode:
        out_lines.append(f'@FX({fx_name}) {{')

    for ch_char in channels.upper():
        if ch_char not in ch_names:
            continue
        ch = ch_names[ch_char]
        vol = 14 - ch * 2          # 14 / 12 / 10 default volumes
        events = extract_events(frames, ch, clock)

        noise_cmts = noise_summary(frames, ch, 0)

        # Skip channel if all silent
        if all(e[0] is None for e in events):
            if fx_mode:
                out_lines.append(f'    // CH_{ch_char}: silent — skipped')
            else:
                out_lines.append(f'// CH_{ch_char}: silent — skipped')
            continue

        if fx_mode:
            out_lines.append(f'    CH_{ch_char}:')
            out_lines.append(f'        @T{round(bpm)}  @V{vol}  @I0')
            note_indent = 8
        else:
            out_lines.append(f'CH_{ch_char}:')
            out_lines.append(f'    @T{round(bpm)}  @V{vol}  @I0')
            if loop_frame is not None:
                lf_rel = loop_frame - start_frame
                out_lines.append(f'LOOP_{ch_char}:   // loop detected at frame {lf_rel}')
            else:
                out_lines.append(f'LOOP_{ch_char}:')
            note_indent = 4

        # Noise comments
        pad = ' ' * note_indent
        for nc in noise_cmts:
            out_lines.append(pad + nc)
        if noise_cmts:
            out_lines.append('')

        writer = MslWriter()
        cur_gate: int | None = None

        i = 0
        while i < len(events):
            note, s, d = events[i]

            # Noise — emit as comment only
            if note == 'N':
                np = frames[s][6] & 0x1F if s < len(frames) else 0
                vl = frames[s][9 + ch] & 0x0F if s < len(frames) else 0
                dur_toks = quantize_frames(d, tpf)
                writer.cmd(f'// [NOISE period={np} vol={vl}]')
                writer.note_or_rest(None, dur_toks)
                i += 1
                continue

            # Check for gate pattern: note followed by rest
            if gate_mode and note is not None and i + 1 < len(events):
                next_note, _, next_dur = events[i + 1]
                if next_note is None:
                    g = try_gate(d, next_dur, tpf)
                    if g is not None:
                        tok, gate_val = g
                        if gate_val != cur_gate:
                            writer.cmd(f'@GATE {gate_val}')
                            cur_gate = gate_val
                        writer.note_or_rest(note, [tok])
                        i += 2
                        continue

            # Normal note or rest
            dur_toks = quantize_frames(d, tpf)
            if note is None:
                if gate_mode and cur_gate is not None and cur_gate != 255:
                    writer.cmd('@GATE 255')
                    cur_gate = 255
            writer.note_or_rest(note, dur_toks)
            i += 1

        rendered = writer.render(indent=note_indent)
        out_lines.extend(rendered)

        if fx_mode:
            out_lines.append(f'{pad}R0')
        else:
            out_lines.append(f'    @RESTART(LOOP_{ch_char})')
        out_lines.append('')

    if fx_mode:
        out_lines.append('}')

    return '\n'.join(out_lines)


# ---------------------------------------------------------------------------
# Info / analysis mode
# ---------------------------------------------------------------------------

def print_info(frames: list[list[int]], fps: float, clock: float):
    total_sec = len(frames) / fps
    print(f'\nPSG Analysis')
    print(f'  Frames     : {len(frames)}')
    print(f'  Duration   : {total_sec:.2f}s  ({total_sec/60:.2f} min)')
    print(f'  Frame rate : {fps} Hz')
    print(f'  PSG clock  : {clock:.0f} Hz')

    # First non-silent frame
    for i, regs in enumerate(frames):
        d = decode_frame(regs)
        if any(d[ch]['active'] for ch in range(3)):
            print(f'  Music start: frame {i}  ({i/fps:.2f}s)')
            break

    # Per-channel note count and range
    print()
    print('  Ch  Events  Notes   Rest%  MIDI-range')
    for ch in range(3):
        events = extract_events(frames, ch, clock)
        notes   = [e for e in events if e[0] is not None and e[0] != 'N']
        rests   = [e for e in events if e[0] is None]
        noises  = [e for e in events if e[0] == 'N']
        note_fr = sum(e[2] for e in notes)
        rest_fr = sum(e[2] for e in rests)
        total   = note_fr + rest_fr + sum(e[2] for e in noises)
        rest_pc = round(rest_fr / total * 100) if total else 0
        midis   = [e[0] for e in notes]
        rng     = f'{midi_to_name(min(midis))}–{midi_to_name(max(midis))}' if midis else '—'
        noise_s = f'  +{len(noises)} noise' if noises else ''
        print(f'  {["A","B","C"][ch]}   {len(events):5d}   {len(notes):5d}   '
              f'{rest_pc:4d}%  {rng}{noise_s}')

    # BPM detection
    print()
    events_all = [extract_events(frames, ch, clock) for ch in range(3)]
    print('  BPM scores (top 5):')
    scores = [(bpm, score_bpm(events_all, bpm, fps)) for bpm in BPM_CANDIDATES]
    scores.sort(key=lambda x: -x[1])
    for bpm, sc in scores[:5]:
        print(f'    {bpm:5.0f} BPM  score={sc:.3f}')

    # Noise usage
    print()
    print('  Noise sections:')
    found_noise = False
    for ch in range(3):
        cmts = noise_summary(frames, ch, 0)
        for c in cmts[:5]:
            print(f'    ch{["A","B","C"][ch]} {c}')
            found_noise = True
    if not found_noise:
        print('    (none)')

    # Envelope mode usage
    print()
    env_frames = sum(
        1 for regs in frames
        if any(regs[8 + ch] & 0x10 for ch in range(3))
    )
    if env_frames:
        shapes = set(regs[13] & 0x0F for regs in frames if any(regs[8+c]&0x10 for c in range(3)))
        descs = [f'{s}={ENVELOPE_SHAPES.get(s, "?")}' for s in sorted(shapes)]
        print(f'  HW envelope: {env_frames} frames  shapes: {", ".join(descs)}')
    else:
        print('  HW envelope: not used')

    # Auto-detection hint
    print()
    hint = 'FX (use --fx)' if is_likely_fx(frames, fps, clock) else 'Song (default)'
    print(f'  Auto-detect  : {hint}')


# ---------------------------------------------------------------------------
# PSG audio synthesis + playback
# ---------------------------------------------------------------------------

import array as _array


def _synthesize_iter(frames: list[list[int]], fps: float, clock: float):
    """Generator: yield (regs, pcm_bytes) per PSG frame, maintaining oscillator state.

    Uses square-wave tone generators, a 17-bit LFSR noise generator, and
    the empirical AY-3-8910 volume curve.  Output: 44100 Hz, 16-bit mono.
    """
    ch_amp      = int(32767 / 3)
    spf         = SAMPLE_RATE / fps
    phase       = [0.0, 0.0, 0.0]
    lfsr        = 0x1FFFF
    noise_phase = 0.0

    for regs in frames:
        mixer        = regs[7]
        periods      = [
            ((regs[1] & 0x0F) << 8) | regs[0],
            ((regs[3] & 0x0F) << 8) | regs[2],
            ((regs[5] & 0x0F) << 8) | regs[4],
        ]
        noise_period = max(1, regs[6] & 0x1F)
        vols         = [regs[8] & 0x0F, regs[9] & 0x0F, regs[10] & 0x0F]
        tone_en      = [not bool(mixer & (1 << c))       for c in range(3)]
        noise_en     = [not bool(mixer & (1 << (c + 3))) for c in range(3)]
        noise_spd    = SAMPLE_RATE * 16 * noise_period / clock

        buf = _array.array('h')
        for _ in range(round(spf)):
            noise_phase += 1.0
            if noise_phase >= noise_spd:
                noise_phase -= noise_spd
                bit  = ((lfsr >> 0) ^ (lfsr >> 3)) & 1
                lfsr = ((lfsr >> 1) | (bit << 16)) & 0x1FFFF
            noise_out = lfsr & 1

            sample = 0
            for c in range(3):
                amp = int(_AY_VOL[vols[c]] * ch_amp)
                p   = periods[c]
                if amp == 0 or (p == 0 and not noise_en[c]):
                    if p > 0:
                        phase[c] = (phase[c] + 1.0) % (SAMPLE_RATE * 16 * p / clock)
                    continue
                if p > 0:
                    period_s = SAMPLE_RATE * 16 * p / clock
                    phase[c] += 1.0
                    if phase[c] >= period_s:
                        phase[c] -= period_s
                    tone_out = 1 if phase[c] < period_s / 2 else 0
                else:
                    tone_out = 0
                out = (tone_en[c] and bool(tone_out)) or (noise_en[c] and bool(noise_out))
                sample += amp if out else -amp
            buf.append(max(-32768, min(32767, sample)))

        yield regs, buf.tobytes()


def synthesize_psg(frames: list[list[int]], fps: float, clock: float) -> bytes:
    """Render PSG register snapshots to 16-bit mono PCM bytes (44100 Hz, mono)."""
    return b''.join(chunk for _, chunk in _synthesize_iter(frames, fps, clock))


def _reg_display(regs: list[int]) -> str:
    """Compact single-line PSG register summary."""
    pa = ((regs[1] & 0x0F) << 8) | regs[0]
    pb = ((regs[3] & 0x0F) << 8) | regs[2]
    pc = ((regs[5] & 0x0F) << 8) | regs[4]
    return (f'A:{pa:03X}/v{regs[8] & 0xF:X}'
            f'  B:{pb:03X}/v{regs[9] & 0xF:X}'
            f'  C:{pc:03X}/v{regs[10] & 0xF:X}'
            f'  NP:{regs[6] & 0x1F:02X}  MX:{regs[7]:02X}')


class _Quit(Exception):
    pass


def play_audio(frames: list[list[int]], fps: float, clock: float,
               show_regs: bool = False) -> None:
    """Play PSG frames with progress bar and keyboard control.

    Keys: Space / p = pause/resume    q / Esc / Ctrl+C = quit
    Requires pyaudio for streaming; falls back to aplay/afplay (no interactivity).
    """
    import os, time, tempfile, subprocess as sp, wave as _wave

    total     = len(frames)
    total_sec = total / fps

    # ---- Unix raw-mode keyboard reader ----
    read_key = lambda: ''
    restore  = lambda: None
    if sys.stdin.isatty():
        try:
            import tty, termios, select as _sel
            _fd  = sys.stdin.fileno()
            _old = termios.tcgetattr(_fd)
            tty.setraw(_fd)
            def read_key():
                if _sel.select([sys.stdin], [], [], 0)[0]:
                    return os.read(_fd, 4).decode('utf-8', errors='replace')
                return ''
            def restore():
                termios.tcsetattr(_fd, termios.TCSADRAIN, _old)
        except Exception:
            pass

    def _progress(i: int, regs: list[int], paused: bool) -> str:
        pct    = i / max(total - 1, 1)
        filled = int(30 * pct)
        bar    = '█' * filled + '░' * (30 - filled)
        state  = 'PAUSED' if paused else f'{i / fps:.1f}/{total_sec:.1f}s'
        line   = f'\r[{bar}] {state}  [p]=pause  [q]=quit'
        if show_regs:
            line += '  │  ' + _reg_display(regs)
        return line

    # ---- pyaudio streaming ----
    try:
        import pyaudio  # type: ignore
        pa = pyaudio.PyAudio()
        st = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, output=True)
    except ImportError:
        pa = st = None

    paused   = False
    interval = max(1, round(fps / 10))   # progress refresh: ~10 Hz

    try:
        if pa:
            sys.stderr.write(_progress(0, frames[0] if frames else [0] * 16, False))
            sys.stderr.flush()

            for i, (regs, chunk) in enumerate(_synthesize_iter(frames, fps, clock)):
                k = read_key()
                if k and k[0] in ('q', 'Q', '\x1b', '\x03'):
                    raise _Quit
                if k and k[0] in (' ', 'p', 'P'):
                    paused = not paused

                while paused:
                    sys.stderr.write(_progress(i, regs, True))
                    sys.stderr.flush()
                    time.sleep(0.05)
                    k = read_key()
                    if k and k[0] in ('q', 'Q', '\x1b', '\x03'):
                        raise _Quit
                    if k and k[0] in (' ', 'p', 'P'):
                        paused = False

                st.write(chunk)

                if i % interval == 0 or i == total - 1:
                    sys.stderr.write(_progress(i, regs, False))
                    sys.stderr.flush()

        else:
            # Fallback: pre-synthesize then hand off to aplay/afplay
            sys.stderr.write('  Synthesizing')
            buf = bytearray()
            for i, (_, chunk) in enumerate(_synthesize_iter(frames, fps, clock)):
                buf.extend(chunk)
                if i % max(1, round(fps)) == 0:
                    sys.stderr.write('.')
                    sys.stderr.flush()
            sys.stderr.write(f'  {total_sec:.1f}s\n')

            tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            tmp_name = tmp.name; tmp.close()
            try:
                with _wave.open(tmp_name, 'wb') as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
                    w.writeframesraw(bytes(buf))
                cmd = (['afplay', tmp_name] if sys.platform == 'darwin'
                       else ['aplay', '-q', '-c', '1', '-r', str(SAMPLE_RATE),
                             '-f', 'S16_LE', tmp_name])
                sp.run(cmd)
            finally:
                os.unlink(tmp_name)

    except _Quit:
        pass
    finally:
        sys.stderr.write('\n')
        sys.stderr.flush()
        restore()
        if st:
            try: st.stop_stream(); st.close()
            except Exception: pass
        if pa:
            try: pa.terminate()
            except Exception: pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        prog='psglog2msl',
        description='Convert openMSX PSG capture to MusaX MSL notation.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
Examples:
  psglog2msl.py stage1.psg stage1.msl
  psglog2msl.py stage1.psg --info           # analysis + auto-detect song/FX
  psglog2msl.py bat.psg --fx                # sound effect → @FX(BAT) block
  psglog2msl.py bat.psg --fx --name BATSND bat.msl
  psglog2msl.py fx.psg fx.msl --fx --gate --adsr --channels A
  psglog2msl.py music.psg out.msl --bpm 150 --fps 50 --title "My Song"
''')
    ap.add_argument('input',  metavar='INPUT.psg')
    ap.add_argument('output', metavar='OUTPUT.msl', nargs='?')

    ap.add_argument('--bpm',      type=float, default=None,
                    help='Force BPM (default: auto-detect)')
    ap.add_argument('--fps',      type=float, default=60.0,
                    help='Frame rate: 50 or 60 (default: 60)')
    ap.add_argument('--clock',    type=float, default=1789772.5,
                    help='PSG clock Hz (default: 1789772.5 NTSC)')
    ap.add_argument('--channels', default='ABC',
                    help='Channels to include, e.g. "AB" (default: ABC)')
    ap.add_argument('--start',    type=int, default=None,
                    help='Start at frame N (default: first non-silent frame)')
    ap.add_argument('--end',      type=int, default=None,
                    help='End at frame N (default: last frame)')
    ap.add_argument('--gate',     action='store_true',
                    help='Use @GATE for staccato note+rest pairs')
    ap.add_argument('--adsr',     action='store_true',
                    help='Analyze volume envelopes and suggest @INST ADSR')
    ap.add_argument('--loop',     action='store_true',
                    help='Try to detect loop point for @RESTART placement')
    ap.add_argument('--info',     action='store_true',
                    help='Print analysis only, do not generate MSL')
    ap.add_argument('--play',     action='store_true',
                    help='Play the PSG log as audio (pyaudio or aplay/afplay)')
    ap.add_argument('--regs',     action='store_true',
                    help='Show live PSG register values during --play')
    ap.add_argument('--fx',       action='store_true',
                    help='Output as @FX block (sound effect mode, ends with R0)')
    ap.add_argument('--name',     default='',
                    help='FX name for @FX(NAME) block (default: stem of input filename)')
    ap.add_argument('--title',    default='',   help='@TITLE metadata')
    ap.add_argument('--author',   default='',   help='@AUTHOR metadata')
    ap.add_argument('--desc',     default='',   help='@DESC metadata')
    ap.add_argument('-v', '--verbose', action='store_true')

    args = ap.parse_args()

    # Read input
    try:
        data = Path(args.input).read_bytes()
    except OSError as e:
        print(f'Error: {e}', file=sys.stderr); sys.exit(1)

    try:
        frames, fps_hint = parse_psg(data)
    except ValueError as e:
        print(f'Error: {e}', file=sys.stderr); sys.exit(1)

    fps = fps_hint if fps_hint and not args.fps else args.fps
    if args.verbose:
        print(f'Loaded {len(frames)} frames from {args.input}', file=sys.stderr)

    # Auto-detect start frame (needed for --play and MSL generation)
    if args.start is None:
        for i, regs in enumerate(frames):
            d = decode_frame(regs)
            if any(d[ch]['active'] for ch in range(3)):
                args.start = i; break
        else:
            args.start = 0
    if args.end is None:
        args.end = len(frames)

    if args.verbose:
        print(f'Range: frames {args.start}–{args.end}', file=sys.stderr)

    # Info mode
    if args.info:
        print_info(frames, fps, args.clock)
        if args.play:
            dur = (args.end - args.start) / fps
            print(f'\nPlaying {args.input}  ({dur:.1f}s)', file=sys.stderr)
            play_audio(frames[args.start:args.end], fps, args.clock,
                       show_regs=args.regs)
        return

    # Play mode
    if args.play:
        dur = (args.end - args.start) / fps
        print(f'Playing {args.input}  ({dur:.1f}s)', file=sys.stderr)
        play_audio(frames[args.start:args.end], fps, args.clock,
                   show_regs=args.regs)
        if not args.output:
            return

    # BPM
    trim_frames = frames[args.start:args.end]
    ev_all = [extract_events(trim_frames, ch, args.clock) for ch in range(3)]
    bpm = detect_bpm(ev_all, fps, args.bpm)
    if args.verbose:
        print(f'BPM: {bpm}', file=sys.stderr)

    # Loop detection
    loop_frame = None
    if args.loop:
        loop_frame = detect_loop(frames, search_end=args.end)
        if loop_frame is not None:
            print(f'Loop point detected at frame {loop_frame}', file=sys.stderr)

    # FX name: explicit --name, or derive from input filename
    fx_name = args.name.upper() if args.name else Path(args.input).stem.upper()

    # Generate MSL
    msl = psg_to_msl(
        frames, fps, args.clock, bpm,
        channels   = args.channels,
        gate_mode  = args.gate,
        adsr_mode  = args.adsr,
        start_frame= args.start,
        end_frame  = args.end,
        loop_frame = loop_frame,
        title      = args.title,
        author     = args.author,
        desc       = args.desc,
        fx_mode    = args.fx,
        fx_name    = fx_name,
        verbose    = args.verbose,
    )

    # Output
    if args.output:
        Path(args.output).write_text(msl, encoding='utf-8')
        print(f'Written {len(msl)} bytes to {args.output}')
    else:
        print(msl)


if __name__ == '__main__':
    main()
