#!/usr/bin/env python3
"""MusaX MSL Editor — text editor with syntax highlighting."""

import sys
import os
import re
import subprocess
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style


# ---------------------------------------------------------------------------
# Syntax Highlighting
# ---------------------------------------------------------------------------

_RE_COMMENT   = re.compile(r'//[^\n]*')
_RE_AT_CMD    = re.compile(r'@[A-Za-z0-9_#\-]+(?:\s*(?:\([^)]*\)|"[^"]*"))?')
_RE_INST_KW   = re.compile(r'\b(ADSR|LFO|FLAGS|PHRASE)\b')
_RE_LABEL     = re.compile(r'^[ \t]*(?!ADSR|LFO|FLAGS\b)([A-Z0-9_\.]+):(?!\d)', re.MULTILINE)
_RE_NOTE      = re.compile(r'\b([A-GR])([#\+\-bB]?)(\d*)([\.t]*)', re.IGNORECASE)
_RE_OL        = re.compile(r'\b([OL])(\d+)', re.IGNORECASE)
_RE_OCTSHIFT  = re.compile(r'[<>]')
_RE_LOOP      = re.compile(r'[{}]|\}\s*\d+t?')
_RE_NUMBER    = re.compile(r'\b\d+\b')
_RE_STRING    = re.compile(r'"[^"]*"')

# Token class names → mapped to Style below
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
    """Return a list of (style, text) pairs for the full document text."""
    length = len(text)
    # Build a per-character style array; later we'll collapse runs.
    styles = [''] * length

    def paint(m: re.Match, cls: str):
        for i in range(m.start(), m.end()):
            styles[i] = cls

    # Order matters: later paints overwrite earlier ones.
    # 1. Numbers (lowest priority — overwritten by anything more specific)
    for m in _RE_NUMBER.finditer(text):
        paint(m, _C['number'])

    # 2. Octave shifts
    for m in _RE_OCTSHIFT.finditer(text):
        paint(m, _C['octshift'])

    # 3. Loop braces
    for m in _RE_LOOP.finditer(text):
        paint(m, _C['loop'])

    # 4. O/L commands
    for m in _RE_OL.finditer(text):
        paint(m, _C['ol'])

    # 5. Notes
    for m in _RE_NOTE.finditer(text):
        # Only colour if the matched char is a real note/rest letter
        ch = m.group(1).upper()
        if ch in 'ABCDEFGR':
            paint(m, _C['note'])

    # 6. Strings
    for m in _RE_STRING.finditer(text):
        paint(m, _C['string'])

    # 7. Labels (flow labels like CH_A:, MAIN_MELODY:)
    for m in _RE_LABEL.finditer(text):
        start = m.start(1)
        end   = m.end(0)          # includes the colon
        for i in range(start, end):
            styles[i] = _C['label']

    # 8. Keywords inside INST blocks — painted after labels so ADSR:/LFO: win
    for m in _RE_INST_KW.finditer(text):
        paint(m, _C['inst_kw'])

    # 9. @-commands (high priority)
    for m in _RE_AT_CMD.finditer(text):
        paint(m, _C['at_cmd'])

    # 10. Comments (highest priority — override everything)
    for m in _RE_COMMENT.finditer(text):
        paint(m, _C['comment'])

    # Collapse into runs
    if not styles:
        return []
    result: StyleAndTextTuples = []
    cur_style = styles[0]
    run_start = 0
    for i in range(1, length):
        if styles[i] != cur_style:
            result.append((cur_style or _C['default'], text[run_start:i]))
            cur_style = styles[i]
            run_start = i
    result.append((cur_style or _C['default'], text[run_start:]))
    return result


