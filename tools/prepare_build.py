"""Prepares everything the PyInstaller and Inno Setup steps need, under build/.

  build/vendor/lhm/       LibreHardwareMonitorLib + dependencies (.NET Framework 4.7.2 builds)
  build/vendor/pawnio/    official PawnIO_setup.exe, unmodified, checked by SHA-256
  build/icon.ico          application icon
  build/componentes.txt   list shown by the installer before installing
  build/THIRD_PARTY_NOTICES.txt

Run it with the build virtualenv active (the Python package list is read from it).
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
LHM_DIR = BUILD / "vendor" / "lhm"
PAWNIO_DIR = BUILD / "vendor" / "pawnio"

LHM_PACKAGE, LHM_VERSION = "LibreHardwareMonitorLib", "0.9.6"
PAWNIO_VERSION = "2.2.0"
PAWNIO_URL = f"https://github.com/namazso/PawnIO.Setup/releases/download/{PAWNIO_VERSION}/PawnIO_setup.exe"
PAWNIO_SHA256 = "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032"

# Runtime Python packages (top level); their dependencies are found from metadata.
PYTHON_PACKAGES = ["pyserial", "psutil", "pystray", "Pillow", "pythonnet"]

# Best match first. pythonnet runs the library on the .NET Framework that ships with Windows.
FRAMEWORK_GROUPS = [".NETFramework4.7.2", ".NETFramework4.7.1", ".NETFramework4.7",
                    ".NETFramework4.6.2", ".NETFramework4.6.1", ".NETFramework4.6",
                    ".NETFramework4.5", ".NETStandard2.0"]
LIB_FOLDERS = ["net472", "net471", "net47", "net462", "net461", "net46", "net45",
               "net40", "netstandard2.0"]


def download(url: str) -> bytes:
    print(f"  baixando {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "fervura-build"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _min_version(spec: str) -> str:
    # NuGet "1.2.3" means >= 1.2.3; ranges look like "[1.2.3, )". Lowest applicable wins.
    m = re.search(r"\d+(\.\d+)*(-[\w.]+)?", spec)
    if not m:
        raise ValueError(f"unsupported version spec {spec!r}")
    return m.group(0)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version.split("-")[0]))


def fetch_nuget(package: str, version: str, seen: dict[str, dict]) -> None:
    key = package.lower()
    if key in seen:
        if _version_key(version) <= _version_key(seen[key]["version"]):
            return
        for old in seen[key]["files"]:  # a dependency asks for a newer one
            (LHM_DIR / old).unlink(missing_ok=True)
    data = download(f"https://api.nuget.org/v3-flatcontainer/{key}/{version.lower()}/{key}.{version.lower()}.nupkg")
    z = zipfile.ZipFile(io.BytesIO(data))
    nuspec = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".nuspec"))))
    meta = next(e for e in nuspec if _local(e.tag) == "metadata")
    info = {_local(e.tag): (e.text or "").strip() for e in meta}
    license_el = next((e for e in meta if _local(e.tag) == "license"), None)
    entry = {
        "id": info.get("id", package), "version": version,
        "authors": info.get("authors", ""),
        "license": (license_el.text.strip() if license_el is not None and license_el.text
                    else info.get("licenseUrl", "não informada")),
        "url": info.get("projectUrl", f"https://www.nuget.org/packages/{package}/{version}"),
        "files": [],
    }
    seen[key] = entry

    names = z.namelist()
    candidates = [f"{prefix}{folder}/" for folder in LIB_FOLDERS
                  for prefix in ("runtimes/win-x64/lib/", "lib/")]
    for prefix in candidates:
        files = [n for n in names if n.lower().startswith(prefix)]
        if files:
            for n in files:
                base = n.rsplit("/", 1)[-1]
                if base.lower().endswith(".dll"):
                    (LHM_DIR / base).write_bytes(z.read(n))
                    entry["files"].append(base)
            break  # "_._" placeholder means the framework already provides it

    deps_el = next((e for e in meta if _local(e.tag) == "dependencies"), None)
    if deps_el is None:
        return
    groups = {g.get("targetFramework", ""): g for g in deps_el if _local(g.tag) == "group"}
    group = next((groups[f] for f in FRAMEWORK_GROUPS if f in groups), groups.get(""))
    if group is None:
        return
    for dep in group:
        if _local(dep.tag) == "dependency":
            fetch_nuget(dep.get("id"), _min_version(dep.get("version", "0")), seen)


def fetch_lhm() -> list[dict]:
    print(f"[1/4] {LHM_PACKAGE} {LHM_VERSION} e dependências (.NET Framework)")
    LHM_DIR.mkdir(parents=True, exist_ok=True)
    for old in LHM_DIR.glob("*.dll"):
        old.unlink()
    seen: dict[str, dict] = {}
    fetch_nuget(LHM_PACKAGE, LHM_VERSION, seen)
    packages = list(seen.values())
    for p in packages:
        print(f"  {p['id']} {p['version']}: {', '.join(p['files']) or '(fornecido pelo .NET Framework)'}")
    return packages


def fetch_pawnio() -> None:
    print(f"[2/4] PawnIO {PAWNIO_VERSION} (instalador oficial, sem modificações)")
    PAWNIO_DIR.mkdir(parents=True, exist_ok=True)
    target = PAWNIO_DIR / "PawnIO_setup.exe"
    data = target.read_bytes() if target.exists() else download(PAWNIO_URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != PAWNIO_SHA256:
        sys.exit(f"SHA-256 do PawnIO_setup.exe não confere: {digest}")
    target.write_bytes(data)
    print(f"  SHA-256 conferido: {digest}")


def make_icon() -> None:
    print("[3/4] ícone")
    sys.path.insert(0, str(ROOT))
    from fervura.tray import render
    render(None, True).save(BUILD / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])


def python_packages() -> list[dict]:
    out, queue, seen = [], list(PYTHON_PACKAGES), set()
    while queue:
        name = queue.pop(0)
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            sys.exit(f"pacote Python {name} não está instalado no ambiente de build")
        md = dist.metadata
        license_ = md.get("License-Expression") or md.get("License") or ""
        if not license_ or len(license_) > 60:
            classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or []
                           if c.startswith("License ::")]
            license_ = ", ".join(classifiers) or "ver pacote"
        out.append({"id": md["Name"], "version": dist.version, "license": license_,
                    "url": md.get("Home-page") or f"https://pypi.org/project/{md['Name']}/"})
        for req in dist.requires or []:
            if "extra ==" in req:
                continue
            if ";" in req and "sys_platform" in req and "win32" not in req:
                continue
            queue.append(re.split(r"[\s;<>=!~\[(]", req, maxsplit=1)[0])
    return out


def write_lists(nuget: list[dict]) -> None:
    print("[4/4] lista de componentes e avisos de terceiros")
    py = python_packages()
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    lines = [
        "Os seguintes componentes serão instalados:",
        "",
        "1. Fervura (este programa)",
        "   Pasta de instalação escolhida a seguir. O programa é executado com privilégios",
        "   de administrador, necessários para ler a temperatura da CPU.",
        "",
        f"2. Python {pyver} (embutido no programa; não altera nenhum Python do sistema)",
        "   Bibliotecas Python incluídas:",
        *[f"   - {p['id']} {p['version']} ({p['license']})" for p in py],
        "",
        "3. LibreHardwareMonitorLib (leitura de sensores; executa no .NET Framework do Windows)",
        *[f"   - {p['id']} {p['version']} ({p['license']})" for p in nuget],
        "",
        f"4. Driver PawnIO {PAWNIO_VERSION} - software de terceiros (namazso, https://pawnio.eu)",
        "   Necessário para ler a temperatura da CPU. Instalado pelo instalador oficial do",
        "   PawnIO, sem modificações, somente se ainda não estiver presente no computador.",
        "   Você será consultado antes da instalação do driver.",
    ]
    (BUILD / "componentes.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")

    notices = [
        "AVISOS DE TERCEIROS - Fervura",
        "",
        f"Python {pyver} - Python Software Foundation License - https://www.python.org/",
        "",
        "Bibliotecas Python:",
        *[f"  {p['id']} {p['version']} - {p['license']} - {p['url']}" for p in py],
        "",
        "Bibliotecas .NET (código-fonte disponível nos endereços indicados):",
        *[f"  {p['id']} {p['version']} - {p['license']} - {p['url']}" for p in nuget],
        "",
        f"PawnIO {PAWNIO_VERSION} - namazso - https://pawnio.eu",
        "  third_party/PawnIO/PawnIO_setup.exe é o instalador oficial, redistribuído sem",
        "  modificações conforme os termos do próprio instalador.",
        f"  SHA-256: {PAWNIO_SHA256}",
    ]
    (BUILD / "THIRD_PARTY_NOTICES.txt").write_text("\r\n".join(notices) + "\r\n", encoding="utf-8-sig")


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    nuget = fetch_lhm()
    fetch_pawnio()
    make_icon()
    write_lists(nuget)
    print("Pronto.")


if __name__ == "__main__":
    main()
