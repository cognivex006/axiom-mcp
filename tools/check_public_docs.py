"""Check published MCP documents, tool help and wheel documentation for private IDs."""

from __future__ import annotations

import argparse
import ast
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ID = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z]-\d{3}(?:-\d+)?|V2-\d{3}|ADR-\d{4}|R\d{2}|(?:SNP|CP)-\d{2}|W\d+-[A-Z][A-Z-]+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
PRIVATE_REFERENCE = re.compile(
    r"(?:repo-seeds/|axiom-specs/|contracts/(?:schemas/)?[A-Za-z0-9._/-]+"
    r"|docs/\d{2}-[A-Z][A-Z0-9-]*\.md)",
    re.IGNORECASE,
)


def findings(name: str, text: str) -> list[str]:
    errors = [
        f"{name}:{text.count(chr(10), 0, match.start()) + 1}: private identifier {match.group()}"
        for match in PRIVATE_ID.finditer(text)
    ]
    errors.extend(
        f"{name}:{text.count(chr(10), 0, match.start()) + 1}: "
        f"internal specification reference {match.group()}"
        for match in PRIVATE_REFERENCE.finditer(text)
    )
    return errors


def public_documents(root: Path) -> list[Path]:
    paths = [
        root / name
        for name in (
            "README.md",
            "Changelog.md",
            "Development.md",
            "AGENTS.md",
            "src/axiom_mcp/README.md",
            "build/lib/axiom_mcp/README.md",
            "tests/native/README.md",
        )
    ]
    for directory in ("docs", "release"):
        paths.extend((root / directory).rglob("*.md"))
    return sorted(set(paths))


def check_wheel(path: Path, expected_readme: bytes) -> list[str]:
    errors = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        readme = "axiom_mcp/README.md"
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if names.count(readme) != 1 or len(metadata) != 1:
            return [f"{path.name}: require one packaged README and one METADATA"]
        if archive.read(readme) != expected_readme:
            errors.append(f"{path.name}: packaged README differs from source")
        for name in (readme, metadata[0]):
            errors.extend(findings(f"{path.name}/{name}", archive.read(name).decode("utf-8")))
        # RFC-style METADATA from Windows setuptools uses CRLF line endings.
        text = archive.read(metadata[0]).decode("utf-8").replace("\r\n", "\n")
        if text.split("\n\n", 1)[-1].rstrip() != expected_readme.decode("utf-8").rstrip():
            errors.append(f"{path.name}: METADATA long description differs from source")
    return errors


def check(root: Path, wheel: Path | None = None, notes: Path | None = None) -> list[str]:
    errors = []
    for path in public_documents(root):
        if not path.is_file():
            errors.append(f"missing public document: {path.relative_to(root)}")
            continue
        errors.extend(findings(str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    readme = (root / "README.md").read_bytes()
    for name in ("src/axiom_mcp/README.md", "build/lib/axiom_mcp/README.md"):
        path = root / name
        if path.is_file() and path.read_bytes() != readme:
            errors.append(f"{name}: README parity failure")
    text = (root / "Changelog.md").read_text(encoding="utf-8")
    if not text.startswith("# Changelog\n") or text.count("\n## Unreleased\n") != 1:
        errors.append("Changelog.md: require one title and one Unreleased section")
    versions = [
        tuple(map(int, v.split("."))) for v in re.findall(r"^## (\d+\.\d+\.\d+)\s*$", text, re.M)
    ]
    if not versions or versions != sorted(set(versions), reverse=True):
        errors.append("Changelog.md: require unique descending release versions")
    for directory in ("release", "tools", "src/axiom_mcp"):
        for path in (root / directory).rglob("*.py"):
            # Immutable vendored evaluator data is provenance, not public documentation.
            if "_canonical" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg not in ("description", "help"):
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        help_text = value.value
                    elif any(
                        isinstance(n, ast.Name) and n.id == "__doc__" for n in ast.walk(value)
                    ):
                        help_text = ast.get_docstring(tree) or ""
                    else:
                        continue
                    errors.extend(findings(str(path.relative_to(root)) + " (help)", help_text))
    for path in (root / ".github/workflows").glob("*.yml"):
        names = "\n".join(
            re.findall(r"^\s*(?:-\s*)?name:\s*(.+)$", path.read_text(encoding="utf-8"), re.M)
        )
        errors.extend(findings(str(path.relative_to(root)) + " (visible names)", names))
    if wheel:
        errors.extend(check_wheel(wheel, readme))
    if notes:
        errors.extend(findings("generated release notes", notes.read_text(encoding="utf-8")))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    errors = check(args.root, args.wheel, args.notes)
    if errors:
        print("\n".join(errors))
        return 1
    print("Public MCP documents, tool help and selected wheel/release text: no private IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
