# Guide de Personnalisation des Thèmes

> **Docs** > **Branding** > Theming

---

## Concept

Le système de thèmes Xen sépare la **configuration** (workflow, jobs) des **assets visuels** (logos, wallpapers). Cette séparation permet :

- Réutiliser un même thème avec différentes configurations
- Mettre à jour les assets sans modifier la config
- Distribuer des thèmes indépendamment

---

## Architecture du Système

```
xen.yaml                    config/themes/<nom>/
    │                              │
    ├── theme: "config/..."  ────► │
    │                              ├── theme.yaml
    └── branding:                  ├── logos/
        └── assets:                │   ├── logo.png
            └── logo: "logos/..." ─┤   └── ...
                                   ├── wallpapers/
                                   └── boot/
```

### Flux de Résolution

1. `main.py` lit le chemin `theme` depuis la config
2. Le chemin est résolu relativement au fichier de config
3. `BrandingProxy` convertit les chemins d'assets en URLs `file://`
4. QML charge les images via ces URLs

---

## Structure d'un Thème

```
config/themes/<nom>/
├── theme.yaml              # Métadonnées et définitions
├── logos/
│   ├── logo.png            # Logo principal (≥256px, fond sombre)
│   ├── logo-256.png        # Tailles spécifiques
│   ├── logo-128.png
│   ├── logo-64.png
│   └── logo-32.png
├── wallpapers/
│   ├── dark.jpg            # Fond d'écran par défaut
│   ├── light.jpg           # Variante claire (optionnel)
│   └── ...
└── boot/
    ├── bootloader.png      # Splash GRUB/systemd-boot
    └── efi-icon.png        # Icône UEFI
```

### Fichier theme.yaml

Exemple exhaustif de toutes les sections d'un `theme.yaml` :

```yaml
# Identité du thème
metadata:
  name: "Ma Distribution"
  codename: "Codename"
  version: "1.0"
  author: "Mon Équipe"
  website: "https://example.com"
  license: "MIT"

# Palette : 10 rôles, tous en hex "#RRGGBB"
colors:
  primary: "#5597e6"          # marque : boutons, accents, étape active
  secondary: "#4a8bd4"        # variante primaire (survols, dégradés)
  accent: "#5597e6"           # accent secondaire (focus, mise en évidence)
  background: "#1a1a1a"       # fond principal de la fenêtre
  surface: "#32373c"          # surface des cartes/panneaux
  text: "#fffded"             # texte courant sur fond sombre
  text_muted: "#9CA3AF"       # texte secondaire / libellés atténués
  success: "#10B981"          # statut OK (prérequis validé)
  warning: "#F59E0B"          # statut avertissement
  error: "#EF4444"            # statut erreur

# Typographie (familles de police par nom)
fonts:
  primary: "Poppins"          # UI globale
  monospace: "Roboto Mono"    # code / journaux
  weights:                    # graisses indicatives (voir note plus bas)
    regular: 400
    medium: 600
    bold: 700

# Logos (catalogue d'assets, chemins relatifs au dossier du thème)
logos:
  main: "logos/logo.png"
  main_256: "logos/logo-256.png"
  main_128: "logos/logo-128.png"
  main_64: "logos/logo-64.png"
  main_32: "logos/logo-32.png"
  mango: "logos/mango.svg"        # logo spécial (SVG)
  selector: "logos/selector.svg"  # logo spécial (SVG)

# Fonds d'écran (catalogue d'assets)
wallpapers:
  default: "wallpapers/dark.jpg"
  welcome_dark: "wallpapers/welcome-dark.jpg"
  welcome_light: "wallpapers/welcome-light.jpg"

# Assets de démarrage (catalogue d'assets)
boot:
  bootloader: "boot/bootloader.png"
  efi_icon: "boot/efi-icon.png"

# Textes de marque (repli derrière l'i18n)
strings:
  welcome_title: "Bienvenue"
  welcome_subtitle: "Installation de votre système"
  install_button: "Installer"
  progress_partitioning: "Partitionnement du disque..."
  progress_installing: "Installation du système..."
  progress_configuring: "Configuration..."
  progress_bootloader: "Installation du bootloader..."
  finished_title: "Installation terminée !"
  finished_message: "Redémarrez pour découvrir votre nouveau système."
  reboot_button: "Redémarrer maintenant"
  error_generic: "Une erreur est survenue"
  error_disk_space: "Espace disque insuffisant"
  error_network: "Connexion réseau requise"
```

