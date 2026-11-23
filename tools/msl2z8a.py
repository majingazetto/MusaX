#!/usr/bin/env python3
import sys
import os
import argparse

# Ensure we can import msl_parser and msl_compiler
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.append(project_root)

from MusaX.tools.msl_parser import MSLParser
from MusaX.tools.msl_compiler import MSLCompiler

def load_constants():
    """Loads constants from CONST.Z8A and returns a mapping from value to name."""
    const_file = os.path.join(project_root, "MusaX", "src", "driver", "CONST.Z8A")
    val_to_name = {}
    if not os.path.exists(const_file):
        return val_to_name

    import re
    base_tick = 768
    with open(const_file, 'r') as f:
        content = f.read()
        match = re.search(r'^\s*BASETIC\s+EQU\s+(\d+)', content, re.MULTILINE | re.IGNORECASE)
        if match:
            base_tick = int(match.group(1))

    const_list = []
    with open(const_file, 'r') as f:
        for line in f:
            match = re.match(r'^\s*([A-Z0-9#\_]+)\s+EQU\s+([^;\n]+)', line, re.IGNORECASE)
            if match:
                name, val_str = match.groups()
                name = name.strip()
                val_str = val_str.strip()
                if val_str.startswith('#'):
                    try: val = int(val_str[1:], 16)
                    except ValueError: continue
                elif val_str.isdigit():
                    val = int(val_str)
                elif 'BASETIC' in val_str.upper():
                    try:
                        expr = val_str.upper().replace('BASETIC', str(base_tick))
                        val = int(eval(expr))
                    except:
                        continue
                else:
                    continue
                const_list.append((name, val))

    for name, val in const_list:
        if name.startswith('LEN') and name != 'LEN': continue
        if 0 <= val <= 95:
            if val not in val_to_name or ("#" not in name and "S" not in name):
                if len(name) <= 3:
                    val_to_name[val] = name
        else:
            if val not in val_to_name:
                val_to_name[val] = name

    for name, val in const_list:
        if name.startswith('LEN') and name != 'LEN':
            val_to_name[val] = name

    priority_cmds = {
        0xFE: "CRESTART", 0xFD: "CTEMPO", 0xFC: "CVOLUME",
        0xFB: "CGATE",    0xFA: "CINST",  0xF9: "CLOOPS",
        0xF8: "CLOOPE",  0xF7: "CGOTO",  0xF6: "CPHASE",
        0xF5: "CDETUNE",  0xF4: "CCHORUS",0xF3: "CFADE",
        0xF2: "CPORTA",   0xF1: "CCALL",  0xF0: "CRET",
        255: "REST", 0x80: "TYPESONG", 0x81: "TYPEFX"
    }
    val_to_name.update(priority_cmds)
    return val_to_name


# ---------------------------------------------------------------------------
# Assembly line formatting  (label col=0, directive col=16, operands col=24)
# Matches the hand-written .Z8A style used across this workspace.
# ---------------------------------------------------------------------------

def _aline(lbl: str, directive: str, operands: str) -> str:
    """Label + directive + operands, column-aligned."""
    pad = max(16 - len(lbl), 1)
    return f'{lbl}{" " * pad}{directive:<8}{operands}\n'

def _line(directive: str, operands: str) -> str:
    """No-label directive line."""
    return f'{"":16}{directive:<8}{operands}\n'

def _g(sym: str, use_module: bool) -> str:
    """Prefix a constant symbol with @ for global scope inside a sjasmplus MODULE.
    Hex literals (#XX), plain numbers, and '0' are returned unchanged."""
    if not use_module or sym.startswith('#') or sym == '0' or sym.isdigit():
        return sym
    return f'@{sym}'


