#!/usr/bin/env python3
"""
Lance un mini-rendu --preview pour chaque nouvel effet et bloc DSL.

Usage (depuis la racine du projet) :
    python3 scripts/test_preview_all.py              # tout
    python3 scripts/test_preview_all.py kaleidoscope # un seul

Chaque test :
- Écrit un scenario temporaire dans /tmp/anomalie_tests/
- Lance scripts/montage_grille.py <scenario> --preview
- Affiche un récap (OK/FAIL + temps de rendu)

Outputs : videos/test_<nom>_preview.mp4 (renommé _preview par --preview).
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)


# Chaque test fait 5-8s à 256 cellules max — minimal pour vérifier rapidement.
TESTS = {
    "kaleidoscope": """
output_video: "videos/test_kaleidoscope.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio: { actif: false }
effets:
  kaleidoscope:
    secteurs: 6
mode_plan: "scenario"
scenario:
  sequences:
    - { taille: 16, effet: "kaleidoscope", duree: 5 }
""",
    "rotation": """
output_video: "videos/test_rotation.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio: { actif: false }
mode_plan: "scenario"
scenario:
  sequences:
    - { taille: 16, effet: "rotation", duree: 5 }
""",
    "datamosh": """
output_video: "videos/test_datamosh.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio: { actif: false }
mode_plan: "scenario"
scenario:
  sequences:
    - { taille: 16, effet: "datamosh", duree: 5 }
""",
    "audioreactif": """
output_video: "videos/test_audioreactif.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio:
  actif: true
  mode: "auto"
  comportement: "continu"
  fichiers: ["musiques/spiral.mp3"]
effets:
  audioreactif:
    mode: "rms"
    fichier: "musiques/spiral.mp3"
    intensite: 0.4
    rotation: 0.0
mode_plan: "scenario"
scenario:
  sequences:
    - { taille: 16, effet: "audioreactif", duree: 6 }
""",
    "transition_smooth": """
output_video: "videos/test_transition_smooth.mp4"
input_anomalie: "videos/IMG_0175.MOV"
max_cases: 256
masques: { actif: false }
audio: { actif: false }
mode_plan: "scenario"
scenario:
  sequences:
    - type: "transition"
      mode: "smooth"
      taille: 16
      video: "videos/IMG_0175.MOV"
      duree_totale: 6
""",
    "rampe": """
output_video: "videos/test_rampe.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio: { actif: false }
mode_plan: "scenario"
scenario:
  sequences:
    - type: "rampe"
      taille_de: 1
      taille_a: 16
      duree_par_taille: 3
      crossfade: 1.5
""",
    "interpolation": """
output_video: "videos/test_interpolation.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio: { actif: false }
mode_plan: "scenario"
scenario:
  sequences:
    - type: "interpolation"
      taille: 16
      effet: "lsd"
      duree_totale: 6
      paliers: 4
      parametres:
        amplitude_onde: [20, 150]
        saturation: [1.0, 3.0]
""",
    "repeter": """
output_video: "videos/test_repeter.mp4"
input_anomalie: null
max_cases: 256
masques: { actif: false }
audio: { actif: false }
mode_plan: "scenario"
scenario:
  sequences:
    - { taille: 8, aleatoire: true, anomalies: 3, duree: 1, repeter: 4 }
""",
}


def lancer_test(nom, yaml_str):
    print(f"\n{'=' * 60}")
    print(f"TEST : {nom}")
    print(f"{'=' * 60}")
    tmp_dir = Path(tempfile.gettempdir()) / "anomalie_tests"
    tmp_dir.mkdir(exist_ok=True)
    tmp = tmp_dir / f"{nom}.yaml"
    tmp.write_text(yaml_str)
    t0 = time.time()
    rc = subprocess.run(
        ["python3", "scripts/montage_grille.py", str(tmp), "--preview"]
    ).returncode
    elapsed = time.time() - t0
    return rc == 0, elapsed


def main():
    selection = sys.argv[1:] or list(TESTS.keys())
    inconnus = [n for n in selection if n not in TESTS]
    if inconnus:
        print(f"Tests inconnus : {inconnus}")
        print(f"Tests disponibles : {list(TESTS.keys())}")
        sys.exit(1)

    resultats = []
    for nom in selection:
        ok, t = lancer_test(nom, TESTS[nom])
        resultats.append((nom, ok, t))

    print(f"\n{'=' * 60}")
    print("RÉCAPITULATIF")
    print(f"{'=' * 60}")
    total = 0.0
    for nom, ok, t in resultats:
        marque = "OK  " if ok else "FAIL"
        print(f"  [{marque}] {nom:<22} {t:>7.1f}s   → videos/test_{nom}_preview.mp4")
        total += t
    print(f"\n  Total : {total:.1f}s sur {len(resultats)} tests")


if __name__ == "__main__":
    main()
