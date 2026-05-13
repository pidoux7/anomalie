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
pip3 install pyyaml pillow tqdm
```

`tqdm` est optionnel (barres de progression sur les phases massives).
Sans `tqdm` installé, le rendu se déroule normalement, juste sans barre.

## Structure

```text
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

# Itérer uniquement sur l'audio (réutilise la vidéo du dernier run)
python3 scripts/montage_grille.py scenarios/scenario_bas.yaml --audio-only
```

Le fichier passé en argument est **mergé sur** `configs/config.yaml`
(deep merge récursif sur les dicts ; les listes sont remplacées).
Concrètement, un scénario ne contient que les clés à overrider.

**`--audio-only`** : saute la génération vidéo (étapes 1-3) et
réutilise `travail_montage/video_sans_audio.mp4` du précédent rendu
complet. Utile pour expérimenter sur les pistes audio sans recalculer
toute la vidéo (de l'ordre de quelques secondes au lieu de plusieurs
minutes). Nécessite qu'un rendu complet ait déjà été lancé au moins
une fois pour la même config (sinon `video_sans_audio.mp4` n'existe pas).

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

    # Bloc transition - mode "paliers" (défaut) : déplie en N paliers
    # cumulatifs ; chaque palier ajoute un groupe de cellules d'un coup.
    - type: "transition"
      taille: 64
      video: "videos/video_b.mp4"
      source: "video"             # cellules anomales = input_anomalie
      anomalies_de: 0
      anomalies_a: 4090
      duree_totale: 40            # secondes
      paliers: 20                 # → 20 paliers de 2s avec cumulatif: true

    # Bloc transition - mode "smooth" : une seule séquence avec un mask
    # animé qui révèle les cellules ~frame par frame (à 30 fps sur 30s :
    # ~5 cellules par frame, perçu comme continu cellule par cellule).
    # Plus rapide à rendre et plus fluide visuellement que le mode paliers.
    - type: "transition"
      mode: "smooth"
      taille: 64
      video: "videos/video_b.mp4"   # vidéo de fond
      duree_totale: 30              # input_anomalie = vidéo qui apparaît
```

**Champs d'une séquence :**

| Champ       | Effet                                                                    |
|-------------|--------------------------------------------------------------------------|
| `taille`    | Taille de la grille (1, 2, 4, 8, 16, …)                                  |
| `anomalies` | Nombre de cellules anomales (défaut 1 si filtre/source/aleatoire)        |
| `filtre`    | Nom (ex `"pixelise_fort"`) **ou** index 1-based dans `filtres_anomalies` |
| `source`    | `"video"` pour utiliser `input_anomalie` au lieu d'un filtre             |
| `effet`     | `"mosaique"` \| `"mur_moniteurs"` \| `"lsd"` \| `"masque"`               |
| `masque`    | Chemin d'image (motif dessiné par les cellules anomales)                 |
| `video`     | Chemin (str) **ou** liste (tirage aléatoire) — override `input_video`    |
| `aleatoire` | `true` pour tirer le filtre au hasard parmi les filtres actifs           |
| `cumulatif` | `true` : conserve les positions anomales du palier précédent             |
| `duree`     | Override la durée de la séquence (en secondes)                           |

**`decalage_aleatoire`** (booléen, défaut `true`) : contrôle le timing
des cellules `source: video`. Avec `true` (défaut), chaque cellule lit
la vidéo externe à son propre offset → look mosaïque. Avec `false`,
toutes les cellules lisent le **même instant** de la vidéo en même temps
→ on voit la vidéo entière à travers le quadrillage, sans saccade.

### Réutilisation : YAML anchors

YAML supporte nativement les ancres `&` et alias `*` (et le merge `<<:`)
pour factoriser des blocs réutilisés à plusieurs endroits du scenario :

```yaml
scenario:
  sequences:
    # Définit une ancre la première fois qu'on utilise le bloc
    - &intro_64 { taille: 64, video: "videos/video_b.mp4", aleatoire: true, anomalies: 100 }

    - { taille: 1 }
    - *intro_64                        # exactement le même bloc

    # Merge : on hérite des champs et on override
    - <<: *intro_64
      anomalies: 500                   # tout pareil sauf anomalies
      duree: 5
```

Très utile pour les scenarios longs (sequence.yaml) où la même séquence
revient plusieurs fois (montée/descente, transitions identiques).

**Note `video`** : disponible uniquement en mode scenario. Si absent, la
séquence utilise `input_video` (config globale). Si présent, la vidéo
dédiée joue **depuis le début** (le seek cumulé du plan ne s'applique
pas à une autre source).

```yaml
sequences:
  - { taille: 8, video: "videos/clip2.mp4" }                              # vidéo unique
  - { taille: 8, video: ["videos/a.mp4", "videos/b.mp4", "videos/c.mp4"]} # tirage aléatoire
  - { taille: 8 }                                                          # input_video par défaut
```

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

