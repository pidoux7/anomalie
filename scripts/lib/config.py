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
#
# La config de base `configs/config.yaml` est toujours chargée. Si un autre
# fichier est passé en argument (typiquement un scénario), il est mergé
# par-dessus la base (deep merge récursif sur les dicts ; les listes sont
# remplacées).
# ============================================================
BASE_CONFIG = "configs/config.yaml"


def _deep_merge(base, override):
    """Merge récursif. Les listes sont remplacées (pas concaténées)."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


config_path = sys.argv[1] if len(sys.argv) > 1 else BASE_CONFIG

if not Path(BASE_CONFIG).exists():
    print(f"Config de base introuvable : {BASE_CONFIG}")
    sys.exit(1)

with open(BASE_CONFIG, "r") as f:
    cfg = yaml.safe_load(f) or {}

# Merge éventuel d'un fichier override (scenario, profil alternatif…)
if Path(config_path).resolve() != Path(BASE_CONFIG).resolve():
    if not Path(config_path).exists():
        print(f"Fichier introuvable : {config_path}")
        sys.exit(1)
    with open(config_path, "r") as f:
        cfg_override = yaml.safe_load(f) or {}
    cfg = _deep_merge(cfg, cfg_override)
    print(f"[config] base={BASE_CONFIG} + override={config_path}")

# Mode --preview : overrides après merge pour un rendu rapide
PREVIEW_MODE = os.environ.get("ANOMALIE_PREVIEW") == "1"
DRY_RUN_MODE = os.environ.get("ANOMALIE_DRY_RUN") == "1"

if PREVIEW_MODE:
    cfg["final_w"] = 1280
    cfg["final_h"] = 720
    cfg["preset_x264"] = "ultrafast"
    cfg["crf"] = 28
    cfg["mode_duree"] = "duree_max"
    cfg["duree_max"] = 30
    cfg["fin_partielle"] = "tronquer"
    cfg.setdefault("performance", {})
    cfg["performance"]["encodeur"] = "x264"  # ultrafast x264 = plus rapide
    # Output dérivé pour ne pas écraser le rendu final
    out_orig = Path(cfg.get("output_video", "videos/montage_final.mp4"))
    cfg["output_video"] = str(out_orig.parent / (out_orig.stem + "_preview" + out_orig.suffix))
    print(f"[preview] 1280x720, 30s max, output={cfg['output_video']}")

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
    if "video" in entry:
        # Chaîne (chemin unique) ou liste (tirage aléatoire au runtime)
        d["video"] = entry["video"]
    if entry.get("aleatoire"):
        d["aleatoire"] = True
    if entry.get("cumulatif"):
        # Conserve les positions anomales du palier précédent à la même
        # taille, puis n'en ajoute que de nouvelles pour atteindre `anomalies`.
        d["cumulatif"] = True
    if "decalage_aleatoire" in entry:
        # Si False : toutes les cellules anomales (source: video) lisent
        # le même instant de la source au lieu d'avoir chacune son offset.
        d["decalage_aleatoire"] = bool(entry["decalage_aleatoire"])

    # Défaut : 1 anomalie dès qu'on précise un filtre/source/aléatoire
    a_specifie = ("filtre" in d or "source" in d or "aleatoire" in d)
    if a_specifie and "anomalies" not in d:
        d["anomalies"] = 1

    # Une séquence explicite renvoie toujours un dict (même vide). C'est ce
    # qui distingue une séquence scenario d'un bloc `auto: "phase"` (qui lui
    # renvoie None et garde le comportement classique avec masques/effets
    # globaux).
    return d


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

        # Bloc transition : deux modes.
        # - "paliers" (défaut) : déplie en N paliers cumulatifs.
        # - "smooth" : une seule séquence avec un effet `transition_smooth`
        #   qui révèle les cellules cellule-par-cellule via un mask animé
        #   (typiquement ~5 cellules par frame à 30 fps, perçu continu).
        if entry.get("type") == "transition":
            taille_t = int(entry["taille"])
            mode_t = entry.get("mode", "paliers")

            if mode_t == "smooth":
                duree_smooth = float(
                    entry.get("duree_totale")
                    or _resolve_duree_seq(entry.get("duree"), duree_par_defaut)
                )
                directives = {"effet": "transition_smooth"}
                if "video" in entry:
                    directives["video"] = entry["video"]
                plan.append((num_phase, taille_t, duree_smooth, directives))
                continue

            n_paliers = int(entry.get("paliers", 20))
            a_de = int(entry.get("anomalies_de", 0))
            a_a = int(entry.get("anomalies_a", taille_t * taille_t))
            duree_totale_t = float(
                entry.get("duree_totale")
                or (_resolve_duree_seq(entry.get("duree"), duree_par_defaut)
                    * n_paliers)
            )
            duree_palier = duree_totale_t / n_paliers

            # Champs communs hérités sur chaque palier (filtre, video, source…)
            champs_communs = {
                k: v for k, v in entry.items()
                if k not in ("type", "taille", "paliers", "anomalies_de",
                             "anomalies_a", "duree_totale", "duree", "anomalies",
                             "cumulatif")
            }

            for k in range(n_paliers):
                frac = (k + 1) / n_paliers
                anomalies_k = int(round(a_de + (a_a - a_de) * frac))
                sub_entry = dict(champs_communs)
                sub_entry["taille"] = taille_t
                sub_entry["anomalies"] = anomalies_k
                sub_entry["duree"] = duree_palier
                if k > 0:
                    sub_entry["cumulatif"] = True
                directives = _construire_directives(sub_entry)
                plan.append((num_phase, taille_t, duree_palier, directives))
            continue

        # Bloc rampe : montée ou descente de tailles (puissances de 2)
        # avec une durée par étage et directives communes.
        if entry.get("type") == "rampe":
            t_de = int(entry["taille_de"])
            t_a = int(entry["taille_a"])
            duree_par = float(
                entry.get("duree_par_taille")
                or _resolve_duree_seq(entry.get("duree"), duree_par_defaut)
            )
            crossfade = float(entry.get("crossfade", 0))
            tailles = []
            if t_de <= t_a:
                t = t_de
                while t <= t_a:
                    tailles.append(t)
                    t *= 2 if t > 0 else 1
                    if t == 0:
                        break
            else:
                t = t_de
                while t >= t_a and t >= 1:
                    tailles.append(t)
                    t //= 2

            champs_communs = {
                k: v for k, v in entry.items()
                if k not in ("type", "taille_de", "taille_a",
                             "duree_par_taille", "duree", "crossfade")
            }

            # Si crossfade > 0 : un seul item avec effet rampe_fade qui
            # gère le rendu de tous les étages + xfade en cascade.
            if crossfade > 0 and len(tailles) >= 2:
                duree_totale_r = (duree_par * len(tailles)
                                    - crossfade * (len(tailles) - 1))
                directives = _construire_directives(dict(champs_communs))
                directives["effet"] = "rampe_fade"
                directives["_rampe_etages"] = list(tailles)
                directives["_rampe_duree_par"] = duree_par
                directives["_rampe_crossfade"] = crossfade
                plan.append((num_phase, tailles[-1], duree_totale_r, directives))
                continue

            for t in tailles:
                sub = dict(champs_communs)
                sub["taille"] = t
                sub["duree"] = duree_par
                directives = _construire_directives(sub)
                plan.append((num_phase, t, duree_par, directives))
            continue

        # Bloc interpolation : déplie en N paliers avec interpolation
        # linéaire d'un ou plusieurs paramètres d'effet.
        if entry.get("type") == "interpolation":
            taille_i = int(entry["taille"])
            n_paliers = int(entry.get("paliers", 10))
            duree_totale_i = float(
                entry.get("duree_totale")
                or (_resolve_duree_seq(entry.get("duree"), duree_par_defaut)
                    * n_paliers)
            )
            duree_palier_i = duree_totale_i / n_paliers
            parametres = entry.get("parametres", {})
            if not isinstance(parametres, dict) or not parametres:
                print(f"ERREUR scenario : interpolation sans 'parametres' : {entry}")
                sys.exit(1)

            champs_communs = {
                k: v for k, v in entry.items()
                if k not in ("type", "taille", "paliers", "duree_totale",
                             "duree", "parametres")
            }

            for k in range(n_paliers):
                frac = k / max(1, n_paliers - 1)
                params_palier = {}
                for nom_p, plage in parametres.items():
                    if not isinstance(plage, (list, tuple)) or len(plage) != 2:
                        print(f"ERREUR scenario : '{nom_p}' doit être [de, a]")
                        sys.exit(1)
                    de, a = float(plage[0]), float(plage[1])
                    params_palier[nom_p] = de + (a - de) * frac
                sub_entry = dict(champs_communs)
                sub_entry["taille"] = taille_i
                sub_entry["duree"] = duree_palier_i
                directives = _construire_directives(sub_entry)
                directives["effet_params"] = params_palier
                plan.append((num_phase, taille_i, duree_palier_i, directives))
            continue

        # Séquence explicite
        taille = entry.get("taille")
        if taille is None:
            print(f"ERREUR scenario : séquence sans 'taille' : {entry}")
            sys.exit(1)
        duree_seq = _resolve_duree_seq(entry.get("duree"), duree_par_defaut)
        directives = _construire_directives(entry)
        # `repeter: N` duplique la séquence N fois dans le plan
        repeter = max(1, int(entry.get("repeter", 1)))
        for _ in range(repeter):
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

# En mode preview, on tronque le plan à 30s pour les scenarios
# (duree_max n'agit que sur mode_plan: "auto")
if PREVIEW_MODE:
    plan_court = []
    cumul = 0.0
    for entry in PLAN:
        if cumul >= 30:
            break
        np_, t_, d_, dirs_ = entry
        d_eff = min(d_, 30 - cumul)
        plan_court.append((np_, t_, d_eff, dirs_))
        cumul += d_eff
    PLAN = plan_court or PLAN[:1]  # au pire on garde 1 séquence

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
    # En mode scenario, les masques/effets globaux ne s'appliquent pas
    # automatiquement : seul ce qui est explicitement déclaré dans
    # scenario.sequences est joué. On le signale pour éviter la confusion.
    suffixe_scenario = " (ignoré en mode scenario)" if MODE_PLAN == "scenario" else ""
    print(f"Masques activés   : {MASQUES_ACTIFS}"
          + (f" ({MSK.get('portee')})" if MASQUES_ACTIFS else "")
          + (suffixe_scenario if MASQUES_ACTIFS else ""))
    print(f"Effets activés    : {EFFETS_ACTIFS}"
          + (f" ({', '.join(EFFETS_LISTE)})" if EFFETS_ACTIFS else "")
          + (suffixe_scenario if EFFETS_ACTIFS else ""))
    print(f"Audio activé      : {AUDIO_ACTIF}"
          + (f" ({AUDIO.get('comportement')}, source={AUDIO.get('audio_source')})" if AUDIO_ACTIF else ""))
    print(f"Encodeur          : {ENCODEUR}"
          + (f" (q={QUALITE_HW})" if ENCODEUR == "videotoolbox" else f" (crf={CRF}, preset={PRESET})"))
    print(f"Parallélisme      : {NB_PARALLEL} jobs simultanés")
    print(f"Cache             : {'actif' if CACHE_ACTIF else 'désactivé'}")
