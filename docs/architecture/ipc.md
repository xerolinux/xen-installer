# Architecture IPC (Inter-Process Communication)

Documentation technique du système de communication entre les processus UI et Engine.

## Vue d'ensemble

Xen utilise une architecture à deux processus pour des raisons de sécurité :

```
┌─────────────────────────────────────────────────────────────┐
│                    UI Process (User)                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              QML Interface + EngineBridge            │    │
│  └─────────────────────────────────────────────────────┘    │
│                            │                                 │
│                      IPCClient                               │
│                            │                                 │
└────────────────────────────┼────────────────────────────────┘
                             │ Unix Socket
                             │ /run/xen/ipc.sock
┌────────────────────────────┼────────────────────────────────┐
│                    Engine Process (Root)                     │
│                            │                                 │
│                      IPCServer                               │
│                            │                                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              IPCDispatcher + Handlers                │    │
│  │    ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐     │    │
│  │    │ PING │ │STATUS│ │BRAND │ │ JOBS │ │INSTAL│     │    │
│  │    └──────┘ └──────┘ └──────┘ └──────┘ └──────┘     │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Composants

### 1. Protocol (`protocol.py`)

Définit le format des messages JSON échangés.

**Types de messages :**
- `REQUEST` : Commande envoyée par l'UI
- `RESPONSE` : Réponse du Engine
- `EVENT` : Notification du Engine vers l'UI

**Structure d'un message :**
```json
{
  "version": "1.0",
  "type": "REQUEST",
  "id": "uuid-unique",
  "timestamp": "2025-01-15T10:30:00Z",
  "command": "GET_STATUS",
  "args": {}
}
```

**Commandes disponibles :**
| Commande | Description |
|----------|-------------|
| `PING` | Test de connexion |
| `GET_STATUS` | État de l'installation |
| `GET_BRANDING` | Configuration branding |
| `GET_JOB_NAMES` | Liste des jobs |
| `START_INSTALLATION` | Démarrer l'installation |
| `CANCEL_INSTALLATION` | Annuler l'installation |
| `VALIDATE_CONFIG` | Valider la configuration |
| `SHUTDOWN` | Arrêter le serveur |

**Événements :**
| Événement | Description |
|-----------|-------------|
| `JOB_STARTED` | Un job commence |
| `JOB_PROGRESS` | Progression d'un job |
| `JOB_COMPLETED` | Un job terminé |
| `ERROR_OCCURRED` | Erreur survenue |
| `INSTALLATION_COMPLETE` | Installation terminée |
| `ENGINE_READY` | Serveur prêt |
| `ENGINE_SHUTDOWN` | Serveur s'arrête |

### 2. Transport (`transport.py`)

Gestion des sockets Unix avec framing length-prefix.

**Protocole de framing :**
```
┌──────────────────┬──────────────────────────────────┐
│  4 bytes (BE)    │         JSON payload             │
│  message length  │         (UTF-8 encoded)          │
└──────────────────┴──────────────────────────────────┘
```

**Sécurité :**
- Socket path : `/run/xen/ipc.sock`
- Permissions répertoire : `0700` (owner only)
- Permissions socket : `0600` (owner read/write)
- Taille max message : 10 MB

### 3. Security (`security.py`)

Validation de sécurité multi-couches.

**Protections :**
- **Whitelist commandes** : Seules les commandes autorisées passent
- **Validation paths** : Protection contre path traversal (`../`)
- **Patterns dangereux** : Détection injection shell (`;`, `|`, `` ` ``, `$`)
- **Limites** : Strings max 4096 chars, nesting max 10 niveaux
- **Sanitization** : Nettoyage automatique des arguments

**Roots autorisés pour paths :**
- `/mnt` (point de montage cible)
- `/tmp` (fichiers temporaires)
- `/run` (runtime)

### 4. Dispatcher (`dispatcher.py`)

Routage des commandes vers les handlers.

```python
from xen.ipc import IPCDispatcher, Command

dispatcher = IPCDispatcher()

def my_handler(args: dict) -> dict:
    return {"result": "success"}

dispatcher.register(Command.GET_STATUS, my_handler)
```

### 5. Server (`server.py`)

Serveur multi-client avec threads.

