" Vim syntax file
" Language:    MusaX Music Language (MSL)
" Maintainer:  Antigravity AI
" Version:     1.1
" Location:    MusaX/tools/msl.vim

if exists("b:current_syntax")
  finish
endif

" Comments
syn match mslComment "//.*$" contains=mslTodo
syn keyword mslTodo TODO FIXME XXX contained

" Keywords and Parameter Blocks
syn keyword mslKeyword ADSR LFO FLAGS PHRASE
syn match mslChannel "\v^[ \t]*\cCH_[A-C]:"
syn match mslLabel "\v^[ \t]*[A-Z0-9_\.]+:\d@!"

" Directives / At-Commands (e.g. @TITLE, @INST, @I0, @T#0600)
syn match mslDirective "\v\@[A-Za-z0-9_#\-]+"

" Notes & Rests (A-G and R, e.g. C, F#, Dm, R0, G8t, A.)
" Matches note names A-G and R (case-insensitive) followed by optional accidental (#, +, -, b), optional duration, and optional modifiers (dot, triplet)
syn match mslNote "\v\c<[A-GR][#+\-b]?\d*[.t]*>"

" Octave and Length Commands
syn match mslOctave "\v\c<O\d+>"
syn match mslLength "\v\c<L\d+t{0,1}>"
syn match mslOctaveShift "[<>]"

" Strings
syn region mslString start='"' end='"' contains=@Spell

" Numbers (Decimal and Hex)
syn match mslNumber "\v<\d+>"
syn match mslHexNumber "\v\#[0-9A-Fa-f]+"

" Loop markers & Subroutine Calls
syn match mslLoop "\v[\{\}]"
syn match mslLoopRepeat "\v\}\s*\d+t{0,1}"

" Highlighting Links
hi def link mslComment      Comment
hi def link mslTodo         Todo
hi def link mslKeyword      Keyword
hi def link mslChannel      Type
hi def link mslLabel        Label
hi def link mslDirective    PreProc
hi def link mslNote         Identifier
hi def link mslOctave       Special
hi def link mslLength       Special
hi def link mslOctaveShift  Operator
hi def link mslString       String
hi def link mslNumber       Number
hi def link mslHexNumber    Number
hi def link mslLoop         Delimiter
hi def link mslLoopRepeat   Delimiter

let b:current_syntax = "msl"
