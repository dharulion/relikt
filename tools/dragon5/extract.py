#!/usr/bin/env python3
"""Extract the articles stored in DRAGON #5's DRAGON.EXE.

The magazine is an E.O.S./WATCOM DOS program packed with DIET.  The overlay is
a sequence of DIET-compressed LLINK blocks; blocks 12..18 contain the article
sections.  Each text block is a small container:

    u32 data_end
    u32 metadata_size
    u32 last_article_index      (the file has last_article_index + 1 records)
    u32 0
    (last_article_index + 1) * (u32 offset, u32 length)
    (last_article_index + 1) * 16-byte original DOS file names
    article records ...
    metadata / section menu at data_end

Every article record starts with a DOS-style header:

    0x07 <menu title> 0x08 <menu author> CRLF [CRLF]
    0x00 0x06 <full title> CRLF 0x08 <full author> CRLF
    <body>

The body uses CP852 and a few one/two-byte display codes.  The extractor
removes only those display codes and writes the text as UTF-8.
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
import sys
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

CRLF = b"\r\n"


@dataclass
class Article:
    block_no: int
    block_offset: int
    number: int
    total: int
    offset: int
    length: int
    source_name: str
    menu_title: str
    menu_author: str
    title: str
    author: str
    body: str
    section: str
    section_no: int
    output_rel: str = ""


@dataclass
class Section:
    block_no: int
    block_offset: int
    section_no: int
    name: str
    articles: list[Article]
    filenames: list[str]


def decode_cp852(data: bytes) -> str:
    return data.decode("cp852")


def parse_record(record: bytes) -> tuple[str, str, str, str, bytes]:
    """Split one article record into menu title/author, full title/author, body."""
    if not record or record[0] != 0x07:
        raise ValueError("record does not start with 0x07")
    sep = record.find(b"\x08", 1)
    if sep < 0:
        raise ValueError("missing menu-title terminator")
    menu_title = record[1:sep]
    rest = record[sep + 1:]

    eol = rest.find(CRLF)
    if eol < 0:
        raise ValueError("missing menu-author line end")
    menu_author = rest[:eol]
    rest = rest[eol + 2:].lstrip(b"\r\n")

    if not rest.startswith(b"\x00\x06"):
        raise ValueError("missing full-title marker 00 06")
    rest = rest[2:]
    eol = rest.find(CRLF)
    if eol < 0:
        raise ValueError("missing full-title line end")
    title = rest[:eol]
    rest = rest[eol + 2:]

    if not rest.startswith(b"\x08"):
        raise ValueError("missing full-author marker 08")
    rest = rest[1:]
    eol = rest.find(CRLF)
    if eol < 0:
        raise ValueError("missing full-author line end")
    author = rest[:eol]
    body = rest[eol + 2:]

    return (
        decode_cp852(menu_title).strip(),
        decode_cp852(menu_author).strip(),
        decode_cp852(title).strip(),
        decode_cp852(author).strip(),
        body,
    )


def strip_display_codes(body: bytes) -> bytes:
    """Remove the magazine's colour/highlight control codes, keep the text."""
    out = bytearray()
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c in (0x02, 0x03):  # colour attribute
            if i + 1 >= n or body[i + 1] > 0x03:
                raise ValueError(
                    "bad colour control at body offset %d: %r"
                    % (i, body[i:i + 4])
                )
            i += 2
        elif c == 0x00:  # 00 06 / 00 07 highlight on/off
            if i + 1 >= n or body[i + 1] not in (0x06, 0x07):
                raise ValueError(
                    "bad 00 control at body offset %d: %r"
                    % (i, body[i:i + 4])
                )
            i += 2
        elif c in (0x01, 0x06, 0x07, 0x08, 0x0F):
            i += 1
        else:
            out.append(c)
            i += 1
    return bytes(out)


def clean_body(body: bytes) -> str:
    text = strip_display_codes(body).decode("cp852")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.lstrip("\n").rstrip()
    return text