### Portée de la fusion (overlay theme.yaml → branding)

Au chargement de la config, `Engine._apply_theme_overlay()`
(`src/xen/core/engine.py`) fusionne le `theme.yaml` **par-dessus** le bloc
`branding:` de la config d'installation. Le thème est **prioritaire** ; le
branding inline sert de valeurs par défaut pour ce que le thème ne définit pas.

**Ne sont fusionnées que ces sections** (`Engine._apply_theme_overlay`) :

| Section theme.yaml | Cible dans `branding` | Effet |
|--------------------|-----------------------|-------|
| `colors` | `branding.colors` | clé à clé (thème prioritaire) |
| `fonts` | `branding.fonts` | clé à clé (thème prioritaire) |
| `strings` | `branding.strings` | clé à clé (thème prioritaire) |
| `metadata.name` | `branding.name` | remplace si non vide |
| `metadata.version` | `branding.version` | remplace si non vide |
| `metadata.codename` | `branding.edition` | remplace si non vide |
| `metadata.website` | `branding.links.website` | remplace si non vide |

> **Important: sections non fusionnées.** Les sections `logos:`, `wallpapers:`
> et `boot:` du `theme.yaml` **ne sont PAS injectées** dans le branding : elles
> servent de **catalogue documentaire** des assets fournis par le thème (et
> respectent une convention de nommage, cf. plus bas). Les assets réellement
> affichés par l'UI proviennent du bloc `branding.assets:` de la config
> d'installation.
>
> **Champs déclarés mais non consommés.** `fonts.weights` et les clés `strings`
> supplémentaires (`progress_*`, `reboot_button`, `error_*`) sont acceptées dans
> le `theme.yaml` mais **absentes des modèles Pydantic** (`BrandingFonts`,
> `BrandingStrings`) : elles sont **ignorées à la validation** et ne sont donc
> pas (encore) exposées au QML. Elles documentent l'intention du thème et
> anticipent de futurs points d'ancrage.

---

## Référence des Sections

### Couleurs (`colors`)

10 rôles, tous au format hexadécimal `#RRGGBB` (le `#` est obligatoire ; validé
par `BrandingColors.validate_hex_color`, `engine.py`) :

| Clé YAML | Propriété QML | Rôle dans l'UI |
|----------|---------------|----------------|
| `primary` | - | Validée, non lue par l'UI (voir note) |
| `secondary` | - | Validée, non lue par l'UI |
| `accent` | - | Validée, non lue par l'UI (voir note) |
| `background` | `branding.backgroundColor` | Fond principal de la fenêtre |
| `surface` | `branding.surfaceColor` | Surface des cartes/panneaux |
| `text` | `branding.textColor` | Texte courant sur fond sombre |
| `text_muted` | `branding.textMutedColor` | Texte secondaire / libellés atténués |
| `success` | `branding.successColor` | Statut OK (prérequis validé) |
| `warning` | `branding.warningColor` | Statut avertissement |
| `error` | `branding.errorColor` | Statut erreur |

> **Couleur de marque fixée par profil.** `branding.primaryColor`,
> `branding.accentColor`, `branding.accentGradientEnd`,
> `branding.backgroundGradientStart` et `branding.backgroundGradientEnd` ne
> lisent pas `colors` : elles valent le dégradé bleu/rouge Intel/AMD, ou le vert
> nVidia quand `branding.edition` contient « nvidia » (`BrandingProxy`).

Toute clé omise reprend la valeur par défaut du modèle `BrandingColors`.

### Typographie et polices embarquées (`fonts`)

Deux familles sont exposées au QML :

| Clé YAML | Propriété QML | Usage |
|----------|---------------|-------|
| `primary` | `branding.fontPrimary` | Police globale de l'UI |
| `monospace` | `branding.fontMonospace` | Zones de code / journaux |

La valeur est un **nom de famille de police**, pas un chemin de fichier.

#### Embarquer une police (rendu identique partout)

