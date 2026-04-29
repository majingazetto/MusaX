#!/usr/bin/env python3
import wave
import struct
import math
import subprocess
import tempfile
import os
import time

SAMPLE_RATE = 44100
AMPLITUDE = 10000

def generate_samples():
    samples = []
    # Do, Mi, Sol, Accord
    notes = [261.63, 329.63, 392.00]
    
    # Sequence
    for freq in notes:
        for i in range(int(SAMPLE_RATE * 0.5)):
            period = SAMPLE_RATE / freq
            val = AMPLITUDE if (i % period) < (period / 2) else -AMPLITUDE
            samples.append(int(val))
            
    # Chord
    for i in range(int(SAMPLE_RATE * 1.0)):
        val = 0
        for freq in notes:
            p = SAMPLE_RATE / freq
            val += (AMPLITUDE // 3) if (i % p) < (p / 2) else -(AMPLITUDE // 3)
        samples.append(int(val))
        
    return samples

def main():
    print("[*] Testing audio with temporary WAV file (like tsxplay)...")
    samples = generate_samples()
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        temp_name = tmp.name
        
    try:
        with wave.open(temp_name, "w") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            for s in samples:
                wav.writeframesraw(struct.pack('<h', s))
        
        print(f"-> Playing: {temp_name}")
        subprocess.run(['aplay', '-q', '-c', '1', temp_name])
        
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)
    
    print("[*] Test finished.")

if __name__ == "__main__":
    main()
