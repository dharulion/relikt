#!/usr/bin/env python3
"""Relikt - one entry point for the demoscene magazine extraction tools.

Each edition lives in its own folder: data/<edition>/, out/<edition>/ and
tmp/<edition>/. Editions found in data/ are discovered automatically; known
editions such as dragon5 also carry block-layout expectations in
tools/magazines.py. Use -e/--edition to select one or --all for every edition.

Commands:
    verify   Validate the source binary, its DIET wrapper and EOS blocks.
    text     Extract article text with the edition's text script.
    media    Extract music and graphics with the edition's media script.
    full     Verify first, then extract text and media.
    status   Inspect the current contents of out/<edition>/.
    unpack   Diagnostic DIET unpack + EXE rebuild into tmp/<edition>/.
    list     Show discovered editions and tool scripts.
    help     Explain the project and list every command and option.
    version  Show the Relikt version.

Examples:
    python relikt.py help
    python relikt.py list
    python relikt.py -e dragon5 verify
    python relikt.py --all full
    python relikt.py status --json
    python relikt.py verify --json --strict
    python relikt.py unpack
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from magazines import (  # noqa: E402 - tools path must be set up first
    Edition,
    discover_editions,
    load_script_module,
    relative_to_root,
    resolve_edition as lookup_edition,
    resolve_out_dir,
    resolve_source as find_source,
)

__version__ = "0.2alpha"
TAGLINE = "Recovering assets from legacy demoscene magazines."

PROJECT_HELP = """\
Relikt is a thin orchestration layer over the extraction scripts in tools/.
It validates the original magazine binary, extracts article text, music and
graphics, and reports what is present in out/<edition>/. Relikt passes the
selected edition to each extractor so the same tooling works for any magazine.

The goal is to recover the content of old DOS demoscene disk magazines so the
articles can be archived, searched and re-used. Every edition has its own
folder: data/<edition>/ for source binaries, out/<edition>/ for extracted
assets, tmp/<edition>/ for diagnostics and tools/<edition>/ for the
edition-specific extractors. Relikt chooses the right scripts from the
selected edition. New editions found in data/ are registered automatically.

Typical workflow:
  1. Place the magazine executable in data/<edition>/ (for example
     data/dragon5/DRAGON.EXE).
  2. Run `python relikt.py -e <edition> verify` to check the binary.
  3. Run `python relikt.py -e <edition> full` to extract everything.
  4. Run `python relikt.py -e <edition> status` to confirm the output.
  Add `--all` instead of `-e` to run a command for every known edition.

Use `python relikt.py help` for the full command reference, or
`python relikt.py <command> --help` for the options of a single command.
"""

DIET_SIG = bytes.fromhex("8edb8ec033f633ffb9")
DIET_VARIANTS = (77, 72, 52, 55)
XM_MAGIC = b"Extended Module: "
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMAGE_SIZES = {320 * 200 + 768, 640 * 480 + 768}
GFX_BANK_SIZE = 192000 + 768 + 6 * 192000 + 8320 + 768

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"

_COLOR = False
_VERBOSE = False


@dataclass
class Check:
    status: str
    message: str
    detail: str = ""


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def _enable_windows_vt() -> bool:
    """Enable ANSI escape processing for the current Windows console.

    Newer Windows consoles understand VT sequences, but the mode is not
    always enabled for the process.  If the console cannot enable it (old
    Windows versions, redirected handles, some terminal hosts), colors must
    be disabled so raw escape codes never reach the user.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (None, 0, invalid_handle):
            return False

        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False

        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:  # noqa: BLE001 - never fail just because of colors
        return False


def _use_color(no_color_flag: bool) -> bool:
    if no_color_flag or "NO_COLOR" in os.environ:
        return False
    if os.environ.get("FORCE_COLOR", "").lower() in ("1", "true", "yes"):
        return True
    if not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
        return False
    return _enable_windows_vt()


def _color(text: str, code: str) -> str:
    return "\x1b[%sm%s\x1b[0m" % (code, text) if _COLOR else text


def print_step(message: str) -> None:
    print(_color("==> ", "36") + _color(message, "1"))


def print_error(message: str) -> None:
    print(_color("error: ", "31") + message, file=sys.stderr)


def print_check(check: Check) -> None:
    styles = {
        OK: (" OK ", "32"),
        WARN: ("WARN", "33"),
        FAIL: ("FAIL", "31"),
        INFO: ("INFO", "36"),
    }
    label, code = styles.get(check.status, ("INFO", "36"))
    print("[%s] %s" % (_color(label, code), check.message))
    if check.detail:
        for line in check.detail.splitlines():
            print("       " + line)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _size(value: int) -> str:
    return "{:,}".format(value)


def _print_status(ok: bool, message: str) -> None:
    print(("[%s] %s" % (_color(" OK ", "32") if ok else _color("FAIL", "31"),
                        message)))


# --------------------------------------------------------------------------
# edition / source resolution
# --------------------------------------------------------------------------

