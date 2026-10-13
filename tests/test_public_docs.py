"""Published documentation, wheel descriptions and release-note boundaries."""

from __future__ import annotations

import importlib.util
import shutil
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = load("mcp_public_guard", "tools/check_public_docs.py")
COLLECTOR = load("mcp_public_notes", "release/collect_portable_assets.py")


def fixture(root):
    for path in GUARD.public_documents(ROOT):
        target = root / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return root


def wheel(path, readme, metadata=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("axiom_mcp/README.md", readme)
        archive.writestr(
            "axiom_mcp-0.1.5.dist-info/METADATA",
            metadata if metadata is not None else b"Name: axiom-mcp\nVersion: 0.1.5\n\n" + readme,
        )
    return path


def test_current_public_docs_and_tool_descriptions_are_clean():
    from axiom_mcp.tools.catalog import TOOL_SPECS

    assert GUARD.check(ROOT) == []
    for tool in TOOL_SPECS:
        assert GUARD.findings(tool.name, tool.summary) == []


@pytest.mark.parametrize(
    "identifier",
    ["C-028", "K-001", "ADR-0031", "V2-031", "R07", "SNP-03", "CP-02", "k-001.md"],
)
def test_nested_public_guide_identifier_is_rejected(tmp_path, identifier):
    root = fixture(tmp_path)
    path = root / "docs/new/nested.md"
    path.parent.mkdir()
    path.write_text(f"Use the {identifier} function.\n", encoding="utf-8")
    assert any(identifier.split(".")[0] in item for item in GUARD.check(root))


@pytest.mark.parametrize(
    "reference",
    [
        "docs/17-FASTAPI-MCP.md",
        "docs/17-fastapi-mcp.md",
        "repo-seeds/axiom-mcp/docs/17-FASTAPI-MCP.md",
        "axiom-specs/contracts/control-api-v1.md",
        "contracts/schemas/query-request.schema.json",
        "tasks/K/K-001.md",
    ],
)
def test_internal_specification_paths_are_rejected(tmp_path, reference):
    root = fixture(tmp_path)
    (root / "docs/internal-link.md").write_text(f"Read `{reference}` before using MCP.\n")
    assert GUARD.check(root)


def test_protocol_versions_tools_errors_and_public_prs_remain_allowed():
    assert (
        GUARD.findings(
            "reference",
            "graph_query; NOT_READY; schema_version 1; protocol 2025-11-25; SHA-256; #123; "
            "docs/reference/mcp.md; docs/cli-version-and-doctor.md; axiom-specs component",
        )
        == []
    )


def test_missing_document_and_mismatched_packaged_readme_are_rejected(tmp_path):
    root = fixture(tmp_path)
    (root / "src/axiom_mcp/README.md").write_text("Different copy", encoding="utf-8")
    (root / "AGENTS.md").unlink()
    errors = GUARD.check(root)
    assert any("missing public" in item for item in errors)
    assert any("parity failure" in item for item in errors)


def test_duplicate_unreleased_and_reordered_versions_are_rejected(tmp_path):
    root = fixture(tmp_path)
    path = root / "Changelog.md"
    path.write_text("# Changelog\n\n## Unreleased\n\n## 0.1.4\n\n## 0.1.5\n\n## Unreleased\n")
    errors = GUARD.check(root)
    assert any("one Unreleased" in item for item in errors)
    assert any("descending" in item for item in errors)


def test_generated_notes_select_only_one_version_and_refuse_unknown_version():
    text = COLLECTOR.render_release_notes("0.1.5")
    assert "## New Features" in text and "v0.1.4...v0.1.5" in text
    assert "0.1.5-py3-none-any.whl" in text
    assert "## Unreleased" not in text and "## 0.1.4" not in text
    assert GUARD.findings("notes", text) == []
    for version in ("latest", "0.1.99", "0.1.5-beta"):
        with pytest.raises(ValueError):
            COLLECTOR.render_release_notes(version)


def test_wheel_readme_metadata_and_source_parity_are_checked(tmp_path):
    readme = (ROOT / "README.md").read_bytes()
    path = wheel(tmp_path / "valid.whl", readme)
    assert GUARD.check_wheel(path, readme) == []
    path = wheel(tmp_path / "leaked.whl", readme, b"Name: axiom-mcp\n\nFollow C-028")
    errors = GUARD.check_wheel(path, readme)
    assert any("C-028" in item for item in errors)
    assert any("long description differs" in item for item in errors)
    path = wheel(tmp_path / "different.whl", b"Changed copy")
    assert any("README differs" in item for item in GUARD.check_wheel(path, readme))


def test_missing_wheel_doc_and_generated_note_leaks_are_rejected(tmp_path):
    path = tmp_path / "empty.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("unrelated.txt", "fine")
    assert GUARD.check_wheel(path, b"README")
    root = fixture(tmp_path / "repo")
    notes = tmp_path / "notes.md"
    notes.write_text("Release K-610", encoding="utf-8")
    assert any("generated release notes" in item for item in GUARD.check(root, notes=notes))


def test_visible_ci_labels_and_packaging_help_are_checked(tmp_path):
    root = fixture(tmp_path)
    workflow = root / ".github/workflows/check.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: K-610 release\n", encoding="utf-8")
    helper = root / "release/new-helper.py"
    helper.write_text(
        '"""C-001 helper."""\nimport argparse\nargparse.ArgumentParser(description=__doc__)\n',
        encoding="utf-8",
    )
    errors = GUARD.check(root)
    assert any("K-610" in item for item in errors)
    assert any("C-001" in item for item in errors)
