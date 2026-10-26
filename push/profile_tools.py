"""Load and restore a private profile archive (encrypted with a passphrase you choose).

Archive layout (a tar, encrypted with openssl AES-256-CBC + PBKDF2-SHA256, 600000 iterations):
  PROFILE-V1                      sentinel, must be present
  manifest.json                   defaults for the interview
  claude/settings.json            -> ~/.claude/settings.json
  claude/memory/<files>           -> ~/.claude/projects/-home-<user>/memory/<files>

Only those paths are ever restored. No credentials or chat history are expected in the archive,
and any other path is ignored.
"""
import io
import json
import os
import re
import subprocess
import tarfile

ITER = "600000"
MAX_TOTAL = 5 * 1024 * 1024
MEMORY_NAME = re.compile(r"[A-Za-z0-9._-]{1,80}\.md")


def _norm(name: str) -> str:
    """Drop one leading './' (tar on Windows writes './PROFILE-V1'); never strip anything else."""
    return name[2:] if name.startswith("./") else name


def has_sentinel(tar_bytes: bytes) -> bool:
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
            return any(_norm(n) == "PROFILE-V1" for n in tf.getnames())
    except tarfile.TarError:
        return False


def decrypt(enc_path: str, passphrase: str) -> bytes | None:
    """Return the tar bytes, or None if the passphrase is wrong or the file is damaged."""
    try:
        p = subprocess.run(
            ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", ITER, "-md", "sha256", "-pass", "stdin", "-in", enc_path],
            input=passphrase.encode(), capture_output=True, timeout=120,
        )
    except Exception:
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    return p.stdout if has_sentinel(p.stdout) else None


def _members(tar_bytes: bytes):
    tf = tarfile.open(fileobj=io.BytesIO(tar_bytes))
    total = 0
    for m in tf.getmembers():
        if not m.isfile():
            continue
        name = _norm(m.name)
        if not name or name.startswith("/") or ".." in name.split("/"):
            continue
        total += m.size
        if total > MAX_TOTAL:
            raise ValueError("profile archive is too large")
        yield tf, m, name


def read_manifest(tar_bytes: bytes) -> dict:
    for tf, m, name in _members(tar_bytes):
        if name == "manifest.json":
            try:
                data = json.loads(tf.extractfile(m).read().decode())
            except Exception:
                return {}
            return data.get("defaults", {}) if isinstance(data, dict) else {}
    return {}


def restore(tar_bytes: bytes, target_root: str, username: str) -> list[str]:
    """Write allowed files under <target_root>/home/<username>/ and return what was written."""
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise ValueError("bad username")
    home = os.path.join(target_root, "home", username)
    written = []
    for tf, m, name in _members(tar_bytes):
        dest = None
        if name == "claude/settings.json":
            dest = os.path.join(home, ".claude", "settings.json")
        elif name.startswith("claude/memory/") and MEMORY_NAME.fullmatch(name[len("claude/memory/"):]):
            dest = os.path.join(home, ".claude", "projects", f"-home-{username}", "memory", name[len("claude/memory/"):])
        if not dest:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(tf.extractfile(m).read())
        os.chmod(dest, 0o600)
        written.append(dest.replace(target_root, "", 1))
    return written
