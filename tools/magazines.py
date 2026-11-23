"""Edition registry and path helpers for Relikt.

Every magazine is kept in its own folder::

    data/<edition>/       source binaries (read-only, source of truth)
    out/<edition>/        extracted articles, music and graphics
    tmp/<edition>/        temporary/diagnostic files
    tools/<edition>/      edition-specific extractor scripts
    examples/<edition>/   optional content listings used as references

The registry below describes the editions whose binary layout is already
known.  Relikt is the only component that decides which edition script to run.
Editions that only exist as a ``data/<edition>/`` folder are still usable:
Relikt discovers them automatically and looks for their scripts in
``tools/<edition>/``.  Adding a magazine therefore means creating its folders
and dropping in ``extract.py`` / ``extract_media.py`` - no changes to
relikt.py are required.  Legacy flat scripts in tools/ are still accepted as
a fallback.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
DATA = ROOT / "data"
OUT = ROOT / "out"
TMP = ROOT / "tmp"
EXAMPLES = ROOT / "examples"

# The extractors import eos_blocks as a top-level module.  Make that work both
# when they are run directly and when Relikt imports them by file path.
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


@dataclass(frozen=True)
class Edition:
    """Everything Relikt needs to know about one magazine edition."""

    key: str
    title: str
    source_names: tuple[str, ...] = ()
    text_script: Path = Path()
    media_script: Path = Path()
    block_roles: tuple[str, ...] = ()
    expected_sections: int | None = None
    expected_articles: int | None = None
    expected_music: int | None = None
    expected_graphics: int | None = None
    diet_packed: bool = False
    source_label: str = ""

    @property
    def data_dir(self) -> Path:
        return DATA / self.key

    @property
    def out_dir(self) -> Path:
        return OUT / self.key

    @property
    def tmp_dir(self) -> Path:
        return TMP / self.key

    @property
    def examples_dir(self) -> Path:
        return EXAMPLES / self.key

    @property
    def source(self) -> Path:
        """The conventional source path, even when the file is missing."""
        name = self.source_names[0] if self.source_names else "MAGAZINE.EXE"
        return self.data_dir / name

    def find_source(self) -> Path | None:
        """Find the actual source binary inside this edition's data folder."""
        if not self.data_dir.is_dir():
            return None
        for name in self.source_names:
            direct = self.data_dir / name
            if direct.is_file():
                return direct
        lowered = {name.lower() for name in self.source_names}
        for path in sorted(self.data_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() != ".exe":
                continue
            if lowered and path.name.lower() not in lowered:
                continue
            return path
        return None


DRAGON5_ROLES = (
    ("data",)
    + ("image",) * 4
    + ("gfxbank",)
    + ("music",) * 6
    + ("text",) * 7
)

REGISTRY: dict[str, Edition] = {
    "dragon5": Edition(
        key="dragon5",
        title="DRAGON #5 (09/1999)",
        source_names=("DRAGON.EXE", "DRAGON5.EXE"),
        source_label="DRAGON.EXE",
        text_script=TOOLS / "dragon5" / "extract.py",
        media_script=TOOLS / "dragon5" / "extract_media.py",
        block_roles=DRAGON5_ROLES,
        expected_sections=7,
        expected_articles=138,
        expected_music=6,
        expected_graphics=12,
        diet_packed=True,
    ),
}

TEXT_SCRIPT_CANDIDATES = (
    "{key}/extract.py",
    "{key}/extract_text.py",
    "{key}/text.py",
    "extract_{key}.py",  # legacy layout
)

MEDIA_SCRIPT_CANDIDATES = (
    "{key}/extract_media.py",
    "{key}/media.py",
    "{key}/graphics.py",
    "extract_{key}_media.py",  # legacy layout
)


def _first_script(key: str, candidates: tuple[str, ...]) -> Path:
    for template in candidates:
        path = TOOLS / template.format(key=key)
        if path.is_file():
            return path
    return TOOLS / candidates[0].format(key=key)


def make_edition(key: str) -> Edition:
    """Build an edition from conventions when it has no explicit registry entry."""
    registered = REGISTRY.get(key)
    if registered is not None:
        return registered
    return Edition(
        key=key,
        title=key.upper(),
        text_script=_first_script(key, TEXT_SCRIPT_CANDIDATES),
        media_script=_first_script(key, MEDIA_SCRIPT_CANDIDATES),
    )


def discover_editions() -> dict[str, Edition]:
    """Return registered editions plus any ``data/<name>/`` folder found."""
    editions = {key: make_edition(key) for key in REGISTRY}
    if DATA.is_dir():
        for child in sorted(DATA.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            editions.setdefault(child.name, make_edition(child.name))
    return dict(sorted(editions.items()))


def resolve_edition(key: str | None) -> Edition:
    key = (key or "dragon5").lower()
    editions = discover_editions()
    if key not in editions:
        raise SystemExit(
            "unknown edition %r (available: %s)"
            % (key, ", ".join(editions))
        )
    return editions[key]


def resolve_source(edition: Edition, override: str | Path | None = None) -> Path:
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return path
    return edition.find_source() or edition.source


def resolve_out_dir(edition: Edition, override: str | Path | None = None) -> Path:
    if not override:
        return edition.out_dir
    path = Path(override).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def add_edition_args(
    parser: argparse.ArgumentParser,
    *,
    include_source: bool = False,
    include_out_dir: bool = False,
) -> None:
    """Add the shared edition/source/output options used by the extractors."""
    parser.add_argument(
        "-e", "--edition", default="dragon5",
        help="magazine edition to work on (default: dragon5)",
    )
    if include_source:
        parser.add_argument(
            "-s", "--source", metavar="PATH",
            help="source binary (default: first EXE in data/<edition>/)",
        )
    if include_out_dir:
        parser.add_argument(
            "-o", "--out-dir", metavar="PATH",
            help="output directory (default: out/<edition>/)",
        )


def load_script_module(script: Path):
    """Import an edition extractor by file path; return None if unavailable."""
    import importlib.util

    if not script.is_file():
        return None
    try:
        relative = script.resolve().relative_to(TOOLS)
    except ValueError:
        relative = Path(script.name)
    parts = [
        "".join(char if char.isalnum() else "_" for char in part)
        for part in relative.with_suffix("").parts
    ]
    module_name = "relikt_edition_" + "_".join(part for part in parts if part)
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def relative_to_root(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
