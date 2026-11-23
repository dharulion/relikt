"""Locate and decode DIET/EOS-style compressed blocks.

The DIET v1.45 compression format used by the E.O.S. LLINK linker stores a
small self-extractor stub followed by a 3-byte magic ("dlz" or "EOS") and the
same 8 header bytes as DIET:

    magic[3] flags[1] cmpr_len_lo[2] crc[2] orig_hi[1] orig_len_lo[2]

then a bit-oriented LZ stream.  This module reuses the DIET LZ decoder that
was validated against the outer DRAGON.EXE layer.
"""
import struct

STUB = b"\xb4\x4c\xcd\x21\x9d\x89"
MAGICS = (b"dlz", b"EOS")

# Real editions contain a few dozen blocks at most. A much larger count means
# the input is corrupt or crafted to exhaust memory/CPU, so refuse to scan it.
MAX_BLOCKS = 1024


class BitReader:
    def __init__(self, data, pos):
        self.data = data
        self.pos = pos
        self.buf = 0
        self.nbits = 0
        self.err = False

    def fill(self):
        if self.err:
            return
        if self.pos + 2 > len(self.data):
            self.err = True
            return
        for _ in range(2):
            self.buf |= self.data[self.pos] << self.nbits
            self.nbits += 8
            self.pos += 1

    def bit(self):
        if self.err:
            return 0
        if self.nbits == 0:
            self.fill()
        v = self.buf & 1
        self.buf >>= 1
        self.nbits -= 1
        if self.nbits == 0:
            self.fill()
        return v

    def rawbyte(self):
        if self.pos >= len(self.data):
            self.err = True
            return 0
        b = self.data[self.pos]
        self.pos += 1
        return b


def _read_matchlen(br):
    nb = 0
    while True:
        x = br.bit()
        nb += 1
        if x:
            return 2 + nb
        if nb >= 4:
            break
    x1 = br.bit()
    x2 = br.bit()
    if x1:
        return 7 + x2
    if x2 == 0:
        x3 = br.bit()
        x4 = br.bit()
        x5 = br.bit()
        return 9 + 4 * x3 + 2 * x4 + x5
    return 17 + br.rawbyte()


def decode_stream(data, pos, orig_len):
    br = BitReader(data, pos)
    out = bytearray()
    while len(out) < orig_len:
        if br.err:
            raise ValueError("input exhausted at %d/%d" % (len(out), orig_len))
        if br.bit():  # literal
            out.append(br.rawbyte())
            continue
        x2 = br.bit()
        v = br.rawbyte()
        if x2 == 0:
            a1 = br.bit()
            if a1:
                matchlen = 2
                a2 = br.bit()
                a3 = br.bit()
                a4 = br.bit()
                matchpos = 2303 - (1024 * a2 + 512 * a3 + 256 * a4 + v)
            elif v != 0xff:
                matchlen = 2
                matchpos = 0xff - v
            else:
                a2 = br.bit()
                if a2 == 0:
                    break
                # segment refresh: no output
                continue
        else:
            a1 = br.bit()
            a2 = br.bit()
            if a2:
                matchpos = 511 - (256 * a1 + v)
            else:
                a3 = br.bit()
                if a3:
                    matchpos = 1023 - (256 * a1 + v)
                else:
                    a4 = br.bit()
                    a5 = br.bit()
                    if a5:
                        matchpos = 2047 - (512 * a1 + 256 * a4 + v)
                    else:
                        a6 = br.bit()
                        a7 = br.bit()
                        if a7:
                            matchpos = 4095 - (1024 * a1 + 512 * a4 + 256 * a6 + v)
                        else:
                            a8 = br.bit()
                            matchpos = 8191 - (
                                2048 * a1 + 1024 * a4 + 512 * a6 + 256 * a8 + v
                            )
            matchlen = _read_matchlen(br)
        dist = matchpos + 1
        if dist > len(out) or matchlen > dist:
            raise ValueError(
                "bad match at out=%d dist=%d len=%d" % (len(out), dist, matchlen)
            )
        start = len(out) - dist
        out += out[start:start + matchlen]
    return out, br.pos


def parse_header(data, stub_off):
    """Return (magic, flags, cmpr_len, crc, orig_len, stream_off) or None."""
    if data[stub_off:stub_off + 6] != STUB:
        return None
    # 6-byte stub + 3-byte magic + 8-byte DIET header.
    if stub_off + 17 > len(data):
        return None
    magic = data[stub_off + 6:stub_off + 9]
    if magic not in MAGICS:
        return None
    h = stub_off + 9
    flags = data[h]
    cmpr_len = ((flags & 0x0F) << 16) | struct.unpack_from("<H", data, h + 1)[0]
    crc = struct.unpack_from("<H", data, h + 3)[0]
    orig_len = ((data[h + 5] & 0xFC) << 14) | struct.unpack_from("<H", data, h + 6)[0]
    return magic, flags, cmpr_len, crc, orig_len, h + 8


def find_blocks(data):
    blocks = []
    s = 0
    while True:
        i = data.find(STUB, s)
        if i < 0:
            break
        try:
            hdr = parse_header(data, i)
        except (IndexError, struct.error):
            hdr = None
        if hdr:
            blocks.append((i,) + hdr)
            if len(blocks) > MAX_BLOCKS:
                raise ValueError("too many EOS blocks (limit %d)" % MAX_BLOCKS)
        s = i + 1
    return blocks


def decode_block(data, block, verbose=False):
    stub_off, magic, flags, cmpr_len, crc, orig_len, stream_off = block
    end = stream_off + cmpr_len
    if end > len(data):
        raise ValueError("block overruns file")
    out, pos = decode_stream(data[:end], stream_off, orig_len)
    if len(out) != orig_len:
        raise ValueError("length mismatch: %d != %d" % (len(out), orig_len))
    return out, pos - stream_off


if __name__ == "__main__":
    import argparse

    from magazines import resolve_edition, resolve_source

    parser = argparse.ArgumentParser(
        description="Scan a magazine binary for EOS compressed blocks."
    )
    parser.add_argument("-e", "--edition", default="dragon5",
                        help="magazine edition (default: dragon5)")
    parser.add_argument("path", nargs="?",
                        help="binary to scan (default: data/<edition> source)")
    args = parser.parse_args()

    path = args.path or str(resolve_source(resolve_edition(args.edition)))
    d = open(path, "rb").read()
    blocks = find_blocks(d)
    print("%d blocks" % len(blocks))
    for b in blocks:
        stub_off, magic, flags, cmpr_len, crc, orig_len, stream_off = b
        print(
            "%08x %s flags=%02x cmpr=%d orig=%d stream=%08x end=%08x"
            % (stub_off, magic.decode(), flags, cmpr_len, orig_len,
               stream_off, stream_off + cmpr_len)
        )