# ---------------------------------------------------------------------------
# The compiled file is first built as a list of items, then rendered:
#   ('text', s)               sjasmplus text only (file header, MODULE...)
#   ('blank',)                an empty line
#   ('sec', sj, sx, level)    a section comment (sjasmplus text, SX80 title)
#   ('ln', label, dir, ops)   a line; ops = [(kind, text)], kind:
#                             'con' a constant (@ inside a MODULE), 'lab' a
#                             label (renamed for SX80), 'lit' as written
#   ('lab', name)             a label alone on its line
# Both renderers emit the same bytes.
# ---------------------------------------------------------------------------

SX_W = 61       # SX80 house style: columns at most
SX_LBL = 12     # mnemonic column
SX_OPS = 8      # mnemonic width

# Names an SX80 label may not take: Z80 registers, conditions,
# mnemonics, operator words and directives (MUSAX.INC's names are added).
SX_RESERVED = set('''
A B C D E H L I R F AF BC DE HL IX IY SP PC IXH IXL IYH IYL
NZ Z NC PO PE P M
ADC ADD AND BIT CALL CCF CP CPD CPDR CPI CPIR CPL DAA DEC DI DJNZ EI EX
EXX HALT IM IN INC IND INDR INI INIR JP JR LD LDD LDDR LDI LDIR NEG NOP
OR OTDR OTIR OUT OUTD OUTI POP PUSH RES RET RETI RETN RL RLA RLC RLCA
RLD RR RRA RRC RRCA RRD RST SBC SCF SET SLA SLL SRA SRL SUB XOR
NOT HIGH LOW MOD SHL SHR LT LE GT GE EQ NE
EQU DEFL DEFINE ORG DB DEFB BYTE DM DEFM DZ DC DW DEFW WORD DS DEFS BLOCK
CSEG DSEG ASEG PUBLIC GLOBAL ENTRY EXTRN EXT END ASSERT PHASE DEPHASE
ALIGN INCLUDE INCBIN IF IFDEF IFNDEF ELSE ENDIF MACRO ENDM REPT ENDR DUP
EDUP LOCAL EXITM
'''.split())


def _const_names():
    """Every EQU name of MusaX's CONST.Z8A (= WRECK's MUSAX.INC)."""
    import re
    const_file = os.path.join(project_root, "MusaX", "src", "driver", "CONST.Z8A")
    names = set()
    if os.path.exists(const_file):
        with open(const_file) as f:
            for line in f:
                m = re.match(r'^([A-Z_][A-Z0-9_]*)\s+EQU\b', line, re.IGNORECASE)
                if m:
                    names.add(m.group(1).upper())
    return names


def _sx_ascii(s):
    return s.encode('ascii', 'replace').decode().upper()


def _sx_wrap(text, first='; ', cont='; '):
    lines, cur = [], first
    for w in text.split():
        if len(cur) + len(w) > SX_W and cur.strip() != first.strip():
            lines.append(cur.rstrip())
            cur = cont
        cur += w + ' '
    lines.append(cur.rstrip())
    return lines


class _SxNames:
    """Labels for SX80: upper case, letters and digits, 8 at most, unique
    in the module and clear of the reserved names."""

    def __init__(self, reserved):
        self.taken = set(reserved)
        self.map = {}

    def fix(self, name, sx):
        self.taken.add(sx)
        self.map[name] = sx

    def alloc(self, base):
        """A new name from BASE (not remembered under any key)."""
        import re
        s = re.sub(r'[^A-Z0-9]', '', base.upper()) or 'L'
        if s[0].isdigit():
            s = 'L' + s
        if len(s) > 8:
            s = s[:7] + s[-1]
        sx, n = s, 1
        while sx in self.taken:
            sx = s[:8 - len(str(n))] + str(n)
            n += 1
        self.taken.add(sx)
        return sx

    def add(self, name):
        """The name of label NAME, made once."""
        if name not in self.map:
            self.fix(name, self.alloc(name))
        return self.map[name]


def _render_sjasm(items, use_module):
    out = []
    for it in items:
        if it[0] == 'text':
            out.append(it[1])
        elif it[0] == 'blank':
            out.append('\n')
        elif it[0] == 'sec':
            out.append(it[1])
        elif it[0] == 'lab':
            out.append(f'{it[1]}:\n')
        else:
            _, lbl, directive, ops = it
            txt = ', '.join(_g(t, use_module) if k == 'con' else t for k, t in ops)
            out.append(_aline(lbl, directive, txt))
    return ''.join(out)


