import argparse
import struct
from pathlib import Path

from magazines import resolve_edition, resolve_source
from eos_blocks import decode_stream

_parser = argparse.ArgumentParser(
    description="Unpack the outer DIET layer of a magazine executable."
)
_parser.add_argument("-e", "--edition", default="dragon5",
                     help="magazine edition (default: dragon5)")
_parser.add_argument("source", nargs="?",
                     help="source EXE (default: data/<edition> source)")
_parser.add_argument("output", nargs="?",
                     help="raw output path (default: tmp/<edition>/<name>_diet_raw.bin)")
_args = _parser.parse_args()

_edition = resolve_edition(_args.edition)
path = _args.source or str(resolve_source(_edition))
outp = _args.output or str(
    _edition.tmp_dir / (Path(path).stem + "_diet_raw.bin")
)
d = open(path, "rb").read()

# DIET EXE v1.45f detection (deark): codestart = 16 * e_cparhdr
if len(d) < 0x40:
    raise SystemExit("file is too small to contain a DIET EXE header")
codestart = 16 * struct.unpack_from("<H", d, 8)[0]
base = codestart - 32
if base < 0 or base + 119 > len(d):
    raise SystemExit("bad DIET header geometry")
sig = bytes.fromhex("8edb8ec033f633ffb9")
pos = None
for cand in (77, 72, 52, 55):
    if d[base + cand:base + cand + len(sig)] == sig:
        pos = cand
        break
if pos != 77:
    raise SystemExit(f"unexpected DIET variant, sig pos {pos}")
dlz = base + 108
if d[dlz:dlz + 3] != b"dlz":
    raise SystemExit("missing DIET 'dlz' marker")
flags1 = d[dlz + 3]
cmpr_len = ((flags1 & 0x0f) << 16) | struct.unpack_from("<H", d, dlz + 4)[0]
orig_len = ((d[dlz + 8] & 0xfc) << 14) | struct.unpack_from("<H", d, dlz + 9)[0]
crc = struct.unpack_from("<H", d, dlz + 6)[0]
cmpr_pos = dlz + 11
if cmpr_pos + cmpr_len > len(d):
    raise SystemExit("DIET compressed payload overruns the file")
print(f"DIET EXE v1.45f flags=0x{flags1:02x} cmpr_len={cmpr_len} orig_len={orig_len} cmpr_pos={cmpr_pos}")
print(f"compressed data ends at 0x{cmpr_pos+cmpr_len:x}; file size 0x{len(d):x}")

# The LZ decoder is shared with the EOS block reader; no second implementation
# is needed here.
try:
    out, end_pos = decode_stream(d, cmpr_pos, orig_len)
except ValueError as exc:
    raise SystemExit(f"DIET decode failed: {exc}") from exc
consumed = end_pos - cmpr_pos
if len(out) > orig_len:
    raise SystemExit(f"decoded more bytes than the header allows: {len(out)} > {orig_len}")
if len(out) != orig_len:
    # DIET elides the final run of zero bytes; diet_rebuild.py pads it back.
    print(f"warning: decoded {len(out)} bytes, header says {orig_len} "
          "(trailing zeros may be elided)")
if consumed != cmpr_len:
    print(f"warning: consumed {consumed} of {cmpr_len} compressed bytes")

print(f"decompressed {len(out)} bytes (expected {orig_len}), input consumed {consumed} of {cmpr_len}")
Path(outp).write_bytes(out)
print("wrote", outp)
