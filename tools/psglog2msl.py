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

# Leading rest suppression threshold: PSG register writes happen sequentially
# within a single ISR frame, so CH_B/C can appear to start 1-2 frames after
# CH_A as a write-order artifact (not real musical offset).  Any leading rest
# whose tick count is at most this value is silently dropped.  96 ticks = L32.
LEAD_REST_SUPPRESS_TICKS = 96

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
    raw_midi = []
    raw_vol  = []
    for regs in frames:
        d = decode_frame(regs)
        cd = d[ch]
        v  = cd['vol']
        if cd['noise_on'] and not cd['tone_on']:
            raw_midi.append('N')
        elif cd['active']:
            freq = period_to_freq(cd['period'], clock)
            raw_midi.append(freq_to_midi(freq))
        else:
            raw_midi.append(None)
            v = 0
        raw_vol.append(v)

    # Suppress short vibrato/glitch artifacts (≤ artifact_window frames)
    # Only applies to pitch changes, not volume.
    merged_midi = raw_midi[:]
    for _ in range(artifact_window):
        for i in range(1, len(merged_midi) - 1):
            if merged_midi[i] != merged_midi[i - 1] and merged_midi[i] != merged_midi[i + 1]:
                merged_midi[i] = merged_midi[i - 1]

    events: list[tuple] = []
    if not merged_midi:
        return []

    cur_midi = merged_midi[0]
    cur_vol  = raw_vol[0]
    start    = 0

    # Threshold for volume-based note split.
    # PSG volume is 0-15. A jump of +3 is a significant attack.
    VOL_JUMP_THRESHOLD = 3

    for i in range(1, len(merged_midi)):
        midi = merged_midi[i]
        vol  = raw_vol[i]

        # Split if pitch changes OR if there's a significant volume jump (attack)
        # after a period of lower volume or silence.
        split = (midi != cur_midi)
        if not split and midi is not None:
            # Same pitch/mode, check for volume attack
            if vol >= cur_vol + VOL_JUMP_THRESHOLD:
                split = True

        if split:
            events.append((cur_midi, start, i - start))
            cur_midi = midi
            start = i

        cur_vol = vol

    events.append((cur_midi, start, len(merged_midi) - start))
    return events


# ---------------------------------------------------------------------------
# Volume envelope extraction (for --adsr)
# ---------------------------------------------------------------------------

def extract_note_data(frames: list[list[int]], ch: int,
                      note_start: int, note_dur: int,
                      rest_dur: int = 0) -> tuple[list[int], list[int]]:
    """Return (volumes, periods) for the note + optional trailing rest."""
    ar = [8, 9, 10][ch]
    pr_lo = [0, 2, 4][ch]
    pr_hi = [1, 3, 5][ch]
    end = min(note_start + note_dur + rest_dur, len(frames))
    vols = [frames[i][ar] & 0x0F for i in range(note_start, end)]
    pers = [((frames[i][pr_hi] & 0x0F) << 8) | frames[i][pr_lo] for i in range(note_start, end)]
    return vols, pers