def _render_sx80(items, names, head):
    out = list(head)
    for it in items:
        if it[0] == 'text':
            continue
        if it[0] == 'blank':
            out.append('')
        elif it[0] == 'sec':
            title = _sx_ascii(it[2])
            if it[3] == 0:
                t = f'; --- {title} '
                out.append(t + '-' * (SX_W - len(t)))
            else:
                out.append(f';{"":15}- {title}')
        elif it[0] == 'lab':
            out.append(names.map[it[1]])
        else:
            _, lbl, directive, ops = it
            txt = ','.join(names.map[t] if k == 'lab' else t for k, t in ops)
            lbl = names.map[lbl] if lbl else ''
            out.append((lbl.ljust(SX_LBL) + directive.ljust(SX_OPS) + txt).rstrip())
    # at most one empty line in a row, none at the end
    res = []
    for l in out:
        if l == '' and res and res[-1] == '':
            continue
        res.append(l)
    while res and res[-1] == '':
        res.pop()
    for l in res:
        if len(l) > SX_W:
            raise ValueError(f'SX80 line over {SX_W} columns: {l}')
    return '\r\n'.join(res) + '\r\n'


def msl2z8a(input_file, output_file=None, song_name=None, sx80=None):
    """sx80: None for sjasmplus (CONST.Z8A, MODULE when the MSL names one),
    or the public name (1-8 letters/digits) of an SX80 module for
    WRECK's MUSAX.INC: the song's header, or the FX table of an FX-only
    file (with a song too, the FX table is the name's 6 first + FX)."""
    import re
    if not output_file:
        output_file = os.path.splitext(input_file)[0] + ".Z8A"

    if sx80 is not None:
        sx80 = sx80.upper()
        if not re.match(r'^[A-Z][A-Z0-9]{0,7}$', sx80):
            print(f"Error: SX80 name {sx80}: 1-8 letters or digits, a letter first.")
            return False

    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return False

    val_to_const = load_constants()

    with open(input_file, 'r') as f:
        source = f.read()

    parser = MSLParser()
    events = parser.parse(source)

    if parser.errors:
        print(f"Errors found in {input_file}:")
        for err in parser.errors:
            print(f"  Line {err.line}, col {err.column}: {err.message}")
        return False

    compiler = MSLCompiler()
    result = compiler.compile(events, base_addr=0)

    bytecode    = result["bytecode"]
    labels      = result["labels"]
    instruments = result["instruments"]
    fx_defs     = result.get("fx_definitions", {})
    metadata    = result.get("metadata", {})

    module_name = metadata.get('MODULE', '').strip().upper()
    use_module  = bool(module_name) and sx80 is None

    display_name = song_name or metadata.get("TITLE") or os.path.splitext(os.path.basename(input_file))[0]
    author       = metadata.get("AUTHOR", "")
    description  = metadata.get("DESC", "")

    if use_module:
        safe_name = module_name
    else:
        safe_name = "".join([c if c.isalnum() or c == '_' else '_' for c in display_name.upper()])

    initial_tempo = "#0600"
    from MusaX.tools.msl_parser import SetTempo
    for ev in events:
        if isinstance(ev, SetTempo):
            initial_tempo = f"#{ev.bpm_step:04X}"
            break

    # In module mode: short generic labels; the MODULE name scopes them.
    hdr_label   = 'HEADER'   if use_module else f'HDR_{safe_name}'
    itbl_label  = 'INST_TABLE' if use_module else f'INST_TABLE_{safe_name}'
    inst_label  = (lambda i: f'INST_{i}') if use_module else (lambda i: f'INST_{safe_name}_{i}')
    fxtbl_label = 'FX_TABLE' if use_module else f'FX_TABLE_{safe_name}'

    items = []
    lab = lambda s: ('lab', s)
    lit = lambda s: ('lit', s)
    con = lambda s: ('con', s)
    ref = lambda s: lit(s) if s == '0' else lab(s)

    def ln(lbl, directive, *ops):
        items.append(('ln', lbl, directive, list(ops)))

    def blank():
        items.append(('blank',))

    def text(s):
        items.append(('text', s))

    def sec(sj, sx, level=0):
        items.append(('sec', sj, sx, level))

    def defb_rows(lbl, data):
        # Max 8 bytes per DEFB line (workspace convention)
        for chunk_start in range(0, len(data), 8):
            chunk = data[chunk_start:chunk_start + 8]
            ln(lbl if chunk_start == 0 else '', 'DEFB',
               *[lit(f'#{b:02X}') for b in chunk])

    # --- File header comment ---
    text(f"; {'*' * 72}\n")
    text(f"; * {os.path.basename(output_file)} - Generated from {os.path.basename(input_file)}\n")
    text(f"; * Title: {display_name}\n")
    if author:      text(f"; * Author: {author}\n")
    if description: text(f"; * Description: {description}\n")
    text(f"; * MusaX-ML Compiler v1.1\n")
    text(f"; {'*' * 72}\n\n")

    if use_module:
        text(_line('MODULE', module_name) + '\n')
    else:
        text('INCLUDE "CONST.Z8A"\n\n')

    # --- FX Table ---
    if fx_defs:
        sec("; --- FX Table ---\n\n", 'FX TABLE')
        first = True
        for name in fx_defs:
            ln(fxtbl_label if first else '', 'DEFW', lab(f'HDR_{name}'), lit('10'))
            first = False
        ln('', 'DEFW', lit('0'), lit('0'))
        blank()

        for name, defn in fx_defs.items():
            fx_instruments = defn.get("instruments", {})
            if fx_instruments:
                fx_itbl = f'INST_TABLE_{name}'
            elif instruments:
                fx_itbl = itbl_label
            else:
                fx_itbl = '0'

            sec(f"; --- FX: {name}\n\n", f'FX: {name}', 1)
            block_labels = defn["labels"]
            ptr_a = next((l for l in block_labels if "CH_A" in l.upper() or "CHA" in l.upper()), "0")
            ptr_b = next((l for l in block_labels if "CH_B" in l.upper() or "CHB" in l.upper()), "0")
            ptr_c = next((l for l in block_labels if "CH_C" in l.upper() or "CHC" in l.upper()), "0")
            ln(f'HDR_{name}', 'DEFB', lit('TYPEFX'))
            ln('', 'DEFW', ref(ptr_a), ref(ptr_b), ref(ptr_c))
            ln('', 'DEFW', ref(fx_itbl))
            blank()

            if fx_instruments:
                sec(f"; --- Instrument Table: {name}\n\n", f'INSTRUMENTS: {name}', 1)
                first = True
                for i in range(max(fx_instruments.keys()) + 1):
                    ln(f'INST_TABLE_{name}' if first else '', 'DEFW',
                       ref(f'INST_{name}_{i}' if i in fx_instruments else '0'))
                    first = False
                blank()
                for i, data in sorted(fx_instruments.items()):
                    defb_rows(f'INST_{name}_{i}', data)
                blank()

    # --- Song Header ---
    song_labels  = [l for l in labels if l.upper() in ["CH_A", "CHA", "CH_B", "CHB", "CH_C", "CHC"]]
    fx_label_set = set()
    for d in fx_defs.values():
        fx_label_set.update(d["labels"])
    has_song_labels = any(l for l in song_labels if l not in fx_label_set)
    has_song = has_song_labels or (not fx_defs and bytecode)
    stream_start = False

    if has_song:
        sec("; --- Song Header ---\n\n", 'SONG HEADER')
        entry_a = next((l for l in song_labels if l not in fx_label_set and ("CH_A" in l.upper() or "CHA" in l.upper())), None)
        entry_b = next((l for l in song_labels if l not in fx_label_set and ("CH_B" in l.upper() or "CHB" in l.upper())), None)
        entry_c = next((l for l in song_labels if l not in fx_label_set and ("CH_C" in l.upper() or "CHC" in l.upper())), None)
        if not any([entry_a, entry_b, entry_c]) and not fx_defs:
            entry_a = "STREAM_START"
            stream_start = True

        ln(hdr_label, 'DEFB', lit('TYPESONG'))
        ln('', 'DEFW', lit(initial_tempo), ref(entry_a or "0"))
        ln('', 'DEFW', lit(initial_tempo), ref(entry_b or "0"))
        ln('', 'DEFW', lit(initial_tempo), ref(entry_c or "0"))
        ln('', 'DEFW', ref(itbl_label if instruments else '0'))
        blank()

    # --- Instrument Table ---
    if instruments:
        sec("; --- Instrument Table ---\n\n", 'INSTRUMENTS')
        first = True
        for i in range(max(instruments.keys()) + 1):
            ln(itbl_label if first else '', 'DEFW',
               ref(inst_label(i) if i in instruments else '0'))
            first = False
        blank()
        for i, data in sorted(instruments.items()):
            defb_rows(inst_label(i), data)
        blank()

    # --- Bytecode Streams ---
    sec("; --- Bytecode Streams ---\n\n", 'BYTECODE')
    offset_to_labels = {}
    for name, addr in labels.items():
        offset_to_labels.setdefault(addr, []).append(name)
    if stream_start and sx80 is not None:
        # the sjasmplus output leaves it undefined (an error there)
        offset_to_labels.setdefault(0, []).insert(0, 'STREAM_START')

    i = 0
    while i < len(bytecode):
        labels_here = offset_to_labels.get(i, [])
        # Extra labels (all but the last) get their own lines with colon
        for name in labels_here[:-1]:
            items.append(('lab', name))
        lbl = labels_here[-1] if labels_here else ''

        b = bytecode[i]

        if b in val_to_const:
            cmd_name = val_to_const[b]
            cmd_ref  = con(cmd_name)

            if cmd_name in ["CTEMPO", "CGOTO", "CRESTART", "CCALL", "REST"] or (0 <= b <= 95):
                if i + 2 < len(bytecode):
                    w_val   = bytecode[i+1] | (bytecode[i+2] << 8)
                    raw_dur = val_to_const.get(w_val, f'#{w_val:04X}')
                    ln(lbl, 'DEFB', cmd_ref)
                    if cmd_name in ["CGOTO", "CRESTART", "CCALL"]:
                        target = next((l for l, a in labels.items() if a == w_val), None)
                        ln('', 'DEFW', lab(target) if target else lit(raw_dur))
                    else:
                        ln('', 'DEFW', con(raw_dur))
                    i += 3
                else:
                    ln(lbl, 'DEFB', cmd_ref)
                    i += 1

            elif cmd_name in ["CFADE", "CCHORUS"]:
                if i + 2 < len(bytecode):
                    v1, v2 = bytecode[i+1], bytecode[i+2]
                    ln(lbl, 'DEFB', cmd_ref, lit(f'#{v1:02X}'), lit(f'#{v2:02X}'))
                    i += 3
                else:
                    ln(lbl, 'DEFB', cmd_ref)
                    i += 1

            elif cmd_name in ["CVOLUME", "CGATE", "CINST", "CLOOPS",
                              "CPHASE", "CDETUNE", "CPORTA"]:
                if i + 1 < len(bytecode):
                    ln(lbl, 'DEFB', cmd_ref, lit(f'#{bytecode[i+1]:02X}'))
                    i += 2
                else:
                    ln(lbl, 'DEFB', cmd_ref)
                    i += 1

            else:
                ln(lbl, 'DEFB', cmd_ref)
                i += 1
        else:
            ln(lbl, 'DEFB', lit(f'#{b:02X}'))
            i += 1

    if use_module:
        blank()
        text(_line('ENDMODULE', f' ; {module_name}'))

    if sx80 is None:
        with open(output_file, 'w') as f:
            f.write(_render_sjasm(items, use_module))
        print(f"Successfully compiled {input_file} -> {output_file}")
        return True

    # --- SX80: one REL module, one public name ---
    names = _SxNames(SX_RESERVED | _const_names())
    if sx80 in names.taken:
        print(f"Error: SX80 name {sx80} is a reserved or MUSAX.INC name.")
        return False
    publics = []
    if has_song:
        names.fix(hdr_label, sx80)
        publics.append(sx80)
    if fx_defs:
        fxname = sx80 if not has_song else sx80[:6] + 'FX'
        if fxname in names.taken:
            print(f"Error: SX80 name {fxname} for the FX table is taken.")
            return False
        names.fix(fxtbl_label, fxname)
        publics.append(fxname)
    # the compiler's own labels, then the MSL's in address order
    if instruments:
        names.fix(itbl_label, names.alloc('ITAB'))
    for i in sorted(instruments):
        names.fix(inst_label(i), names.alloc(f'I{i}'))
    for name, defn in fx_defs.items():
        names.fix(f'HDR_{name}', names.alloc(f'H{name}'))
        fx_instruments = defn.get("instruments", {})
        if fx_instruments:
            names.fix(f'INST_TABLE_{name}', names.alloc(f'T{name}'))
        for i in sorted(fx_instruments):
            names.fix(f'INST_{name}_{i}', names.alloc(f'I{i}{name}'))
    if stream_start:
        names.add('STREAM_START')
    renamed = []
    for name, addr in sorted(labels.items(), key=lambda kv: (kv[1], kv[0])):
        if name not in names.map:
            sx = names.add(name)
            if sx != name:
                renamed.append((sx, name))
    for it in items:            # any label left (none expected)
        if it[0] == 'ln':
            for k, t in it[3]:
                if k == 'lab' and t not in names.map:
                    names.add(t)

    base = os.path.splitext(os.path.basename(output_file))[0].upper()
    t = f'; --- {base}: MUSAX DATA '
    head = [t + '-' * (SX_W - len(t))]
    head += _sx_wrap(_sx_ascii(f'GENERATED FROM {os.path.basename(input_file)} BY '
                               f'MSL2Z8A --SX80 {sx80}: EDIT THE MSL, NOT THIS FILE'))
    head += _sx_wrap(_sx_ascii(f'TITLE: {display_name}'))
    if author:
        head += _sx_wrap(_sx_ascii(f'AUTHOR: {author}'))
    if description:
        head += _sx_wrap(_sx_ascii(f'DESCRIPTION: {description}'))
    if has_song:
        head.append(f'; {sx80} = THE SONG HEADER (MUSPLAY HL)')
    if fx_defs:
        head.append(f'; {publics[-1]} = THE FX TABLE (FXPTR)')
    if renamed:
        head.append('; LABELS OF THE MSL, RENAMED:')
        for sx, name in renamed:
            head.append(_sx_ascii(f';   {sx:<8} = {name}')[:SX_W])
    head += ['', '', f'{"":{SX_LBL}}{"INCLUDE":<{SX_OPS}}"MUSAX.INC"',
             f'{"":{SX_LBL}}{"PUBLIC":<{SX_OPS}}{",".join(publics)}', '']

    with open(output_file, 'w', newline='') as f:
        f.write(_render_sx80(items, names, head))
    print(f"Successfully compiled {input_file} -> {output_file} (SX80 {sx80})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MusaX MSL to Z8A Compiler")
    parser.add_argument("input", help="Input .MSL file")
    parser.add_argument("-o", "--output", help="Output .Z8A file")
    parser.add_argument("-s", "--song-name", help="Override song name for labels")
    parser.add_argument("--sx80", metavar="NAME",
                        help="SX80 module (WRECK's MUSAX.INC) whose public "
                             "name is NAME (1-8 letters or digits)")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    ok = msl2z8a(args.input, args.output, args.song_name, args.sx80)
    sys.exit(0 if ok else 1)
