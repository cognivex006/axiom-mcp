"""Collect one portable wheel only after all three native proofs bind its bytes."""

import argparse
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path

LANES = {"windows-x64", "linux-x64", "macos-x64"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_release_notes(version: str, repository: Path | None = None) -> str:
    repository = repository or Path(__file__).resolve().parents[1]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("exact release version required")
    text = (repository / "Changelog.md").read_text(encoding="utf-8")
    entries = re.findall(r"^## (\d+\.\d+\.\d+)\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    matches = [body.strip() for name, body in entries if name == version]
    if len(matches) != 1:
        raise ValueError("exactly one matching Changelog release required")
    changes = re.sub(r"^### ", "## ", matches[0], flags=re.M)
    return (
        f"# Axiom MCP {version}\n\n{changes}\n\n## Installation\n\n"
        "Download the portable wheel and verify its SHA-256 against `SHA256SUMS`, then install "
        "it in a Python 3.13 virtual environment:\n\n"
        f"```sh\npython -m pip install axiom_mcp-{version}-py3-none-any.whl\n```\n\n"
        "Configure stdio or Streamable HTTP with registered credential references. "
        "Tool discovery alone does not establish authenticated graph access.\n\n"
        "See the [installation guide](https://github.com/cognivex006/axiom-mcp/blob/main/docs/"
        "install-and-upgrade.md) and [tool reference](https://github.com/cognivex006/axiom-mcp/"
        "blob/main/docs/reference/mcp.md).\n"
    )


def collect(root: Path, source: str, output: Path):
    if not re.fullmatch(r"[0-9a-f]{40}", source) or output.exists():
        raise ValueError("immutable source and new output required")
    proofs = list(root.rglob("wheel-provenance.json"))
    if len(proofs) != 1:
        raise ValueError("exactly one owner wheel proof required")
    proof = json.loads(proofs[0].read_bytes())
    name = proof.get("wheel", "")
    if Path(name).name != name or not name.endswith("-py3-none-any.whl"):
        raise ValueError("portable wheel name required")
    wheel = proofs[0].parent / name
    if proof.get("source_revision") != source or sha(wheel) != proof.get("sha256"):
        raise ValueError("wheel source/hash mismatch")
    reports = {}
    for path in root.rglob("native-report.json"):
        report = json.loads(path.read_bytes())
        lane = report.get("platform")
        if lane in reports or lane not in LANES:
            raise ValueError("duplicate/unsupported native lane")
        if (
            report.get("execution") != "native"
            or report.get("source_revision") != source
            or report.get("wheel_sha256") != proof["sha256"]
            or report.get("installed_wheel_verified") is not True
            or report.get("negative_cases_verified") is not True
        ):
            raise ValueError("native proof mismatch")
        cases = report.get("cases", [])
        if not cases or any(c.get("exit_code") != c.get("expected_exit_code") for c in cases):
            raise ValueError("failed/missing native cases")
        reports[lane] = path
    if set(reports) != LANES:
        raise ValueError("three native lanes required")
    inputs = {}
    for path in root.rglob("*-runtime-inputs.json"):
        item = json.loads(path.read_bytes())
        lane = item.get("platform")
        if lane not in LANES or lane in inputs or item.get("source_revision") != source:
            raise ValueError("runtime input source/lane mismatch")
        if item.get("owner_wheel_sha256") != proof["sha256"]:
            raise ValueError("runtime inputs use another owner wheel")
        files = [path]
        for key in ("inputs", "wheelhouse", "dependency_lock"):
            row = item[key]
            if Path(row["name"]).name != row["name"]:
                raise ValueError("unsafe runtime asset path")
            file = path.parent / row["name"]
            if sha(file) != row["sha256"]:
                raise ValueError("runtime asset hash mismatch")
            files.append(file)
        inputs[lane] = files
    if proof.get("runtime_inputs_required") and set(inputs) != LANES:
        raise ValueError("native dependency inputs required for distribution")
    repository = Path(__file__).resolve().parents[1]
    version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    release_notes = render_release_notes(version, repository)
    output.mkdir(parents=True)
    shutil.copyfile(wheel, output / name)
    shutil.copyfile(proofs[0], output / "wheel-provenance.json")
    for lane, path in reports.items():
        shutil.copyfile(path, output / (lane + "-native-report.json"))
    for files in inputs.values():
        for file in files:
            if (output / file.name).exists():
                raise ValueError("duplicate runtime output name")
            shutil.copyfile(file, output / file.name)
    (output / "SOURCE-REVISION.txt").write_text(source + "\n", encoding="utf-8", newline="\n")
    (output / "RELEASE-NOTES.md").write_text(
        release_notes,
        encoding="utf-8",
        newline="\n",
    )
    (output / "SHA256SUMS").write_text(
        "".join(sha(p) + "  " + p.name + "\n" for p in sorted(output.iterdir())),
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    collect(args.input, args.source_revision, args.out)