Une famille référencée dans `fonts.*` doit être disponible pour Qt. Les polices
déjà fournies par le système ou par le paquet Arch (Poppins, Roboto Mono, Noto
Sans, Noto emoji…) n'ont rien à faire de plus. Pour une police **absente de
l'hôte** (cas typique de « Lemon Milk »), il faut l'embarquer dans le thème afin
que le rendu soit identique partout, y compris sur un live ISO qui n'a pas accès
aux polices du système hôte.

Procédure :

1. Déposer le fichier `.ttf`, `.otf` ou `.ttc` dans le dossier `fonts/` du thème,
   p. ex. `config/themes/<nom>/fonts/LEMONMILK-Regular.otf`.
2. Au démarrage, `_register_theme_fonts()` (`src/xen/main.py`) parcourt
   `<theme>/fonts/` et enregistre chaque fichier via
   `QFontDatabase.addApplicationFont()`. Le dossier est résolu à partir du chemin
   **absolu** du thème (`theme_base`), donc indépendamment du répertoire de
   travail.
3. Référencer la police par son **nom de famille** dans `theme.yaml` :

   ```yaml
   fonts:
     primary: "Lemon Milk"   # fichier: fonts/LEMONMILK-Regular.otf
   ```

Le nom de famille est celui embarqué dans le fichier de police, pas le nom du
fichier. Voir aussi `config/themes/<nom>/fonts/README.md`.

> **Graisses (`fonts.weights`)**: les clés `regular` / `medium` / `bold` (valeurs
> indicatives, p. ex. `400` / `600` / `700`) documentent les graisses attendues du
> thème. Elles ne figurent pas dans le modèle `BrandingFonts` et ne sont donc pas
> exposées au QML : ce sont des valeurs déclaratives.

### Logos : tailles et variantes (`logos`)