Quatre effets couvrent toute la grille (par opposition aux anomalies par
cellule). On les appelle de **deux manières** :

- **Mode auto** : `effets.actif: true`, on liste les effets dans
  `effets.liste`, ils s'enchaînent automatiquement sur les phases
  ciblées par `effets.portee`/`effets.seuil_cases`.
- **Mode scenario** : `effet: "<nom>"` dans une séquence
  `scenario.sequences` (override la phase entière).

| Effet            | Description                                                                  |
|------------------|------------------------------------------------------------------------------|
| `mosaique`       | Grille modulée par une vidéo "carte" (luminance pixel = luminance cellule)   |
| `mur_moniteurs`  | Chaque cellule joue une vidéo différente tirée d'une liste                   |
| `lsd`            | Grille + distorsion ondulatoire + aberration chromatique + saturation        |
| `masque`         | Les cellules anomales dessinent un motif depuis une image (cf. ci-dessous)   |

### `mosaique`

Paramètres (sous `effets.mosaique` dans `configs/config.yaml`) :

| Clé                    | Effet                                                              |
|------------------------|--------------------------------------------------------------------|
| `video_carte`          | Chemin de la vidéo qui sert de carte de luminance                  |
| `debut` / `fin`        | Bornes dans la carte (`mm:ss`, `hh:mm:ss` ou secondes)             |
| `boucler_carte`        | Si la carte est plus courte que l'effet, la boucle                 |
| `carte_en_noir_blanc`  | Force la carte en N&B avant modulation                             |
| `intensite`            | 0.0 (pas d'effet) → 1.0 (modulation pleine)                        |
| `mode_fusion`          | `"multiply"` (assombrit) \| `"screen"` (éclaircit) \| `"overlay"`  |

```yaml
# Mode auto : ajouter à la liste
effets:
  liste: ["mosaique"]

# Mode scenario : sur une séquence
sequences:
  - { taille: 32, effet: "mosaique" }                  # 1 boucle source
  - { taille: 32, effet: "mosaique", duree: 60 }       # 60s
```

### `mur_moniteurs`

Paramètres (sous `effets.mur_moniteurs`) :

| Clé                  | Effet                                                                  |
|----------------------|------------------------------------------------------------------------|
| `videos`             | Liste de chemins vidéo tirés au sort par cellule                       |
| `decalage_aleatoire` | `true` = chaque cellule démarre à un point aléatoire dans son clip     |
| `inclure_source`     | `true` = la source principale est ajoutée au tirage                    |

```yaml
sequences:
  - { taille: 32, effet: "mur_moniteurs", duree: 30 }
```

### `lsd`

Paramètres (sous `effets.lsd`) :

| Clé              | Effet                                                                  |
|------------------|------------------------------------------------------------------------|
| `amplitude_onde` | Amplitude des ondulations en pixels (0 = aucune, 50-200 = visible)     |
| `vitesse_onde`   | Vitesse des ondulations (cycles par seconde)                           |
| `frequence_onde` | Nombre d'ondes visibles à l'écran                                      |
| `aberration`     | Décalage des canaux R/B en pixels (aberration chromatique)             |
| `saturation`     | 1.0 (normal) → 3.0 (très saturé)                                       |
| `teinte`         | Décalage de teinte en degrés (240 = bleu/violet)                       |
| `lignes_force`   | Force des contours superposés (0 = aucun, 1.0 = max)                   |
| `lignes_mode`    | `"edges"` (lignes nettes) \| `"wires"` (lignes fines)                  |

```yaml
sequences:
  - { taille: 16, effet: "lsd", duree: 20 }
```

### `masque`

Voir la section [Masques](#masques) ci-dessous. Quand utilisé comme effet
dans une séquence scenario, le motif est piloté par le bloc `masques:`
(images, mode_filtre, evolution…). Pour fournir un masque ad-hoc à une
seule séquence (sans toucher au bloc global), utilise le champ
`masque:` directement :

```yaml
sequences:
  - { taille: 32, masque: "masques/coeur.png" }                     # masque ad-hoc
  - { taille: 32, masque: "masques/7.png", filtre: "negatif" }      # + filtre fixe
```

### Enchaîner plusieurs effets en mode auto

```yaml
effets:
  actif: true
  liste: ["mosaique", "mur_moniteurs", "lsd"]
  durees:
    mosaique: 30           # secondes
    mur_moniteurs: 20
    lsd: null              # null = 1 boucle source
```

Chaque effet devient une sous-séquence dans les phases ciblées. Tu peux
dupliquer un nom dans `liste` pour le faire passer plusieurs fois.

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

[MIT](LICENSE) © 2026 Guillaume Faure et Alban Tardif
