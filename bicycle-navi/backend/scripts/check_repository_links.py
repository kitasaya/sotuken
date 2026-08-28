"""Repository内Markdownのローカルリンク切れを検査する。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[3]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "data:")


def target_path(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = unquote(target.split("#", 1)[0].strip())
    if not target or target.startswith(EXTERNAL_PREFIXES):
        return None
    return (source.parent / target).resolve()


def main() -> int:
    missing: list[tuple[Path, int, str]] = []
    checked = 0
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    for relative in sorted(path for path in tracked if path):
        source = ROOT / relative
        for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for match in LINK_RE.finditer(line):
                resolved = target_path(source, match.group(1))
                if resolved is None:
                    continue
                checked += 1
                if not resolved.exists():
                    missing.append((source.relative_to(ROOT), line_no, match.group(1)))

    print(f"checked_local_links={checked}")
    print(f"broken_local_links={len(missing)}")
    for source, line_no, target in missing:
        print(f"{source}:{line_no}: {target}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