La section `logos:` du `theme.yaml` est un **catalogue** des logos fournis par le
thème (elle n'est pas fusionnée dans le branding: cf. « Portée de la fusion »).
Elle suit une convention de nommage par taille :

| Clé | Convention |
|-----|------------|
| `main`, `main_256`, `main_128`, `main_64`, `main_32` | Logo sur fond **sombre**, décliné par taille (px) |
| `mango`, `selector` | Logos spéciaux au format **SVG** |

Fournir plusieurs tailles permet de servir à chaque écran un asset au plus près
de sa taille d'affichage (un petit logo d'en-tête vs. un grand logo d'accueil).
Le **choix effectif** de l'asset se fait côté config d'installation : c'est le
bloc `branding.assets:` (`logo`, `logo_small`, `background`, …) qui pointe
vers le fichier voulu, ensuite résolu en URL `file://` par le `BrandingProxy` et
consommé par le QML. Autrement dit, le `theme.yaml` **décrit** les logos
disponibles ; la config **sélectionne** ceux qui sont câblés à l'UI.

### Fonds d'écran (`wallpapers`)

Également un **catalogue** (non fusionné). Convention typique :

| Clé | Rôle attendu |
|-----|--------------|
| `default` | Fond général de l'assistant |
| `welcome_dark` / `welcome_light` | Fond de l'écran d'accueil (variantes sombre / clair) |

Le fond réellement affiché (assistant et écran d'accueil) provient de
`branding.assets.background`.

### Chaînes (`strings`) et priorité i18n

Textes de marque de l'installeur. **Ordre de priorité** appliqué à l'affichage :

```
i18n (config/i18n/*.conf, Qt)  >  strings du thème  >  valeurs par défaut du modèle
```

Autrement dit, quand une traduction existe pour la locale active, elle prime ; les
`strings` du thème servent de repli, et le modèle `BrandingStrings` fournit un
dernier défaut.

Clés déclarées dans le `theme.yaml` de référence :

| Clé | Exposée au QML | Propriété QML |
|-----|:--------------:|---------------|
| `welcome_title` | oui | `branding.welcomeTitle` |
| `welcome_subtitle` | oui | `branding.welcomeSubtitle` |
| `install_button` | oui | `branding.installButton` |
| `finished_title` | modèle uniquement¹ | - |
| `finished_message` | modèle uniquement¹ | - |
| `progress_partitioning` | non² | - |
| `progress_installing` | non² | - |
| `progress_configuring` | non² | - |
| `progress_bootloader` | non² | - |
| `reboot_button` | non² | - |
| `error_generic` | non² | - |
| `error_disk_space` | non² | - |
| `error_network` | non² | - |

¹ Présentes dans le modèle `BrandingStrings` mais sans propriété dédiée dans le
`BrandingProxy` à ce jour.
² Absentes du modèle `BrandingStrings` : acceptées dans le `theme.yaml` mais
ignorées à la validation (déclaratives). L'affichage de ces messages passe
aujourd'hui par l'i18n.

Les trois propriétés exposées (`welcomeTitle`, `welcomeSubtitle`, `installButton`)
interpolent des variables i18n (`distro_name`, `distro_tagline`) avant de retomber
sur la valeur du thème.

### Liens externes (`links`)

Liens affichés dans l'UI (p. ex. lien du site en bas de l'écran d'accueil).
Modèle `BrandingLinks` (`engine.py`) :

| Clé YAML | Propriété QML | Rôle |
|----------|---------------|------|
| `website` | `branding.websiteUrl` | Site web principal de la distribution |
| `website_label` | `branding.websiteLabel` | Libellé affiché pour le lien (repli : l'URL sans `/` final) |

**Où les déclarer** : dans le bloc `branding.links:` de la **config d'installation** :

```yaml
branding:
  links:
    website: "https://example.com"
    website_label: "example.com"
```

> Le `theme.yaml` ne peut alimenter **que** `website`, et seulement via
> `metadata.website` (voir « Portée de la fusion »). `website_label` se déclare
> donc dans la config d'installation.

---

## Lier un Thème à une Configuration

### Configuration Minimale

```yaml
# config/examples/ma-distro.yaml
version: "1.0"

# Chemin vers le dossier du thème (RELATIF à ce fichier config)
theme: "../themes/ma-distro"  # Résout vers config/themes/ma-distro

branding:
  name: "Ma Distribution"
  version: "2025.1"

  # Assets (chemins relatifs au dossier theme)
  assets:
    logo: "logos/logo.png"
    logo_small: "logos/logo-64.png"
    background: "wallpapers/dark.jpg"

  colors:
    primary: "#5597e6"
    background: "#1a1a1a"
    text: "#fffded"

jobs:
  - welcome
  - partition
  - install
  - finished
```

### Configuration Complète

```yaml
version: "1.0"
theme: "../themes/ma-distro"  # Relatif au fichier config

branding:
  name: "Ma Distribution"
  version: "2025.1"
  edition: "Xenlash"

  colors:
    primary: "#5597e6"
    secondary: "#4a8bd4"
    accent: "#5597e6"
    background: "#1a1a1a"
    surface: "#32373c"
    text: "#fffded"
    text_muted: "#9CA3AF"

  assets:
    logo: "logos/logo.png"
    logo_small: "logos/logo-64.png"
    background: "wallpapers/dark.jpg"

  strings:
    welcome_title: "Bienvenue"
    welcome_subtitle: "Installation de votre système"
    install_button: "Installer"
    finished_title: "Installation terminée !"
    finished_message: "Redémarrez pour découvrir votre nouveau système."

  fonts:
    primary: "Poppins"
    monospace: "Roboto Mono"

  # Liens externes (affichés dans l'UI, ex. écran d'accueil)
  links:
    website: "https://example.com"
    website_label: "example.com"
```

---

## Créer un Nouveau Thème

### Étape 1 : Créer la Structure

```bash
mkdir -p config/themes/ma-distro/{logos,wallpapers,boot}
```

### Étape 2 : Préparer les Assets

| Asset | Format | Dimensions | Notes |
|-------|--------|------------|-------|
| `logo.png` | PNG (transparent) | ≥256×256 | Logo principal |
| `logo-*.png` | PNG | 32/64/128/256 | Tailles spécifiques |
| `wallpapers/*.jpg` | JPG/PNG | 1920×1080+ | Fond d'écran |
| `boot/*.png` | PNG | Variable | Bootloader splash |

### Étape 3 : Créer theme.yaml

```yaml
metadata:
  name: "Ma Distribution"
  version: "1.0"
  author: "Mon Équipe"

colors:
  primary: "#3B82F6"
  background: "#0F172A"
  text: "#F8FAFC"

logos:
  main: "logos/logo.png"

wallpapers:
  default: "wallpapers/default.jpg"

strings:
  welcome_title: "Bienvenue"
  install_button: "Installer"
```

### Étape 4 : Créer la Configuration

```yaml
# config/examples/ma-distro.yaml
version: "1.0"
theme: "../themes/ma-distro"  # Relatif au fichier config

branding:
  name: "Ma Distribution"
  assets:
    logo: "logos/logo.png"
    background: "wallpapers/default.jpg"
  colors:
    primary: "#3B82F6"
    background: "#0F172A"

jobs:
  - welcome
  - partition
  - install
  - finished
```

### Étape 5 : Tester

```bash
python -m xen.main --config config/examples/ma-distro.yaml --debug
```

---

## Propriétés Exposées au QML

Le `BrandingProxy` (`src/xen/gui/bridge.py`) expose les propriétés suivantes au
QML (contexte `branding`) :

### Textes

| Propriété QML | Source YAML |
|---------------|-------------|
| `branding.name` | `branding.name` (ou `metadata.name`) |
| `branding.editionLabel` | `« <edition> Edition »` si `branding.edition` (ou `metadata.codename`) est non vide |
| `branding.welcomeTitle` | i18n → `branding.strings.welcome_title` |
| `branding.welcomeSubtitle` | i18n → `branding.strings.welcome_subtitle` |
| `branding.installButton` | i18n → `branding.strings.install_button` |
| `branding.finishedTitle` | i18n → `branding.strings.finished_title` |
| `branding.finishedSubtitle` | i18n → « <name> has been successfully installed » |
| `branding.finishedMessage` | i18n → `branding.strings.finished_message` |

Les textes i18n sont retraduits en direct lors d'un changement de langue
(signal `brandingChanged`, slot `retranslate()`).

### Couleurs

| Propriété QML | Source |
|---------------|--------|
| `branding.primaryColor` | Fixée par profil (voir « Couleurs ») |
| `branding.accentColor` | Fixée par profil |
| `branding.accentGradientEnd` | Fixée par profil |
| `branding.backgroundGradientStart` | Fixée par profil |
| `branding.backgroundGradientEnd` | Fixée par profil |
| `branding.backgroundColor` | `branding.colors.background` |
| `branding.surfaceColor` | `branding.colors.surface` |
| `branding.textColor` | `branding.colors.text` |
| `branding.textMutedColor` | `branding.colors.text_muted` |
| `branding.successColor` | `branding.colors.success` |
| `branding.warningColor` | `branding.colors.warning` |
| `branding.errorColor` | `branding.colors.error` |

### Polices

| Propriété QML | Source YAML |
|---------------|-------------|
| `branding.fontPrimary` | `branding.fonts.primary` |
| `branding.fontMonospace` | `branding.fonts.monospace` |

### URLs d'Assets

Toutes résolues en URL absolue `file:///...` (chaîne vide si l'asset est
introuvable). Source = bloc `branding.assets:` de la config d'installation.

| Propriété QML | Source YAML (`branding.assets.*`) |
|---------------|-----------------------------------|
| `branding.logoUrl` | `logo` |
| `branding.logoSmallUrl` | `logo_small` |
| `branding.backgroundUrl` | `background` |
| `branding.iconUserUrl` | `icon_user` |
| `branding.iconFullnameUrl` | `icon_fullname` |
| `branding.iconHostnameUrl` | `icon_hostname` |
| `branding.iconPasswordUrl` | `icon_password` |
| `branding.iconCheckUrl` | `icon_check` |
| `branding.iconCrossUrl` | `icon_cross` |

### Liens

| Propriété QML | Source YAML (`branding.links.*`) |
|---------------|----------------------------------|
| `branding.websiteUrl` | `website` |
| `branding.websiteLabel` | `website_label` (repli : `website` sans `/` final) |

### Slots (résolution dynamique)

| Slot QML | Effet |
|----------|-------|
| `branding.themeIconUrl(cheminRelatif)` | Résout n'importe quel chemin relatif au thème en URL `file://` |

---

## Résolution des Assets

Tous les chemins d'assets sont **relatifs au dossier du thème**. Le passage
chemin relatif → URL absolue est assuré par `BrandingProxy._resolve_asset()`
(`bridge.py`) :

1. `main.py` calcule `theme_base = (dossier_de_la_config / theme).resolve()`: un
   chemin **absolu**, indépendant du répertoire de travail courant.
2. Pour chaque asset : `full_path = theme_base / chemin_relatif`.
3. Si `full_path` **existe** → `QUrl.fromLocalFile(full_path.resolve())` →
   `file:///chemin/absolu`.
4. Si le chemin est vide **ou** le fichier absent → chaîne vide (le QML applique
   alors son repli : logo de secours, dégradé, emoji…).

C'est cette résolution absolue (à partir de `theme_base`, non du CWD) qui garantit
un fonctionnement correct **hors répertoire courant**, par exemple lancé depuis
un raccourci `.desktop` ou un script wrapper. C'est aussi la raison pour laquelle
les chemins d'assets se déclarent toujours **relativement au dossier du thème**.

En mode `--debug`, chaque résolution est tracée (`Resolved: … -> file://…` ou
`Asset not found: …`), ce qui aide à diagnostiquer un asset manquant.

---

## Utilisation dans QML

### Afficher le Logo

```qml
Image {
    source: branding.logoUrl
    fillMode: Image.PreserveAspectFit
    visible: status === Image.Ready
}

// Fallback si logo non disponible
Rectangle {
    visible: logoImage.status !== Image.Ready
    color: branding.primaryColor
    Text {
        text: branding.name.charAt(0)
        color: branding.textColor
    }
}
```

### Fond d'Écran

```qml
Image {
    anchors.fill: parent
    source: branding.backgroundUrl
    fillMode: Image.PreserveAspectCrop
    visible: status === Image.Ready
}

// Gradient fallback
Rectangle {
    anchors.fill: parent
    visible: backgroundImage.status !== Image.Ready
    gradient: Gradient {
        GradientStop { position: 0.0; color: branding.backgroundColor }
        GradientStop { position: 1.0; color: Qt.darker(branding.backgroundColor, 1.3) }
    }
}
```

---

## Récapitulatif des sections de `theme.yaml`

Vue d'ensemble de toutes les sections d'un `theme.yaml`, avec leur mode de
consommation :

| Section | Contenu | Fusionnée dans `branding` ? | Exposée au QML |
|---------|---------|:---------------------------:|----------------|
| `metadata` | Identité : `name`, `codename`, `version`, `author`, `website`, `license` | Partielle : `name`→name, `version`→version, `codename`→edition, `website`→links.website | `name`, `editionLabel`, `websiteUrl` |
| `colors` | 10 rôles de couleur `#RRGGBB` | Oui (clé à clé) | 7 propriétés `*Color` (`primary`/`secondary`/`accent` non lus) |
| `fonts` | `primary`, `monospace` (+ `weights` indicatif) | Oui pour les 2 familles ; `weights` ignoré | `fontPrimary`, `fontMonospace` |
| `logos` | Catalogue de logos (tailles `main*`, SVG `mango`/`selector`) | Non (catalogue) | Via `branding.assets` de la config |
| `wallpapers` | Catalogue de fonds (`default`, `welcome_*`) | Non (catalogue) | Via `branding.assets.background` |
| `boot` | Assets de démarrage (`bootloader`, `efi_icon`) | Non (catalogue) | - |
| `strings` | Textes de marque (accueil, progression, fin, erreurs) | Oui, mais seules 5 clés sont dans le modèle | `welcomeTitle`, `welcomeSubtitle`, `installButton`, `finishedTitle`, `finishedMessage` |

Rappel : les **assets** effectivement affichés (logos, fonds, icônes utilisateurs,
bootloader…) proviennent du bloc `branding.assets:` de la **config
d'installation** ; le `theme.yaml` en fournit le catalogue et surcharge l'identité
(couleurs, polices, textes, métadonnées).

---

## Dépannage

### Les images ne s'affichent pas

1. Vérifier que le chemin `theme` est correct
2. Vérifier que les fichiers existent dans le dossier du thème
3. Lancer avec `--debug` pour voir le chemin résolu :
   ```
   Theme base: /chemin/absolu/config/themes/ma-distro
   ```

### Fallback au lieu du logo

Le fallback (cercle avec initiale) s'affiche si :
- Le fichier n'existe pas
- Le chemin d'asset est vide dans la config
- Le format d'image n'est pas supporté

### Couleurs non appliquées

Vérifier le format hexadécimal : `#RRGGBB` (avec le `#`)
