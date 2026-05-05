"""
Utilitaires ffmpeg / ffprobe partagés par les autres modules de la lib.

Ces fonctions sont volontairement sans état et ne dépendent pas de la
configuration : elles peuvent être réutilisées dans n'importe quel contexte.
"""

import subprocess
import sys


def run(cmd):
    """Lance un ffmpeg ; logge la commande et arrête tout en cas d'erreur."""
    print(f">>> {' '.join(cmd[:4])} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERREUR ffmpeg :", result.stderr[-1000:])
        sys.exit(1)


def get_duration(path):
    """Retourne la durée d'un fichier média en secondes (via ffprobe)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def parser_timestamp(t):
    """
    Convertit un timestamp en secondes (float).
    Accepte :
      - 30      → 30.0
      - 30.5    → 30.5
      - "30"    → 30.0
      - "1:30"  → 90.0 (mm:ss)
      - "1:30.5"→ 90.5
      - "1:23:45" → 5025.0 (hh:mm:ss)
      - None    → None
    """
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return float(t)
    s = str(t).strip()
    if ":" not in s:
        return float(s)
    parts = s.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Timestamp invalide : {t}")
