"""Check source syntax, release versions, bundled resources and repository hygiene."""

import ast
import json
import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_FILES = {
    "sponsor_config.json", "ecliptica_config.json", "untranslated_locations.txt",
    "sponsors.dat", "sponsors_original.txt", "tools/.mitm_ca_trusted",
    "tools/mitm_addon.py", "tools/mitm_addon_gen.py", "tools/mitm_sponsors.txt",
    "tools/proxy.pac", "tools/caddy/Caddyfile", "tools/caddy/.ca_trusted",
    "tools/mitmproxy.exe", "tools/mitmweb.exe",
}


def check():
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = [pathlib.PurePosixPath(name) for name in tracked if name]
    errors = []
    for relative in files:
        name = relative.as_posix()
        if (name in RUNTIME_FILES
                or any(part in {".venv", "__pycache__", "build", "dist", "_sounddevice_data"}
                       for part in relative.parts)
                or relative.suffix.lower() in {".log", ".pyc", ".pem", ".key"}
                or relative.name == ".env"):
            errors.append(f"Generated or local file tracked: {name}")
        if relative.suffix in {".py", ".spec"}:
            try:
                source = (ROOT / relative).read_text(encoding="utf-8-sig")
                compile(source, name, "exec")
            except (OSError, SyntaxError) as error:
                errors.append(f"Source check failed: {name}: {error}")

    source = (ROOT / "slashco_updater.py").read_text(encoding="utf-8")
    version = re.search(r'APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', source).group(1)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"当前版本：`{version}`" not in readme:
        errors.append("README version differs from APP_VERSION")
    section = re.search(rf"(?ms)^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", changelog)
    if section is None or not section[1].strip():
        errors.append(f"Missing release notes: {version}")

    bundled_count = 0
    for spec in ROOT.glob("*.spec"):
        for node in ast.walk(ast.parse(spec.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "Analysis":
                continue
            for keyword in node.keywords:
                if keyword.arg != "datas":
                    continue
                for source_path, _destination in ast.literal_eval(keyword.value):
                    bundled_count += 1
                    if not (ROOT / source_path).exists():
                        errors.append(f"Missing bundled resource in {spec.name}: {source_path}")

    images = json.loads((ROOT / "images.json").read_text(encoding="utf-8"))
    references = set()
    for value in images["mappings"].values():
        for name in value if isinstance(value, list) else [value]:
            if not isinstance(name, str) or not name or any(char in name for char in "/\\:") or name in {".", ".."}:
                errors.append(f"Invalid image filename: {name!r}")
                continue
            references.add(name)
    actual = {path.name for path in (ROOT / "img_assets").iterdir() if path.is_file()}
    for name in sorted(references - actual):
        errors.append(f"Missing image: {name}")
    for name in sorted(actual - references):
        errors.append(f"Unreferenced image: {name}")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"OK: version {version}, {len(files)} tracked files, {len(references)} images, "
          f"{bundled_count} bundled resource entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
