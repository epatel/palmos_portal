#!/usr/bin/env python3
"""Convert Star Wars Asciimation HTML to PalmOS sw1.pdb format.

Extracts the film data from the HTML page, applies LZW compression
matching the StarWarsAscii PalmOS viewer format, and creates a PDB file.

Usage:
    python convert_film.py input.html output.pdb
    python convert_film.py --from-text film.txt output.pdb

The StarWarsAscii viewer expects:
- PDB type=DATA, creator=SWIV, name=sw1.txt
- Records containing LZW-compressed 16KB chunks
- Text with \\r\\n line endings, max 67 chars per line
- Frame format: duration number + 14 lines of ASCII art
"""

import argparse
import sys
import os

# Add parent paths for palm module access
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from palm.pdb import PalmDatabase, Record


def lzw_compress(data: bytes) -> bytes:
    """LZW compress data matching the StarWarsAscii PalmOS viewer.

    Returns compressed bytes with mode byte 0x02 prefix.
    Uses 9-bit initial code size, growing dictionary.
    """
    dict_map = {bytes([i]): i for i in range(256)}
    next_code = 256
    code_size = 9
    threshold = 512
    out_bits = 0
    out_bit_count = 0
    output = bytearray()

    def emit(code):
        nonlocal out_bits, out_bit_count
        out_bits |= code << out_bit_count
        out_bit_count += code_size
        while out_bit_count >= 8:
            output.append(out_bits & 0xFF)
            out_bits >>= 8
            out_bit_count -= 8

    w = bytes([data[0]])
    for i in range(1, len(data)):
        c = bytes([data[i]])
        wc = w + c
        if wc in dict_map:
            w = wc
        else:
            emit(dict_map[w])
            if next_code < 65536:
                dict_map[wc] = next_code
                next_code += 1
            # Grow code size AFTER next_code exceeds threshold
            if next_code > threshold and code_size < 16:
                threshold *= 2
                code_size += 1
            w = c
    emit(dict_map[w])
    if out_bit_count > 0:
        output.append(out_bits & 0xFF)
    return b'\x02' + bytes(output)


def lzw_decompress(data: bytes) -> bytes:
    """LZW decompress data from StarWarsAscii PalmOS format."""
    if not data or data[0] != 2:
        return data[1:] if data else b''

    src = data[1:]
    bit_buf = 0
    bit_count = 0
    code_size = 9
    next_code = 256
    threshold = 512
    src_idx = 0
    dict_prefix = [0] * 65536
    dict_char = [0] * 65536
    for i in range(256):
        dict_prefix[i] = -1
        dict_char[i] = i
    output = bytearray()
    prev_code = -1

    while src_idx < len(src):
        while bit_count < code_size and src_idx < len(src):
            bit_buf |= src[src_idx] << bit_count
            src_idx += 1
            bit_count += 8
        if bit_count < code_size:
            break
        code = bit_buf & ((1 << code_size) - 1)
        bit_buf >>= code_size
        bit_count -= code_size

        if code < next_code:
            decoded = []
            c = code
            while c >= 256:
                decoded.append(dict_char[c])
                c = dict_prefix[c]
            decoded.append(c)
            decoded.reverse()
            output.extend(decoded)
            if prev_code >= 0 and next_code < 65536:
                dict_prefix[next_code] = prev_code
                dict_char[next_code] = decoded[0]
                next_code += 1
                if next_code >= threshold and code_size < 16:
                    threshold *= 2
                    code_size += 1
        elif code == next_code:
            decoded = []
            c = prev_code
            while c >= 256:
                decoded.append(dict_char[c])
                c = dict_prefix[c]
            decoded.append(c)
            decoded.reverse()
            decoded.append(decoded[0])
            output.extend(decoded)
            dict_prefix[next_code] = prev_code
            dict_char[next_code] = decoded[0]
            next_code += 1
            if next_code >= threshold and code_size < 16:
                threshold *= 2
                code_size += 1
        prev_code = code
    return bytes(output)


def extract_film_from_html(html_path: str) -> str:
    """Extract the film variable from the Asciimation HTML page."""
    with open(html_path, 'r') as f:
        html = f.read()

    start = html.index("var film = '") + len("var film = '")
    end = start
    while end < len(html):
        if html[end] == "'" and html[end - 1] != '\\':
            break
        end += 1

    raw = html[start:end]

    # Proper JS unescape
    film = ''
    i = 0
    while i < len(raw):
        if raw[i] == '\\' and i + 1 < len(raw):
            nc = raw[i + 1]
            if nc == 'n':
                film += '\n'
                i += 2
            elif nc == "'":
                film += "'"
                i += 2
            elif nc == '\\':
                film += '\\'
                i += 2
            else:
                film += '\\'
                i += 1
        else:
            film += raw[i]
            i += 1

    return film


def film_to_pdb(film_text: str, output_path: str, max_line_width: int = 67):
    """Convert film text to PalmOS PDB format with LZW compression."""
    # Convert line endings and truncate lines
    lines = film_text.split('\n')
    truncated = [l[:max_line_width] for l in lines]
    film_data = '\r\n'.join(truncated).encode('ascii', errors='replace')

    # Split into 16KB chunks and compress
    chunk_size = 16384
    records = []
    for i in range(0, len(film_data), chunk_size):
        chunk = film_data[i:i + chunk_size]
        compressed = lzw_compress(chunk)
        records.append(Record(data=compressed, attributes=0x40, unique_id=0))

    # Create PDB
    db = PalmDatabase(
        name='sw1.txt',
        db_type='DATA',
        creator='SWIV',
        attributes=2048,
        version=0,
        records=records,
    )
    db.to_file(output_path)

    print(f"Input: {len(film_data)} bytes, {len(lines)} lines")
    print(f"Output: {output_path}")
    print(f"  {len(records)} records, {sum(len(r.data) for r in records)} bytes compressed")
    print(f"  Ratio: {sum(len(r.data) for r in records) / len(film_data) * 100:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Convert Star Wars Asciimation to PalmOS PDB")
    parser.add_argument("input", help="Input HTML file or text file (with --from-text)")
    parser.add_argument("output", help="Output PDB file path")
    parser.add_argument("--from-text", action="store_true",
                        help="Input is a plain text film file, not HTML")
    parser.add_argument("--max-width", type=int, default=67,
                        help="Max characters per line (default: 67)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify by decompressing after creation")
    args = parser.parse_args()

    if args.from_text:
        with open(args.input, 'r') as f:
            film = f.read()
    else:
        print(f"Extracting film from {args.input}...")
        film = extract_film_from_html(args.input)

    print(f"Converting {len(film)} chars...")
    film_to_pdb(film, args.output, args.max_width)

    if args.verify:
        print("Verifying...")
        db = PalmDatabase.from_file(args.output)
        decompressed = b''.join(lzw_decompress(r.data) for r in db.records)
        lines = film.split('\n')
        expected = '\r\n'.join(l[:args.max_width] for l in lines).encode('ascii', errors='replace')
        if decompressed == expected:
            print("Verification: OK")
        else:
            print(f"Verification: MISMATCH! expected={len(expected)} got={len(decompressed)}")
            sys.exit(1)


if __name__ == '__main__':
    main()
