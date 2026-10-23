# Architecture Overview

> **Docs** > **Architecture** > Overview

---

## Concept Fondamental : un seul processus élevé

Xen tourne dans **un seul processus** : l'interface QML (PySide6) et
l'exécution des Jobs partagent le même processus Python, lancé en root via
polkit. C'est le même modèle que Calamares sur XeroLinux.

```
xen.desktop ──► xen-polkit ──► pkexec ──► /usr/lib/xen/xen-root-launch ──► /usr/bin/xen
 (liveuser)                     (polkit)     (rétablit la session Wayland)     (root)
```

| Composant | Rôle |
|-----------|------|
| `data/xen-polkit` | Lanceur côté utilisateur : appelle `pkexec` en passant `WAYLAND_DISPLAY` et `XDG_RUNTIME_DIR` en arguments (pkexec les supprime de l'environnement) |
| `data/xen-root-launch` | Lanceur côté root (cible `exec.path` de l'action polkit) : valide ces deux arguments, les exporte avec `QT_QPA_PLATFORM="wayland;xcb"`, puis exécute `/usr/bin/xen` |
| `xen.main` | Charge la configuration, crée l'application Qt et le `EngineBridge` |
| `EngineBridge` | Expose l'état au QML et exécute les Jobs dans un thread de travail du même processus |

`xen` refuse de démarrer sans root, sauf avec `--dry-run` (aucune commande
système n'est alors exécutée). Voir [`docs/deployment/polkit.md`](../deployment/polkit.md).

---

## Système de Jobs

Un **Job** est une unité de travail atomique dans le processus d'installation.

### Interface BaseJob

```python
class BaseJob(ABC):
    @abstractmethod
    def run(self, context: JobContext) -> JobResult:
        """Exécute le job. Retourne succès ou erreur."""

    @abstractmethod
    def estimate_duration(self) -> int:
        """Estimation en secondes pour l'UI."""

    def validate(self, context: JobContext) -> JobResult:
        """Validation avant exécution (optionnel)."""

    def cleanup(self, context: JobContext) -> None:
        """Nettoyage après exécution (optionnel)."""
```

### Cycle de Vie

```
   PENDING → RUNNING → COMPLETED
                ↓
              FAILED → (retry possible)
```

### Jobs Standards

| Job | Rôle | Status |
|-----|------|--------|
| `welcome` | Écran d'accueil, vérifications pré-installation (RAM, disque, CPU, EFI, Internet, GPU via `xen.jobs.requirements`) | ✅ Implémenté |
| `locale` | Sélection langue, timezone, clavier + détection auto | ✅ Implémenté |
| `packagechooser` | Sélection de paquets optionnels par groupes (mode online) | ✅ Implémenté |
| `partition` | Partitionnement disque (Effacer, Installer à côté, Manuel) : un plan unique validé puis exécuté par `xen.partitioning` | ✅ Implémenté |
| `users` | Création utilisateurs, hostname, mot de passe | ✅ Implémenté |
| `services` | Activation/désactivation d'unités systemd sur la cible | ✅ Implémenté |
| `systemupdate` | Mise à jour complète du système avant paquets (mode online) | ✅ Implémenté |
| `packages` | Installation packages (pacman/apt support) | ✅ Implémenté |
| `gpu` | Détection GPU (dGPU/iGPU), drivers recommandés | ✅ Implémenté |
| `install` | Copie des fichiers système | ✅ Implémenté |
| `bootloader` | Installation GRUB/systemd-boot | ✅ Implémenté |
| `finalize` | Scripts de finalisation XeroLinux (`/usr/local/bin/*`) | ✅ Implémenté |
| `finished` | Résumé, nettoyage, reboot/shutdown | ✅ Implémenté |

---

## Configuration et Thèmes

Chaque distribution fournit son propre fichier de configuration + thème :

```
config/
├── examples/                # Configurations par distribution
│   ├── xerolinux.yaml         # XeroLinux (online + offline)
│   ├── xerolinux-nvidia.yaml  # XeroLinux nVidia (online + offline)
│   └── minimal.yaml           # Template minimal
└── themes/                  # Assets visuels (optionnel, thème séparé)
```

### Structure d'une Configuration

```yaml
version: "1.0"

# Branding déclaré en ligne (pas de theme: séparé) - voir plus bas
branding:
  name: "XeroLinux"
  version: "2025"

  # Couleurs de l'interface
  colors:
    primary: "#5597e6"
    background: "#1a1a1a"
    text: "#fffded"

  # Textes personnalisables
  strings:
    welcome_title: "Bienvenue"
    install_button: "Installer"

jobs:
  - name: welcome
  - name: partition
    config:
      default_filesystem: ext4
  - name: bootloader
    config:
      type: grub
  - name: finished
```

### Système de Thèmes

Le système de branding supporte deux approches : déclarer les couleurs/textes
directement dans `branding:` (ce que font les configs XeroLinux), ou les
séparer dans un dossier `config/themes/<nom>/` référencé via `theme:` pour
les distributions qui veulent découpler les assets visuels de la config :

```
xen.yaml ──► theme: "config/themes/<nom>"
                            │
                            ▼
              config/themes/<nom>/
              ├── logos/logo.png    ◄── assets.logo
              └── wallpapers/dark.jpg ◄── assets.background
```

Avantages du dossier de thème séparé :
- Réutilisation des assets entre configurations
- Mise à jour indépendante des visuels
- Distribution de thèmes tiers

Documentation complète : [`docs/branding/theming.md`](../branding/theming.md)

### Résolution des Jobs

1. L'Engine lit `jobs` dans l'ordre
2. Chaque `name` est résolu vers le module `xen.jobs.<name>`, dont la première
   sous-classe de `BaseJob` au nom finissant par `Job` est instanciée
3. Le bloc `config` est injecté dans le constructeur du Job

---

## Flux d'Exécution

```
1. xen-polkit ──► pkexec ──► xen-root-launch ──► xen (root)
   │
2. ├── main.py charge xen.yaml et instancie les Jobs
   │
3. ├── Création de l'application Qt, du EngineBridge, chargement du QML
   │
4. ├── L'utilisateur parcourt les écrans ; les choix vont dans les selections
   │
5. └── "Installer" : le EngineBridge exécute les Jobs séquentiellement
         │          dans un thread de travail
6.       └── Chaque Job émet sa progression ──► signaux Qt ──► QML
```

---

## Extensibilité

### Créer un Job Custom

```python
# xen/jobs/my_custom_job.py
from xen.jobs.base import BaseJob, JobContext, JobResult

class Job(BaseJob):
    name = "my_custom_job"
    description = "My custom installation step"

    def run(self, context: JobContext) -> JobResult:
        context.report_progress(50, "Working...")
        # Logique ici
        return JobResult.ok("Custom job completed")

    def estimate_duration(self) -> int:
        return 30  # secondes
```

Puis dans votre `xen.yaml` :

```yaml
jobs:
  - name: my_custom_job
    config:
      option: value
```
