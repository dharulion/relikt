#!/usr/bin/env python3
"""Extract music and graphics from DRAGON #5's DRAGON.EXE.

The magazine executable is an E.O.S./DIET LLINK container.  After the
executable block it contains:

    block 1..3   320x200 indexed images, 768-byte VGA palette after each image
    block 4      640x480 indexed image, 768-byte VGA palette after the image
    block 5      graphics bank: one 640x300 picture with its own palette,
                 followed by six 640x300 pictures sharing the 256-colour
                 magazine palette stored at block offset 0x10B183
    block 6..11  complete FastTracker II XM modules

Files are written below out/<edition>/music and out/<edition>/graphics.
Existing article text is never touched.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

SCRIPT = Path(__file__).resolve()
TOOLS_DIR = SCRIPT.parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# magazines.py puts the shared tools/ directory on sys.path, so eos_blocks is
# importable even when this script lives in tools/dragon5/ and is run directly.
from magazines import (  # noqa: E402 - path bootstrap must run first
    add_edition_args,
    relative_to_root,
    resolve_edition,
    resolve_out_dir,
    resolve_source,
)
from eos_blocks import decode_block, find_blocks  # noqa: E402

XM_MAGIC = b"Extended Module: "


@dataclass
class XMInfo:
    block_no: int
    block_offset: int
    data: bytes
    name: str
    version: int
    header_size: int
    song_length: int
    channels: int
    patterns: int
    instruments: int
    tempo: int
    bpm: int


@dataclass
class ImageAsset:
    filename: str
    width: int
    height: int
    pixels: bytes
    palette: bytes
    block_no: int
    offset: int
    note: str


def scale_vga(value: int) -> int:
    """Scale a 6-bit VGA DAC value (0..63) to 8-bit."""
    value &= 0x3F
    return (value << 2) | (value >> 4)


def png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_indexed_png(path: Path, width: int, height: int,
                      pixels: bytes, palette: bytes) -> None:
    """Write an 8-bit indexed PNG using the standard library only."""
    if len(pixels) != width * height:
        raise ValueError(
            "%s: pixel count %d != %d" % (path.name, len(pixels), width * height)
        )
    if len(palette) != 768:
        raise ValueError("%s: palette must be 768 bytes" % path.name)

    plte = bytes(scale_vga(v) for v in palette)
    scanlines = bytearray()
    stride = width
    for y in range(height):
        scanlines.append(0)  # PNG filter: None
        scanlines += pixels[y * stride:(y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    idat = zlib.compress(bytes(scanlines), 9)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"PLTE", plte)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def parse_xm(block_no: int, block_offset: int, data: bytes) -> XMInfo:
    if not data.startswith(XM_MAGIC):
        raise ValueError("block %d is not an XM module" % block_no)
    if len(data) < 80:
        raise ValueError("block %d is too short for an XM header" % block_no)

    name = data[17:37].split(b"\x00")[0].decode("latin-1").strip()
    if data[37] != 0x1A:
        raise ValueError("block %d: missing XM tracker marker" % block_no)

    version = struct.unpack_from("<H", data, 58)[0]
    header_size = struct.unpack_from("<I", data, 60)[0]
    (
        song_length,
        _restart,
        channels,
        patterns,
        instruments,
        _flags,
        tempo,
        bpm,
    ) = struct.unpack_from("<8H", data, 64)

    pos = 60 + header_size
    if pos > len(data):
        raise ValueError("block %d: XM header overruns module" % block_no)

    for _ in range(patterns):
        if pos + 9 > len(data):
            raise ValueError("block %d: truncated pattern header" % block_no)
        phlen, _packing, _rows, dsize = struct.unpack_from("<IBHH", data, pos)
        pos += phlen + dsize
        if pos > len(data):
            raise ValueError("block %d: truncated pattern data" % block_no)

    for _ in range(instruments):
        if pos + 29 > len(data):
            raise ValueError("block %d: truncated instrument header" % block_no)
        isize = struct.unpack_from("<I", data, pos)[0]
        nsamp = struct.unpack_from("<H", data, pos + 27)[0]
        sample_headers = pos + isize
        if sample_headers + nsamp * 40 > len(data):
            raise ValueError("block %d: truncated sample headers" % block_no)
        sample_bytes = 0
        for s in range(nsamp):
            sample_bytes += struct.unpack_from(
                "<I", data, sample_headers + s * 40
            )[0]
        pos = sample_headers + nsamp * 40 + sample_bytes
        if pos > len(data):
            raise ValueError("block %d: truncated sample data" % block_no)

    if pos != len(data):
        raise ValueError(
            "block %d: XM parse ended at %d, module length is %d"
            % (block_no, pos, len(data))
        )

    return XMInfo(
        block_no=block_no,
        block_offset=block_offset,
        data=data,
        name=name,
        version=version,
        header_size=header_size,
        song_length=song_length,
        channels=channels,
        patterns=patterns,
        instruments=instruments,
        tempo=tempo,
        bpm=bpm,
    )


def safe_component(text: str, fallback: str) -> str:
    text = text.strip().strip('"').strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def xm_filename(index: int, info: XMInfo) -> str:
    title = safe_component(info.name, "untitled")
    return "%02d - %s.xm" % (index, title)


def image_assets(block1: bytes, block2: bytes, block3: bytes,
                 block4: bytes, block5: bytes) -> list[ImageAsset]:
    assets: list[ImageAsset] = []

    for block_no, data in ((1, block1), (2, block2), (3, block3)):
        if len(data) != 320 * 200 + 768:
            raise ValueError(
                "block %d: expected %d bytes, got %d"
                % (block_no, 320 * 200 + 768, len(data))
            )
        assets.append(
            ImageAsset(
                filename="block%02d_320x200.png" % block_no,
                width=320,
                height=200,
                pixels=data[:320 * 200],
                palette=data[320 * 200:],
                block_no=block_no,
                offset=0,
                note="image followed by 768-byte VGA palette",
            )
        )

    if len(block4) != 640 * 480 + 768:
        raise ValueError(
            "block 4: expected %d bytes, got %d"
            % (640 * 480 + 768, len(block4))
        )
    assets.append(
        ImageAsset(
            filename="block04_640x480.png",
            width=640,
            height=480,
            pixels=block4[:640 * 480],
            palette=block4[640 * 480:],
            block_no=4,
            offset=0,
            note="image followed by 768-byte VGA palette",
        )
    )

    # Block 5 layout (validated):
    #   0x000000  640x300 image
    #   0x02EE00  768-byte palette for the first image
    #   0x02F100  640x1813 continuous image (6 * 192000 + 8320 bytes)
    #   0x10B183  768-byte shared palette for the six pages
    #   0x14A580  768-byte VGA BIOS default palette (wrong for the artwork)
    #
    # The palette at 0x10B183 is the real one: its entry 0 is 011121, the
    # magazine's dark-blue UI background, and byte-identical copies are
    # embedded in pages 4 and 5.  The table at 0x14A580 is simply the stock
    # VGA BIOS default and must not be used for the pictures.
    expected_block5 = 192000 + 768 + 6 * 192000 + 8320 + 768
    if len(block5) != expected_block5:
        raise ValueError(
            "block 5: expected %d bytes, got %d"
            % (expected_block5, len(block5))
        )

    block5_palette_offset = 0x10B183

    pic0_pixels = block5[:192000]
    pic0_palette = block5[0x2EE00:0x2EE00 + 768]
    assets.append(
        ImageAsset(
            filename="block05_pic0_640x300.png",
            width=640,
            height=300,
            pixels=pic0_pixels,
            palette=pic0_palette,
            block_no=5,
            offset=0,
            note="uses palette at block offset 0x2EE00 (indices 208-223)",
        )
    )

    scroll_offset = 0x2F100
    scroll_len = 0x14A580 - scroll_offset
    scroll_pixels = block5[scroll_offset:scroll_offset + scroll_len]
    scroll_palette = block5[
        block5_palette_offset:block5_palette_offset + 768
    ]
    assets.append(
        ImageAsset(
            filename="block05_scroll_640x1813.png",
            width=640,
            height=scroll_len // 640,
            pixels=scroll_pixels,
            palette=scroll_palette,
            block_no=5,
            offset=scroll_offset,
            note=(
                "continuous picture; uses the shared block-5 palette at "
                "0x10B183. Last 13 rows are solid background"
            ),
        )
    )

    for page in range(6):
        offset = scroll_offset + page * 192000
        assets.append(
            ImageAsset(
                filename="block05_page%d_640x300.png" % (page + 1),
                width=640,
                height=300,
                pixels=block5[offset:offset + 192000],
                palette=scroll_palette,
                block_no=5,
                offset=offset,
                note="300-row slice of the block-5 picture",
            )
        )

    return assets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract DRAGON #5 music and graphics into out/<edition>/."
    )
    add_edition_args(parser, include_source=True, include_out_dir=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    edition = resolve_edition(args.edition)
    source = resolve_source(edition, args.source)
    out_dir = resolve_out_dir(edition, args.out_dir)

    if not source.is_file():
        print("source not found: %s" % source, file=sys.stderr)
        return 1

    binary = source.read_bytes()
    try:
        blocks = find_blocks(binary)
    except ValueError as exc:
        print("cannot scan source: %s" % exc, file=sys.stderr)
        return 1
    if len(blocks) < 12:
        print("expected at least 12 EOS blocks, found %d" % len(blocks), file=sys.stderr)
        return 1

    decoded: list[bytes] = []
    for block_no, block_info in enumerate(blocks):
        try:
            data, _used = decode_block(binary, block_info)
        except ValueError as exc:
            print("block %d: decode failed (%s)" % (block_no, exc),
                  file=sys.stderr)
            return 1
        decoded.append(data)

    xms: list[XMInfo] = []
    for block_no in range(6, 12):
        xms.append(parse_xm(block_no, blocks[block_no][0], decoded[block_no]))

    assets = image_assets(
        decoded[1], decoded[2], decoded[3], decoded[4], decoded[5]
    )

    music_dir = out_dir / "music"
    gfx_dir = out_dir / "graphics"
    music_dir.mkdir(parents=True, exist_ok=True)
    gfx_dir.mkdir(parents=True, exist_ok=True)

    music_rows: list[tuple[str, XMInfo]] = []
    for index, info in enumerate(xms, 1):
        filename = xm_filename(index, info)
        path = music_dir / filename
        path.write_bytes(info.data)
        music_rows.append((filename, info))
        print("music  %s  (%d bytes)" % (filename, len(info.data)))

    for asset in assets:
        path = gfx_dir / asset.filename
        write_indexed_png(
            path, asset.width, asset.height, asset.pixels, asset.palette
        )
        print(
            "gfx    %s  (%dx%d, %d bytes)"
            % (asset.filename, asset.width, asset.height, path.stat().st_size)
        )

    media_md = out_dir / "media.md"
    lines = [
        "# %s - extracted music and graphics" % edition.title,
        "",
        "- Edition: `%s`" % edition.key,
        "- Source: `%s`" % relative_to_root(source),
        "- Extractor: `%s`" % relative_to_root(SCRIPT),
        "- Music: 6 complete FastTracker II XM modules (EOS blocks 6-11)",
        "- Graphics: 4 standalone images plus the block-5 graphics bank",
        "- Music files are byte-for-byte copies of the decoded EOS blocks.",
        "- PNG files are 8-bit indexed images; 6-bit VGA palette values are "
        "scaled to 8-bit with `(v << 2) | (v >> 4)`.",
        "- Existing article text in `out/<edition>/` is unchanged; this file only "
        "describes the new media assets.",
        "",
        "## Music",
        "",
        "| File | Internal name | Channels | Patterns | Instruments | Tempo | BPM | Size | Source block | EOS block offset |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for filename, info in music_rows:
        name = info.name or "(empty)"
        lines.append(
            "| [%s](music/%s) | %s | %d | %d | %d | %d | %d | %d | %d | 0x%08X |"
            % (
                filename,
                filename.replace(" ", "%20"),
                name.replace("|", "\\|"),
                info.channels,
                info.patterns,
                info.instruments,
                info.tempo,
                info.bpm,
                len(info.data),
                info.block_no,
                info.block_offset,
            )
        )

    block_offsets = [block_info[0] for block_info in blocks]
    lines += [
        "",
        "## Graphics",
        "",
        "| File | Size | Palette source | Source block | EOS block offset | Notes |",
        "|---|---|---|---:|---:|---|",
    ]
    for asset in assets:
        lines.append(
            "| [%s](graphics/%s) | %dx%d | %s | %d | 0x%08X | %s |"
            % (
                asset.filename,
                asset.filename,
                asset.width,
                asset.height,
                "block offset 0x%X" % (
                    0x2EE00 if asset.filename == "block05_pic0_640x300.png"
                    else 0x10B183 if asset.block_no == 5
                    else asset.offset + asset.width * asset.height
                ),
                asset.block_no,
                block_offsets[asset.block_no],
                asset.note,
            )
        )

    lines += [
        "",
        "## Block-5 layout",
        "",
        "The block-5 graphics bank is 1,353,856 bytes:",
        "",
        "| Offset | Size | Contents |",
        "|---:|---:|---|",
        "| `0x000000` | 192,000 | 640x300 picture 0 |",
        "| `0x02EE00` | 768 | palette for picture 0 (indices 208-223) |",
        "| `0x02F100` | 1,160,320 | 640x1813 picture area |",
        "| `0x10B183` | 768 | shared 256-colour palette for pictures 1-6 |",
        "| `0x14A580` | 768 | stock VGA BIOS default palette (unused) |",
        "",
        "Pictures 1-6 are 300-row slices of the 640x1813 picture area. The "
        "final 13 rows are solid background and are included in the full "
        "scroll.",
        "",
        "### Palette",
        "",
        "Earlier versions of this extractor used the 768-byte table at "
        "`0x14A580`, which turned out to be the **stock VGA BIOS default "
        "palette** and produced the wrong colours.",
        "",
        "The real palette is at `0x10B183`. Entry 0 is `011121`, the "
        "magazine's dark-blue UI background, and the table contains the same "
        "colours as the magazine's UI screens. Byte-identical copies are "
        "embedded in pictures 4 and 5, and the copies stored in pictures "
        "1, 2 and 6 are subsets of it. The block-5 pictures are therefore "
        "rendered with this palette.",
        "",
        "The six `block05_page*.png` files are provided as 640x300 slices for "
        "convenience; `block05_scroll_640x1813.png` is the complete picture.",
        "",
    ]

    media_md.write_text("\n".join(lines), encoding="utf-8")
    print("wrote %s" % media_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
