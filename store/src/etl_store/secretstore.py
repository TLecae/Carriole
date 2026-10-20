"""Protection de la clé de chiffrement des secrets.

Sous Windows, la clé Fernet est protégée par DPAPI : seul le compte Windows qui l'a créée peut la relire (le fichier
copié sur un autre poste ou par un autre utilisateur est illisible). Ailleurs, le fichier est en clair avec les droits
0600 (serveur : préférer `ETL_SECRET_KEY` ou un gestionnaire de secrets).
"""

from __future__ import annotations

import sys

PREFIX = b"dpapi:"


def available() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes):  # pragma: no cover - Windows seulement
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _crypt(data: bytes, protect: bool) -> bytes:  # pragma: no cover - Windows seulement
    import ctypes

    source, _keep = _blob(data)
    out = type(source)()
    function = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError(f"DPAPI : échec de {'la protection' if protect else 'la lecture'} de la clé")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(key: bytes) -> bytes:
    """Contenu à écrire dans le fichier de clé."""
    return PREFIX + _crypt(key, True) if available() else key


def unprotect(content: bytes) -> bytes:
    """Clé en clair depuis le contenu du fichier (clé protégée, ou ancienne clé en clair)."""
    if content.startswith(PREFIX):  # pas de strip() ici : le blob DPAPI est binaire
        if not available():
            raise OSError("la clé de chiffrement est protégée par Windows : elle ne se lit que sur le poste d'origine")
        return _crypt(content[len(PREFIX) :], False).strip()
    return content.strip()
