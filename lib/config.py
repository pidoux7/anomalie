"""
Chargement de la configuration YAML, détection de l'encodeur et calcul du plan.

L'import de ce module lit `sys.argv[1]` (ou `config.yaml` par défaut),
charge la config, valide les fichiers requis, détecte l'encodeur ffmpeg,
calcule la liste des phases et le plan d'exécution.

Toutes les autres parties de la lib accèdent aux constantes via
`from lib import config` puis `config.NOM`.
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERREUR : pip3 install pyyaml")
    sys.exit(1)

try:
    from PIL import Image  # noqa: F401  (utilisé par lib.masques)
    PIL_OK = True
except ImportError:
    PIL_OK = False


# ============================================================
# 1. Lecture du YAML
# ============================================================
config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
if not Path(config_path).exists():
    print(f"Config introuvable : {config_path}")
    sys.exit(1)

with open(config_path, "r") as f:
    cfg = yaml.safe_load(f)

INPUT_VIDEO    = cfg["input_video"]
INPUT_ANOMALIE = cfg.get("input_anomalie")
OUTPUT_VIDEO   = cfg["output_video"]
FINAL_W        = cfg["final_w"]
FINAL_H        = cfg["final_h"]
CELLULES_CARREES = cfg["cellules_carrees"]
DUREE_TOTALE   = cfg.get("duree_totale", 240)
DUREE_MAX      = cfg.get("duree_max", 600)
MAX_CASES      = cfg["max_cases"]
MODE_DUREE     = cfg.get("mode_duree", "duree_max")
N_BOUCLES      = cfg.get("n_boucles_par_phase", 1)
FIN_PARTIELLE  = cfg.get("fin_partielle", "etendre")
MODE_ANOMALIE  = cfg["mode_anomalie"]
CHANGEMENT_ANOMALIE   = cfg["changement_anomalie"]
INTERVALLE_CHANGEMENT = cfg["intervalle_changement"]
MODE_QUANTITE         = cfg["mode_quantite"]
NB_ANOMALIES_FIXE     = cfg["nb_anomalies_fixe"]
RATIO_PROPORTIONNEL   = cfg["ratio_proportionnel"]
PRESET = cfg["preset_x264"]
CRF    = str(cfg["crf"])

# ============================================================
# 2. Performance (encodeur, parallélisme, cache)
# ============================================================
PERF = cfg.get("performance", {})
ENCODEUR_CFG = PERF.get("encodeur", "auto")
QUALITE_HW   = int(PERF.get("qualite_hw", 65))
PARAL_CFG    = PERF.get("parallelisme", "auto")
CACHE_ACTIF  = PERF.get("cache_actif", True)

if PARAL_CFG == "auto":
    NB_PARALLEL = max(1, (os.cpu_count() or 4) - 1)
else:
    NB_PARALLEL = max(1, int(PARAL_CFG))


def detecter_encodeur():
    """Détecte le meilleur encodeur disponible selon la config."""
    if ENCODEUR_CFG != "auto":
        return ENCODEUR_CFG

    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True
        )
        encs = r.stdout
    except Exception:
        return "x264"

    if "h264_videotoolbox" in encs and sys.platform == "darwin":
        return "videotoolbox"
    if "h264_nvenc" in encs:
        return "nvenc"
    return "x264"


ENCODEUR = detecter_encodeur()


def args_encodage(crf_value=None):
    """
    Retourne les args ffmpeg pour l'encodage selon l'encodeur sélectionné.
    Utilise CRF par défaut, ou la valeur passée en argument.
    """
    if crf_value is None:
        crf_value = CRF

    if ENCODEUR == "videotoolbox":
        return ["-c:v", "h264_videotoolbox",
                "-q:v", str(QUALITE_HW),
                "-allow_sw", "1"]
    elif ENCODEUR == "nvenc":
        return ["-c:v", "h264_nvenc",
                "-preset", "p4",
                "-cq", str(crf_value)]
    else:
        return ["-c:v", "libx264",
                "-preset", PRESET,
                "-crf", str(crf_value)]


# ============================================================
# 3. Anomalies, masques, effets, audio (config)
# ============================================================
ANOMALIES = [
    (f["nom"], f["filtre"])
    for f in cfg["filtres_anomalies"]
    if f.get("actif", True)
]

MSK = cfg.get("masques", {"actif": False})
MASQUES_ACTIFS = MSK.get("actif", False)
if MASQUES_ACTIFS and not PIL_OK:
    print("ERREUR : système de masques activé mais Pillow non installé.")
    print("  Installe avec : pip3 install pillow")
    sys.exit(1)

EFFETS = cfg.get("effets", {"actif": False})
EFFETS_ACTIFS = EFFETS.get("actif", False)
EFFETS_LISTE = EFFETS.get("liste", []) if EFFETS_ACTIFS else []

AUDIO = cfg.get("audio", {"actif": False})
AUDIO_ACTIF = AUDIO.get("actif", False)
AUDIO_MODE = AUDIO.get("mode", "auto")               # "auto" ou "scenario"
AUDIO_SCENARIO = AUDIO.get("scenario", [])
AUDIO_CONFLIT_DEFAUT = AUDIO.get("conflit_par_defaut", "ecrase")

# Validation immédiate des fichiers audio
if AUDIO_ACTIF:
    fichiers_audio = AUDIO.get("fichiers", [])
    for entree in fichiers_audio:
        chemin = entree if isinstance(entree, str) else entree.get("fichier")
        if not chemin or not Path(chemin).exists():
            print(f"ERREUR audio (vérification au démarrage) : fichier introuvable : {chemin}")
            print("  Corrige la liste 'fichiers' dans config.yaml avant de relancer.")
            sys.exit(1)

# Validation des fichiers vidéo nécessaires aux effets
if EFFETS_ACTIFS:
    if "mosaique" in EFFETS_LISTE:
        carte = EFFETS.get("mosaique", {}).get("video_carte")
        if not carte or not Path(carte).exists():
            print(f"ERREUR effets : video_carte introuvable : {carte}")
            sys.exit(1)
    if "mur_moniteurs" in EFFETS_LISTE:
        videos_mur = EFFETS.get("mur_moniteurs", {}).get("videos", [])
        for v in videos_mur:
            if not Path(v).exists():
                print(f"ERREUR effets : vidéo mur introuvable : {v}")
                sys.exit(1)
        if not videos_mur and not EFFETS.get("mur_moniteurs", {}).get("inclure_source", True):
            print("ERREUR effets : mur_moniteurs sans vidéos (cocher inclure_source ou ajouter des vidéos)")
            sys.exit(1)


# ============================================================
# 4. Répertoires de travail
# ============================================================
WORK_DIR = Path("travail_montage")
WORK_DIR.mkdir(exist_ok=True)

CACHE_DIR = WORK_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 5. Calcul des phases (tailles de grille successives)
# ============================================================
def pair_inf(n):
    return (n // 2) * 2


def calculer_phases(max_cases):
    phases = []
    t = 1
    while t * t <= max_cases:
        phases.append(t)
        t *= 2
    return phases


PHASES = calculer_phases(MAX_CASES)
diviseur = max(2, PHASES[-1] * 2)
FINAL_W = (FINAL_W // diviseur) * diviseur
FINAL_H = (FINAL_H // diviseur) * diviseur


def _duree_video(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())


if not Path(INPUT_VIDEO).exists():
    print(f"Vidéo introuvable : {INPUT_VIDEO}")
    sys.exit(1)

DUREE_SOURCE = _duree_video(INPUT_VIDEO)


# ============================================================
# 6. Plan d'exécution : liste de séquences à générer
#
# Le PLAN est une liste de tuples 4-éléments :
#     (num_phase, taille, duree_seq, directives)
# où directives est un dict optionnel pour le mode "scenario", ou None
# pour le mode "auto" (comportement classique géré par creer_phase).
# ============================================================
MODE_PLAN = cfg.get("mode_plan", "auto")
SCENARIO  = cfg.get("scenario", {})


def _resolve_filtre(filtre):
    """
    filtre peut être :
      - str (nom)         → cherche dans ANOMALIES par nom
      - int (index 1-based) → ANOMALIES[index-1]
    Retourne (nom, expr_filtre).
    """
    if isinstance(filtre, int):
        idx = filtre - 1
        if idx < 0 or idx >= len(ANOMALIES):
            print(f"ERREUR scenario : index de filtre {filtre} hors bornes "
                  f"(1-{len(ANOMALIES)})")
            sys.exit(1)
        return ANOMALIES[idx]
    for nom, expr in ANOMALIES:
        if nom == filtre:
            return (nom, expr)
    print(f"ERREUR scenario : filtre '{filtre}' introuvable. "
          f"Disponibles : {[a[0] for a in ANOMALIES]}")
    sys.exit(1)


def _resolve_duree_seq(duree_explicite, duree_par_defaut):
    if duree_explicite is not None:
        return float(duree_explicite)
    if duree_par_defaut == "boucle":
        return DUREE_SOURCE
    return float(duree_par_defaut)


def _construire_directives(entry):
    """Construit le dict de directives à partir d'une entrée scenario."""
    d = {}
    if "filtre" in entry:
        d["filtre"] = _resolve_filtre(entry["filtre"])
    if "anomalies" in entry:
        d["anomalies"] = int(entry["anomalies"])
    if entry.get("source") == "video":
        d["source"] = "video"
    if "effet" in entry:
        d["effet"] = entry["effet"]
    if "masque" in entry:
        d["masque"] = entry["masque"]
    if entry.get("aleatoire"):
        d["aleatoire"] = True

    # Défaut : 1 anomalie dès qu'on précise un filtre/source/aléatoire
    a_specifie = ("filtre" in d or "source" in d or "aleatoire" in d)
    if a_specifie and "anomalies" not in d:
        d["anomalies"] = 1

    return d or None