def resolve_edition(args: argparse.Namespace) -> Edition:
    key = getattr(args, "edition", None) or "dragon5"
    if str(key).lower() in {"all", "*"}:
        raise SystemExit("'all' selects multiple editions; use resolve_editions()")
    return lookup_edition(key)


def resolve_editions(args: argparse.Namespace) -> list[Edition]:
    """Return one edition, or every discovered edition for --all."""
    key = str(getattr(args, "edition", None) or "dragon5").lower()
    if getattr(args, "all", False) or key in {"all", "*"}:
        return list(discover_editions().values())
    return [lookup_edition(key)]


def resolve_source(args: argparse.Namespace, edition: Edition) -> Path:
    return resolve_source_for(edition, getattr(args, "source", None))


def resolve_source_for(edition: Edition, raw: str | Path | None = None) -> Path:
    return find_source(edition, raw)


def _edition_heading(edition: Edition) -> None:
    print()
    print("=" * 78)
    print("Edition: %s (%s)" % (edition.title, edition.key))
    print("=" * 78)


def _run_for_editions(
    args: argparse.Namespace,
    command,
) -> int:
    """Run a single-edition command for one or all editions."""
    editions = resolve_editions(args)
    if len(editions) == 1:
        return command(editions[0])
    if getattr(args, "source", None):
        raise SystemExit("--source cannot be combined with --all")
    codes = []
    for edition in editions:
        _edition_heading(edition)
        codes.append(command(edition))
    return 0 if all(code == 0 for code in codes) else 1


# --------------------------------------------------------------------------
# low-level verification helpers
# --------------------------------------------------------------------------

