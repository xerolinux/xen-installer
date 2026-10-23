# Guide des Tests

> **Docs** > Testing

---

## Vue d'Ensemble

Xen utilise une suite de tests complète pour garantir la qualité, la sécurité et la stabilité du code.

| Catégorie | Fichier | Tests | Description |
|-----------|---------|-------|-------------|
| Engine | `test_engine.py` | 5 | Configuration, chargement, branding |
| Jobs | `test_jobs.py` | 10 | BaseJob, JobResult, JobContext |
| Thèmes | `test_theme_consistency.py` | 19 | Cohérence config/thème, assets |
| Sécurité | `test_security.py` | 15+ | Validation, injection, credentials |

---

## Lancer les Tests

### Tous les Tests

```bash
# Mode simple
pytest

# Mode verbose avec détails
pytest -v

# Avec couverture de code
pytest --cov=xen --cov-report=html
```

### Tests Spécifiques

```bash
# Tests unitaires uniquement
pytest tests/unit/

# Tests de sécurité
pytest tests/unit/test_security.py -v

# Tests de thème
pytest tests/unit/test_theme_consistency.py -v

# Un test spécifique
pytest tests/unit/test_engine.py::TestEngine::test_branding_loaded -v
```

### Tests avec Qt (headless)

```bash
# Mode offscreen pour CI/serveur
QT_QPA_PLATFORM=offscreen pytest -v
```

---

## Catégories de Tests

### 1. Tests Unitaires (`test_engine.py`, `test_jobs.py`)

Tests des composants de base :

```python
# Exemple: Test de chargement de configuration
def test_branding_loaded():
    engine = Engine.from_config_file("config/examples/xerolinux.yaml")
    assert engine.config.branding.name == "XeroLinux"
```

**Couverture :**
- `XenConfig` : Validation Pydantic, normalisation jobs
- `Engine` : Chargement config, gestion erreurs
- `BaseJob` : Interface abstraite, cycle de vie
- `JobResult` : Succès/échec, données

### 2. Tests de Cohérence Thème (`test_theme_consistency.py`)

Vérifie que les configurations et thèmes sont cohérents :

```python
# Exemple: Vérification qu'une config n'utilise pas de theme: (branding inline)
def test_xerolinux_has_no_theme_key():
    # xerolinux.yaml déclare son branding directement en ligne,
    # sans dossier de thème séparé.
```

**Vérifications :**
- Configs XeroLinux existent et se chargent sans erreur
- Configs XeroLinux n'utilisent pas de `theme:` (branding inline)
- Chargement retourne le bon nom de branding
- Validation YAML de tous les exemples de configuration

### 3. Tests de Sécurité (`test_security.py`)

Tests critiques pour la sécurité :

#### Configuration Security
```python
def test_no_hardcoded_passwords():
    """Config files should not contain hardcoded passwords."""

def test_yaml_safe_load():
    """All YAML files should be loadable with safe_load."""
```

#### Path Traversal Prevention
```python
def test_theme_path_no_traversal():
    """Theme paths should not allow directory traversal."""

def test_asset_path_validation():
    """Asset paths should not contain traversal sequences."""
```

#### Code Security
```python
def test_no_eval_or_exec():
    """Source code should not use eval() or exec()."""

def test_no_shell_injection_risk():
    """Check for potential shell injection vulnerabilities."""

def test_no_pickle_usage():
    """Pickle should not be used (security risk)."""
```

#### Input Validation
```python
def test_branding_colors_format():
    """Color values should be valid hex format."""

def test_job_name_validation():
    """Job names should be valid Python identifiers."""
```

### 4. Tests de partitionnement sur disques réels (`scripts/vmtest/`)

Les opérations root (partitionnement, LUKS, mkfs, redimensionnement, montages)
ne sont jamais testées sur l'hôte : elles tournent dans une VM Arch ISO
(QEMU, KVM, OVMF/UEFI) avec des disques virtuels construits par scénario
(Windows-like NTFS, ext4, Btrfs, LUKS, secteurs 4Kn...).

```bash
python scripts/vmtest/run.py all
python scripts/vmtest/run.py [--keep] [--timeout SECS] erase_btrfs alongside_ext4
```

Prérequis : QEMU, bsdtar, edk2-ovmf et un `archlinux-*.iso` dans `~/ISOs`.
Les paquets de l'invité viennent de l'Arch Linux Archive à la date de l'ISO,
et un seul `run.py` tourne à la fois (verrou dans `scripts/vmtest/.cache/`).
Chaque scénario (`scripts/vmtest/scenarios/<nom>.py`) affiche `PASS` ou lève
une `AssertionError` ; le code de sortie est 0 seulement si tous passent.
Les scénarios `boot_*` font l'installation complète depuis le squashfs de
`~/XeroBuild/XeroISO/xerolinux-*.iso`, redémarrent sur le disque cible et
attendent l'invite `login:` sur la console série.

