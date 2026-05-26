import os
import urllib.request
import re

def reverse_bits(b):
    # Reverse the 8 bits of byte b (LSB-first to MSB-first)
    return int('{:08b}'.format(b)[::-1], 2)

def main():
    src_dir = os.path.dirname(os.path.abspath(__file__))
    vram_path = os.path.join(src_dir, 'VRAM.BIN')
    
    url = "https://raw.githubusercontent.com/dhepper/font8x8/master/font8x8_basic.h"
    print(f"Fetching standard font from {url}...")
    try:
        with urllib.request.urlopen(url) as response:
            content = response.read().decode('utf-8')
    except Exception as e:
        print(f"Error fetching font: {e}")
        return

    # Extract all arrays like { 0xXX, 0xXX, ... }
    # Each row is 8 hex values
    pattern = r'\{\s*(0x[0-9a-fA-F]{2}\s*,\s*){7}0x[0-9a-fA-F]{2}\s*\}'
    matches = re.finditer(pattern, content)
    
    data = bytearray()
    for match in matches:
        hex_vals = re.findall(r'0x[0-9a-fA-F]{2}', match.group(0))
        bytes_vals = [int(val, 16) for val in hex_vals]
        # Reverse the bits of each byte because Hepper's font is LSB-first
        # and MSX is MSB-first!
        bytes_vals_rev = [reverse_bits(b) for b in bytes_vals]
        data.extend(bytes_vals_rev)
        
    print(f"Parsed {len(data) // 8} characters from header.")
    
    # We expect 128 characters (1024 bytes) from the basic set
    if len(data) < 1024:
        print(f"Error: only parsed {len(data)} bytes, expected at least 1024.")
        return
        
    # Truncate to 1024 bytes (128 characters)
    data = bytearray(data[:1024])
    
    # Expand to 2048 bytes (256 characters)
    data = data.ljust(2048, b'\x00')
    
    # Copy Uppercase letters 'A'-'Z' (ASCII 65-90) to Lowercase 'a'-'z' (ASCII 97-122)
    # This gives us two sets of uppercase letters (useful for multi-color font rendering)
    for i in range(26):
        src_offset = (65 + i) * 8
        dst_offset = (97 + i) * 8
        data[dst_offset:dst_offset+8] = data[src_offset:src_offset+8]
        
    # Copy numbers '0'-'9' (ASCII 48-57) to custom Cyan range (160-169)
    for i in range(10):
        src_offset = (48 + i) * 8
        dst_offset = (160 + i) * 8
        data[dst_offset:dst_offset+8] = data[src_offset:src_offset+8]
        
    # Define custom box border characters at 0x80 to 0x85
    # Char 0x80: Vertical line │ (centered vertical line, 2px wide)
    data[0x80*8 : 0x80*8 + 8] = [0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18]
    # Char 0x81: Horizontal line ─ (centered horizontal line, 2px high)
    data[0x81*8 : 0x81*8 + 8] = [0x00, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00]
    # Char 0x82: Top-Left corner ┌
    data[0x82*8 : 0x82*8 + 8] = [0x00, 0x00, 0x00, 0x1F, 0x1F, 0x18, 0x18, 0x18]
    # Char 0x83: Top-Right corner ┐
    data[0x83*8 : 0x83*8 + 8] = [0x00, 0x00, 0x00, 0xF8, 0xF8, 0x18, 0x18, 0x18]
    # Char 0x84: Bottom-Left corner └
    data[0x84*8 : 0x84*8 + 8] = [0x18, 0x18, 0x18, 0x1F, 0x1F, 0x00, 0x00, 0x00]
    # Char 0x85: Bottom-Right corner ┘
    data[0x85*8 : 0x85*8 + 8] = [0x18, 0x18, 0x18, 0xF8, 0xF8, 0x00, 0x00, 0x00]
    
    # Define custom volume bar character patterns at 0xE1 to 0xE8
    # Char 0xE1: 1/8 columns filled from left
    data[0xE1*8 : 0xE1*8 + 8] = [0x80] * 8
    # Char 0xE2: 2/8 columns filled
    data[0xE2*8 : 0xE2*8 + 8] = [0xC0] * 8
    # Char 0xE3: 3/8 columns filled
    data[0xE3*8 : 0xE3*8 + 8] = [0xE0] * 8
    # Char 0xE4: 4/8 columns filled
    data[0xE4*8 : 0xE4*8 + 8] = [0xF0] * 8
    # Char 0xE5: 5/8 columns filled
    data[0xE5*8 : 0xE5*8 + 8] = [0xF8] * 8
    # Char 0xE6: 6/8 columns filled
    data[0xE6*8 : 0xE6*8 + 8] = [0xFC] * 8
    # Char 0xE7: 7/8 columns filled
    data[0xE7*8 : 0xE7*8 + 8] = [0xFE] * 8
    # Char 0xE8: 8/8 columns filled (Full block)
    data[0xE8*8 : 0xE8*8 + 8] = [0xFF] * 8
    
    # Define custom stippled volume bar character patterns at 0xF1 to 0xF8
    # Char 0xF1: 1/8 columns filled stippled
    data[0xF1*8 : 0xF1*8 + 8] = [0x00, 0x80, 0x00, 0x80, 0x00, 0x80, 0x00, 0x80]
    # Char 0xF2: 2/8 columns filled stippled
    data[0xF2*8 : 0xF2*8 + 8] = [0x40, 0x80, 0x40, 0x80, 0x40, 0x80, 0x40, 0x80]
    # Char 0xF3: 3/8 columns filled stippled
    data[0xF3*8 : 0xF3*8 + 8] = [0x40, 0xA0, 0x40, 0xA0, 0x40, 0xA0, 0x40, 0xA0]
    # Char 0xF4: 4/8 columns filled stippled
    data[0xF4*8 : 0xF4*8 + 8] = [0x50, 0xA0, 0x50, 0xA0, 0x50, 0xA0, 0x50, 0xA0]
    # Char 0xF5: 5/8 columns filled stippled
    data[0xF5*8 : 0xF5*8 + 8] = [0x50, 0xA8, 0x50, 0xA8, 0x50, 0xA8, 0x50, 0xA8]
    # Char 0xF6: 6/8 columns filled stippled
    data[0xF6*8 : 0xF6*8 + 8] = [0x54, 0xA8, 0x54, 0xA8, 0x54, 0xA8, 0x54, 0xA8]
    # Char 0xF7: 7/8 columns filled stippled
    data[0xF7*8 : 0xF7*8 + 8] = [0x54, 0xAA, 0x54, 0xAA, 0x54, 0xAA, 0x54, 0xAA]
    # Char 0xF8: 8/8 columns filled stippled (Full block stippled)
    data[0xF8*8 : 0xF8*8 + 8] = [0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA]
    
    with open(vram_path, 'wb') as f:
        f.write(data)
        
    print(f"Successfully generated custom {vram_path}")

if __name__ == '__main__':
    main()