def parse_filename_table(block: bytes, count: int, first_article: int) -> list[str]:
    table_end = 16 + count * 8
    table_len = count * 16
    if table_end + table_len != first_article:
        raise ValueError(
            "filename table does not end at first article "
            "(%d + %d != %d)" % (table_end, table_len, first_article)
        )
    names = []
    for i in range(count):
        raw = block[table_end + i * 16:table_end + i * 16 + 16]
        name = raw.split(b"\x00", 1)[0].decode("ascii")
        if not name:
            raise ValueError("empty filename at index %d" % i)
        names.append(name)
    return names


def parse_section(block_no: int, block_offset: int, block: bytes) -> Section | None:
    if len(block) < 16:
        return None
    data_end, metadata_size, last_index, reserved = struct.unpack_from(
        "<IIII", block, 0
    )
    if reserved != 0 or last_index > 512:
        return None
    count = last_index + 1
    table_end = 16 + count * 8
    if table_end > data_end or data_end > len(block):
        return None
    if data_end + metadata_size > len(block):
        return None

    tail = block[data_end:data_end + metadata_size]
    if not tail.startswith(b"\x07"):
        return None
    title_end = tail.find(CRLF, 1)
    if title_end < 0:
        return None
    section_name = decode_cp852(tail[1:title_end]).strip()

    entries = [struct.unpack_from("<II", block, 16 + i * 8) for i in range(count)]
    if not entries:
        return None
    first_article = entries[0][0]
    if first_article < table_end:
        return None
    for i, (offset, length) in enumerate(entries):
        if length == 0 or offset < table_end or offset + length > data_end:
            return None
        expected = entries[i + 1][0] if i + 1 < count else data_end
        if offset + length != expected:
            return None

    try:
        filenames = parse_filename_table(block, count, first_article)
    except ValueError:
        return None

    articles: list[Article] = []
    for i, (offset, length) in enumerate(entries):
        record = block[offset:offset + length]
        try:
            menu_title, menu_author, title, author, raw_body = parse_record(record)
        except ValueError:
            return None
        articles.append(
            Article(
                block_no=block_no,
                block_offset=block_offset,
                number=i + 1,
                total=count,
                offset=offset,
                length=length,
                source_name=filenames[i],
                menu_title=menu_title,
                menu_author=menu_author,
                title=title or menu_title,
                author=author or menu_author,
                body=clean_body(raw_body),
                section=section_name,
                section_no=0,
            )
        )
    return Section(
        block_no=block_no,
        block_offset=block_offset,
        section_no=0,
        name=section_name,
        articles=articles,
        filenames=filenames,
    )