def load_eos_module():
    """Import tools/eos_blocks.py without modifying it."""
    tools = str(TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import eos_blocks  # noqa: PLC0415 - deliberate lazy import

    return eos_blocks


def diet_header_info(data: bytes) -> dict | None:
    """Return the outer DIET v1.45 header details, or None."""
    if len(data) < 0x40 or data[:2] not in (b"MZ", b"ZM"):
        return None

    codestart = 16 * struct.unpack_from("<H", data, 8)[0]
    base = codestart - 32
    if base < 0 or base + 119 > len(data):
        return None

    variant = None
    for candidate in DIET_VARIANTS:
        start = base + candidate
        if data[start:start + len(DIET_SIG)] == DIET_SIG:
            variant = candidate
            break
    if variant is None:
        return None

    dlz = base + 108
    if data[dlz:dlz + 3] != b"dlz":
        return None

    flags = data[dlz + 3]
    compressed = ((flags & 0x0F) << 16) | struct.unpack_from("<H", data, dlz + 4)[0]
    original = ((data[dlz + 8] & 0xFC) << 14) | struct.unpack_from("<H", data, dlz + 9)[0]
    return {
        "variant": variant,
        "codestart": codestart,
        "compressed_length": compressed,
        "original_length": original,
        "compressed_end": dlz + 11 + compressed,
    }


def _classify_block(block_no, data, parse_section, parse_xm):
    """Return (role, detail, error) for one decoded EOS block."""
    if data.startswith(XM_MAGIC):
        if parse_xm is None:
            return "music", None, None
        try:
            info = parse_xm(block_no, 0, data)
            return "music", info, None
        except Exception as exc:  # noqa: BLE001 - report any malformed module
            return "music", None, str(exc)

    if parse_section is not None:
        try:
            section = parse_section(block_no, 0, data)
        except Exception:  # noqa: BLE001 - non-text blocks may not decode cleanly
            section = None
        if section is not None:
            return "text", section, None

    if len(data) in IMAGE_SIZES:
        return "image", None, None
    if len(data) == GFX_BANK_SIZE:
        return "gfxbank", None, None
    return "data", None, None


def _finalize_verify(result: dict, checks: list[Check], strict: bool,
                     started: float) -> tuple[dict, int]:
    failures = sum(1 for c in checks if c.status == FAIL)
    warnings = sum(1 for c in checks if c.status == WARN)
    passed = failures == 0 and not (strict and warnings)
    result["checks"] = [asdict(c) for c in checks]
    result["summary"] = {
        "blocks": len(result["blocks"]),
        "decoded_blocks": sum(1 for b in result["blocks"] if b["decoded_size"] is not None),
        "sections": len(result["sections"]),
        "articles": sum(s["articles"] for s in result["sections"]),
        "music": len(result["music"]),
        "graphics": len(result["graphics"]),
        "warnings": warnings,
        "failures": failures,
        "passed": passed,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }
    return result, 0 if passed else 1


def _check_count(add, label: str, actual: int, expected: int | None) -> None:
    """Report a count check only when the edition defines an expectation."""
    if expected is None:
        add(INFO, "%s: %d (no expectation configured)" % (label, actual))
    elif actual == expected:
        add(OK, "%s: %d (expected %d)" % (label, actual, expected))
    else:
        add(WARN, "%s: %d (expected %d)" % (label, actual, expected))


def verify_source(edition: Edition, source: Path, strict: bool = False) -> tuple[dict, int]:
    """Validate one source binary against the edition's expected structure."""
    started = time.perf_counter()
    checks: list[Check] = []
    result: dict = {
        "edition": edition.key,
        "title": edition.title,
        "source": str(source),
        "size": None,
        "sha256": None,
        "diet": None,
        "blocks": [],
        "sections": [],
        "music": [],
        "graphics": [],
        "checks": [],
        "summary": {},
    }

    def add(status: str, message: str, detail: str = "") -> None:
        checks.append(Check(status, message, detail))

    if not source.exists():
        add(FAIL, "source binary found", "expected: %s" % source)
        return _finalize_verify(result, checks, strict, started)
    if not source.is_file():
        add(FAIL, "source path is a file", "not a file: %s" % source)
        return _finalize_verify(result, checks, strict, started)

    try:
        data = source.read_bytes()
    except OSError as exc:
        add(FAIL, "source binary is readable", str(exc))
        return _finalize_verify(result, checks, strict, started)

    result["size"] = len(data)
    result["sha256"] = hashlib.sha256(data).hexdigest()
    add(INFO, "source binary: %s (%s bytes)" % (source.name, _size(len(data))))

    if data[:2] in (b"MZ", b"ZM"):
        add(OK, "DOS MZ executable header")
    else:
        add(FAIL, "DOS MZ executable header", "first bytes: %r" % data[:4])

    diet = diet_header_info(data)
    result["diet"] = diet
    if edition.diet_packed:
        if diet is None:
            add(FAIL, "DIET v1.45f wrapper detected",
                "no known DIET signature/header in the executable")
        else:
            add(OK, "DIET v1.45f wrapper detected",
                "variant offset %d, compressed %s -> original %s bytes, "
                "payload ends at 0x%08X"
                % (diet["variant"], _size(diet["compressed_length"]),
                   _size(diet["original_length"]), diet["compressed_end"]))
            if diet["compressed_end"] > len(data):
                add(FAIL, "DIET compressed payload fits in the file",
                    "payload ends at 0x%08X, file is 0x%08X bytes"
                    % (diet["compressed_end"], len(data)))
    elif diet is not None:
        add(INFO, "DIET v1.45f wrapper detected (not required for this edition)")

    try:
        eos = load_eos_module()
    except Exception as exc:  # noqa: BLE001 - missing/broken tool module
        add(FAIL, "tools/eos_blocks.py is importable", str(exc))
        return _finalize_verify(result, checks, strict, started)

    try:
        blocks = eos.find_blocks(data)
    except Exception as exc:  # noqa: BLE001 - the scanner should never throw
        add(FAIL, "EOS block scan completed", str(exc))
        return _finalize_verify(result, checks, strict, started)

    if not blocks:
        add(FAIL, "EOS compressed blocks found", "the scanner returned no blocks")
        return _finalize_verify(result, checks, strict, started)

    if edition.block_roles:
        _check_count(add, "EOS block count", len(blocks), len(edition.block_roles))
    else:
        add(INFO, "EOS block count: %d" % len(blocks))

    if diet is not None and blocks[0][0] < diet["compressed_end"]:
        add(WARN, "EOS blocks follow the DIET payload",
            "first EOS block 0x%08X is inside the DIET payload ending at 0x%08X"
            % (blocks[0][0], diet["compressed_end"]))
    elif diet is not None:
        add(OK, "EOS blocks follow the DIET payload")

    decoded: list[bytes | None] = []
    decode_errors: list[str] = []
    for block_no, block_info in enumerate(blocks):
        try:
            payload, _used = eos.decode_block(data, block_info)
            decoded.append(payload)
        except Exception as exc:  # noqa: BLE001 - collect per-block failures
            decoded.append(None)
            decode_errors.append("block %d: %s" % (block_no, exc))

    if decode_errors:
        add(FAIL, "decoded EOS blocks: %d/%d"
            % (len(blocks) - len(decode_errors), len(blocks)),
            "\n".join(decode_errors))
    else:
        add(OK, "decoded EOS blocks: %d/%d" % (len(blocks), len(blocks)))

    result["scripts"] = {
        "text": _rel(edition.text_script),
        "media": _rel(edition.media_script),
    }
    text_module = None
    media_module = None
    module_errors: list[str] = []
    try:
        text_module = load_script_module(edition.text_script)
    except Exception as exc:  # noqa: BLE001 - a broken extractor is reportable
        module_errors.append("%s: %s" % (_rel(edition.text_script), exc))
    try:
        media_module = load_script_module(edition.media_script)
    except Exception as exc:  # noqa: BLE001 - a broken extractor is reportable
        module_errors.append("%s: %s" % (_rel(edition.media_script), exc))
    if module_errors:
        add(FAIL, "edition extractor modules are importable", "\n".join(module_errors))
    elif text_module is None and media_module is None:
        add(INFO, "no edition-specific extractors; using generic block checks")
    else:
        add(OK, "edition extractor modules are importable")

    parse_section = getattr(text_module, "parse_section", None) if text_module else None
    parse_xm = getattr(media_module, "parse_xm", None) if media_module else None
    image_assets_fn = (
        getattr(media_module, "image_assets", None) if media_module else None
    )

    roles: list[str] = []
    xm_errors: list[str] = []
    for block_no, block_info in enumerate(blocks):
        payload = decoded[block_no]
        entry = {
            "index": block_no,
            "offset": block_info[0],
            "magic": block_info[1].decode("ascii", "replace"),
            "flags": block_info[2],
            "compressed": block_info[3],
            "original": block_info[5],
            "decoded_size": len(payload) if payload is not None else None,
            "role": "undecoded",
            "detail": "",
        }
        if payload is not None:
            role, extra, error = _classify_block(block_no, payload, parse_section, parse_xm)
            entry["role"] = role
            if role == "music":
                if error:
                    xm_errors.append("block %d: %s" % (block_no, error))
                    entry["detail"] = "invalid XM: %s" % error
                else:
                    result["music"].append({
                        "block": block_no,
                        "name": extra.name,
                        "size": len(payload),
                        "channels": extra.channels,
                        "patterns": extra.patterns,
                        "instruments": extra.instruments,
                    })
                    entry["detail"] = "%s, %d bytes" % (extra.name or "(untitled)", len(payload))
            elif role == "text":
                result["sections"].append({
                    "block": block_no,
                    "name": extra.name,
                    "articles": len(extra.articles),
                })
                entry["detail"] = "%s, %d articles" % (extra.name, len(extra.articles))
            elif role == "image":
                entry["detail"] = "%d bytes" % len(payload)
            elif role == "gfxbank":
                entry["detail"] = "graphics bank (block %d)" % block_no
            else:
                entry["detail"] = "%d bytes" % len(payload)
            roles.append(role)
        else:
            roles.append("undecoded")
        result["blocks"].append(entry)

    if xm_errors:
        add(FAIL, "XM modules parse cleanly", "\n".join(xm_errors))

    if edition.block_roles:
        mismatches: list[str] = []
        for index, expected in enumerate(edition.block_roles):
            actual = roles[index] if index < len(roles) else "missing"
            if actual != expected:
                mismatches.append("block %d: expected %s, found %s"
                                  % (index, expected, actual))
        if mismatches:
            add(WARN, "EOS block layout matches %s" % edition.title,
                "\n".join(mismatches))
        else:
            add(OK, "EOS block layout matches %s" % edition.title)

    section_count = len(result["sections"])
    article_count = sum(s["articles"] for s in result["sections"])
    _check_count(add, "text sections", section_count, edition.expected_sections)
    _check_count(add, "articles", article_count, edition.expected_articles)
    _check_count(add, "music modules", len(result["music"]), edition.expected_music)

    # Edition media helpers commonly take the standalone images followed by
    # the graphics bank (dragon5 expects five payloads: blocks 1..5).
    asset_indexes = [
        index for index, role in enumerate(edition.block_roles)
        if role in {"image", "gfxbank"}
    ]
    if image_assets_fn and asset_indexes and all(
        index < len(decoded) and decoded[index] for index in asset_indexes
    ):
        try:
            assets = image_assets_fn(*[decoded[index] for index in asset_indexes])
        except Exception as exc:  # noqa: BLE001 - validates the media layout
            assets = []
            add(FAIL, "graphics layout parses cleanly", str(exc))
        for asset in assets:
            result["graphics"].append({
                "file": asset.filename,
                "width": asset.width,
                "height": asset.height,
                "block": asset.block_no,
            })
        if assets:
            _check_count(add, "graphics assets",
                         len(assets), edition.expected_graphics)

    return _finalize_verify(result, checks, strict, started)


# --------------------------------------------------------------------------
# output inspection
# --------------------------------------------------------------------------

def _expected_matches(expected: int | None, actual: int) -> bool:
    return expected is None or actual == expected


def collect_output_stats(edition: Edition) -> dict:
    out_dir = edition.out_dir
    stats: dict = {
        "edition": edition.key,
        "title": edition.title,
        "out": str(out_dir),
        "sections": [],
        "articles": 0,
        "text_invalid": [],
        "music": {"count": 0, "invalid": []},
        "graphics": {"count": 0, "invalid": []},
        "files": [],
        "expected": {
            "sections": edition.expected_sections,
            "articles": edition.expected_articles,
            "music": edition.expected_music,
            "graphics": edition.expected_graphics,
        },
        "complete": False,
    }

    if not out_dir.is_dir():
        return stats

    for child in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        if child.name.lower() in {"music", "graphics"}:
            continue
        text_files = sorted(child.glob("*.txt"))
        if not text_files:
            continue
        stats["sections"].append({"name": child.name, "articles": len(text_files)})
        stats["articles"] += len(text_files)
        for path in text_files:
            try:
                path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                stats["text_invalid"].append(_rel(path))

    music_dir = out_dir / "music"
    if music_dir.is_dir():
        music_files = sorted(music_dir.glob("*.xm"))
        stats["music"]["count"] = len(music_files)
        for path in music_files:
            try:
                if not path.read_bytes()[:len(XM_MAGIC)] == XM_MAGIC:
                    stats["music"]["invalid"].append(path.name)
            except OSError:
                stats["music"]["invalid"].append(path.name)

    graphics_dir = out_dir / "graphics"
    if graphics_dir.is_dir():
        graphics_files = sorted(graphics_dir.glob("*.png"))
        stats["graphics"]["count"] = len(graphics_files)
        for path in graphics_files:
            try:
                if not path.read_bytes()[:len(PNG_MAGIC)] == PNG_MAGIC:
                    stats["graphics"]["invalid"].append(path.name)
            except OSError:
                stats["graphics"]["invalid"].append(path.name)

    for name in ("index.md", "manifest.tsv", "media.md", "help.txt"):
        path = out_dir / name
        if path.is_file():
            stats["files"].append({"name": name, "size": path.stat().st_size})

    file_names = {item["name"] for item in stats["files"]}
    expected_files = {"index.md", "manifest.tsv", "media.md", "help.txt"}
    stats["complete"] = (
        _expected_matches(edition.expected_articles, stats["articles"])
        and _expected_matches(edition.expected_sections, len(stats["sections"]))
        and _expected_matches(edition.expected_music, stats["music"]["count"])
        and _expected_matches(edition.expected_graphics, stats["graphics"]["count"])
        and not stats["text_invalid"]
        and not stats["music"]["invalid"]
        and not stats["graphics"]["invalid"]
        and expected_files <= file_names
    )
    return stats


def _fmt_expected(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def render_status(stats: dict) -> None:
    expected = stats["expected"]
    print("Edition: %s" % stats["title"])
    print("Output:  %s" % _rel(Path(stats["out"])))
    if not Path(stats["out"]).is_dir():
        _print_status(False, "output folder is missing")
        return

    text_ok = (_expected_matches(expected["articles"], stats["articles"])
               and _expected_matches(expected["sections"], len(stats["sections"])))
    _print_status(
        text_ok,
        "text: %d articles in %d sections (expected %s / %s)"
        % (stats["articles"], len(stats["sections"]),
           _fmt_expected(expected["articles"]),
           _fmt_expected(expected["sections"])),
    )
    _print_status(not stats["text_invalid"],
                  "text encoding: UTF-8%s"
                  % ("" if not stats["text_invalid"] else
                     " (%d invalid files)" % len(stats["text_invalid"])))
    _print_status(
        _expected_matches(expected["music"], stats["music"]["count"])
        and not stats["music"]["invalid"],
        "music: %d XM files (expected %s)%s"
        % (stats["music"]["count"], _fmt_expected(expected["music"]),
           "" if not stats["music"]["invalid"] else
           ", %d invalid" % len(stats["music"]["invalid"])),
    )
    _print_status(
        _expected_matches(expected["graphics"], stats["graphics"]["count"])
        and not stats["graphics"]["invalid"],
        "graphics: %d PNG files (expected %s)%s"
        % (stats["graphics"]["count"], _fmt_expected(expected["graphics"]),
           "" if not stats["graphics"]["invalid"] else
           ", %d invalid" % len(stats["graphics"]["invalid"])),
    )

    present = [item["name"] for item in stats["files"]]
    required = ("index.md", "manifest.tsv", "media.md", "help.txt")
    missing = [name for name in required if name not in present]
    _print_status(not missing,
                  "manifests: %s%s"
                  % (", ".join(present) or "(none)",
                     "" if not missing else ", missing: " + ", ".join(missing)))

    if _VERBOSE and stats["sections"]:
        print()
        print("Sections:")
        for section in stats["sections"]:
            print("  %-40s %3d articles"
                  % (section["name"], section["articles"]))

    print()
    _print_status(stats["complete"],
                  "output is complete" if stats["complete"]
                  else "output is incomplete")


def cmd_status(args: argparse.Namespace) -> int:
    editions = resolve_editions(args)
    if len(editions) == 1:
        stats = collect_output_stats(editions[0])
        if args.json:
            print(json.dumps(stats, indent=2, ensure_ascii=False))
        else:
            render_status(stats)
        return 0 if stats["complete"] else 1

    reports = {edition.key: collect_output_stats(edition) for edition in editions}
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for edition in editions:
            _edition_heading(edition)
            render_status(reports[edition.key])
    return 0 if all(report["complete"] for report in reports.values()) else 1


# --------------------------------------------------------------------------
# running the existing tools
# --------------------------------------------------------------------------

def run_tool(script: Path, extra: Sequence[str] = (), quiet: bool = False) -> int:
    if not script.is_file():
        print_error("tool script not found: %s" % script)
        return 127

    command = [sys.executable, "-u", str(script), *[str(item) for item in extra]]
    print_step("Running %s" % _rel(script))
    if _VERBOSE:
        print("    %s" % subprocess.list2cmdline(command))

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,
        )
    except OSError as exc:
        print_error("could not start the tool: %s" % exc)
        return 127

    captured: list[str] = []
    assert process.stdout is not None
    try:
        for line in process.stdout:
            line = line.rstrip("\r\n")
            if quiet:
                captured.append(line)
            else:
                print("  | " + line)
        return_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise

    if return_code != 0:
        if quiet:
            for line in captured:
                print("  | " + line)
        print_error("%s failed with exit code %d" % (_rel(script), return_code))
    return return_code


def _ensure_source(edition: Edition, args: argparse.Namespace) -> bool:
    source = resolve_source(args, edition)
    if source.is_file():
        return True
    print_error("source binary not found: %s" % source)
    print("        Put the magazine executable in data/%s/, or use --source."
          % edition.key)
    return False


def _tool_args(edition: Edition, args: argparse.Namespace) -> list[str]:
    extra = ["--edition", edition.key]
    if getattr(args, "source", None):
        extra += ["--source", str(resolve_source(args, edition))]
    return extra


def _text_one(edition: Edition, args: argparse.Namespace) -> int:
    if not _ensure_source(edition, args):
        return 2

    return_code = run_tool(
        edition.text_script, _tool_args(edition, args), quiet=args.quiet
    )
    if return_code != 0:
        return return_code

    stats = collect_output_stats(edition)
    print_step("Text extraction finished: %d articles in %d sections"
               % (stats["articles"], len(stats["sections"])))
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    return _run_for_editions(args, lambda edition: _text_one(edition, args))


def _media_one(edition: Edition, args: argparse.Namespace) -> int:
    if not _ensure_source(edition, args):
        return 2

    return_code = run_tool(
        edition.media_script, _tool_args(edition, args), quiet=args.quiet
    )
    if return_code != 0:
        return return_code

    stats = collect_output_stats(edition)
    print_step("Media extraction finished: %d music files, %d graphics files"
               % (stats["music"]["count"], stats["graphics"]["count"]))
    return 0


def cmd_media(args: argparse.Namespace) -> int:
    return _run_for_editions(args, lambda edition: _media_one(edition, args))


def cmd_verify(args: argparse.Namespace) -> int:
    editions = resolve_editions(args)
    if len(editions) > 1 and getattr(args, "source", None):
        raise SystemExit("--source cannot be combined with --all")

    if len(editions) == 1:
        source = resolve_source(args, editions[0])
        result, return_code = verify_source(editions[0], source,
                                            strict=args.strict)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            render_verify(result)
        return return_code

    reports: dict[str, dict] = {}
    codes: list[int] = []
    for edition in editions:
        source = resolve_source(args, edition)
        result, return_code = verify_source(edition, source, strict=args.strict)
        reports[edition.key] = result
        codes.append(return_code)
        if not args.json:
            _edition_heading(edition)
            render_verify(result)
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    return 0 if all(code == 0 for code in codes) else 1


def render_verify(result: dict) -> None:
    summary = result["summary"]
    print("Relikt verification: %s" % result["title"])
    print("Source:  %s" % result["source"])
    if result["size"] is not None:
        print("Size:    %s bytes" % _size(result["size"]))
    if result["sha256"]:
        print("SHA-256: %s" % result["sha256"])
    print()

    for raw in result["checks"]:
        print_check(Check(**raw))

    if _VERBOSE:
        if result["blocks"]:
            print()
            print("EOS blocks:")
            print("  #   offset      role      stored     decoded    details")
            for block in result["blocks"]:
                print("  %-3d 0x%08X  %-8s  %-9s  %-9s  %s"
                      % (block["index"], block["offset"], block["role"],
                         _size(block["compressed"]),
                         _size(block["decoded_size"])
                         if block["decoded_size"] is not None else "-",
                         block["detail"]))
        if result["sections"]:
            print()
            print("Text sections:")
            for section in result["sections"]:
                print("  block %-2d  %-32s %3d articles"
                      % (section["block"], section["name"], section["articles"]))
        if result["music"]:
            print()
            print("Music:")
            for track in result["music"]:
                print("  block %-2d  %-32s %s bytes"
                      % (track["block"], track["name"] or "(untitled)",
                         _size(track["size"])))
        if result["graphics"]:
            print()
            print("Graphics: %d assets" % len(result["graphics"]))

    print()
    if summary["passed"]:
        warnings = summary["warnings"]
        print(_color(
            "Verification passed%s: %d blocks, %d sections, %d articles, "
            "%d music, %d graphics in %.2fs"
            % (" with %d warning(s)" % warnings if warnings else "",
               summary["blocks"], summary["sections"], summary["articles"],
               summary["music"], summary["graphics"],
               summary["duration_seconds"]),
            "32",
        ))
    else:
        print(_color(
            "Verification failed: %d error(s), %d warning(s) in %.2fs"
            % (summary["failures"], summary["warnings"],
               summary["duration_seconds"]),
            "31",
        ))


def _full_one(edition: Edition, args: argparse.Namespace) -> int:
    source = resolve_source(args, edition)
    if not source.is_file():
        _ensure_source(edition, args)
        return 2

    print_step("Step 1/3: verify source binary")
    result, return_code = verify_source(edition, source, strict=args.strict)
    render_verify(result)
    if return_code != 0:
        print_error("verification failed; extraction aborted")
        return return_code

    tool_extra = _tool_args(edition, args)

    print()
    print_step("Step 2/3: extract article text")
    return_code = run_tool(edition.text_script, tool_extra, quiet=args.quiet)
    if return_code != 0:
        return return_code

    print()
    print_step("Step 3/3: extract music and graphics")
    return_code = run_tool(edition.media_script, tool_extra, quiet=args.quiet)
    if return_code != 0:
        return return_code

    print()
    stats = collect_output_stats(edition)
    render_status(stats)
    return 0 if stats["complete"] else 1


def cmd_full(args: argparse.Namespace) -> int:
    return _run_for_editions(args, lambda edition: _full_one(edition, args))


def _unpack_one(edition: Edition, args: argparse.Namespace) -> int:
    source = resolve_source(args, edition)
    if not source.is_file():
        print_error("source binary not found: %s" % source)
        return 2

    tmp_dir = (
        resolve_out_dir(edition, args.tmp_dir)
        if args.tmp_dir
        else edition.tmp_dir
    )
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print_error("cannot create %s: %s" % (tmp_dir, exc))
        return 2

    stem = source.stem.replace(" ", "_")
    raw = tmp_dir / ("%s_diet_raw.bin" % stem)
    rebuilt = tmp_dir / ("%s_unpacked.exe" % stem)

    print_step("Unpacking outer DIET layer")
    return_code = run_tool(TOOLS / "diet_unpack.py", (source, raw), quiet=args.quiet)
    if return_code != 0:
        return return_code

    print_step("Rebuilding the DOS executable")
    return_code = run_tool(TOOLS / "diet_rebuild.py", (raw, rebuilt), quiet=args.quiet)
    if return_code != 0:
        return return_code

    if not rebuilt.is_file():
        print_error("rebuild did not produce %s" % rebuilt)
        return 1
    header = rebuilt.read_bytes()[:2]
    print_step("DIET diagnostics finished")
    _print_status(header in (b"MZ", b"ZM"),
                  "%s is %s bytes with MZ header %r"
                  % (_rel(rebuilt), _size(rebuilt.stat().st_size), header))
    return 0 if header in (b"MZ", b"ZM") else 1


def cmd_unpack(args: argparse.Namespace) -> int:
    return _run_for_editions(args, lambda edition: _unpack_one(edition, args))


def cmd_list(args: argparse.Namespace) -> int:
    editions = discover_editions()
    print("Relikt editions (%d):" % len(editions))
    for key, edition in editions.items():
        source = edition.find_source() or edition.source
        source_state = "present" if source.is_file() else "missing"
        print("  %-10s %s" % (key, edition.title))
        print("             data:   %s" % _rel(edition.data_dir))
        print("             out:    %s" % _rel(edition.out_dir))
        print("             tmp:    %s" % _rel(edition.tmp_dir))
        print("             source: %s [%s]" % (_rel(source), source_state))
        print("             text:   %s%s"
              % (_rel(edition.text_script),
                 "" if edition.text_script.is_file() else " [missing]"))
        print("             media:  %s%s"
              % (_rel(edition.media_script),
                 "" if edition.media_script.is_file() else " [missing]"))

    print()
    print("Shared tool scripts:")
    for name in ("magazines.py", "eos_blocks.py", "diet_unpack.py",
                 "diet_rebuild.py"):
        path = TOOLS / name
        print("  %-28s %s" % (name, "present" if path.is_file() else "missing"))
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _find_subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if action.dest == "command" and hasattr(action, "choices"):
            return action
    return None


def print_help(parser: argparse.ArgumentParser) -> None:
    """Explain the project and print every command with all of its options."""
    print("Relikt %s - %s" % (__version__, TAGLINE))
    print()
    print(PROJECT_HELP.rstrip())
    print()
    print(parser.format_help().rstrip())

    subparsers = _find_subparsers(parser)
    if subparsers is not None:
        seen: set[int] = set()
        for name, subparser in subparsers.choices.items():
            if id(subparser) in seen:
                continue
            seen.add(id(subparser))
            print()
            print("=" * 78)
            print("%s - command options" % name)
            print("=" * 78)
            print(subparser.format_help().rstrip())


def cmd_help(args: argparse.Namespace) -> int:
    parser = build_parser()
    topic = getattr(args, "topic", None)
    if not topic:
        print_help(parser)
        return 0

    subparsers = _find_subparsers(parser)
    subparser = subparsers.choices.get(topic.lower()) if subparsers else None
    if subparser is None:
        available: list[str] = []
        seen: set[int] = set()
        if subparsers is not None:
            for name, candidate in subparsers.choices.items():
                if id(candidate) not in seen:
                    seen.add(id(candidate))
                    available.append(name)
        print_error("unknown command %r (available: %s)"
                    % (topic, ", ".join(available)))
        return 1

    print(subparser.format_help().rstrip())
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print("Relikt %s" % __version__)
    print(TAGLINE)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relikt.py",
        description=(
            "Relikt %s - %s\n"
            "Orchestrates the per-edition extractor scripts in tools/."
            % (__version__, TAGLINE)
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python relikt.py help                   explain the project and all options\n"
            "  python relikt.py version                show the Relikt version\n"
            "  python relikt.py list                   show all known editions\n"
            "  python relikt.py verify                 verify the default edition (dragon5)\n"
            "  python relikt.py -e other verify        verify one other edition\n"
            "  python relikt.py --all full             extract every known edition\n"
            "  python relikt.py text                   extract articles only\n"
            "  python relikt.py media                  extract music and graphics only\n"
            "  python relikt.py status                 inspect out/<edition>/\n"
            "  python relikt.py unpack                 DIET diagnostics into tmp/<edition>/\n"
            "  python relikt.py verify --json --strict machine-readable, warnings fail\n"
            "\n"
            "Run `python relikt.py help <command>` for one command only.\n"
        ),
    )
    parser.add_argument("--version", action="version",
                        version="Relikt %s" % __version__)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-e", "--edition", default="dragon5",
        help="magazine edition to work on (default: dragon5)",
    )
    common.add_argument(
        "-a", "--all", action="store_true",
        help="run the command for every discovered edition",
    )
    common.add_argument("-v", "--verbose", action="store_true",
                        help="show extra detail (commands, block table, sections)")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="hide tool output unless a step fails")
    common.add_argument("--no-color", action="store_true",
                        help="disable colored status output (also honours "
                             "NO_COLOR; FORCE_COLOR=1 forces it)")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    verify = subparsers.add_parser(
        "verify", aliases=["check"], parents=[common],
        help="validate the source binary and its EOS block structure",
        description="Validate the source binary, DIET wrapper and EOS blocks. "
                    "This step reads the source only and writes nothing.",
    )
    verify.add_argument("-s", "--source", metavar="PATH",
                        help="check another binary instead of the edition default")
    verify.add_argument("--strict", action="store_true",
                        help="return a failure when warnings are reported")
    verify.add_argument("--json", action="store_true",
                        help="print a machine-readable JSON report")
    verify.set_defaults(func=cmd_verify)

    text = subparsers.add_parser(
        "text", aliases=["articles"], parents=[common],
        help="extract article text with the edition's text script",
        description="Extract only the articles. Runs the edition's text "
                    "extractor with --edition and --source.",
    )
    text.set_defaults(func=cmd_text)

    media = subparsers.add_parser(
        "media", aliases=["gfx"], parents=[common],
        help="extract music and graphics with the edition's media script",
        description="Extract only music and graphics. Runs the edition's "
                    "media extractor with --edition and --source.",
    )
    media.set_defaults(func=cmd_media)

    full = subparsers.add_parser(
        "full", aliases=["all"], parents=[common],
        help="verify, then extract text, music and graphics",
        description="Run verification first and abort if the binary does not "
                    "look like the expected edition. Then extract all assets.",
    )
    full.add_argument("--strict", action="store_true",
                      help="abort on verification warnings as well as errors")
    full.set_defaults(func=cmd_full)

    status = subparsers.add_parser(
        "status", parents=[common],
        help="inspect out/<edition>/ (or every edition with --all)",
        description="Count and validate the files currently present in "
                    "out/<edition>/.",
    )
    status.add_argument("--json", action="store_true",
                        help="print a machine-readable JSON report")
    status.set_defaults(func=cmd_status)

    unpack = subparsers.add_parser(
        "unpack", parents=[common],
        help="run DIET unpack/rebuild diagnostics into tmp/<edition>/",
        description="Run tools/diet_unpack.py and tools/diet_rebuild.py. "
                    "This is a diagnostic step; extraction does not require it.",
    )
    unpack.add_argument("-s", "--source", metavar="PATH",
                        help="source binary to unpack (default: edition source)")
    unpack.add_argument("--tmp-dir", default=None,
                        help="diagnostic output directory (default: tmp/<edition>/)")
    unpack.set_defaults(func=cmd_unpack)

    listing = subparsers.add_parser(
        "list", parents=[common],
        help="show registered editions and tool scripts",
        description="Show the editions Relikt knows about and whether the "
                    "underlying tool scripts are present.",
    )
    listing.set_defaults(func=cmd_list)

    help_parser = subparsers.add_parser(
        "help",
        help="explain the project and show every command and option",
        description="Explain what Relikt does and print the full command "
                    "reference, including every option of every command.",
    )
    help_parser.add_argument(
        "topic", nargs="?", metavar="COMMAND",
        help="show only the options of one command (for example: verify)",
    )
    help_parser.set_defaults(func=cmd_help)

    version_parser = subparsers.add_parser(
        "version",
        help="show the Relikt version",
        description="Print the current Relikt version.",
    )
    version_parser.set_defaults(func=cmd_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global _COLOR, _VERBOSE

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)

    _VERBOSE = bool(getattr(args, "verbose", False))
    _COLOR = _use_color(bool(getattr(args, "no_color", False)))

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print()
        print_error("interrupted")
        return 130
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - keep CLI failures readable
        print_error(str(exc))
        if _VERBOSE:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