class MSLLexer(Lexer):
    def lex_document(self, document):
        text = document.text
        tokens = _tokenize(text)

        # Map absolute-position tokens to per-line lists
        lines: list[StyleAndTextTuples] = []
        current_line: StyleAndTextTuples = []
        for style, chunk in tokens:
            parts = chunk.split('\n')
            for k, part in enumerate(parts):
                if part:
                    current_line.append((style, part))
                if k < len(parts) - 1:
                    lines.append(current_line)
                    current_line = []
        lines.append(current_line)

        def get_line(lineno):
            if lineno < len(lines):
                return lines[lineno]
            return []

        return get_line


# ---------------------------------------------------------------------------
# Colour Scheme (Borland-inspired)
# ---------------------------------------------------------------------------

EDITOR_STYLE = Style.from_dict({
    # Chrome
    'titlebar':        'bg:#00AAAA fg:#000000',
    'titlebar.name':   'bg:#00AAAA fg:#000000 bold',
    'titlebar.mod':    'bg:#00AAAA fg:#AA0000 bold',
    'statusbar':       'bg:#000077 fg:#AAAAAA',
    'build-ok':        'bg:#000077 fg:#55FF55 bold',
    'build-err':       'bg:#000077 fg:#FF5555 bold',
    'fkeybar':         'bg:#00AAAA fg:#000000',
    'fkey':            'bg:#FFFFFF fg:#000000 bold',
    'fkey-label':      'bg:#00AAAA fg:#000000',

    # Editor area
    'default':         'bg:#0000AA fg:#FFFFFF',
    'comment':         'bg:#0000AA fg:#555555 italic',
    'at_cmd':          'bg:#0000AA fg:#55FFFF bold',
    'inst_kw':         'bg:#0000AA fg:#55FFFF',
    'label':           'bg:#0000AA fg:#FFFFFF bold',
    'note':            'bg:#0000AA fg:#FFFF55',
    'ol':              'bg:#0000AA fg:#55FF55',
    'octshift':        'bg:#0000AA fg:#55FF55 bold',
    'loop':            'bg:#0000AA fg:#FF5555 bold',
    'number':          'bg:#0000AA fg:#55FF55',
    'string':          'bg:#0000AA fg:#FF55FF',

    # Text area widget internals
    'text-area':                'bg:#0000AA fg:#FFFFFF',
    'text-area focused':        'bg:#0000AA fg:#FFFFFF',
    'cursor-line':              'bg:#000077',

    # Error panel
    'errorpanel':      'bg:#AA0000 fg:#FFFFFF',
    'errorpanel.selected': 'bg:#FFFF55 fg:#AA0000 bold',
})


# ---------------------------------------------------------------------------
# Editor State
# ---------------------------------------------------------------------------

class EditorState:
    def __init__(self):
        self.filepath: Path | None = None
        self.modified: bool = False
        self.bank_file: str = ''
        self.errors: list[tuple[int, str]] = []   # (line, message)
        self.selected_error: int = 0
        self.show_errors: bool = False
        self.build_message: str = ''   # last compiler output summary
        self.build_ok: bool | None = None  # None = never built


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _title_text(state: EditorState) -> StyleAndTextTuples:
    name = state.filepath.name if state.filepath else 'untitled.msl'
    result: StyleAndTextTuples = [
        ('class:titlebar',      ' MusaX v1.9 ─ '),
        ('class:titlebar.name', name),
    ]
    if state.modified:
        result.append(('class:titlebar.mod', ' [modified]'))
    result.append(('class:titlebar', ' '))
    return result


def _status_text(state: EditorState, buf: Buffer) -> StyleAndTextTuples:
    doc  = buf.document
    ln   = doc.cursor_position_row + 1
    col  = doc.cursor_position_col + 1
    bank = f'  BANK: {state.bank_file}' if state.bank_file else ''
    pos  = f' Ln {ln:3d}  Col {col:3d}{bank}  '

    if state.build_ok is None:
        build = [('class:statusbar', pos)]
    elif state.build_ok:
        build = [
            ('class:statusbar', pos),
            ('class:build-ok', f'[OK] {state.build_message} '),
        ]
    else:
        build = [
            ('class:statusbar', pos),
            ('class:build-err', f'[{len(state.errors)} error(s)] {state.build_message} '),
        ]
    return build


