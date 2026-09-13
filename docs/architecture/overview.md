# Architecture Overview

> **Docs** > **Architecture** > Overview

---

## Concept Fondamental : Séparation UI / Engine

Xen repose sur une séparation stricte entre deux processus :

| Composant | Contexte | Responsabilité |
|-----------|----------|----------------|
| **UI** | User (non-root) | Affichage, interactions utilisateur, collecte des choix |
| **Engine** | Root (privilégié) | Exécution des Jobs, opérations système |

Cette séparation garantit :
- **Sécurité** : L'UI n'a jamais accès root
- **Isolation** : Un crash UI ne corrompt pas l'installation
- **Auditabilité** : Seul l'Engine exécute des commandes privilégiées

---

## Communication IPC

```
┌──────────────┐                    ┌──────────────┐
│     UI       │◄──── Socket ──────►│    Engine    │
│  (PySide6)   │      Unix          │   (Python)   │
└──────────────┘                    └──────────────┘
       │                                   │
       ▼                                   ▼
   QML Views                          Job Runner
```

### Protocole

Messages JSON sur socket Unix :

```json
// UI → Engine : Requête
{"type": "start_job", "job": "partition", "params": {...}}

// Engine → UI : Réponse/Événement
{"type": "progress", "job": "partition", "percent": 45, "message": "..."}
```

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
    def estimate_time(self) -> int:
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
| `welcome` | Écran d'accueil, vérifications pré-installation | ✅ Implémenté |
| `requirements` | Checks système (RAM, Disk, CPU, EFI, Internet, GPU) | ✅ Implémenté |
| `locale` | Sélection langue, timezone, clavier + détection auto | ✅ Implémenté |
| `packagechooser` | Sélection de paquets optionnels par groupes (mode online) | ✅ Implémenté |
| `partition` | Partitionnement disque avec sécurité critique | ✅ Implémenté |
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
│   ├── archlinux.yaml         # Arch Linux générique
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
      type: systemd-boot
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
2. Chaque `name` est résolu vers `xen.jobs.<name>.Job`
3. Le bloc `config` est injecté dans le constructeur du Job

---

## Flux d'Exécution

```
1. main.py démarre
   │
2. ├─► Fork UI (user context)
   │     └── Charge QML, affiche branding
   │
3. └─► Fork Engine (root context)
         │
4.       ├── Charge xen.yaml
         │
5.       ├── Instancie les Jobs
         │
6.       └── Attend commandes IPC
                │
7.              ├── UI envoie "start"
                │
8.              └── Engine exécute Jobs séquentiellement
                      │
9.                    └── Chaque Job émet progress → UI
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