**Fonctionnalités :**
- Accept multi-clients concurrent
- Thread par client
- Broadcast d'événements
- Graceful shutdown

```python
from xen.ipc import IPCServer

with IPCServer("/tmp/test.sock") as server:
    server.broadcast_event(Event.JOB_PROGRESS, {"percent": 50})
```

### 6. Client (`client.py`)

Client pour le processus UI.

**Fonctionnalités :**
- Commandes synchrones
- Commandes asynchrones avec callback
- Subscription aux événements
- Reconnection automatique

```python
from xen.ipc import IPCClient

with IPCClient("/tmp/test.sock") as client:
    # Synchrone
    result = client.ping("hello")

    # Asynchrone
    client.send_command_async(Command.GET_STATUS, callback=my_callback)

    # Événements
    client.subscribe_event(Event.JOB_PROGRESS, on_progress)
```

## Launcher (`launcher.py`)

Gestion du cycle de vie des processus.

### EngineProcess

Lance le processus Engine avec élévation de privilèges.

```python
from xen.launcher import EngineProcess

with EngineProcess(config_path, socket_path) as engine:
    engine.start()  # Lance avec pkexec/sudo
    engine.wait_for_ready()  # Attend le socket
    # ... utilisation ...
    engine.stop()  # Arrêt graceful
```

### Handlers Engine

Le dispatcher Engine inclut des handlers pour :
- `PING` : Retourne `{"pong": True}`
- `GET_STATUS` : État actuel de l'installation
- `GET_BRANDING` : Configuration branding complète
- `GET_JOB_NAMES` : Liste des jobs configurés
- `START_INSTALLATION` : Démarre l'exécution
- `CANCEL_INSTALLATION` : Annule l'installation
- `VALIDATE_CONFIG` : Valide la configuration
- `SHUTDOWN` : Arrête le serveur

## Modes d'exécution

### Mode Production (défaut)
```bash
python -m xen.main
```
- UI fork le Engine avec pkexec
- Communication via `/run/xen/ipc.sock`
- Engine tourne en root
- Nécessite une configuration polkit (voir [`docs/deployment/polkit.md`](../deployment/polkit.md))

### Mode Développement (`--no-fork`)
```bash
python -m xen.main --no-fork
```
- Processus unique
- Pas d'élévation de privilèges
- Idéal pour tester l'UI

### Mode Engine (`--engine`)
```bash
python -m xen.main --engine --socket /tmp/test.sock
```
- Lance uniquement le serveur IPC
- Pour tests et debugging

## Gestion des erreurs

### Codes d'erreur (`IPCErrorCode`)

| Code | Description |
|------|-------------|
| `INVALID_MESSAGE` | Message mal formé |
| `UNKNOWN_COMMAND` | Commande non reconnue |
| `CONNECTION_LOST` | Connexion perdue |
| `PERMISSION_DENIED` | Commande non autorisée |
| `VALIDATION_FAILED` | Validation échouée |
| `HANDLER_ERROR` | Erreur dans le handler |
| `TIMEOUT` | Timeout dépassé |

### Exceptions

```python
from xen.ipc import (
    IPCError,           # Base
    IPCConnectionError, # Problèmes connexion
    IPCProtocolError,   # Erreurs protocole
    IPCSecurityError,   # Violations sécurité
    IPCTimeoutError,    # Timeouts
    IPCValidationError, # Validation échouée
)
```

## Tests

```bash
# Tous les tests IPC
pytest tests/unit/test_ipc.py -v

# Tests spécifiques
pytest tests/unit/test_ipc.py::TestIPCServer -v
pytest tests/unit/test_ipc.py::TestIPCClient -v
pytest tests/unit/test_ipc.py::TestIPCIntegration -v
```

## Fichiers

```
src/xen/ipc/
├── __init__.py       # Exports publics
├── protocol.py       # Messages et enums
├── transport.py      # Socket Unix
├── security.py       # Validation sécurité
├── dispatcher.py     # Routage commandes
├── server.py         # Serveur multi-client
├── client.py         # Client UI
└── exceptions.py     # Exceptions IPC

src/xen/
├── launcher.py       # Gestion processus
└── main.py           # Point d'entrée (modes)
```
