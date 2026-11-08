"""Validate the local operator's private catalog directory before SQLite opens it."""

import os
import stat
import subprocess
from pathlib import Path

CATALOG_NAME = "placsp.sqlite3"
CATALOG_FILES = (CATALOG_NAME, *(f"{CATALOG_NAME}-{suffix}" for suffix in ("journal", "wal", "shm")))

_CHECK_WINDOWS_ACL = r"""
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$trusted = @($sid.Value, 'S-1-5-18', 'S-1-5-32-544')
$dir = $env:INDAI_TENDERS_ACL_PATH
$paths = @($dir)
foreach ($name in @('placsp.sqlite3','placsp.sqlite3-journal','placsp.sqlite3-wal','placsp.sqlite3-shm')) {
    $path = Join-Path $dir $name
    if (Test-Path -LiteralPath $path) { $paths += $path }
}
foreach ($path in $paths) {
    if ($path -eq $dir) { $acl = [IO.Directory]::GetAccessControl($path) }
    else { $acl = [IO.File]::GetAccessControl($path) }
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { exit 1 }
    $rules = $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
    if (@($rules | Where-Object {
        $_.IdentityReference.Value -notin $trusted -or $_.AccessControlType -ne 'Allow'
    }).Count) { exit 1 }
    $modify = [Security.AccessControl.FileSystemRights]::Modify
    if (-not @($rules | Where-Object {
        $_.IdentityReference.Value -eq $sid.Value -and
        ($_.FileSystemRights -band $modify) -eq $modify -and
        -not ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
        ($path -ne $dir -or (
            ($_.InheritanceFlags -band [Security.AccessControl.InheritanceFlags]::ObjectInherit) -and
            -not ($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::NoPropagateInherit)
        ))
    }).Count) { exit 1 }
}
exit 0
"""


def private_catalog_path(directory: str) -> Path:
    candidate = Path(directory).expanduser()
    if not candidate.is_absolute() or candidate.drive.startswith("\\\\"):
        raise ValueError("Se requiere una ruta absoluta en una unidad local para el catálogo.")
    root = candidate.resolve(strict=True)
    forbidden = [Path(__file__).resolve().parents[1]]
    forbidden.extend(Path(value).resolve() for value in (
        os.environ.get("PUBLIC"), os.environ.get("OneDrive"),
        os.environ.get("OneDriveConsumer"), os.environ.get("OneDriveCommercial"),
    ) if value)
    if not root.is_dir() or any(root.is_relative_to(path) for path in forbidden):
        raise ValueError("El catálogo requiere un directorio privado existente fuera del repositorio.")
    for path in (candidate, *candidate.parents):
        _reject_redirect(path.lstat())
    existing = []
    for name in CATALOG_FILES:
        try:
            info = (root / name).lstat()
        except FileNotFoundError:
            continue
        _reject_redirect(info)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("El catálogo contiene un archivo no seguro.")
        existing.append(info)
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError("El directorio del catálogo no permite lectura y escritura.")
    if os.name == "nt":
        _verify_windows_acl(root)
    elif any(info.st_uid != os.geteuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
             for info in (root.stat(), *existing)):
        raise ValueError("El catálogo requiere permisos exclusivos del operador.")
    return root / CATALOG_NAME


def _reject_redirect(info):
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("El directorio y los archivos del catálogo deben ser rutas físicas.")


def _verify_windows_acl(root: Path) -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _CHECK_WINDOWS_ACL],
        env={**os.environ, "INDAI_TENDERS_ACL_PATH": str(root)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise ValueError("ACL del catálogo no válida para el piloto local.")