def _fkey_bar() -> StyleAndTextTuples:
    keys = [
        ('F2', 'Save'), ('F3', 'Open'),
        ('F6', 'Instr'), ('F9', 'Build'), ('F10', 'Play'),
        ('Ctrl+Q', 'Quit'),
    ]
    result: StyleAndTextTuples = []
    for k, label in keys:
        result.append(('class:fkey', f' {k} '))
        result.append(('class:fkey-label', f' {label}  '))
    return result


def _error_lines(state: EditorState) -> StyleAndTextTuples:
    if not state.errors:
        return [('class:errorpanel', '  (no errors)')]
    result: StyleAndTextTuples = []
    for i, (ln, msg) in enumerate(state.errors[:5]):
        cls = 'class:errorpanel.selected' if i == state.selected_error else 'class:errorpanel'
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
        return False   # caller must handle Save-As
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
        state.filepath = path
        state.modified = False
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
        state.modified = True
        state.bank_file = _extract_bank(main_buf.text)

    # Main editing buffer
    main_buf = Buffer(
        name='main',
        multiline=True,
        on_text_changed=_on_text_changed,
    )
    if initial_file and initial_file.exists():
        _do_load(state, main_buf, initial_file)
    elif initial_file:
        state.filepath = initial_file

    # --- Widgets ---

    title_bar = Window(
        content=FormattedTextControl(lambda: _title_text(state)),
        height=1,
        style='class:titlebar',
    )

    status_bar = Window(
        content=FormattedTextControl(lambda: _status_text(state, main_buf)),
        height=1,
        style='class:statusbar',
    )

    fkey_bar = Window(
        content=FormattedTextControl(_fkey_bar),
        height=1,
        style='class:fkeybar',
    )

    error_panel = ConditionalContainer(
        content=Window(
            content=FormattedTextControl(lambda: _error_lines(state)),
            height=5,
            style='class:errorpanel',
        ),
        filter=Condition(lambda: state.show_errors and bool(state.errors)),
    )

    body = Window(
        content=BufferControl(
            buffer=main_buf,
            lexer=MSLLexer(),
            focusable=True,
        ),
        style='class:default',
    )

    layout = Layout(
        HSplit([
            title_bar,
            body,
            error_panel,
            status_bar,
            fkey_bar,
        ]),
        focused_element=body,
    )

    # --- Key Bindings ---

    kb = KeyBindings()

    @kb.add('f2')
    def _save(event):
        if state.filepath is None:
            # TODO: Save-As dialog in next iteration
            return
        _do_save(state, main_buf)

    @kb.add('f3')
    def _open(event):
        # TODO: file picker dialog in next iteration
        pass

    @kb.add('f9')
    def _compile(event):
        _run_compile(state, main_buf)

    @kb.add('f10')
    def _play(event):
        _run_play(state, main_buf, event.app)

    @kb.add('f6')
    def _instr(event):
        # TODO: Instrument Editor mode in next iteration
        pass

    @kb.add('f12')
    def _toggle_errors_f12(event):
        state.show_errors = not state.show_errors

    @kb.add('c-e')
    def _toggle_errors_ce(event):
        state.show_errors = not state.show_errors

    @kb.add('c-n')
    def _new(event):
        main_buf.set_document(Document('', 0), bypass_readonly=True)
        state.filepath = None
        state.modified = False
        state.errors = []
        state.show_errors = False
        state.bank_file = ''

    @kb.add('c-q')
    def _quit(event):
        event.app.exit()

    # Error panel navigation
    @kb.add('up', filter=Condition(lambda: state.show_errors and bool(state.errors)))
    def _err_up(event):
        state.selected_error = max(0, state.selected_error - 1)

    @kb.add('down', filter=Condition(lambda: state.show_errors and bool(state.errors)))
    def _err_down(event):
        state.selected_error = min(len(state.errors) - 1, state.selected_error + 1)

    @kb.add('enter', filter=Condition(lambda: state.show_errors and bool(state.errors)))
    def _err_jump(event):
        if state.errors:
            ln, _ = state.errors[state.selected_error]
            # Move cursor to that line
            lines = main_buf.text.split('\n')
            pos = sum(len(l) + 1 for l in lines[:ln - 1])
            main_buf.cursor_position = min(pos, len(main_buf.text))
            state.show_errors = False

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=EDITOR_STYLE,
        full_screen=True,
        mouse_support=True,
    )
    return app


