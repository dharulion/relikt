import argparse
import struct

from magazines import resolve_edition

_parser = argparse.ArgumentParser(
    description="Rebuild a DOS EXE from a decompressed DIET payload."
)
_parser.add_argument("-e", "--edition", default="dragon5",
                     help="magazine edition (default: dragon5)")
_parser.add_argument("source", nargs="?",
                     help="raw DIET payload (default: tmp/<edition> input)")
_parser.add_argument("output", nargs="?",
                     help="rebuilt EXE (default: tmp/<edition> output)")
_args = _parser.parse_args()

_edition = resolve_edition(_args.edition)
src = _args.source or str(_edition.tmp_dir / f"{_edition.key}_diet_raw.bin")
dst = _args.output or str(_edition.tmp_dir / f"{_edition.key}_unpacked.exe")
blob = open(src, "rb").read()
if len(blob) < 28:
    raise SystemExit("payload is too small to contain an MZ header")

# Locate the embedded original MZ header (DIET v1.45f intermediate layout).
# It sits at the position recorded by the original header's own fields. We can
# find it robustly by scanning near the end for a plausible MZ header.
mz = None
for i in range(len(blob) - 28, max(0, len(blob) - 4096), -1):
    if blob[i:i+2] in (b"MZ", b"ZM"):
        crlc = struct.unpack_from("<H", blob, i + 6)[0]
        lfarlc = struct.unpack_from("<H", blob, i + 24)[0]
        if crlc < 1000 and lfarlc >= 28 and lfarlc < 4096:
            mz = i
            break
if mz is None:
    raise SystemExit("embedded MZ header not found")
print("embedded MZ at", hex(mz))

hdr = bytearray(blob[mz:mz + 28])
hdr[3] &= 0x01
(
    cblp, cp, crlc, cparhdr, minalloc, maxalloc, ss, sp, csum, ip, cs, lfarlc, ovno
) = struct.unpack_from("<13H", hdr, 2)
num_relocs = crlc
reloc_table_pos = lfarlc
start_of_dos_code = cparhdr * 16
print(f"num_relocs={num_relocs} lfarlc={reloc_table_pos} cparhdr={cparhdr} start={start_of_dos_code}")

cdata1_size = reloc_table_pos - 28
cdata2_size = start_of_dos_code - (reloc_table_pos + 4 * num_relocs)
encoded_pos = mz + reloc_table_pos
if cdata1_size < 0 or cdata2_size < 0:
    raise SystemExit("bad header geometry")

# Decode the delta/RLE-coded relocation table.
ipos = encoded_pos
seg = 0
offs = 0
relocs = bytearray()
for _ in range(num_relocs):
    if ipos + 2 > len(blob):
        raise SystemExit("truncated relocation table")
    n = struct.unpack_from("<H", blob, ipos)[0]
    ipos += 2
    if n & 0x8000:
        if n >= 0xC000:
            offs += n
        else:
            offs += n - 0x8000
        offs &= 0xFFFF
    else:
        if ipos + 2 > len(blob):
            raise SystemExit("truncated relocation table")
        seg = n
        offs = struct.unpack_from("<H", blob, ipos)[0]
        ipos += 2
    relocs += struct.pack("<HH", offs, seg)
encoded_size = ipos - encoded_pos
print(f"encoded reloc bytes={encoded_size}, decoded={len(relocs)}")

out = bytearray()
out += hdr
out += blob[mz + 28: mz + 28 + cdata1_size]
out += relocs
cdata2 = blob[encoded_pos + encoded_size: encoded_pos + encoded_size + cdata2_size]
if len(cdata2) < cdata2_size:
    # The decompressed image may be short by the final run of zero bytes
    # (deark elides them); cdata2 is a fixed-size field before the image.
    cdata2 += b"\x00" * (cdata2_size - len(cdata2))
out += cdata2
out += blob[:mz]
print(f"rebuilt EXE: {len(out)} bytes -> {dst}")
open(dst, "wb").write(out)
