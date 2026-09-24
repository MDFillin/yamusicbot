"""Скачивает клиент fxTunnel (https://github.com/mephistofox/fxtun.dev) и сверяет контрольную сумму.

Суммы зашиты здесь, а не берутся из релиза: так подменённый файл не пройдёт проверку.
"""

import hashlib
import os
import sys
import urllib.request

VERSION = "v3.9.2"
SHA256 = {
    "amd64": "2a7255dbf21741dbd4393dac9cd3468d4e8782ade2bce28386d864616650d71f",
    "arm64": "6a8cf8ed824cc73ae464893575f5b92eda955241d3e812141a794efb71487c71",
}
TARGET = os.environ.get("FXTUNNEL_TARGET", "/usr/local/bin/fxtunnel")


def main() -> None:
    arch = os.environ.get("TARGETARCH") or {"x86_64": "amd64", "aarch64": "arm64"}.get(os.uname().machine, "")
    if arch not in SHA256:
        sys.exit(f"fxTunnel: архитектура «{arch}» не поддерживается (есть: {', '.join(SHA256)})")
    url = f"https://github.com/mephistofox/fxtun.dev/releases/download/{VERSION}/fxtunnel-linux-{arch}"
    print(f"Скачиваю {url}")
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[arch]:
        sys.exit(f"fxTunnel: контрольная сумма не совпала ({digest}), файл не устанавливаю")
    with open(TARGET, "wb") as f:
        f.write(data)
    os.chmod(TARGET, 0o755)
    print(f"fxTunnel {VERSION} ({arch}) установлен в {TARGET}")


if __name__ == "__main__":
    main()