def construire_plan_auto():
    """Plan classique (comportement avant le mode scenario)."""
    plan = []

    if MODE_DUREE == "duree_max":
        duree_phase = DUREE_SOURCE * len(PHASES)
        cumul = 0
        num_phase = 1
        while cumul < DUREE_MAX:
            duree_restante = DUREE_MAX - cumul
            if duree_restante >= duree_phase:
                for taille in PHASES:
                    plan.append((num_phase, taille, DUREE_SOURCE, None))
                cumul += duree_phase
                num_phase += 1
            else:
                if FIN_PARTIELLE == "tronquer":
                    break
                elif FIN_PARTIELLE == "depasser":
                    for taille in PHASES:
                        plan.append((num_phase, taille, DUREE_SOURCE, None))
                    cumul += duree_phase
                    num_phase += 1
                    break
                else:  # "etendre"
                    for taille in PHASES:
                        if duree_restante <= 0:
                            break
                        d = min(DUREE_SOURCE, duree_restante)
                        plan.append((num_phase, taille, d, None))
                        duree_restante -= d
                    break
        return plan

    if MODE_DUREE == "boucle":
        duree_seq = DUREE_SOURCE
    elif MODE_DUREE == "n_boucles":
        duree_seq = DUREE_SOURCE * N_BOUCLES
    else:  # "total"
        duree_seq = DUREE_TOTALE / len(PHASES)

    for taille in PHASES:
        plan.append((1, taille, duree_seq, None))
    return plan


