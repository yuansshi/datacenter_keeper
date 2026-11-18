"""Reject accidental runtime data, credentials, or internal deployment references."""

from __future__ import annotations

import ipaddress
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {"node_modules", ".venv", "__pycache__", "backups", "releases", ".claude", ".codex"}
PATTERNS = {
    "private-key material": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "internal issue tracker": re.compile(r"https?://[\w.-]+\.atlassian\.net\b"),
    "private GitLab host": re.compile(r"https?://gitlab\.(?!com(?:[/:]|$))[\w.-]+"),
    "internal lab deployment": re.compile(r"https?://(?=[^/\s]*\.cisco\.com)(?=[^/\s]*(?:lab|system|test))[^/\s]+"),
    "personal filesystem path": re.compile(r"/" r"Users/[^/\s]+/|/" r"home/(?!labkeeper(?:/|\b))[^/\s]+/"),
}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
EXAMPLE_NETWORKS = tuple(ipaddress.ip_network(x) for x in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "198.18.0.0/15"))


def main() -> None:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    errors: list[str] = []
    checked = 0
    for name in filter(None, names):
        path = ROOT / name
        if any(part in FORBIDDEN_PARTS for part in path.relative_to(ROOT).parts):
            errors.append(f"{name}: generated/private directory")
        if (path.name.startswith(".env") and path.name != ".env.example") or path.name == "labkeeper.yaml" or path.suffix in {".db", ".sqlite", ".sqlite3", ".pem", ".key"}:
            errors.append(f"{name}: runtime or credential file")
        if path.is_symlink():
            errors.append(f"{name}: source exports must not contain symlinks")
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        checked += 1
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    errors.append(f"{name}:{line_number}: {label}")
            if name.startswith("backend/tests/") or name == "scripts/check_public_source.py":
                continue  # Unit fixtures may deliberately model private networks.
            for match in IPV4.finditer(line):
                try:
                    address = ipaddress.ip_address(match.group())
                except ValueError:
                    continue
                if address.is_private and not address.is_loopback and not address.is_unspecified and not any(address in network for network in EXAMPLE_NETWORKS):
                    errors.append(f"{name}:{line_number}: use a documentation address in public examples")
    if errors:
        raise SystemExit("\n".join(sorted(set(errors))))
    print(f"Public-source checks passed for {checked} text files.")


if __name__ == "__main__":
    main()
