import os
import tempfile
from pathlib import Path

def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """
    Write file safely: write to temp file in same directory and atomically replace target.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)  # atomic replace
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise
