#!/usr/bin/env python3
"""MusaX MSL Editor — syntax-highlighted editor with vi mode and theme support."""

import sys
import os
import re
import subprocess
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
from prompt_toolkit.layout.containers import HSplit, Window, ConditionalContainer
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
        self.mode:           str         = 'msl'   # 'msl' | 'z8a'
        self.vi_mode:        bool        = False
        self.theme:          str         = 'retrobox'


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
    buf = z8a_buf if state.mode == 'z8a' else main_buf
    doc = buf.document
    ln  = doc.cursor_position_row + 1
    col = doc.cursor_position_col + 1
    bank = f'  BANK: {state.bank_file}' if state.bank_file and state.mode == 'msl' else ''
    pos  = f' Ln {ln:3d}  Col {col:3d}{bank}  '

    if state.mode == 'z8a':
        return [('class:statusbar', pos + '[Z8A read-only ─ Esc or F4 to return] ')]

    result: StyleAndTextTuples = [('class:statusbar', pos)]
    result += _vi_indicator(state)

    if state.build_ok is True:
        result.append(('class:build-ok', f'  [OK] {state.build_message} '))
    elif state.build_ok is False:
        result.append(('class:build-err', f'  [{len(state.errors)} error(s)] {state.build_message} '))

    return result


def _fkey_bar(state: EditorState) -> StyleAndTextTuples:
    if state.mode == 'z8a':
        keys = [('Esc', 'Back'), ('F4', 'Back to MSL')]
    else:
        vi_label = 'VI:ON' if state.vi_mode else 'VI:off'
        keys = [
            ('F2/^S', 'Save'), ('F3/^O', 'Open'), ('F4', 'View Z8A'), ('F5', vi_label),
            ('F6', 'Instr'), ('F9/^B', 'Build'), ('F10/^R', 'Play'),
            ('Ctrl+T', state.theme), ('Ctrl+Q', 'Quit'),
        ]
    result: StyleAndTextTuples = []
    for k, label in keys:
        result.append(('class:fkey',       f' {k} '))
        result.append(('class:fkey-label', f' {label}  '))
    return result


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
        height=1, style='class:fkeybar',
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

    layout = Layout(
        HSplit([title_bar, msl_body, z8a_body, error_panel, status_bar, fkey_bar]),
        focused_element=msl_window,
    )

    # --- Key Bindings ---

    kb = KeyBindings()

    @kb.add('f2')
    @kb.add('c-s')
    def _save(event):
        if state.filepath is None or state.mode == 'z8a':
            return
        _do_save(state, main_buf)

    @kb.add('f3')
    @kb.add('c-o')
    def _open(event):
        pass  # TODO: file picker

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
    def _compile(event):
        if state.mode == 'z8a':
            return
        _run_compile(state, main_buf)

    @kb.add('f10')
    @kb.add('c-r')
    async def _play(event):
        if state.mode == 'z8a' or state.filepath is None:
            return
        _run_compile(state, main_buf)
        if not state.build_ok:
            return
        def do_play():
            subprocess.run(
                [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'play', str(state.filepath)],
            )
        await run_in_terminal(do_play)

    @kb.add('f6')
    def _instr(event):
        pass  # TODO: Instrument Editor

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
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    app  = build_app(path)
    app.run()


if __name__ == '__main__':
    main()
