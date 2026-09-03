"""Materialize a generated app from the checked-in Next.js template. One npm ci, many clones."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

LOCKED_FILES = frozenset({
    "package.json", "package-lock.json", "tsconfig.json", "next.config.ts",
    "eslint.config.mjs", "vitest.config.mts", "vitest.setup.ts", "postcss.config.mjs",
})
SKIP_DIRS = frozenset({"node_modules", ".next", ".git"})
SKIP_FILES = frozenset({"tsconfig.tsbuildinfo", ".DS_Store", "next-env.d.ts"})


def materialize(template_dir: Path, apps_dir: Path, run_id: str) -> Path:
    """Copy the template's source files, then clone node_modules with APFS clonefile.

    Turbopack refuses a node_modules symlink that points outside the app, so it has to be a
    real directory. `cp -Rc` is copy-on-write, about 4 seconds for 30k files, nothing per run.
    """
    template_dir, apps_dir = Path(template_dir).resolve(), Path(apps_dir)
    app = apps_dir / run_id
    if app.exists():
        raise FileExistsError(f"{app} already exists")
    apps_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, app, ignore=shutil.ignore_patterns(*SKIP_DIRS, "tsconfig.tsbuildinfo"))
    nm = template_dir / "node_modules"
    if nm.exists():
        res = subprocess.run(["cp", "-Rc", str(nm), str(app / "node_modules")], capture_output=True, text=True)
        if res.returncode != 0:
            shutil.copytree(nm, app / "node_modules", symlinks=True)
    return app


def tree_hashes(root: Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def diff_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(f for f, h in after.items() if before.get(f) != h)


def ensure_node_modules(template_dir: Path) -> None:
    template_dir = Path(template_dir)
    if not (template_dir / "node_modules").exists():
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=template_dir, check=True)