Les scénarios `gui_*` (hors de `all`, lancés par `python scripts/vmtest/run.py gui`)
testent la vraie interface : `makepkg` construit `xen-app` et `xen-cfg-xerolinux`
depuis l'arbre de travail, la VM démarre l'ISO XeroLinux jusqu'au bureau Plasma
live, un agent de commande (credential systemd via SMBIOS, port virtio-serial)
installe ces paquets avec `pacman -U`, puis l'installeur est lancé par son entrée
de menu (`xen-polkit`, pkexec, Wayland). Les pages sont pilotées par injection
QMP (pointeur absolu, clavier) et chaque étape est vérifiée par OCR (tesseract,
modèle `eng`) sur une capture d'écran, sauvegardée dans
`scripts/vmtest/.cache/gui-<scénario>/`. Prérequis en plus : `tesseract` avec
`tesseract-data-eng` (ou `eng.traineddata` dans `scripts/vmtest/.cache/tessdata`)
et `python-pillow`.
Voir `scripts/vmtest/README.md`.

---

## CI/CD Pipeline

Le fichier `.github/workflows/ci.yml` définit le pipeline CI :

### Jobs

| Job | Description | Déclencheur |
|-----|-------------|-------------|
| `lint` | Ruff check + format | Push, PR |
| `typecheck` | Mypy type checking | Push, PR |
| `test` | Unit tests (Python 3.11, 3.12) | Push, PR |
| `security` | Security tests + bandit + pip-audit | Push, PR |
| `theme-check` | Theme consistency | Push, PR |
| `build` | Build package + verify | Push, PR |

### Workflow

```yaml
# Déclenché sur push/PR vers main ou develop
on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]
```

### Exécution Locale (simulation CI)

```bash
# Simuler le pipeline CI localement
pip install ruff mypy bandit pip-audit

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Type check
mypy src/ --ignore-missing-imports

# Tests
QT_QPA_PLATFORM=offscreen pytest -v

# Security scan
bandit -r src/ -ll
pip-audit
```

---

## Écrire de Nouveaux Tests

### Convention de Nommage

```
tests/
├── unit/
│   ├── test_<module>.py      # Tests unitaires par module
│   └── test_<feature>.py     # Tests par fonctionnalité
└── integration/
    └── test_<scenario>.py    # Tests d'intégration
```

### Structure d'un Test

```python
"""
Tests for <module/feature>.

<description of what is tested>
"""

import pytest
from xen.module import Class

class TestClassName:
    """Tests for ClassName."""

    @pytest.fixture
    def instance(self) -> Class:
        """Create test instance."""
        return Class()

    def test_feature_description(self, instance: Class) -> None:
        """Feature should behave in a specific way."""
        result = instance.method()
        assert result == expected
```

### Fixtures Communes

Il n'y a pas de `conftest.py` partagé : chaque module de test déclare ses
propres fixtures. Les données de disques simulés communes sont dans
`tests/mock_disks.py` (`mock_disks()`, `finalize_mock_disk()`).

---

## Couverture de Code

### Générer le Rapport

```bash
# HTML report
pytest --cov=xen --cov-report=html
open htmlcov/index.html

# Terminal report
pytest --cov=xen --cov-report=term-missing
```

### Objectifs de Couverture

| Module | Objectif | Actuel |
|--------|----------|--------|
| `core/engine.py` | 80% | ~75% |
| `jobs/base.py` | 90% | ~85% |
| `gui/bridge.py` | 70% | ~60% |
| Global | 75% | ~70% |

---

## Tests de Régression

Les tests de régression sont automatiquement exécutés via CI :

1. **Chaque PR** : Tous les tests doivent passer
2. **Merge dans main** : Full test suite + security scan
3. **Release** : Tests + build + verification

### Ajouter un Test de Régression

Quand un bug est corrigé :

```python
def test_regression_issue_123():
    """
    Regression test for issue #123.

    Bug: Theme path was not resolved correctly when config
    is in a subdirectory.

    Fix: Use config_path.parent for relative resolution.
    """
    # Setup that triggered the bug
    config = create_config_in_subdir()

    # Verify fix
    engine = Engine.from_config_file(config)
    assert engine.get_theme_path() is not None
```

---

## Dépannage

### Tests Qt Échouent en CI

```bash
# Utiliser le mode offscreen
QT_QPA_PLATFORM=offscreen pytest
```

### Tests de Sécurité Trop Stricts

```python
# Ajouter des exceptions si nécessaire
@pytest.mark.skip(reason="False positive - documented exception")
def test_specific_case():
    pass
```

### Fixtures Non Trouvées

```bash
# Vérifier le PYTHONPATH
pytest --collect-only
```

---

## Ressources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-cov](https://pytest-cov.readthedocs.io/)
- [bandit Security Linter](https://bandit.readthedocs.io/)
- [PySide6 Testing](https://doc.qt.io/qtforpython/tutorials/basictutorial/qml.html)