# ---------------------------------------------------------------------------
# Compile / Play integration
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).parent


def _run_compile(state: EditorState, buf: Buffer):
    if state.filepath is None:
        return
    _do_save(state, buf)
    try:
        result = subprocess.run(
            [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'build',
             str(state.filepath)],
            capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        state.errors = _parse_compiler_errors(combined)
        state.selected_error = 0
        if state.errors:
            state.build_ok = False
            state.show_errors = True
            state.build_message = ''
        else:
            state.build_ok = True
            state.show_errors = False
            state.build_message = _extract_build_summary(combined)
    except Exception as e:
        state.errors = [(0, str(e))]
        state.build_ok = False
        state.build_message = ''
        state.show_errors = True


def _run_play(state: EditorState, buf: Buffer, app: Application):
    if state.filepath is None:
        return
    _do_save(state, buf)
    # Compile first; only launch sim if clean
    result = subprocess.run(
        [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'build',
         str(state.filepath)],
        capture_output=True, text=True, timeout=10,
    )
    combined = result.stdout + result.stderr
    state.errors = _parse_compiler_errors(combined)
    state.selected_error = 0
    if state.errors:
        state.build_ok = False
        state.build_message = ''
        state.show_errors = True
        return
    state.build_ok = True
    state.build_message = _extract_build_summary(combined)
    # Suspend the TUI; the simulator takes the terminal completely.
    # app.input.detach() restores normal terminal state for the subprocess.
    # After the subprocess exits, prompt_toolkit redraws everything.
    state.sim_state = 'PLAYING'
    with app.input.detach():
        subprocess.run(
            [sys.executable, str(_TOOLS_DIR / 'musax.py'), 'play',
             str(state.filepath)],
        )
    state.sim_state = 'STOPPED'
    # The simulator wrote directly to the terminal while we held the screen.
    # reset() clears the renderer's virtual-screen cache so the next render
    # is a full repaint rather than an incremental diff.
    app.renderer.reset()
    app.invalidate()


# Compiler output formats:
#   success: "Successfully compiled foo.msl -> foo.Z8A"
#   error:   "  Line N, col M: message"
_RE_COMPILER_ERR     = re.compile(r'Line\s+(\d+),\s*col\s+\d+:\s*(.+)', re.IGNORECASE)
_RE_COMPILER_SUCCESS = re.compile(r'Successfully compiled .+ -> (.+)', re.IGNORECASE)


def _parse_compiler_errors(output: str) -> list[tuple[int, str]]:
    errors = []
    for m in _RE_COMPILER_ERR.finditer(output):
        errors.append((int(m.group(1)), m.group(2).strip()))
    return errors


def _extract_build_summary(output: str) -> str:
    m = _RE_COMPILER_SUCCESS.search(output)
    if not m:
        return output.strip().splitlines()[0] if output.strip() else ''
    z8a = Path(m.group(1).strip())
    try:
        size = z8a.stat().st_size
        return f'{z8a.name}  {size} bytes'
    except OSError:
        return z8a.name


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    app = build_app(path)
    app.run()


if __name__ == '__main__':
    main()