def construire_plan_scenario():
    """
    Plan défini explicitement par l'utilisateur dans la section `scenario`.
    Supporte des entrées explicites et des blocs `{ auto: "phase" }` qui
    déplient une phase classique complète.
    """
    duree_par_defaut = SCENARIO.get("duree_par_defaut", "boucle")
    sequences_cfg = SCENARIO.get("sequences", [])
    if not sequences_cfg:
        print("ERREUR : mode_plan='scenario' mais 'scenario.sequences' est vide.")
        sys.exit(1)

    plan = []
    num_phase = 1

    for entry in sequences_cfg:
        # Bloc auto : déplie une phase complète (1 → 2 → 4 → … → MAX)
        if "auto" in entry and entry["auto"] == "phase":
            boucles = int(entry.get("boucles", 1))
            duree_seq = _resolve_duree_seq(entry.get("duree"), duree_par_defaut)
            for _ in range(boucles):
                for taille in PHASES:
                    plan.append((num_phase, taille, duree_seq, None))
                num_phase += 1
            continue

        # Séquence explicite
        taille = entry.get("taille")
        if taille is None:
            print(f"ERREUR scenario : séquence sans 'taille' : {entry}")
            sys.exit(1)
        duree_seq = _resolve_duree_seq(entry.get("duree"), duree_par_defaut)
        directives = _construire_directives(entry)
        plan.append((num_phase, int(taille), duree_seq, directives))

    # En mode scenario on garde un seul num_phase logique sauf si auto a
    # incrémenté ; on conserve num_phase sur les séquences explicites
    # (1 par bloc explicite n'aurait pas grand sens).
    return plan


