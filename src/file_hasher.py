import hashlib
import os
from pathlib import Path
from typing import BinaryIO, Union


def compute_file_hash(file_input: Union[str, Path, bytes, BinaryIO]) -> str:
    """
    Compute SHA256 hash of a file or file-like object.
    """
    hasher = hashlib.sha256()

    if isinstance(file_input, (str, Path)):
        with open(file_input, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
    elif isinstance(file_input, bytes):
        hasher.update(file_input)
    else:
        start_pos = file_input.tell()
        file_input.seek(0)

        for chunk in iter(lambda: file_input.read(8192), b""):
            hasher.update(chunk)

        file_input.seek(start_pos)

    return hasher.hexdigest()


def get_file_size(file_input: Union[str, Path, bytes, BinaryIO]) -> int:
    """
    Get the size of a file or file-like object.
    """
    if isinstance(file_input, (str, Path)):
        return os.path.getsize(file_input)
    elif isinstance(file_input, bytes):
        return len(file_input)
    else:
        start_pos = file_input.tell()
        file_input.seek(0, 2)
        size = file_input.tell()
        file_input.seek(start_pos)
        return size

