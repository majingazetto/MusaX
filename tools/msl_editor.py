#!/usr/bin/env python3
"""MusaX MSL Editor — syntax-highlighted editor with vi mode and theme support."""

import sys
import os
import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.application import get_app, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style


# ---------------------------------------------------------------------------
# MSL Syntax Highlighting
# ---------------------------------------------------------------------------

_RE_COMMENT   = re.compile(r'//[^\n]*')
_RE_AT_CMD    = re.compile(r'@(?:MODULE|NAMESPACE)\s+[A-Za-z_][A-Za-z0-9_]*|@[A-Za-z0-9_#\-]+(?:\s*(?:\([^)]*\)|"[^"]*"))?')
_RE_INST_KW   = re.compile(r'\b(ADSR|LFO|FLAGS|PHRASE)\b')
_RE_LABEL     = re.compile(r'^[ \t]*(?!ADSR|LFO|FLAGS\b)([A-Z0-9_\.]+):(?!\d)', re.MULTILINE)
_RE_NOTE      = re.compile(r'\b([A-GR])([#\+\-bB]?)(\d*)([\.t]*)', re.IGNORECASE)
_RE_OL        = re.compile(r'\b([OL])(\d+)', re.IGNORECASE)
_RE_OCTSHIFT  = re.compile(r'[<>]')
_RE_LOOP      = re.compile(r'[{}]|\}\s*\d+t?')
_RE_NUMBER    = re.compile(r'\b\d+\b')
_RE_STRING    = re.compile(r'"[^"]*"')

_C = {
    'comment':  'class:comment',
    'at_cmd':   'class:at_cmd',
    'inst_kw':  'class:inst_kw',
    'label':    'class:label',
    'note':     'class:note',
    'ol':       'class:ol',
    'octshift': 'class:octshift',
    'loop':     'class:loop',
    'number':   'class:number',
    'string':   'class:string',
    'default':  'class:default',
}


def _tokenize(text: str) -> StyleAndTextTuples:
    length = len(text)
    styles = [''] * length

    def paint(m: re.Match, cls: str):
        for i in range(m.start(), m.end()):
            styles[i] = cls

    for m in _RE_NUMBER.finditer(text):  paint(m, _C['number'])
    for m in _RE_OCTSHIFT.finditer(text): paint(m, _C['octshift'])
    for m in _RE_LOOP.finditer(text):    paint(m, _C['loop'])
    for m in _RE_OL.finditer(text):      paint(m, _C['ol'])
    for m in _RE_NOTE.finditer(text):
        if m.group(1).upper() in 'ABCDEFGR':
            paint(m, _C['note'])
    for m in _RE_STRING.finditer(text):  paint(m, _C['string'])
    for m in _RE_LABEL.finditer(text):
        for i in range(m.start(1), m.end(0)):
            styles[i] = _C['label']
    for m in _RE_INST_KW.finditer(text): paint(m, _C['inst_kw'])
    for m in _RE_AT_CMD.finditer(text):  paint(m, _C['at_cmd'])
    for m in _RE_COMMENT.finditer(text): paint(m, _C['comment'])

    if not styles:
        return []
    result: StyleAndTextTuples = []
    cur_style, run_start = styles[0], 0
    for i in range(1, length):
        if styles[i] != cur_style:
            result.append((cur_style or _C['default'], text[run_start:i]))
            cur_style, run_start = styles[i], i
    result.append((cur_style or _C['default'], text[run_start:]))
    return result


class MSLLexer(Lexer):
    def lex_document(self, document):
        text   = document.text
        tokens = _tokenize(text)
        lines: list[StyleAndTextTuples] = []
        cur: StyleAndTextTuples = []
        for style, chunk in tokens:
            parts = chunk.split('\n')
            for k, part in enumerate(parts):
                if part: cur.append((style, part))
                if k < len(parts) - 1:
                    lines.append(cur); cur = []
        lines.append(cur)
        def get_line(n): return lines[n] if n < len(lines) else []
        return get_line


# ---------------------------------------------------------------------------
# Z8A Syntax Highlighting (Z80 assembly output)
# ---------------------------------------------------------------------------

_RE_Z8A_COMMENT   = re.compile(r';[^\n]*')
_RE_Z8A_DIRECTIVE = re.compile(r'\b(DEFB|DEFW|DEFS|INCLUDE|ORG|EQU)\b')
_RE_Z8A_LABEL     = re.compile(r'^[ \t]*([A-Z][A-Z0-9_]*):?(?=\s)', re.MULTILINE)
_RE_Z8A_HEX       = re.compile(r'#[0-9A-Fa-f]+')
_RE_Z8A_CONST     = re.compile(r'\b([A-Z][A-Z0-9_]{2,})\b')
_RE_Z8A_NOTE      = re.compile(r'\b([A-G]s?\d)\b')
_RE_Z8A_NUMBER    = re.compile(r'\b\d+\b')


def _tokenize_z8a(text: str) -> StyleAndTextTuples:
    length = len(text)
    styles = [''] * length

    def paint(m: re.Match, cls: str):
        for i in range(m.start(), m.end()):
            styles[i] = cls

    for m in _RE_Z8A_NUMBER.finditer(text):   paint(m, _C['number'])
    for m in _RE_Z8A_CONST.finditer(text):    paint(m, _C['note'])
    for m in _RE_Z8A_NOTE.finditer(text):     paint(m, _C['note'])
    for m in _RE_Z8A_HEX.finditer(text):      paint(m, _C['ol'])
    for m in _RE_Z8A_DIRECTIVE.finditer(text):paint(m, _C['at_cmd'])
    for m in _RE_Z8A_LABEL.finditer(text):
        for i in range(m.start(1), m.end(1)):
            styles[i] = _C['label']
    for m in _RE_Z8A_COMMENT.finditer(text):  paint(m, _C['comment'])

    if not styles:
        return []
    result: StyleAndTextTuples = []
    cur_style, run_start = styles[0], 0
    for i in range(1, length):
        if styles[i] != cur_style:
            result.append((cur_style or _C['default'], text[run_start:i]))
            cur_style, run_start = styles[i], i
    result.append((cur_style or _C['default'], text[run_start:]))
    return result


class Z8ALexer(Lexer):
    def lex_document(self, document):
        text   = document.text
        tokens = _tokenize_z8a(text)
        lines: list[StyleAndTextTuples] = []
        cur: StyleAndTextTuples = []
        for style, chunk in tokens:
            parts = chunk.split('\n')
            for k, part in enumerate(parts):
                if part: cur.append((style, part))
                if k < len(parts) - 1:
                    lines.append(cur); cur = []
        lines.append(cur)
        def get_line(n): return lines[n] if n < len(lines) else []
        return get_line


# ---------------------------------------------------------------------------
# Instrument Data Model
# ---------------------------------------------------------------------------