def analyze_note(vols: list[int], periods: list[int], note_frames: int = None) -> dict:
    """Estimate ATT/DEC/SUS/REL and LFO parameters from PSG data.

    vols/periods may include trailing rest frames; note_frames limits ADSR
    analysis to the actual note duration, preventing rest silence from being
    misclassified as a decay/pluck envelope.
    """
    if not vols:
        return {'ATT': 255, 'DEC': 0, 'SUS': 255, 'REL': 0,
                'LFO_DEST': 0, 'LFO_WAVE': 0, 'LFO_SPEED': 0, 'LFO_AMP': 0, 'PEAK_VOL': 0}

    if note_frames is None:
        note_frames = len(vols)
    note_vols = vols[:note_frames] if note_frames < len(vols) else vols

    peak_i  = max(range(len(note_vols)), key=lambda i: note_vols[i])
    peak    = note_vols[peak_i]

    # Attack: frames from 0 to peak
    attack_fr = peak_i + 1
    ATT = min(255, round(255 / attack_fr)) if attack_fr > 0 else 255

    # Pure decay detection (check note-only frames, not trailing rest)
    tail_after_peak = note_vols[peak_i:]
    if note_vols[-1] == 0:
        decay_frames = next((i for i, v in enumerate(tail_after_peak) if v == 0),
                            len(tail_after_peak))
        decay_frames = max(1, decay_frames)
        DEC = min(255, round(255 / decay_frames))
        SUS, REL = 0, 0
    else:
        # Plateau sustain
        n = len(note_vols)
        mid_start = peak_i + max(1, n // 8)
        mid_end   = max(mid_start + 1, n * 3 // 4)
        plateau   = note_vols[mid_start:mid_end]
        SUS_raw   = round(sum(plateau) / len(plateau)) if plateau else peak
        # Scale so MusaX cur_vol = ch_vol * SUS/255 ≈ SUS_raw when ch_vol = peak.
        # Old formula (SUS_raw/15*255) gave cur_vol = peak*SUS_raw/15 ≠ SUS_raw.
        SUS = min(255, round(SUS_raw * 255 / peak)) if peak > 0 else 255

        # DEC: rate to drain adsr_acc from 255 → SUS over the observed decay window
        decay_frames = max(1, mid_start - peak_i)
        DEC = min(255, round((255 - SUS) / decay_frames)) if SUS < 255 else 0

        tail = note_vols[mid_end:]
        if tail and tail[0] > 0:
            first_zero = next((i for i, v in enumerate(tail) if v == 0), len(tail))
            # REL: rate to drain adsr_acc from SUS → 0 over first_zero frames
            REL = min(255, round(SUS / first_zero)) if first_zero > 0 else 255
        else:
            REL = 0

    # LFO (Vibrato) detection
    # Look for oscillations in periods during the sustain/stable part
    lfo_dest, lfo_wave, lfo_speed, lfo_amp = 0, 0, 0, 0

    if len(periods) >= 8:
        # Use only the stable part of the note (skip initial attack)
        stable_pers = periods[max(1, peak_i):len(periods)-1]
        if len(stable_pers) >= 5:
            avg_p = sum(stable_pers) / len(stable_pers)
            # Find zero crossings (crossings of the average)
            crossings = []
            for i in range(1, len(stable_pers)):
                if (stable_pers[i-1] - avg_p) * (stable_pers[i] - avg_p) < 0:
                    crossings.append(i)
            
            if len(crossings) >= 2:
                # Average distance between crossings * 2 = wavelength in frames
                dists = [crossings[i] - crossings[i-1] for i in range(1, len(crossings))]
                avg_wavelength = (sum(dists) / len(dists)) * 2
                if 2 <= avg_wavelength <= 40:
                    lfo_dest = 1 # Pitch
                    lfo_wave = 0 # Triangle (approx)
                    lfo_speed = min(255, round(256 / avg_wavelength))
                    
                    max_p = max(stable_pers)
                    min_p = min(stable_pers)
                    if min_p > 0 and max_p > min_p:
                        cents_peak = abs(1200 * math.log2(max_p / min_p)) / 2
                        # MusaX LFO amp 1-15: 15 is roughly 1 semitone (100 cents).
                        # Be very sensitive: if peak is > 1.5 cents, give it at least amp 1.
                        lfo_amp = min(15, max(1, round(cents_peak / 100 * 15)))
                    else:
                        lfo_dest = 0 # Not a real oscillation

    return {
        'ATT': ATT, 'DEC': DEC, 'SUS': SUS, 'REL': REL,
        'LFO_DEST': lfo_dest, 'LFO_WAVE': lfo_wave, 'LFO_SPEED': lfo_speed, 'LFO_AMP': lfo_amp,
        'PEAK_VOL': peak
    }


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
# Duration quantizer — Timeline-locked to prevent desync
# ---------------------------------------------------------------------------

def _ticks_to_tokens(ticks: int) -> list[str]:
    """Convert a raw tick count to 1-2 MSL note-value tokens.

    Finds the combination of 1 or 2 notes that minimizes the absolute 
    error relative to 'ticks'. This prevents large overshoots/undershoots
    when a single note doesn't align well with the target.
    """
    res = []
    rem = ticks

    # For very long durations, peel off L1s first
    while rem > (NOTE_TICKS['L1'] + 24):
        res.append('L1')
        rem -= NOTE_TICKS['L1']

    if rem <= 24:
        return res

    best_err = rem
    best_res = []

    # O(N^2) search over the note table (N=13) to find the best 1-2 note fit
    for t1, n1 in NOTE_TABLE:
        # Try single note
        err1 = abs(rem - t1)
        if err1 < best_err:
            best_err = err1
            best_res = [n1]
        
        # Try double note combination
        for t2, n2 in NOTE_TABLE:
            err2 = abs(rem - (t1 + t2))
            if err2 < best_err:
                best_err = err2
                best_res = [n1, n2]

    return res + best_res


class Quantizer:
    """Timeline-locked accumulator to prevent inter-channel desync.

    Maintains a record of total_ticks already emitted as MSL notes,
    and for each new event (from start_frame to end_frame), determines 
    how many additional ticks are needed to reach the absolute target 
    on the timeline.
    """
    def __init__(self, tpf: float, start_frame: int = 0):
        self.tpf = tpf
        self.start_frame = start_frame
        self.elapsed_ticks = 0

    def next_event(self, end_frame: int) -> list[str]:
        target = round((end_frame - self.start_frame) * self.tpf)
        needed = target - self.elapsed_ticks
        toks = _ticks_to_tokens(needed)
        for t in toks:
            self.elapsed_ticks += NOTE_TICKS[t]
        return toks

    def skip_to(self, frame: int):
        """Intentionally skip time (e.g. for leading rest suppression)."""
        self.elapsed_ticks = round((frame - self.start_frame) * self.tpf)

    def add_padding(self, ticks: int):
        """Add exact ticks to the timeline (used for intro equalization)."""
        self.elapsed_ticks += ticks


def _intro_ticks(events: list[tuple], loop_rel: int, tpf: float, start_frame: int) -> int:
    """Count ticks the event loop will emit in the intro (before loop_rel).

    Mirrors leading-rest suppression and straddle-clipping so the result
    matches what psg_to_msl will actually write, allowing exact padding.
    """
    q = Quantizer(tpf, start_frame)
    leading = True
    for note, s, d in events:
        if s >= loop_rel:
            break
        if leading and note is None:
            if (round((s + d) * tpf) - round(s * tpf)) <= LEAD_REST_SUPPRESS_TICKS:
                q.skip_to(s + d)
                continue
        leading = False
        end = min(s + d, loop_rel)
        q.next_event(end)
    return q.elapsed_ticks


# ---------------------------------------------------------------------------
# Gate detection
# ---------------------------------------------------------------------------

def try_gate(note_start: int, total_end: int,
             tpf: float, start_frame: int,
             current_ticks: int, note_dur: int, rest_dur: int) -> tuple[list[str], int] | None:
    """If note+rest total quantizes to a clean value, return (len_tokens, gate_val).

    gate_val is 0-255 (MusaX @GATE parameter).
    Uses absolute positions and the current accumulator to match MSL exactly.
    Returns None if not a clear staccato pattern.
    """
    target_ticks = round((total_end - start_frame) * tpf)
    needed       = target_ticks - current_ticks

    # For gating to be musical, the total should ideally be exactly 1 note token
    # (e.g. L4). If it's a complex duration (e.g. L4+L16), gating is too messy.
    toks = _ticks_to_tokens(needed)
    if not toks or len(toks) > 1:
        return None

    gate_ratio = note_dur / (note_dur + rest_dur)
    gate_val   = round(gate_ratio * 255)

    if 32 <= gate_val <= 223:               # meaningful gate (not just legato/mute)
        return toks, gate_val
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

def detect_loop(frames: list[list[int]], min_len: int = 40,
                search_end: int | None = None) -> int | None:
    """Find the absolute frame index where the song loops back.

    Uses tone-enable + period fingerprints (ignores volume/envelope) so that
    sustain notes and ADSR tails don't prevent a match.  Samples windows from
    the musically active region (up to the last note) and finds the EARLIEST
    earlier occurrence — that earliest position is the loop start.
    Returns None if no musical loop is found (e.g. song plays once and fades).
    """
    if search_end is None:
        search_end = len(frames)
    if search_end < min_len * 4:
        return None

    def _fp(regs: list[int]) -> tuple:
        mx = regs[7]
        return (
            tuple(not bool(mx & (1 << c)) for c in range(3)),
            ((regs[1] & 0xF) << 8) | regs[0],
            ((regs[3] & 0xF) << 8) | regs[2],
            ((regs[5] & 0xF) << 8) | regs[4],
        )

    def _audible(regs: list[int]) -> bool:
        """True if any channel is tone-enabled AND has positive volume."""
        mx = regs[7]
        return any(
            not bool(mx & (1 << c)) and (regs[8 + c] & 0xF) > 0
            for c in range(3)
        )

    fp  = [_fp(f) for f in frames[:search_end]]
    aud = [_audible(f) for f in frames[:search_end]]

    # Find last audible frame — restrict search to this region so we
    # never match silence/idle register state against silence.
    last_active = 0
    for i in range(search_end - 1, -1, -1):
        if aud[i]:
            last_active = i
            break
    if last_active < min_len * 2:
        return None   # too little musical content

    W = min_len
    active_end = last_active + 1   # exclusive
    last_quarter = active_end * 3 // 4
    step = max(1, W // 2)
    best: int | None = None

    for tail_pos in range(last_quarter, active_end - W, step):
        window = tuple(fp[tail_pos:tail_pos + W])
        # Only use tail windows that contain actual audible activity
        if not any(aud[tail_pos + j] for j in range(W)):
            continue
        for anchor in range(0, tail_pos - W):
            if not aud[anchor]:
                continue   # skip silent anchor start frames
            if tuple(fp[anchor:anchor + W]) == window:
                if best is None or anchor < best:
                    best = anchor
                break

    return best


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
               no_inst: bool = False,
               verbose: bool = False) -> str:
    """Convert decoded PSG frames to MSL text."""

    # Trim to requested range
    frames = frames[start_frame:end_frame]

    fpq = fps * 60.0 / bpm
    tpf = TPQN / fpq   # ticks per frame

    if verbose:
        print(f'  BPM={bpm}  fps={fps}  frames/quarter={fpq:.2f}  ticks/frame={tpf:.2f}',
              file=sys.stderr)

    # ---- Note Data Analysis ----
    ch_events = {}
    ch_note_data = {}
    for ch in range(3):
        events = extract_events(frames, ch, clock)
        ch_events[ch] = events
        data_list = []
        for j, (note, s, d) in enumerate(events):
            if note is None or note == 'N':
                data_list.append(None)
                continue
            rest_d = events[j+1][2] if j+1 < len(events) and events[j+1][0] is None else 0
            vols, pers = extract_note_data(frames, ch, s, d, min(rest_d, 10))
            data_list.append(analyze_note(vols, pers, note_frames=d))
        ch_note_data[ch] = data_list

    # ---- Instrument Clustering (Multi-Instrument + LFO) ----
    instruments: list[dict] = []
    if not no_inst:
        # 1. Collect all valid note profiles
        all_profiles = []
        for ch in range(3):
            for d in ch_note_data[ch]:
                if d is not None:
                    all_profiles.append(d)

        def adsr_lfo_dist(a, b):
            # Strict rule: LFO vs No-LFO are different instruments
            if a['LFO_DEST'] != b['LFO_DEST']: return 1000
            
            d = (abs(a['ATT'] - b['ATT']) + abs(a['DEC'] - b['DEC']) + 
                 abs(a['SUS'] - b['SUS']) + abs(a['REL'] - b['REL'])) / 2
            
            if a['LFO_DEST'] > 0:
                d += abs(a['LFO_SPEED'] - b['LFO_SPEED'])
                d += abs(a['LFO_AMP'] - b['LFO_AMP']) * 20
            return d

        # 2. Iterative clustering
        # Threshold for creating a new instrument
        CLUSTER_THRESHOLD = 80

        for prof in all_profiles:
            if not instruments:
                instruments.append(prof.copy())
                continue
                
            # Find best match
            best_match_id = -1
            min_d = 9999
            for i, inst in enumerate(instruments):
                d = adsr_lfo_dist(prof, inst)
                if d < min_d:
                    min_d = d; best_match_id = i
            
            if min_d > CLUSTER_THRESHOLD:
                if len(instruments) < 16:
                    instruments.append(prof.copy())
            else:
                # Refine instrument by moving it slightly towards this new note (moving average)
                inst = instruments[best_match_id]
                for k in ['ATT', 'DEC', 'SUS', 'REL', 'LFO_SPEED', 'LFO_AMP']:
                    inst[k] = round(inst[k] * 0.9 + prof[k] * 0.1)
                # PEAK_VOL is tracked per note, so we don't average it here

        if not instruments:
            instruments.append({'ATT': 255, 'DEC': 10, 'SUS': 200, 'REL': 20, 
                            'LFO_DEST': 0, 'LFO_WAVE': 0, 'LFO_SPEED': 0, 'LFO_AMP': 0})

        def find_inst_id(note_data):
            best_id = 0
            min_d = adsr_lfo_dist(note_data, instruments[0])
            for i in range(1, len(instruments)):
                d = adsr_lfo_dist(note_data, instruments[i])
                if d < min_d:
                    min_d = d; best_id = i
            return best_id

        # ---- ATT clamping: attack must complete within half the shortest note ----
        # Find minimum note duration (frames) per instrument across all channels.
        inst_min_dur = [float('inf')] * len(instruments)
        for ch in range(3):
            for j, (note, s, d) in enumerate(ch_events[ch]):
                if note is None or note == 'N' or ch_note_data[ch][j] is None:
                    continue
                iid = find_inst_id(ch_note_data[ch][j])
                if d < inst_min_dur[iid]:
                    inst_min_dur[iid] = d

        for inst, min_dur in zip(instruments, inst_min_dur):
            if min_dur < float('inf') and min_dur >= 1:
                # Attack must finish in at most half the shortest note using this instrument.
                # ATT rate must be >= ceil(255 / (min_dur / 2)) to complete in time.
                half_dur = max(1, min_dur // 2)
                min_att = math.ceil(255 / half_dur)
                if inst['ATT'] < min_att:
                    inst['ATT'] = min(255, min_att)

    # ---- Header ----
    out_lines: list[str] = []

    if title:  out_lines.append(f'@TITLE  "{title}"')
    if author: out_lines.append(f'@AUTHOR "{author}"')
    if desc:   out_lines.append(f'@DESC   "{desc}"')
    if title or author or desc:
        out_lines.append('')

    # Global instruments (only if NOT in FX mode)
    if not no_inst and not fx_mode:
        for i, inst in enumerate(instruments):
            name = f"Inst{i}" if i > 0 else "Lead"
            out_lines += [
                f'@INST({i}, "{name}") {{',
                f'    ADSR: {inst["ATT"]}, {inst["DEC"]}, {inst["SUS"]}, {inst["REL"]}',
                f'    LFO:  {inst["LFO_DEST"]}, {inst["LFO_WAVE"]}, {inst["LFO_SPEED"]}, {inst["LFO_AMP"]}, 0',
                f'    FLAGS: 0',
                f'}}',
                '',
            ]

    # ---- Channels ----
    ch_names = {'A': 0, 'B': 1, 'C': 2}

    effective_loop_rel: int | None = None
    if not fx_mode and loop_frame is not None:
        lr = loop_frame - start_frame
        if lr >= fpq:
            effective_loop_rel = lr

    ch_intro_ticks: dict[str, int] = {}
    max_intro: int = 0
    if effective_loop_rel is not None:
        for _cc in channels.upper():
            if _cc not in ch_names:
                continue
            _ev = ch_events[ch_names[_cc]]
            if all(e[0] is None for e in _ev):
                continue
            ch_intro_ticks[_cc] = _intro_ticks(_ev, effective_loop_rel, tpf, 0)
        if ch_intro_ticks:
            max_intro = max(ch_intro_ticks.values())

    if fx_mode:
        out_lines.append(f'@FX({fx_name}) {{')
        if not no_inst:
            for i, inst in enumerate(instruments):
                name = f"Inst{i}" if i > 0 else "Lead"
                out_lines += [
                    f'    @INST({i}, "{name}") {{',
                    f'        ADSR: {inst["ATT"]}, {inst["DEC"]}, {inst["SUS"]}, {inst["REL"]}',
                    f'        LFO:  {inst["LFO_DEST"]}, {inst["LFO_WAVE"]}, {inst["LFO_SPEED"]}, {inst["LFO_AMP"]}, 0',
                    f'        FLAGS: 0',
                    f'    }}',
                    '',
                ]

    for ch_char in channels.upper():
        if ch_char not in ch_names:
            continue
        ch = ch_names[ch_char]
        events = ch_events[ch]
        data   = ch_note_data[ch]

        noise_cmts = noise_summary(frames, ch, 0)

        # Skip channel if all silent
        if all(e[0] is None for e in events):
            if fx_mode:
                out_lines.append(f'    // CH_{ch_char}: silent — skipped')
            else:
                out_lines.append(f'// CH_{ch_char}: silent — skipped')
            continue

        # Initial peak volume tracking
        first_note_idx = next((j for j, e in enumerate(events) if e[0] is not None), 0)
        initial_vol = data[first_note_idx]['PEAK_VOL'] if data[first_note_idx] else 11

        if fx_mode:
            out_lines.append(f'    CH_{ch_char}:')
            out_lines.append(f'        @T{round(bpm)}  @V{initial_vol}' + ('  @I0' if not no_inst else ''))
            note_indent = 8
        else:
            out_lines.append(f'CH_{ch_char}:')
            out_lines.append(f'    @T{round(bpm)}  @V{initial_vol}' + ('  @I0' if not no_inst else ''))
            note_indent = 4

        if not fx_mode and effective_loop_rel is None:
            out_lines.append(f'LOOP_{ch_char}:')

        pad = ' ' * note_indent
        for nc in noise_cmts:
            out_lines.append(pad + nc)
        if noise_cmts:
            out_lines.append('')

        quantizer = Quantizer(tpf, 0)
        writer = MslWriter()
        cur_gate: int | None = None
        cur_inst: int | None = 0
        cur_vol: int = initial_vol
        leading = True
        loop_label_done = (effective_loop_rel is None)

        i = 0
        while i < len(events):
            note, s, d = events[i]
            note_info = data[i]

            # Loop label injection
            if not loop_label_done:
                if s >= effective_loop_rel or (s < effective_loop_rel < s + d):
                    out_lines.extend(writer.render(indent=note_indent))
                    writer = MslWriter()
                    
                    if s < effective_loop_rel < s + d:
                        writer.note_or_rest(note, quantizer.next_event(effective_loop_rel))
                        out_lines.extend(writer.render(indent=note_indent))
                        writer = MslWriter()

                    _pad_ticks = max_intro - quantizer.elapsed_ticks
                    if _pad_ticks > 24:
                        _toks = _ticks_to_tokens(_pad_ticks)
                        _pw = MslWriter()
                        _pw.note_or_rest(None, _toks)
                        out_lines.extend(_pw.render(indent=note_indent))
                        for _t in _toks: quantizer.add_padding(NOTE_TICKS[_t])
                    
                    out_lines.append(f'LOOP_{ch_char}:')
                    loop_label_done = True
                    leading = False
                    
                    if s < effective_loop_rel < s + d:
                        writer.note_or_rest(note, quantizer.next_event(s + d))
                        i += 1
                        continue

            # Artifact suppression
            if leading and note is None:
                if (round((s + d) * tpf) - round(s * tpf)) <= LEAD_REST_SUPPRESS_TICKS:
                    quantizer.skip_to(s + d)
                    i += 1
                    continue
            leading = False

            # Noise
            if note == 'N':
                np = frames[s][6] & 0x1F if s < len(frames) else 0
                vl = frames[s][9 + ch] & 0x0F if s < len(frames) else 0
                dur_toks = quantizer.next_event(s + d)
                writer.cmd(f'// [NOISE period={np} vol={vl}]')
                writer.note_or_rest(None, dur_toks)
                i += 1
                continue

            # Instrument and Dynamic Volume Tracking
            if note is not None and note_info:
                if not no_inst:
                    inst_id = find_inst_id(note_info)
                    if inst_id != cur_inst:
                        writer.cmd(f'@I{inst_id}')
                        cur_inst = inst_id
                
                v = note_info['PEAK_VOL']
                if v != cur_vol:
                    writer.cmd(f'@V{v}')
                    cur_vol = v

            # Gating
            if gate_mode and note is not None and i + 1 < len(events):
                next_note, _, next_dur = events[i + 1]
                if next_note is None:
                    g = try_gate(s, s + d + next_dur, tpf, 0,
                                 quantizer.elapsed_ticks, d, next_dur)
                    if g is not None:
                        toks, gate_val = g
                        if gate_val != cur_gate:
                            writer.cmd(f'@GATE {gate_val}')
                            cur_gate = gate_val
                        writer.note_or_rest(note, toks)
                        for t in toks:
                            quantizer.add_padding(NOTE_TICKS[t])
                        i += 2
                        continue

            # Standard Note/Rest
            dur_toks = quantizer.next_event(s + d)
            if note is None:
                if gate_mode and cur_gate is not None and cur_gate != 255:
                    writer.cmd('@GATE 255')
                    cur_gate = 255
            writer.note_or_rest(note, dur_toks)
            i += 1

        out_lines.extend(writer.render(indent=note_indent))

        if not loop_label_done:
            out_lines.append(f'LOOP_{ch_char}:')

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

    # Loop detection
    print()
    loop_fr = detect_loop(frames)
    if loop_fr is not None:
        intro_sec = loop_fr / fps
        body_sec  = (len(frames) - loop_fr) / fps
        print(f'  Loop point   : frame {loop_fr}  ({intro_sec:.2f}s intro + {body_sec:.2f}s loop body)')
        print(f'                 use --loop to embed LOOP_X: label at this position')
    else:
        print('  Loop point   : not detected')

    # Auto-detection hint
    print()
    hint = 'FX (use --fx)' if is_likely_fx(frames, fps, clock) else 'Song (default)'
    print(f'  Auto-detect  : {hint}')


# ---------------------------------------------------------------------------
# PSG audio synthesis + playback
# ---------------------------------------------------------------------------

import array as _array


def _synthesize_iter(frames: list[list[int]], fps: float, clock: float,
                     channels: set[int] | None = None):
    """Generator: yield (regs, pcm_bytes) per PSG frame, maintaining oscillator state.

    Uses square-wave tone generators, a 17-bit LFSR noise generator, and
    the empirical AY-3-8910 volume curve.  Output: 44100 Hz, 16-bit mono.
    channels: set of channel indices to mix {0,1,2}; None means all three.
    """
    active = channels if channels is not None else {0, 1, 2}
    ch_amp      = int(32767 / max(1, len(active)))
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
                if c not in active:
                    continue
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


def synthesize_psg(frames: list[list[int]], fps: float, clock: float,
                   channels: set[int] | None = None) -> bytes:
    """Render PSG register snapshots to 16-bit mono PCM bytes (44100 Hz, mono)."""
    return b''.join(chunk for _, chunk in _synthesize_iter(frames, fps, clock, channels))


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
               show_regs: bool = False,
               channels: set[int] | None = None) -> None:
    """Play PSG frames with progress bar and keyboard control.

    Keys: Space / p = pause/resume    q / Esc / Ctrl+C = quit
    Requires pyaudio for streaming; falls back to aplay/afplay (no interactivity).
    channels: set of channel indices {0,1,2} to mix; None means all three.
    """
    import os, time, tempfile, subprocess as sp, wave as _wave

    active    = channels if channels is not None else {0, 1, 2}
    total     = len(frames)
    total_sec = total / fps

    _CH_LABELS = ['A', 'B', 'C']
    ch_tag = '[' + ''.join(l if i in active else '-' for i, l in enumerate(_CH_LABELS)) + ']'

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
        line   = f'\r[{bar}] {ch_tag} {state}  [p]=pause  [q]=quit'
        if show_regs:
            line += '  │  ' + _reg_display(regs)
        return line

    # ---- pyaudio streaming ----
    try:
        import pyaudio  # type: ignore
        # Suppress ALSA/JACK C-library chatter that PortAudio emits during device enumeration.
        _devnull = os.open(os.devnull, os.O_WRONLY)
        _saved_fd = os.dup(2)
        os.dup2(_devnull, 2)
        try:
            pa = pyaudio.PyAudio()
            st = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, output=True)
        finally:
            os.dup2(_saved_fd, 2)
            os.close(_devnull)
            os.close(_saved_fd)
    except ImportError:
        pa = st = None

    paused   = False
    interval = max(1, round(fps / 10))   # progress refresh: ~10 Hz

    try:
        if pa:
            sys.stderr.write(_progress(0, frames[0] if frames else [0] * 16, False))
            sys.stderr.flush()

            for i, (regs, chunk) in enumerate(_synthesize_iter(frames, fps, clock, active)):
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
            for i, (_, chunk) in enumerate(_synthesize_iter(frames, fps, clock, active)):
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
# CLI helpers
# ---------------------------------------------------------------------------

def parse_play_channels(spec: str) -> set[int]:
    """Parse a channel spec string into a set of indices {0,1,2}.

    Accepts letters A/B/C or digits 1/2/3, in any order/combination.
    Unknown characters are silently ignored.  Returns {0,1,2} for empty input.
    """
    _MAP = {'A': 0, 'B': 1, 'C': 2, '1': 0, '2': 1, '3': 2}
    result = {_MAP[c] for c in spec.upper() if c in _MAP}
    return result if result else {0, 1, 2}


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
    ap.add_argument('--fps',      type=float, default=None,
                    help='Frame rate: 50 or 60 (default: read from file header, else 60)')
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
    ap.add_argument('--play-ch', default='ABC',
                    help='Channels to play with --play: A/B/C or 1/2/3, any combo '
                         '(e.g. A, BC, 13, ABC). Default: ABC (all)')
    ap.add_argument('--regs',     action='store_true',
                    help='Show live PSG register values during --play')
    ap.add_argument('--fx',       action='store_true',
                    help='Output as @FX block (sound effect mode, ends with R0)')
    ap.add_argument('--no-inst',  action='store_true',
                    help='Force no instruments for FX, use only volume/rest commands')
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

    if fps_hint:
        fps = fps_hint
    elif args.fps:
        fps = args.fps
    else:
        fps = 60.0
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

    play_channels = parse_play_channels(args.play_ch)

    # Info mode
    if args.info:
        print_info(frames, fps, args.clock)
        if args.play:
            dur = (args.end - args.start) / fps
            print(f'\nPlaying {args.input}  ({dur:.1f}s)', file=sys.stderr)
            play_audio(frames[args.start:args.end], fps, args.clock,
                       show_regs=args.regs, channels=play_channels)
        return

    # Play mode
    if args.play:
        dur = (args.end - args.start) / fps
        print(f'Playing {args.input}  ({dur:.1f}s)', file=sys.stderr)
        play_audio(frames[args.start:args.end], fps, args.clock,
                   show_regs=args.regs, channels=play_channels)
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
        no_inst    = args.no_inst,
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
