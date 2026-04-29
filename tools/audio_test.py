#!/usr/bin/env python3
import subprocess
import math
import struct
import time
import sys

# Audio parameters
SAMPLE_RATE = 44100
AMPLITUDE = 10000

def play_note(freq, duration_sec, process):
    num_samples = int(SAMPLE_RATE * duration_sec)
    buffer = bytearray()
    for i in range(num_samples):
        # Square wave
        period = SAMPLE_RATE / freq
        sample = AMPLITUDE if (i % period) < (period / 2) else -AMPLITUDE
        buffer.extend(struct.pack('<h', int(sample)))
    
    process.stdin.write(buffer)
    process.stdin.flush()

def main():
    print("[*] Testing audio with aplay (Do-Mi-Sol)...")
    
    # Standard aplay command for raw PCM from stdin
    cmd = ['aplay', '-t', 'raw', '-f', 'S16_LE', '-r', str(SAMPLE_RATE), '-c', '1', '-']
    
    try:
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Do (C4 ~ 261.63 Hz)
        print("-> Playing DO (C4)")
        play_note(261.63, 0.5, process)
        
        # Mi (E4 ~ 329.63 Hz)
        print("-> Playing MI (E4)")
        play_note(329.63, 0.5, process)
        
        # Sol (G4 ~ 392.00 Hz)
        print("-> Playing SOL (G4)")
        play_note(392.00, 0.5, process)
        
        # Chord (Do-Mi-Sol)
        print("-> Playing CHORD")
        num_samples = int(SAMPLE_RATE * 1.0)
        buffer = bytearray()
        for i in range(num_samples):
            val = 0
            for f in [261.63, 329.63, 392.00]:
                p = SAMPLE_RATE / f
                val += (AMPLITUDE // 3) if (i % p) < (p / 2) else -(AMPLITUDE // 3)
            buffer.extend(struct.pack('<h', int(val)))
        process.stdin.write(buffer)
        process.stdin.flush()
        
        time.sleep(1.0) # Let it finish playing
        process.terminate()
        print("[*] Test finished.")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