_LFO_DEST_NAMES = ['Off', 'Pitch', 'Vol']
_LFO_WAVE_NAMES = ['TRI', 'SAW', 'SQR']   # 3 waves only — sim has no wave 3
_LFO_DEST_MAX   = len(_LFO_DEST_NAMES) - 1
_LFO_WAVE_MAX   = len(_LFO_WAVE_NAMES) - 1

_INST_FORM_FIELD_COUNT = 11   # Name + 4 ADSR + 5 LFO + FLAGS

_RE_INST_BLOCK = re.compile(
    r'@INST\s*\(\s*(\d+)\s*,\s*"([^"]*)"\s*\)\s*\{([^}]*)\}',
    re.IGNORECASE | re.DOTALL,
)


class InstrumentData:
    def __init__(self, id: int, name: str, adsr: list, lfo: list, flags: int, source: str = 'song'):
        self.id     = id
        self.name   = name
        self.adsr   = list(adsr)   # [att, dec, sus, rel]
        self.lfo    = list(lfo)    # [dest, wave, speed, amp, delay]
        self.flags  = flags
        self.source = source       # 'bank' | 'song'

    def copy(self) -> 'InstrumentData':
        return InstrumentData(self.id, self.name, self.adsr, self.lfo, self.flags, self.source)


def _inst_make_default(inst_id: int) -> InstrumentData:
    return InstrumentData(inst_id, f'Inst{inst_id}', [255, 10, 200, 20], [0, 0, 0, 0, 0], 0, 'song')


def _inst_get_field(inst: InstrumentData, idx: int):
    if idx == 0:  return inst.name
    if 1 <= idx <= 4: return inst.adsr[idx - 1]
    if 5 <= idx <= 9: return inst.lfo[idx - 5]
    return inst.flags


def _inst_set_field(inst: InstrumentData, idx: int, raw_val: str) -> bool:
    try:
        if idx == 0:
            inst.name = str(raw_val).strip()[:24]
        elif 1 <= idx <= 4:
            inst.adsr[idx - 1] = max(0, min(255, int(raw_val)))
        elif 5 <= idx <= 9:
            if idx == 5:
                inst.lfo[0] = max(0, min(_LFO_DEST_MAX, int(raw_val)))
            elif idx == 6:
                inst.lfo[1] = max(0, min(_LFO_WAVE_MAX, int(raw_val)))
            else:
                inst.lfo[idx - 5] = max(0, min(255, int(raw_val)))
        else:
            inst.flags = max(0, min(255, int(raw_val)))
        return True
    except (ValueError, IndexError):
        return False


def _inst_cycle_field(inst: InstrumentData, idx: int, delta: int):
    if idx == 0:
        return
    if idx == 5:
        inst.lfo[0] = (inst.lfo[0] + delta) % (len(_LFO_DEST_NAMES))
    elif idx == 6:
        inst.lfo[1] = (inst.lfo[1] + delta) % (len(_LFO_WAVE_NAMES))
    elif 1 <= idx <= 4:
        inst.adsr[idx - 1] = max(0, min(255, inst.adsr[idx - 1] + delta))
    elif 7 <= idx <= 9:
        inst.lfo[idx - 5] = max(0, min(255, inst.lfo[idx - 5] + delta))
    else:
        inst.flags = max(0, min(255, inst.flags + delta))


def _parse_inst_blocks(text: str, source: str = 'song') -> dict:
    result = {}
    for m in _RE_INST_BLOCK.finditer(text):
        inst_id   = int(m.group(1))
        inst_name = m.group(2)
        body      = m.group(3)
        adsr  = [0, 0, 0, 0]
        lfo   = [0, 0, 0, 0, 0]
        flags = 0
        am = re.search(r'ADSR:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', body, re.I)
        if am:
            adsr = [int(v) for v in am.groups()]
        lm = re.search(r'LFO:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', body, re.I)
        if lm:
            lfo = [int(v) for v in lm.groups()]
        fm = re.search(r'FLAGS:\s*(\d+)', body, re.I)
        if fm:
            flags = int(fm.group(1))
        result[inst_id] = InstrumentData(inst_id, inst_name, adsr, lfo, flags, source)
    return result


def _format_inst_block(inst: InstrumentData) -> str:
    a, l = inst.adsr, inst.lfo
    return (
        f'@INST({inst.id}, "{inst.name}") {{\n'
        f'    ADSR: {a[0]}, {a[1]}, {a[2]}, {a[3]}\n'
        f'    LFO:  {l[0]}, {l[1]}, {l[2]}, {l[3]}, {l[4]}\n'
        f'    FLAGS: {inst.flags}\n'
        f'}}'
    )


def _update_text_inst(text: str, inst: InstrumentData) -> str:
    pattern = re.compile(
        r'@INST\s*\(\s*' + str(inst.id) + r'\s*,\s*"[^"]*"\s*\)\s*\{[^}]*\}',
        re.IGNORECASE | re.DOTALL,
    )
    block = _format_inst_block(inst)
    if pattern.search(text):
        return pattern.sub(block, text, count=1)
    sep = '\n' if text and not text.endswith('\n') else ''
    return text + sep + block + '\n'


