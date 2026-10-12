"""Скачивает клиент fxTunnel (https://github.com/mephistofox/fxtun.dev) и сверяет контрольную сумму.

Суммы зашиты здесь, а не берутся из релиза: так подменённый файл не пройдёт проверку (сверены с checksums.txt
релиза). Автор удаляет старые релизы — если сборка падает с 404, нужно обновить VERSION и суммы.
"""

import hashlib
import os
import sys
import urllib.error
import urllib.request

VERSION = "v3.16.1"
SHA256 = {
    "amd64": "232449c9a04ea5db4d40482ed506d9f878515e437658265d387cdda4d054774c",
    "arm64": "46ec98894033340be6d0d44e39210a5b3f288f14a32b7391ee8bd29c686d4823",
}
TARGET = os.environ.get("FXTUNNEL_TARGET", "/usr/local/bin/fxtunnel")


def main() -> None:
    arch = os.environ.get("TARGETARCH") or {"x86_64": "amd64", "aarch64": "arm64"}.get(os.uname().machine, "")
    if arch not in SHA256:
        sys.exit(f"fxTunnel: архитектура «{arch}» не поддерживается (есть: {', '.join(SHA256)})")
    url = f"https://github.com/mephistofox/fxtun.dev/releases/download/{VERSION}/fxtunnel-linux-{arch}"
    print(f"Скачиваю {url}")
    try:
        with urllib.request.urlopen(url, timeout=300) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"fxTunnel {VERSION}: релиза больше нет на GitHub (автор удаляет старые). "
                     "Обновите бота: git pull — или, если туннель не нужен, уберите tunnel из COMPOSE_PROFILES")
        raise
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[arch]:
        sys.exit(f"fxTunnel: контрольная сумма не совпала ({digest}), файл не устанавливаю")
    with open(TARGET, "wb") as f:
        f.write(data)
    os.chmod(TARGET, 0o755)
    print(f"fxTunnel {VERSION} ({arch}) установлен в {TARGET}")


if __name__ == "__main__":
    main()
