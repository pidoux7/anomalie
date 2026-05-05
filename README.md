# Anomalie

Outil de montage vidéo qui décompose une source en grilles progressives
(1×1 → 2×2 → 4×4 → … → N×N) et y injecte des **anomalies** (filtres
appliqués à certaines cellules, vidéos externes substituées, motifs
dessinés par masque, ou effets pleine grille comme la mosaïque, le mur
de moniteurs ou le LSD). L'audio est composable via une timeline
multi-pistes.

## Prérequis

- Python 3.10+
- `ffmpeg` et `ffprobe` accessibles dans le `PATH`
- Sur macOS : `brew install ffmpeg` (videotoolbox détecté automatiquement)

```bash
pip3 install pyyaml pillow
```

## Structure

```
anomalie/
├── configs/         # config.yaml (réglages de base, partagés)
├── scenarios/       # fichiers scenario qui héritent de configs/config.yaml
├── scripts/
│   ├── montage_grille.py    # orchestrateur (point d'entrée)
│   └── lib/                 # package modulaire
│       ├── config.py        # chargement YAML, encodeur, plan
│       ├── commun.py        # helpers ffmpeg/ffprobe
│       ├── masques.py       # image → positions, tirage d'anomalies
│       ├── grille.py        # cache, segments, assemblage de la grille
│       ├── effets.py        # mosaïque, mur_moniteurs, lsd, masque
│       ├── phases.py        # création d'une phase + concaténation
│       └── audio.py         # extraction, mix, timeline scenario, mux
├── videos/          # sources et sorties .mp4 / .MOV (ignoré par git)
├── masques/         # images PNG/JPG pour le mode masque
├── musiques/        # pistes audio (ignoré par git)
└── travail_montage/ # cache disque + fichiers intermédiaires (auto)
```

## Lancement

Toujours depuis la racine du projet :

```bash
# Avec la config par défaut (configs/config.yaml)
python3 scripts/montage_grille.py

# Avec un scénario (mergé par-dessus configs/config.yaml)
python3 scripts/montage_grille.py scenarios/scenario_bas.yaml
```

Le fichier passé en argument est **mergé sur** `configs/config.yaml`
(deep merge récursif sur les dicts ; les listes sont remplacées).
Concrètement, un scénario ne contient que les clés à overrider.

## Deux modes de plan

### `mode_plan: "auto"` (par défaut)

On enchaîne automatiquement les phases (1 → 2 → 4 → … → max_cases)
selon `mode_duree` et `duree_max`, avec des anomalies tirées au hasard
selon `mode_anomalie`, `mode_quantite`, etc.

### `mode_plan: "scenario"`

On définit explicitement chaque séquence dans `scenario.sequences`. On
peut mélanger des entrées explicites et des blocs `auto: "phase"` qui
déplient une phase complète à la façon classique.

```yaml
mode_plan: "scenario"
scenario:
  duree_par_defaut: "boucle"     # "boucle" = DUREE_SOURCE / séquence
  sequences:
    - { taille: 1 }                                              # plein cadre, 0 anomalie
    - { taille: 4, filtre: "pixelise_fort" }                     # 1 anomalie (défaut)
    - { taille: 4, anomalies: 2, filtre: 3 }                     # filtre par index 1-based
    - { taille: 8, anomalies: 5, source: "video" }               # depuis input_anomalie
    - { taille: 16, effet: "lsd", duree: 30 }                    # effet pleine grille
    - { taille: 32, masque: "masques/7.png" }                    # masque ad-hoc
    - { taille: 8, aleatoire: true, anomalies: 3 }               # filtres tirés au hasard
    - { auto: "phase", boucles: 2 }                              # 2 phases classiques
```

**Champs d'une séquence :**