def construire_plan():
    """Dispatcher selon mode_plan."""
    if MODE_PLAN == "scenario":
        return construire_plan_scenario()
    return construire_plan_auto()


PLAN = construire_plan()
NB_PHASES_PLAN = max(p[0] for p in PLAN)
DUREE_TOTALE = sum(p[2] for p in PLAN)


# ============================================================
# 7. Résumé console (appelé explicitement par l'orchestrateur)
# ============================================================
def afficher_resume():
    print(f"Config       : {config_path}")
    print(f"Résolution   : {FINAL_W}x{FINAL_H}")
    print(f"Mode durée   : {MODE_DUREE}"
          + (f" (cible {DUREE_MAX}s, fin={FIN_PARTIELLE})" if MODE_DUREE == "duree_max" else ""))
    print(f"Source       : {DUREE_SOURCE:.2f}s")
    print(f"Plan         : {NB_PHASES_PLAN} phase(s), {len(PLAN)} séquences")
    print(f"Durée finale : {DUREE_TOTALE:.1f}s ({DUREE_TOTALE/60:.2f} min)")
    print(f"Séquences    : {[t*t for _, t, _, _ in PLAN[:10]]}{'...' if len(PLAN) > 10 else ''}")
    print(f"Mode plan    : {MODE_PLAN}")
    print(f"Mode anomalie     : {MODE_ANOMALIE} | changement: {CHANGEMENT_ANOMALIE}")
    print(f"Filtres actifs    : {len(ANOMALIES)}")
    print(f"Masques activés   : {MASQUES_ACTIFS}"
          + (f" ({MSK.get('portee')})" if MASQUES_ACTIFS else ""))
    print(f"Effets activés    : {EFFETS_ACTIFS}"
          + (f" ({', '.join(EFFETS_LISTE)})" if EFFETS_ACTIFS else ""))
    print(f"Audio activé      : {AUDIO_ACTIF}"
          + (f" ({AUDIO.get('comportement')}, source={AUDIO.get('audio_source')})" if AUDIO_ACTIF else ""))
    print(f"Encodeur          : {ENCODEUR}"
          + (f" (q={QUALITE_HW})" if ENCODEUR == "videotoolbox" else f" (crf={CRF}, preset={PRESET})"))
    print(f"Parallélisme      : {NB_PARALLEL} jobs simultanés")
    print(f"Cache             : {'actif' if CACHE_ACTIF else 'désactivé'}")