def _remove_text_inst(text: str, inst_id: int) -> str:
    pattern = re.compile(
        r'\n?@INST\s*\(\s*' + str(inst_id) + r'\s*,\s*"[^"]*"\s*\)\s*\{[^}]*\}\n?',
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub('\n', text)


# ---------------------------------------------------------------------------
# Colour Schemes
# ---------------------------------------------------------------------------

# --- Borland (default) ---
BORLAND_STYLE = Style.from_dict({
    'titlebar':              'bg:#00AAAA fg:#000000',
    'titlebar.name':         'bg:#00AAAA fg:#000000 bold',
    'titlebar.mod':          'bg:#00AAAA fg:#AA0000 bold',
    'titlebar.z8a':          'bg:#AA5500 fg:#FFFF55 bold',
    'statusbar':             'bg:#000077 fg:#AAAAAA',
    'build-ok':              'bg:#000077 fg:#55FF55 bold',
    'build-err':             'bg:#000077 fg:#FF5555 bold',
    'vi-insert':             'bg:#000077 fg:#55FF55 bold',
    'vi-normal':             'bg:#000077 fg:#FFAA00 bold',
    'fkeybar':               'bg:#00AAAA fg:#000000',
    'fkey':                  'bg:#FFFFFF fg:#000000 bold',
    'fkey-label':            'bg:#00AAAA fg:#000000',
    'default':               'bg:#0000AA fg:#FFFFFF',
    'comment':               'bg:#0000AA fg:#555555 italic',
    'at_cmd':                'bg:#0000AA fg:#55FFFF bold',
    'inst_kw':               'bg:#0000AA fg:#55FFFF',
    'label':                 'bg:#0000AA fg:#FFFFFF bold',
    'note':                  'bg:#0000AA fg:#FFFF55',
    'ol':                    'bg:#0000AA fg:#55FF55',
    'octshift':              'bg:#0000AA fg:#55FF55 bold',
    'loop':                  'bg:#0000AA fg:#FF5555 bold',
    'number':                'bg:#0000AA fg:#55FF55',
    'string':                'bg:#0000AA fg:#FF55FF',
    'text-area':             'bg:#0000AA fg:#FFFFFF',
    'text-area focused':     'bg:#0000AA fg:#FFFFFF',
    'cursor-line':           'bg:#000077',
    'errorpanel':            'bg:#AA0000 fg:#FFFFFF',
    'errorpanel.selected':   'bg:#FFFF55 fg:#AA0000 bold',
    'secpanel':              'bg:#000055 fg:#AAAAFF',
    'secpanel.selected':     'bg:#55FFFF fg:#000055 bold',
    'section-ind':           'bg:#000077 fg:#55FFFF bold',
})

# --- Retrobox (xterm-256 → hex) ---
# Palette: bg=#1C1C1C fg=#D7D7AF, accent teal=#87AFAF, olive=#AFAF00
# green=#87FF5F, pink=#FF5F87, gold=#FFD75F, orange=#FF875F
RETROBOX_STYLE = Style.from_dict({
    'titlebar':              'bg:#4E4E4E fg:#FFFFD7 bold',
    'titlebar.name':         'bg:#4E4E4E fg:#FFFFD7 bold',
    'titlebar.mod':          'bg:#4E4E4E fg:#FF875F bold',
    'titlebar.z8a':          'bg:#303030 fg:#FFD75F bold',
    'statusbar':             'bg:#121212 fg:#767676',
    'build-ok':              'bg:#121212 fg:#87FF5F bold',
    'build-err':             'bg:#121212 fg:#FF5F87 bold',
    'vi-insert':             'bg:#121212 fg:#87FF5F bold',
    'vi-normal':             'bg:#121212 fg:#FF875F bold',
    'fkeybar':               'bg:#4E4E4E fg:#D7D7AF',
    'fkey':                  'bg:#D7D7AF fg:#1C1C1C bold',
    'fkey-label':            'bg:#4E4E4E fg:#D7D7AF',
    'default':               'bg:#1C1C1C fg:#D7D7AF',
    'comment':               'bg:#1C1C1C fg:#767676 italic',
    'at_cmd':                'bg:#1C1C1C fg:#87AFAF bold',
    'inst_kw':               'bg:#1C1C1C fg:#87AFAF',
    'label':                 'bg:#1C1C1C fg:#FFFFD7 bold',
    'note':                  'bg:#1C1C1C fg:#AFAF00',
    'ol':                    'bg:#1C1C1C fg:#87FF5F bold',
    'octshift':              'bg:#1C1C1C fg:#87FF5F bold',
    'loop':                  'bg:#1C1C1C fg:#FF5F87 bold',
    'number':                'bg:#1C1C1C fg:#FFD75F',
    'string':                'bg:#1C1C1C fg:#87FFAF',
    'text-area':             'bg:#1C1C1C fg:#D7D7AF',
    'text-area focused':     'bg:#1C1C1C fg:#D7D7AF',
    'cursor-line':           'bg:#303030',
    'errorpanel':            'bg:#5F0000 fg:#D7D7AF',
    'errorpanel.selected':   'bg:#FFD75F fg:#1C1C1C bold',
    'secpanel':              'bg:#1C1C1C fg:#87AFAF',
    'secpanel.selected':     'bg:#87AFAF fg:#1C1C1C bold',
    'section-ind':           'bg:#121212 fg:#87AFAF bold',
})

THEMES      = {'borland': BORLAND_STYLE, 'retrobox': RETROBOX_STYLE}
THEME_CYCLE = ['borland', 'retrobox']


# ---------------------------------------------------------------------------
# Editor State
# ---------------------------------------------------------------------------

class EditorState:
    def __init__(self):
        self.filepath:       Path | None = None
        self.modified:       bool        = False
        self.bank_file:      str         = ''
        self.errors:         list[tuple[int, str]] = []
        self.selected_error: int         = 0
        self.show_errors:    bool        = False
        self.build_message:  str         = ''
        self.build_ok:       bool | None = None
        self.mode:           str         = 'msl'   # 'msl' | 'z8a' | 'picker' | 'sections' | 'instr'
        self.vi_mode:        bool        = False
        self.theme:          str         = 'retrobox'
        self.picker_dir:     Path        = Path.cwd()
        self.picker_entries: list        = []
        self.picker_sel:     int         = 0
        self.sections_list:  list        = []
        self.sections_sel:   int         = 0
        # Instrument editor
        self.instr_bank:     dict        = {}   # {id: InstrumentData}
        self.instr_song:     dict        = {}   # {id: InstrumentData}
        self.instr_list_sel: int         = 0
        self.instr_form_sel: int         = 0
        self.instr_focus:    str         = 'list'  # 'list' | 'form'
        self.instr_edit:     object      = None    # InstrumentData working copy


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _get_z8a_path(state: EditorState) -> Path | None:
    return state.filepath.with_suffix('.Z8A') if state.filepath else None


def _vi_indicator(state: EditorState) -> StyleAndTextTuples:
    if not state.vi_mode:
        return []
    try:
        im = get_app().vi_state.input_mode
        if im == InputMode.NAVIGATION:
            return [('class:vi-normal', ' [NORMAL]')]
    except Exception:
        pass
    return [('class:vi-insert', ' [VI]')]


def _title_text(state: EditorState) -> StyleAndTextTuples:
    if state.mode == 'z8a':
        z8a_path = _get_z8a_path(state)
        name = z8a_path.name if z8a_path else 'output.Z8A'
        return [
            ('class:titlebar.z8a', f' MusaX v1.9 ─ Z8A View: {name}'),
            ('class:titlebar', ' [read-only] '),
        ]
    if state.mode == 'picker':
        return [
            ('class:titlebar', ' MusaX v1.9 ─ Open File ─ '),
            ('class:titlebar.name', str(state.picker_dir)),
            ('class:titlebar', ' '),
        ]
    if state.mode == 'sections':
        name = state.filepath.name if state.filepath else 'untitled.msl'
        return [
            ('class:titlebar', ' MusaX v1.9 ─ Sections ─ '),
            ('class:titlebar.name', name),
            ('class:titlebar', ' '),
        ]
    if state.mode == 'instr':
        bank = f' ─ BANK: {state.bank_file}' if state.bank_file else ''
        return [
            ('class:titlebar', ' MusaX Instrument Editor'),
            ('class:titlebar.name', bank),
            ('class:titlebar', ' '),
        ]
    name = state.filepath.name if state.filepath else 'untitled.msl'
    result: StyleAndTextTuples = [
        ('class:titlebar',      ' MusaX v1.9 ─ '),
        ('class:titlebar.name', name),
    ]
    if state.modified:
        result.append(('class:titlebar.mod', ' [modified]'))
    result.append(('class:titlebar', ' '))
    return result


def _status_text(state: EditorState, main_buf: Buffer, z8a_buf: Buffer) -> StyleAndTextTuples:
    if state.mode == 'picker':
        n = len(state.picker_entries)
        return [('class:statusbar', f'  {state.picker_sel + 1}/{n}  [Enter] open  [Esc] cancel ')]

    if state.mode == 'sections':
        n = len(state.sections_list)
        return [('class:statusbar', f'  {state.sections_sel + 1}/{n}  [Enter] jump  [Esc] cancel ')]

    if state.mode == 'instr':
        inst = state.instr_edit
        slot = f'Slot {state.instr_list_sel}'
        panel = f'  [{state.instr_focus.upper()}]'
        name  = f'  {inst.name}' if inst else ''
        return [('class:statusbar', f'  {slot}{name}{panel}  Tab=switch panel ')]

    buf = z8a_buf if state.mode == 'z8a' else main_buf
    doc = buf.document
    ln  = doc.cursor_position_row + 1
    col = doc.cursor_position_col + 1
    bank = f'  BANK: {state.bank_file}' if state.bank_file and state.mode == 'msl' else ''
    pos  = f' Ln {ln:3d}  Col {col:3d}{bank}  '

    if state.mode == 'z8a':
        return [('class:statusbar', pos + '[Z8A read-only ─ Esc or F4 to return] ')]

    result: StyleAndTextTuples = [('class:statusbar', pos)]

    sec = _section_at_cursor(main_buf.text, doc.cursor_position_row)
    if sec:
        kind, name = sec
        label = name if kind == 'ch' else (f'FX:{name}' if kind == 'fx' else f'>{name}')
        result.append(('class:section-ind', f'[{label}] '))

    result += _vi_indicator(state)

    if state.build_ok is True:
        result.append(('class:build-ok', f'  [OK] {state.build_message} '))
    elif state.build_ok is False:
        result.append(('class:build-err', f'  [{len(state.errors)} error(s)] {state.build_message} '))

    return result


def _fkey_bar(state: EditorState) -> StyleAndTextTuples:
    # Fixed-width cells: key in 4-char field + label in 7-char field.
    # Each cell = 6 + 10 = 16 chars → 5 cells = 80 chars, exact fit for 80-col terminals.
    def _row_fixed(keys: list) -> StyleAndTextTuples:
        row: StyleAndTextTuples = []
        for k, label in keys:
            row.append(('class:fkey',       f' {k:<4} '))
            row.append(('class:fkey-label', f' {label:<7}  '))
        row.append(('class:fkeybar', '\n'))
        return row

    def _row_var(keys: list) -> StyleAndTextTuples:
        row: StyleAndTextTuples = []
        for k, label in keys:
            row.append(('class:fkey',       f' {k} '))
            row.append(('class:fkey-label', f' {label}  '))
        row.append(('class:fkeybar', '\n'))
        return row

    if state.mode == 'picker':
        return _row_var([('↑↓', 'Navigate'), ('Enter', 'Open'), ('Esc', 'Cancel')])
    if state.mode == 'sections':
        return _row_var([('↑↓', 'Navigate'), ('Enter', 'Jump'), ('Esc', 'Cancel')])
    if state.mode == 'instr':
        row1 = [('F2', 'Save'), ('F4', 'ToBank'), ('F5', 'ToSong'), ('Del', 'Delete'), ('F6', 'Try!')]
        return _row_fixed(row1) + _row_fixed([('Tab', 'Focus'), ('↑↓', 'Move'), ('←→', '±value'), ('Ret', 'Name'), ('Esc', 'Back')])
    if state.mode == 'z8a':
        return _row_var([('F2/^S', 'Save Z8A'), ('Esc', 'Back'), ('F4', 'Back')])

    vi_label = 'VI:ON' if state.vi_mode else 'VI:off'
    row1 = [('F2', 'Save'),  ('F3',  'Open'),  ('F4', 'Z8A'),  ('F5', vi_label), ('F6', 'Instr')]
    row2 = [('F9', 'Build'), ('F10', 'Play'),  ('^G', 'Sect'), ('^T', 'Theme'),  ('^Q', 'Quit')]
    return _row_fixed(row1) + _row_fixed(row2)


def _error_lines(state: EditorState) -> StyleAndTextTuples:
    if not state.errors:
        return [('class:errorpanel', '  (no errors)')]
    result: StyleAndTextTuples = []
    for i, (ln, msg) in enumerate(state.errors[:5]):
        cls    = 'class:errorpanel.selected' if i == state.selected_error else 'class:errorpanel'
        prefix = '▸ ' if i == state.selected_error else '  '
        result.append((cls, f'{prefix}Line {ln}: {msg}\n'))
    return result


# ---------------------------------------------------------------------------
# File Operations
# ---------------------------------------------------------------------------

def _extract_bank(text: str) -> str:
    m = re.search(r'@BANK\s+"([^"]+)"', text)
    return m.group(1) if m else ''


def _do_save(state: EditorState, buf: Buffer) -> bool:
    if state.filepath is None:
        return False
    try:
        state.filepath.write_text(buf.text, encoding='utf-8')
        state.modified = False
        return True
    except OSError:
        return False


def _do_load(state: EditorState, buf: Buffer, path: Path) -> bool:
    try:
        text = path.read_text(encoding='utf-8')
        buf.set_document(Document(text, 0), bypass_readonly=True)
        state.filepath  = path
        state.modified  = False
        state.bank_file = _extract_bank(text)
        return True
    except OSError:
        return False


def _picker_refresh(state: EditorState, new_dir: Path | None = None):
    if new_dir is not None:
        state.picker_dir = new_dir
    d = state.picker_dir
    try:
        all_items = list(d.iterdir())
    except PermissionError:
        all_items = []
    dirs  = sorted([p for p in all_items if p.is_dir()], key=lambda p: p.name.lower())
    files = sorted([p for p in all_items if p.is_file() and p.suffix.lower() == '.msl'],
                   key=lambda p: p.name.lower())
    parent = [d.parent] if d.parent != d else []
    state.picker_entries = parent + dirs + files
    state.picker_sel     = 0


def _picker_text(state: EditorState) -> StyleAndTextTuples:
    entries = state.picker_entries
    sel     = state.picker_sel
    n       = len(entries)
    try:
        rows = get_app().output.get_size().rows - 4
    except Exception:
        rows = 20
    rows = max(5, rows)

    start = max(0, min(sel - rows // 2, n - rows))
    end   = min(start + rows, n)

    result: StyleAndTextTuples = []
    if not entries:
        result.append(('class:comment', '  (no .msl files here)\n'))
        return result
    for i in range(start, end):
        p         = entries[i]
        is_parent = (n > 0 and p == state.picker_dir.parent)
        if is_parent:
            label = '../'
        elif p.is_dir():
            label = f'{p.name}/'
        else:
            label = p.name
        if i == sel:
            style = 'class:errorpanel.selected'
        elif is_parent or p.is_dir():
            style = 'class:ol'
        else:
            style = 'class:note'
        result.append((style, f'  {label}\n'))
    return result


# ---------------------------------------------------------------------------
# Section Navigation
# ---------------------------------------------------------------------------

_RE_SECTION_HDR = re.compile(
    r'^(?:(CH_[ABC])\s*:|@FX\s*\(\s*(\w+)\s*\)|PHRASE\s*\(\s*(\w+)\s*\))',
    re.MULTILINE,
)

_SECTION_TAG = {'ch': 'CH', 'fx': 'FX', 'phrase': 'PH'}
_SECTION_STYLE = {
    'ch':     'class:ol',
    'fx':     'class:at_cmd',
    'phrase': 'class:label',
}


def _get_sections(text: str) -> list[tuple[int, str, str]]:
    result = []
    for m in _RE_SECTION_HDR.finditer(text):
        line = text[:m.start()].count('\n')
        if m.group(1):
            result.append((line, 'ch', m.group(1)))
        elif m.group(2):
            result.append((line, 'fx', m.group(2)))
        elif m.group(3):
            result.append((line, 'phrase', m.group(3)))
    return result


def _section_at_cursor(text: str, cursor_row: int) -> tuple[str, str] | None:
    current = None
    for ln, kind, name in _get_sections(text):
        if ln <= cursor_row:
            current = (kind, name)
        else:
            break
    return current


def _sections_text(state: EditorState) -> StyleAndTextTuples:
    entries = state.sections_list
    if not entries:
        return [('class:comment', '  (no sections found)\n')]
    result: StyleAndTextTuples = []
    sel = state.sections_sel
    for i, (ln, kind, name) in enumerate(entries):
        tag    = _SECTION_TAG.get(kind, '  ')
        prefix = '▸ ' if i == sel else '  '
        if i == sel:
            style = 'class:secpanel.selected'
        else:
            style = _SECTION_STYLE.get(kind, 'class:secpanel')
        result.append((style, f'{prefix}[{tag}] {name}  (line {ln + 1})\n'))
    return result


# ---------------------------------------------------------------------------
# Instrument Editor UI
# ---------------------------------------------------------------------------

def _instr_load_edit(state: EditorState):
    i = state.instr_list_sel
    if i in state.instr_song:
        state.instr_edit = state.instr_song[i].copy()
        state.instr_edit.source = 'song'
    elif i in state.instr_bank:
        state.instr_edit = state.instr_bank[i].copy()
        state.instr_edit.source = 'bank'
    else:
        state.instr_edit = _inst_make_default(i)


def _instr_load_bank(state: EditorState):
    if state.bank_file:
        bank_path = state.filepath.parent / state.bank_file if state.filepath else Path(state.bank_file)
        try:
            text = bank_path.read_text(encoding='utf-8')
            state.instr_bank = _parse_inst_blocks(text, 'bank')
        except OSError:
            state.instr_bank = {}
    else:
        state.instr_bank = {}


def _inst_list_text(state: EditorState) -> StyleAndTextTuples:
    result: StyleAndTextTuples = []
    focused = state.instr_focus == 'list'
    sel = state.instr_list_sel
    for i in range(16):
        in_bank = i in state.instr_bank
        in_song = i in state.instr_song
        if in_bank and in_song:
            tag  = '[SONG]*'
            name = state.instr_song[i].name
            tag_style = 'class:loop'
        elif in_song:
            tag  = '[SONG] '
            name = state.instr_song[i].name
            tag_style = 'class:note'
        elif in_bank:
            tag  = '[BANK] '
            name = state.instr_bank[i].name
            tag_style = 'class:at_cmd'
        else:
            tag  = '       '
            name = '(empty)'
            tag_style = 'class:comment'
        if focused and i == sel:
            row_style = 'class:secpanel.selected'
            prefix    = '▸'
        elif not focused and i == sel:
            row_style = 'class:label'
            prefix    = '▸'
        else:
            row_style = 'class:comment' if not (in_bank or in_song) else 'class:default'
            prefix    = ' '
        result.append((row_style, f'{prefix}{i:>2}  {name:<16}  '))
        result.append((tag_style if not (focused and i == sel) else row_style, f'{tag}\n'))
    return result


def _inst_form_text(state: EditorState) -> StyleAndTextTuples:
    inst = state.instr_edit
    if inst is None:
        return [('class:comment', '  (no instrument)\n')]
    result: StyleAndTextTuples = []
    focused = state.instr_focus == 'form'
    sel     = state.instr_form_sel

    W = 11   # label column width — colon always at the same column

    def field_row(idx: int, label: str, value: str):
        if focused and idx == sel:
            sty = 'class:secpanel.selected'
            pfx = '▸ '
        elif not focused and idx == sel:
            sty = 'class:label'
            pfx = '▸ '
        else:
            sty = 'class:default'
            pfx = '  '
        result.append((sty, f'{pfx}{label:<{W}}: {value}\n'))

    def grp(title: str):
        result.append(('class:comment', f'  ── {title} {"─" * (W - len(title) - 1)}\n'))

    field_row(0, 'Name', inst.name[:30])
    a = inst.adsr
    grp('ADSR')
    field_row(1, 'Att', f'{a[0]:>3}')
    field_row(2, 'Dec', f'{a[1]:>3}')
    field_row(3, 'Sus', f'{a[2]:>3}')
    field_row(4, 'Rel', f'{a[3]:>3}')
    l = inst.lfo
    dest_name = _LFO_DEST_NAMES[l[0]] if 0 <= l[0] <= _LFO_DEST_MAX else str(l[0])
    wave_name = _LFO_WAVE_NAMES[l[1]] if 0 <= l[1] <= _LFO_WAVE_MAX else str(l[1])
    grp('LFO')
    field_row(5, 'Dest',  dest_name)
    field_row(6, 'Wave',  wave_name)
    field_row(7, 'Speed', f'{l[2]:>3}')
    field_row(8, 'Amp',   f'{l[3]:>3}')
    field_row(9, 'Delay', f'{l[4]:>3}')
    grp('FLAGS')
    field_row(10, 'FLAGS', f'{inst.flags}')

    src_label = 'SONG' if inst.source == 'song' else 'BANK'
    result.append(('class:comment', f'\n  [{src_label}]  ←→/±=change  Ret=name\n'))
    return result


# ---------------------------------------------------------------------------
# Build the Application
# ---------------------------------------------------------------------------

def build_app(initial_file: Path | None = None) -> Application:
    state = EditorState()

    def _on_text_changed(_buf):
        state.modified      = True
        state.bank_file     = _extract_bank(main_buf.text)
        state.build_ok      = None
        state.build_message = ''

    main_buf = Buffer(name='main', multiline=True, on_text_changed=_on_text_changed)
    # Z8A viewer buffer — read_only=True prevents user edits; bypass_readonly=True
    # is still available for programmatic content updates.
    z8a_buf  = Buffer(name='z8a',  multiline=True, read_only=True)

    if initial_file and initial_file.exists():
        _do_load(state, main_buf, initial_file)
    elif initial_file:
        state.filepath = initial_file

    async def _ask_save_path() -> bool:
        """Prompt for a file path when none is set. Returns True if path was acquired."""
        def _ask():
            try:
                ans = input('Save as: ').strip()
                return ans if ans else None
            except (KeyboardInterrupt, EOFError):
                return None
        path_str = await run_in_terminal(_ask)
        if path_str:
            p = Path(path_str).expanduser()
            if not p.suffix:
                p = p.with_suffix('.msl')
            state.filepath = p
            return True
        return False

    # --- Widgets ---

    title_bar = Window(
        content=FormattedTextControl(lambda: _title_text(state)),
        height=1, style='class:titlebar',
    )
    status_bar = Window(
        content=FormattedTextControl(lambda: _status_text(state, main_buf, z8a_buf)),
        height=1, style='class:statusbar',
    )
    fkey_bar = Window(
        content=FormattedTextControl(lambda: _fkey_bar(state)),
        height=2, style='class:fkeybar',
    )
    error_panel = ConditionalContainer(
        content=Window(
            content=FormattedTextControl(lambda: _error_lines(state)),
            height=5, style='class:errorpanel',
        ),
        filter=Condition(lambda: state.show_errors and bool(state.errors) and state.mode == 'msl'),
    )

    msl_window = Window(
        content=BufferControl(buffer=main_buf, lexer=MSLLexer(), focusable=True),
        style='class:default',
    )
    msl_body = ConditionalContainer(content=msl_window, filter=Condition(lambda: state.mode == 'msl'))

    z8a_window = Window(
        content=BufferControl(buffer=z8a_buf, lexer=Z8ALexer(), focusable=True),
        style='class:default',
    )
    z8a_body = ConditionalContainer(content=z8a_window, filter=Condition(lambda: state.mode == 'z8a'))

    picker_window = Window(
        content=FormattedTextControl(lambda: _picker_text(state), focusable=True),
        style='class:default',
    )
    picker_body = ConditionalContainer(content=picker_window, filter=Condition(lambda: state.mode == 'picker'))

    sections_window = Window(
        content=FormattedTextControl(lambda: _sections_text(state), focusable=True),
        style='class:secpanel',
    )
    sections_body = ConditionalContainer(content=sections_window, filter=Condition(lambda: state.mode == 'sections'))

    instr_list_window = Window(
        content=FormattedTextControl(lambda: _inst_list_text(state), focusable=True),
        width=32, style='class:default',
    )
    instr_form_window = Window(
        content=FormattedTextControl(lambda: _inst_form_text(state), focusable=True),
        style='class:default',
    )
    instr_body = ConditionalContainer(
        content=VSplit([instr_list_window, instr_form_window]),
        filter=Condition(lambda: state.mode == 'instr'),
    )

    layout = Layout(
        HSplit([title_bar, msl_body, z8a_body, picker_body, sections_body, instr_body, error_panel, status_bar, fkey_bar]),
        focused_element=msl_window,
    )

    # --- Key Bindings ---

    kb = KeyBindings()

    @kb.add('f2')
    @kb.add('c-s')
    async def _save_or_export(event):
        if state.mode == 'z8a':
            z8a_src = _get_z8a_path(state)
            if z8a_src is None or not z8a_src.exists():
                return
            def _ask_dest():
                try:
                    ans = input(f'Save Z8A as [{z8a_src}]: ').strip()
                    return ans if ans else str(z8a_src)
                except (KeyboardInterrupt, EOFError):
                    return None
            dest_str = await run_in_terminal(_ask_dest)
            if dest_str:
                dest = Path(dest_str).expanduser()
                try:
                    shutil.copy2(str(z8a_src), str(dest))
                except OSError:
                    pass
            return
        if state.filepath is None:
            if not await _ask_save_path():
                return
        _do_save(state, main_buf)

    @kb.add('f3')
    @kb.add('c-o')
    def _open(event):
        if state.mode != 'msl':
            return
        start = state.filepath.parent if state.filepath else Path.cwd()
        _picker_refresh(state, start)
        state.mode = 'picker'
        event.app.layout.focus(picker_window)

    @kb.add('f4')
    def _view_z8a(event):
        if state.mode == 'z8a':
            state.mode = 'msl'
            event.app.layout.focus(msl_window)
            return
        if state.filepath is None:
            return
        if not state.build_ok:
            _run_compile(state, main_buf)
            if not state.build_ok:
                return
        _load_z8a_view(state, z8a_buf)
        state.mode = 'z8a'
        event.app.layout.focus(z8a_window)

    @kb.add('escape', filter=Condition(lambda: state.mode == 'z8a'))
    def _back_from_z8a(event):
        state.mode = 'msl'
        event.app.layout.focus(msl_window)

    _picker_active = Condition(lambda: state.mode == 'picker')

    @kb.add('up', eager=True, filter=_picker_active)
    def _picker_up(event):
        if state.picker_entries:
            state.picker_sel = max(0, state.picker_sel - 1)

    @kb.add('down', eager=True, filter=_picker_active)
    def _picker_down(event):
        if state.picker_entries:
            state.picker_sel = min(len(state.picker_entries) - 1, state.picker_sel + 1)

    @kb.add('enter', eager=True, filter=_picker_active)
    def _picker_enter(event):
        if not state.picker_entries:
            return
        p = state.picker_entries[state.picker_sel]
        if p.is_dir():
            _picker_refresh(state, p)
        else:
            _do_load(state, main_buf, p)
            state.errors        = []
            state.show_errors   = False
            state.build_ok      = None
            state.build_message = ''
            state.mode          = 'msl'
            event.app.layout.focus(msl_window)

    @kb.add('escape', eager=True, filter=_picker_active)
    def _picker_cancel(event):
        state.mode = 'msl'
        event.app.layout.focus(msl_window)

    # --- Section navigator (Ctrl+G) ---

    @kb.add('c-g', filter=Condition(lambda: state.mode == 'msl'))
    def _open_sections(event):
        state.sections_list = _get_sections(main_buf.text)
        state.sections_sel  = 0
        # Pre-select the section the cursor is currently in
        cur_row = main_buf.document.cursor_position_row
        for i, (ln, _, _) in enumerate(state.sections_list):
            if ln <= cur_row:
                state.sections_sel = i
        state.mode = 'sections'
        event.app.layout.focus(sections_window)

    _sec_active = Condition(lambda: state.mode == 'sections')

    @kb.add('up',    eager=True, filter=_sec_active)
    def _sec_up(event):
        if state.sections_list:
            state.sections_sel = max(0, state.sections_sel - 1)

    @kb.add('down',  eager=True, filter=_sec_active)
    def _sec_down(event):
        if state.sections_list:
            state.sections_sel = min(len(state.sections_list) - 1, state.sections_sel + 1)

    @kb.add('enter', eager=True, filter=_sec_active)
    def _sec_jump(event):
        if not state.sections_list:
            state.mode = 'msl'
            event.app.layout.focus(msl_window)
            return
        ln, _, _ = state.sections_list[state.sections_sel]
        lines    = main_buf.text.split('\n')
        pos      = sum(len(l) + 1 for l in lines[:ln])
        main_buf.cursor_position = min(pos, len(main_buf.text))
        state.mode = 'msl'
        event.app.layout.focus(msl_window)

    @kb.add('escape', eager=True, filter=_sec_active)
    def _sec_cancel(event):
        state.mode = 'msl'
        event.app.layout.focus(msl_window)

    @kb.add('f5', filter=Condition(lambda: state.mode == 'msl'))
    def _toggle_vi(event):
        state.vi_mode = not state.vi_mode
        if state.vi_mode:
            event.app.editing_mode = EditingMode.VI
            # Start in INSERT so typing works immediately without pressing 'i'
            event.app.vi_state.input_mode = InputMode.INSERT
        else:
            event.app.editing_mode = EditingMode.EMACS

    @kb.add('f9')
    @kb.add('c-b')
    async def _compile(event):
        if state.mode != 'msl':
            return
        if state.filepath is None:
            if not await _ask_save_path():
                return
        _run_compile(state, main_buf)

    @kb.add('f10')
    @kb.add('c-r')
    async def _play(event):
        if state.mode != 'msl':
            return
        if state.filepath is None:
            if not await _ask_save_path():
                return
        _run_compile(state, main_buf)
        if not state.build_ok:
            return
        def do_play():
            subprocess.run(
                [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'play', str(state.filepath)],
            )
        await run_in_terminal(do_play)

    @kb.add('f6', filter=Condition(lambda: state.mode == 'msl'))
    def _instr_open(event):
        state.instr_song = _parse_inst_blocks(main_buf.text, 'song')
        _instr_load_bank(state)
        state.instr_list_sel = 0
        state.instr_form_sel = 0
        state.instr_focus    = 'list'
        _instr_load_edit(state)
        state.mode = 'instr'
        event.app.layout.focus(instr_list_window)

    _instr_active = Condition(lambda: state.mode == 'instr')

    @kb.add('escape', eager=True, filter=_instr_active)
    def _instr_back(event):
        state.mode = 'msl'
        event.app.layout.focus(msl_window)

    @kb.add('tab', eager=True, filter=_instr_active)
    def _instr_tab(event):
        if state.instr_focus == 'list':
            state.instr_focus = 'form'
            event.app.layout.focus(instr_form_window)
        else:
            state.instr_focus = 'list'
            event.app.layout.focus(instr_list_window)

    @kb.add('up', eager=True, filter=_instr_active)
    def _instr_up(event):
        if state.instr_focus == 'list':
            state.instr_list_sel = max(0, state.instr_list_sel - 1)
            _instr_load_edit(state)
        else:
            state.instr_form_sel = max(0, state.instr_form_sel - 1)

    @kb.add('down', eager=True, filter=_instr_active)
    def _instr_down(event):
        if state.instr_focus == 'list':
            state.instr_list_sel = min(15, state.instr_list_sel + 1)
            _instr_load_edit(state)
        else:
            state.instr_form_sel = min(_INST_FORM_FIELD_COUNT - 1, state.instr_form_sel + 1)

    def _form_change(delta: int):
        if state.instr_focus == 'form' and state.instr_edit:
            _inst_cycle_field(state.instr_edit, state.instr_form_sel, delta)

    @kb.add('+',     eager=True, filter=_instr_active)
    @kb.add('right', eager=True, filter=_instr_active)
    def _instr_inc(event):
        _form_change(1)

    @kb.add('-',    eager=True, filter=_instr_active)
    @kb.add('left', eager=True, filter=_instr_active)
    def _instr_dec(event):
        _form_change(-1)

    @kb.add('enter', eager=True, filter=_instr_active)
    async def _instr_edit_name(event):
        if state.instr_focus != 'form' or state.instr_edit is None:
            return
        if state.instr_form_sel != 0:
            return                      # only Name needs free-text entry
        inst = state.instr_edit
        def _ask():
            try:
                ans = input(f'Name [{inst.name}]: ').strip()
                return ans if ans != '' else None
            except (KeyboardInterrupt, EOFError):
                return None
        val = await run_in_terminal(_ask)
        if val is not None:
            inst.name = val[:24]

    @kb.add('f2', filter=_instr_active)
    async def _instr_save(event):
        inst = state.instr_edit
        if inst is None:
            return
        if inst.source == 'song':
            new_text = _update_text_inst(main_buf.text, inst)
            main_buf.set_document(Document(new_text, main_buf.cursor_position), bypass_readonly=True)
            state.instr_song[inst.id] = inst.copy()
            _do_save(state, main_buf)
        else:
            if not state.bank_file:
                def _ask_bank():
                    try:
                        return input('Bank file name [instruments.msxi]: ').strip() or 'instruments.msxi'
                    except (KeyboardInterrupt, EOFError):
                        return None
                name = await run_in_terminal(_ask_bank)
                if not name:
                    return
                state.bank_file = name
            bank_path = (state.filepath.parent / state.bank_file) if state.filepath else Path(state.bank_file)
            try:
                existing = bank_path.read_text(encoding='utf-8') if bank_path.exists() else ''
            except OSError:
                existing = ''
            new_bank = _update_text_inst(existing, inst)
            try:
                bank_path.write_text(new_bank, encoding='utf-8')
                state.instr_bank[inst.id] = inst.copy()
                state.instr_bank[inst.id].source = 'bank'
            except OSError:
                pass

    @kb.add('f4', filter=_instr_active)
    async def _instr_copy_to_bank(event):
        inst = state.instr_edit
        if inst is None:
            return
        if not state.bank_file:
            def _ask_bank():
                try:
                    return input('Bank file name [instruments.msxi]: ').strip() or 'instruments.msxi'
                except (KeyboardInterrupt, EOFError):
                    return None
            name = await run_in_terminal(_ask_bank)
            if not name:
                return
            state.bank_file = name
        bank_path = (state.filepath.parent / state.bank_file) if state.filepath else Path(state.bank_file)
        try:
            existing = bank_path.read_text(encoding='utf-8') if bank_path.exists() else ''
        except OSError:
            existing = ''
        copy = inst.copy()
        copy.source = 'bank'
        new_bank = _update_text_inst(existing, copy)
        try:
            bank_path.write_text(new_bank, encoding='utf-8')
            state.instr_bank[copy.id] = copy
        except OSError:
            pass

    @kb.add('f5', filter=_instr_active)
    def _instr_copy_to_song(event):
        inst = state.instr_edit
        if inst is None:
            return
        copy = inst.copy()
        copy.source = 'song'
        new_text = _update_text_inst(main_buf.text, copy)
        main_buf.set_document(Document(new_text, main_buf.cursor_position), bypass_readonly=True)
        state.instr_song[copy.id] = copy
        state.instr_edit = copy

    @kb.add('delete', eager=True, filter=_instr_active)
    def _instr_delete(event):
        i = state.instr_list_sel
        in_song = i in state.instr_song
        in_bank = i in state.instr_bank
        if in_song:
            new_text = _remove_text_inst(main_buf.text, i)
            main_buf.set_document(Document(new_text, main_buf.cursor_position), bypass_readonly=True)
            del state.instr_song[i]
        elif in_bank and state.bank_file:
            bank_path = (state.filepath.parent / state.bank_file) if state.filepath else Path(state.bank_file)
            try:
                existing = bank_path.read_text(encoding='utf-8')
                new_bank = _remove_text_inst(existing, i)
                bank_path.write_text(new_bank, encoding='utf-8')
                del state.instr_bank[i]
            except OSError:
                pass
        _instr_load_edit(state)

    @kb.add('f6', filter=_instr_active)
    async def _instr_preview(event):
        inst = state.instr_edit
        if inst is None:
            return
        inst_block = _format_inst_block(inst)
        preview_msl = (
            f'{inst_block}\n\n'
            f'CH_A:\n'
            f'PREVIEW:\n'
            f'    @T120\n'
            f'    @V15\n'
            f'    @I{inst.id}\n'
            f'    O4 C2 E2 G2 >C2\n'
            f'    @RESTART(PREVIEW)\n'
        )
        tmp = tempfile.NamedTemporaryFile(
            suffix='.msl', mode='w', delete=False,
            prefix='musax_preview_', encoding='utf-8',
        )
        tmp.write(preview_msl)
        tmp.close()
        tmp_path = tmp.name
        def do_preview():
            try:
                subprocess.run(
                    [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'play', '-l', '1', tmp_path],
                )
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        await run_in_terminal(do_preview)

    @kb.add('c-t')
    def _cycle_theme(event):
        idx = THEME_CYCLE.index(state.theme)
        state.theme = THEME_CYCLE[(idx + 1) % len(THEME_CYCLE)]
        event.app.style = THEMES[state.theme]

    @kb.add('f12')
    def _toggle_errors_f12(event):
        if state.mode == 'msl':
            state.show_errors = not state.show_errors

    @kb.add('c-e')
    def _toggle_errors_ce(event):
        if state.mode == 'msl':
            state.show_errors = not state.show_errors

    @kb.add('c-n')
    def _new(event):
        if state.mode == 'z8a':
            return
        main_buf.set_document(Document('', 0), bypass_readonly=True)
        state.filepath     = None
        state.modified     = False
        state.errors       = []
        state.show_errors  = False
        state.bank_file    = ''

    @kb.add('c-q')
    def _quit(event):
        event.app.exit()

    _err_active = Condition(lambda: state.show_errors and bool(state.errors) and state.mode == 'msl')

    @kb.add('up',    filter=_err_active)
    def _err_up(event):   state.selected_error = max(0, state.selected_error - 1)

    @kb.add('down',  filter=_err_active)
    def _err_down(event): state.selected_error = min(len(state.errors) - 1, state.selected_error + 1)

    @kb.add('enter', filter=_err_active)
    def _err_jump(event):
        if state.errors:
            ln, _ = state.errors[state.selected_error]
            lines = main_buf.text.split('\n')
            pos   = sum(len(l) + 1 for l in lines[:ln - 1])
            main_buf.cursor_position = min(pos, len(main_buf.text))
            state.show_errors = False

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=THEMES[state.theme],
        full_screen=True,
        mouse_support=True,
    )
    return app


# ---------------------------------------------------------------------------
# Compile / Play / Z8A View integration
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).parent


def _run_compile(state: EditorState, buf: Buffer):
    if state.filepath is None:
        return
    _do_save(state, buf)
    try:
        result = subprocess.run(
            [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'build', str(state.filepath)],
            capture_output=True, text=True, timeout=10,
        )
        combined    = result.stdout + result.stderr
        state.errors = _parse_compiler_errors(combined)
        state.selected_error = 0
        if state.errors:
            state.build_ok     = False
            state.show_errors  = True
            state.build_message = ''
        else:
            state.build_ok      = True
            state.show_errors   = False
            state.build_message = _extract_build_summary(combined)
    except Exception as e:
        state.errors       = [(0, str(e))]
        state.build_ok     = False
        state.build_message = ''
        state.show_errors   = True


def _load_z8a_view(state: EditorState, z8a_buf: Buffer):
    z8a_path = _get_z8a_path(state)
    if z8a_path and z8a_path.exists():
        try:
            text = z8a_path.read_text(encoding='utf-8')
        except OSError:
            text = f'; Error reading {z8a_path}'
    else:
        text = '; Z8A file not found — press F9 to build first'
    z8a_buf.set_document(Document(text, 0), bypass_readonly=True)


_RE_COMPILER_ERR     = re.compile(r'Line\s+(\d+),\s*col\s+\d+:\s*(.+)', re.IGNORECASE)
_RE_COMPILER_SUCCESS = re.compile(r'Successfully compiled .+ -> (.+)', re.IGNORECASE)


def _parse_compiler_errors(output: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in _RE_COMPILER_ERR.finditer(output)]


def _extract_build_summary(output: str) -> str:
    m = _RE_COMPILER_SUCCESS.search(output)
    if not m:
        return output.strip().splitlines()[0] if output.strip() else ''
    z8a = Path(m.group(1).strip())
    try:    return f'{z8a.name}  {z8a.stat().st_size} bytes'
    except: return z8a.name


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog='msl_editor',
        description='MusaX MSL Editor — TUI editor for MSL song files.',
    )
    parser.add_argument(
        'file', nargs='?', metavar='FILE.msl',
        help='MSL file to open (omit to start with an empty buffer)',
    )
    # Future: --instruments FILE.ins  to preload an instrument bank
    args = parser.parse_args()

    path = Path(args.file) if args.file else None
    app  = build_app(path)
    app.run()


if __name__ == '__main__':
    main()