def safe_name(text: str, fallback: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    if not text:
        text = fallback
    if len(text) > 100:
        text = text[:100].rstrip(" .")
    return text


def write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def extract_help(binary: bytes) -> tuple[str, int] | None:
    marker = b"Help\r\n"
    start = binary.find(marker, 0x1EB000)
    if start < 0:
        return None
    end = binary.find(b"\x0fEOS", start)
    if end < 0:
        return None
    body = binary[start + len(marker):end]
    return clean_body(body), start


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract DRAGON #5 articles into out/<edition>/."
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

    sections: list[Section] = []
    for block_no, block_info in enumerate(blocks):
        try:
            decoded, _ = decode_block(binary, block_info)
        except Exception as exc:  # not a text block
            print("block %d: skip (%s)" % (block_no, exc))
            continue
        section = parse_section(block_no, block_info[0], decoded)
        if section is None:
            continue
        section.section_no = len(sections) + 1
        for article in section.articles:
            article.section_no = section.section_no
        sections.append(section)

    if not sections:
        print("no article sections found", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    source_label = edition.source_label or source.name
    used_paths: set[Path] = set()
    rows: list[list[str]] = []
    total_articles = sum(len(s.articles) for s in sections)

    for section in sections:
        folder = "%02d - %s" % (
            section.block_no,
            safe_name(section.name, "section-%d" % section.block_no),
        )
        section_dir = out_dir / folder
        for article in section.articles:
            stem = "%02d - %s" % (
                article.number,
                safe_name(article.title, article.source_name.rsplit(".", 1)[0]),
            )
            out_path = section_dir / (stem + ".txt")
            if out_path in used_paths:
                n = 2
                while True:
                    candidate = section_dir / ("%s (%d).txt" % (stem, n))
                    if candidate not in used_paths:
                        out_path = candidate
                        break
                    n += 1
            used_paths.add(out_path)

            header = [
                "Title: %s" % article.title,
            ]
            if article.menu_title and article.menu_title != article.title:
                header.append("Menu title: %s" % article.menu_title)
            header += [
                "Author: %s" % (article.author or article.menu_author),
                "Section: %s" % section.name,
                "Source: %s, block %d @ 0x%08X, record 0x%06X "
                "(%d bytes), original file %s"
                % (
                    source_label,
                    article.block_no,
                    article.block_offset,
                    article.offset,
                    article.length,
                    article.source_name,
                ),
                "=" * 79,
                "",
            ]
            text = "\n".join(header) + article.body + "\n"
            write_utf8(out_path, text)

            rel = out_path.relative_to(out_dir).as_posix()
            article.output_rel = rel
            rows.append(
                [
                    section.name,
                    str(article.number),
                    str(article.total),
                    article.title,
                    article.menu_title,
                    article.author,
                    article.source_name,
                    str(article.block_no),
                    "0x%08X" % article.block_offset,
                    "0x%06X" % article.offset,
                    str(article.length),
                    rel,
                ]
            )

    # Machine-readable manifest.
    with (out_dir / "manifest.tsv").open(
        "w", encoding="utf-8", newline="\n"
    ) as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "section",
                "number",
                "section_total",
                "title",
                "menu_title",
                "author",
                "source_file",
                "block",
                "block_offset",
                "record_offset",
                "record_length",
                "output",
            ]
        )
        writer.writerows(rows)

    # Human-readable index.
    index_lines = [
        "# %s - extracted articles" % edition.title,
        "",
        "- Edition: `%s`" % edition.key,
        "- Source: `%s`" % relative_to_root(source),
        "- Sections: %d" % len(sections),
        "- Articles: %d" % total_articles,
        "- Text encoding: UTF-8 (source text decoded from CP852)",
        "- Extractor: `%s`" % relative_to_root(SCRIPT),
        "",
        "Article order follows the source section and record order. Each article "
        "file starts with a short metadata block (title, author, section, source) "
        "followed by the original body text. The built-in help screen is saved "
        "separately in [`help.txt`](help.txt).",
        "",
        "A machine-readable list is available in [`manifest.tsv`](manifest.tsv).",
        "",
    ]
    for section in sections:
        index_lines += [
            "## %02d - %s (%d articles)"
            % (section.block_no, section.name, len(section.articles)),
            "",
            "| # | Title | Author | Original file |",
            "|---:|---|---|---|",
        ]
        for article in section.articles:
            # The exact relative path was recorded when the file was written,
            # including any duplicate-name suffix.
            target = article.output_rel or "%02d - %s/%02d - %s.txt" % (
                section.block_no,
                safe_name(section.name, "section-%d" % section.block_no),
                article.number,
                safe_name(article.title, article.source_name.rsplit(".", 1)[0]),
            )
            index_lines.append(
                "| %d | %s | %s | [%s](<%s>) |"
                % (
                    article.number,
                    md_escape(article.title),
                    md_escape(article.author),
                    article.source_name,
                    target,
                )
            )
        index_lines.append("")

    help_info = extract_help(binary)
    if help_info:
        help_body, help_offset = help_info
        help_text = (
            "Title: Help\n"
            "Author: Candle\n"
            "Section: Built-in help\n"
            "Source: %s, plaintext help resource at 0x%08X\n"
            "%s\n\n%s\n" % (source_label, help_offset, "=" * 79, help_body)
        )
        write_utf8(out_dir / "help.txt", help_text)
        index_lines += [
            "## Additional content",
            "",
            "- [help.txt](help.txt) - built-in help screen (author: Candle)",
            "",
        ]

    write_utf8(out_dir / "index.md", "\n".join(index_lines).rstrip() + "\n")

    print(
        "extracted %d articles from %d sections into %s"
        % (total_articles, len(sections), out_dir)
    )
    for section in sections:
        print(
            "  %2d  %-22s %d articles"
            % (section.block_no, section.name, len(section.articles))
        )
    if help_info:
        print("  help.txt written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
