#!/usr/bin/env python3
import sys
import os
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TextArea, Static, Label
from textual.containers import Container, Horizontal, Vertical
from textual.binding import Binding

# Ensure we can import MusaX tools
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.append(project_root)

from MusaX.tools.msl_parser import MSLParser
from MusaX.tools.msl_compiler import MSLCompiler

class ChannelEditor(Vertical):
    """A widget for editing a single MusaX channel."""
    
    def __init__(self, channel_name: str, **kwargs):
        super().__init__(**kwargs)
        self.channel_name = channel_name

    def compose(self) -> ComposeResult:
        yield Label(f"Channel {self.channel_name}")
        yield TextArea(language="python", classes="editor", id=f"editor-{self.channel_name}")

class MusaXEditorApp(App):
    """MusaX-ML TUI Editor."""
    
    # ... (CSS and BINDINGS remain the same)
    CSS = """
    Screen {
        background: #1e1e1e;
    }
    
    Horizontal {
        height: 1fr;
    }
    
    ChannelEditor {
        width: 1fr;
        border: solid #333;
        margin: 1;
        padding: 1;
    }
    
    .editor {
        height: 1fr;
        border: none;
        background: #000;
    }
    
    #status-pane {
        height: 10;
        border: solid #333;
        margin: 1;
        padding: 1;
        background: #111;
        color: #888;
    }
    
    #instr-pane {
        width: 40;
        border: solid #333;
        margin: 1;
        padding: 1;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "play", "Play"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ChannelEditor("A")
            yield ChannelEditor("B")
            yield ChannelEditor("C")
            with Vertical(id="instr-pane"):
                yield Label("Instruments")
                yield Static("ADSR: 10, 5, 255, 10\nLFO: 1, 0, 2, 12, 20")
        yield Static("Status: Ready", id="status-pane")
        yield Footer()

    def action_play(self) -> None:
        status = self.query_one("#status-pane")
        status.update("Status: Compiling...")
        
        # 1. Collect MML
        try:
            mml_a = self.query_one("#editor-A").text
            mml_b = self.query_one("#editor-B").text
            mml_c = self.query_one("#editor-C").text
            
            # Create a full MSL source
            full_msl = []
            if mml_a.strip():
                full_msl.append("CH_A:")
                full_msl.append(mml_a)
            if mml_b.strip():
                full_msl.append("CH_B:")
                full_msl.append(mml_b)
            if mml_c.strip():
                full_msl.append("CH_C:")
                full_msl.append(mml_c)
            
            if not full_msl:
                status.update("Status: Error - No music data to play!")
                return
                
            msl_source = "\n".join(full_msl)
            
            # 2. Compile
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.msl', mode='w', delete=False) as tmp:
                tmp.write(msl_source)
                tmp_msl = tmp.name
                
            status.update(f"Status: Playing {tmp_msl}...")
            
            # 3. Launch simulator
            # On macOS, we can open a new terminal window for the simulator
            # This keeps the TUI editor alive and visible.
            cmd = f"python3 MusaX/tools/musax.py play {tmp_msl}"
            
            if sys.platform == 'darwin':
                # Create a temporary script to run the command in a new terminal
                with tempfile.NamedTemporaryFile(suffix='.sh', mode='w', delete=False) as sh:
                    sh.write(f"#!/bin/bash\n{cmd}\n")
                    sh_name = sh.name
                os.chmod(sh_name, 0o755)
                subprocess.Popen(['open', '-a', 'Terminal', sh_name])
            else:
                # Fallback: run in background and hope audio works (no dashboard)
                # Or use x-terminal-emulator on Linux
                subprocess.Popen(['x-terminal-emulator', '-e', f'bash -c "{cmd}"'])
                
            status.update("Status: Simulator launched in new terminal.")
            
        except Exception as e:
            status.update(f"Status: Error - {str(e)}")

if __name__ == "__main__":
    app = MusaXEditorApp()
    app.run()
