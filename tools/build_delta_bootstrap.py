"""Build a small self-contained Python zipapp for the first r116 delta update."""
import argparse
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path) -> dict:
    source = (ROOT / "tools/bootstrap_delta.py").read_text(encoding="utf-8")
    original = "from meowkeeper.app import delta_update as delta"
    if source.count(original) != 1:
        raise ValueError("差量過渡工具匯入契約已變更，停止封裝。")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("__main__.py", source.replace(original, "import delta_engine as delta"))
        archive.writestr("delta_engine.py", (ROOT / "src/meowkeeper/app/delta_update.py").read_bytes())
    return {"asset": output.name, "size": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    print(build(parser.parse_args().output))
