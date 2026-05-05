# MusaX CLI Hub (`musax.py`)

The CLI Hub is the central entry point for the MusaX toolchain. It unifies the compiler, simulator, and analysis tools into a single command-line interface.

## Usage

```bash
python3 tools/musax.py [command] [options]
```

## Available Commands

### 1. `build`
Compiles an MSL source file into a Z8A assembly file.

**Usage:**
```bash
python3 tools/musax.py build input.msl [-o output.Z8A] [-s "Song Name"]
```

- `-o, --output`: Specify the output filename (defaults to same name as input).
- `-s, --song-name`: Override the song name used for labels and headers.

### 2. `play`
Compiles (if necessary) and plays a music file using the high-precision simulator.

**Usage:**
```bash
python3 tools/musax.py play input.msl [-l loops]
python3 tools/musax.py play input.Z8A [-l loops]
```

- `-l, --loops`: Number of times to loop the playback (0 = infinite).
- **Note:** If an `.msl` file is provided, it is compiled in a temporary directory before playback.

### 3. `info`
Displays technical information and metadata about a music file.

**Usage:**
```bash
python3 tools/musax.py info input.msl
```

**Information shown:**
- Title, Author, and Description (from `@TITLE`, `@AUTHOR`, `@DESC` tags).
- Number of instrument definitions.
- List of channel entry labels.
- Total bytecode size.

## Why use the Hub?
Instead of managing multiple independent scripts (`msl2z8a.py`, `musax_sim.py`), the Hub provides a streamlined workflow where you can go from source code to playback in a single command. It also ensures that all tools use the same core parser and compiler logic.