| Champ       | Effet                                                                |
|-------------|----------------------------------------------------------------------|
| `taille`    | Taille de la grille (1, 2, 4, 8, 16, …)                              |
| `anomalies` | Nombre de cellules anomales (défaut 1 si filtre/source/aleatoire)    |
| `filtre`    | Nom (ex `"pixelise_fort"`) **ou** index 1-based dans `filtres_anomalies` |
| `source`    | `"video"` pour utiliser `input_anomalie` au lieu d'un filtre         |
| `effet`     | `"mosaique"` \| `"mur_moniteurs"` \| `"lsd"` \| `"masque"`           |
| `masque`    | Chemin d'image (motif dessiné par les cellules anomales)             |
| `aleatoire` | `true` pour tirer le filtre au hasard parmi les filtres actifs       |
| `duree`     | Override la durée de la séquence (en secondes)                       |

## Audio

### `audio.mode: "auto"` (par défaut)

Comportement classique : liste `audio.fichiers`, gestion `comportement: reset/continu`,
fade in/out, mix éventuel avec l'audio source.

### `audio.mode: "scenario"`

Timeline explicite multi-pistes. Chaque piste est ancrée par phase,
séquence, plage de durée, ou jouée en fond.

```yaml
audio:
  mode: "scenario"
  conflit_par_defaut: "ecrase"   # ou "mix"
  scenario:
    - { ancre: "phase",    n: 1, fichier: "musiques/intro.mp3" }
    - { ancre: "sequence", n: 3, fichier: "musiques/break.mp3", debut: "1:00" }
    - { ancre: "duree",    de: "0:00", a: "2:30", fichier: "musiques/a.mp3" }
    - { ancre: "fond",     fichier: "musiques/ambient.mp3", volume: 0.3, mode: "mix" }
```

| Ancre      | Sens                                                            |
|------------|-----------------------------------------------------------------|
| `phase`    | `n: <num_phase>` → bornes de la phase                           |
| `sequence` | `n: <num_seq_1based>` → bornes de la séquence                   |
| `duree`    | `de:` / `a:` → bornes absolues (timestamps `mm:ss` ou secondes) |
| `fond`     | Toute la durée totale (mode `mix` par défaut)                   |

**Conflit entre pistes** :
- `mode: "ecrase"` : la dernière piste déclarée prend la fenêtre, les
  précédentes sont coupées sur cet intervalle ; les trous restants sont
  remplis avec du silence.
- `mode: "mix"` : la piste est mixée par-dessus la timeline (avec
  `volume`).

`audio.conflit_par_defaut` fixe le défaut quand `mode` n'est pas précisé
sur la piste.

## Effets pleine grille

Activables via `effets.actif: true` (mode auto) ou `effet: "<nom>"` dans
une séquence scenario.

- **`mosaique`** : la grille reproduit une autre vidéo (carte) où chaque
  cellule joue la source modulée par le pixel correspondant de la carte.
- **`mur_moniteurs`** : chaque cellule joue une vidéo différente tirée
  parmi `effets.mur_moniteurs.videos`.
- **`lsd`** : grille classique + distorsion ondulatoire + aberration
  chromatique + saturation + détection de contours.
- **`masque`** : redirige vers le système de masque (image → positions).

## Masques

Une image en niveaux de gris est seuillée : les pixels sombres
deviennent les positions des cellules anomales. Évolutions :

- `statique` : motif unique tout le long de la phase
- `sequence` : on cycle dans une liste d'images
- `apparition` : le motif se construit progressivement

Voir [configs/config.yaml](configs/config.yaml) section `masques:` pour
tous les paramètres.

## Performance

```yaml
performance:
  encodeur: "auto"      # "auto" | "videotoolbox" | "x264" | "nvenc"
  qualite_hw: 65        # qualité encodeur matériel (1-100, plus haut = mieux)
  parallelisme: "auto"  # nb cœurs CPU - 1, ou un entier
  cache_actif: true     # ne re-génère pas un segment déjà calculé
```

Le cache vit dans `travail_montage/cache/` : la clé inclut la source
(taille + mtime), les bornes, le filtre, l'encodeur et le CRF. Pour
forcer un recalcul, supprimer ce dossier.

## Licence

[MIT](LICENSE) © 2026 Guillaume Faure
