"""Immutable, addressable artifacts: runs/<run_id>/NN-stage.json, hashed, never overwritten."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ArtifactError(Exception):
    pass


def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    return hash_bytes(Path(path).read_bytes())


def write(run_dir: Path, seq: int, stage: str, artifact: BaseModel) -> tuple[Path, str]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{seq:02d}-{stage}.json"
    if path.exists():
        raise ArtifactError(f"{path} already exists; artifacts are immutable")
    data = artifact.model_dump_json(indent=2).encode()
    path.write_bytes(data)
    return path, hash_bytes(data)


def load(path: Path, model: type[T], *, expected_sha: str | None = None,
         expected_parent: str | None = None) -> T:
    path = Path(path)
    data = path.read_bytes()
    sha = hash_bytes(data)
    if expected_sha is not None and sha != expected_sha:
        raise ArtifactError(f"{path}: hash mismatch, expected {expected_sha}, got {sha}")
    try:
        obj = model.model_validate_json(data)
    except ValidationError as e:
        raise ArtifactError(f"{path}: schema invalid: {e}") from e
    if expected_parent is not None and getattr(obj, "parent", None) != expected_parent:
        raise ArtifactError(f"{path}: parent mismatch, expected {expected_parent}")
    return obj
