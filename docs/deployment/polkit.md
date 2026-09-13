# Configuration Polkit pour Xen

Documentation pour configurer PolicyKit (polkit) afin de permettre l'élévation de privilèges de l'Engine Xen.

## Vue d'ensemble

Xen utilise une architecture à deux processus pour des raisons de sécurité :

```
┌─────────────────────────────────────────────────────────────┐
│   UI Process (utilisateur)     Engine Process (root)        │
│   ┌──────────────────┐         ┌──────────────────┐         │
│   │  Interface QML   │◄───────►│  Jobs système    │         │
│   │  (non privilégié)│   IPC   │  (privilégié)    │         │
│   └──────────────────┘         └──────────────────┘         │
│                        pkexec                                │
└─────────────────────────────────────────────────────────────┘
```

L'Engine nécessite des privilèges root pour :
- Partitionner les disques
- Monter les systèmes de fichiers
- Installer le bootloader
- Configurer le système cible

## Prérequis

- `polkit` (PolicyKit) installé sur le système
- Agent polkit fonctionnel (GUI ou console)

### Vérifier l'installation

```bash
# Vérifier que polkit est installé
which pkexec

# Vérifier le service polkit
systemctl status polkit

# Lister les agents disponibles
ls /usr/lib/polkit-*/polkit-agent-helper-*
```

## Configuration Production

### 1. Créer la policy Xen

Créez le fichier `/usr/share/polkit-1/actions/org.xen.installer.policy` :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">

<policyconfig>
  <vendor>Xen Installer</vendor>
  <vendor_url>https://github.com/XeroLinux/xen-installer</vendor_url>
  <icon_name>system-installer</icon_name>

  <action id="org.xen.installer.run-engine">
    <description>Run Xen installation engine</description>
    <description xml:lang="fr">Exécuter le moteur d'installation Xen</description>
    <message>Authentication is required to run the Xen installer engine</message>
    <message xml:lang="fr">L'authentification est requise pour exécuter le moteur d'installation Xen</message>
    <defaults>
      <!-- Environnement Live ISO : pas d'authentification -->
      <allow_any>yes</allow_any>
      <allow_inactive>yes</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/bin/python3</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>

</policyconfig>
```

### 2. Variantes de configuration

#### Live ISO (sans authentification)

Pour un environnement Live ISO où l'utilisateur a déjà un accès complet :

```xml
<defaults>
  <allow_any>yes</allow_any>
  <allow_inactive>yes</allow_inactive>
  <allow_active>yes</allow_active>
</defaults>
```

#### Système installé (avec authentification)

Pour un système installé nécessitant une authentification admin :

```xml
<defaults>
  <allow_any>auth_admin</allow_any>
  <allow_inactive>auth_admin</allow_inactive>
  <allow_active>auth_admin_keep</allow_active>
</defaults>
```

#### Options de `<defaults>`

| Valeur | Description |
|--------|-------------|
| `no` | Toujours refusé |
| `yes` | Toujours autorisé |
| `auth_self` | Authentification utilisateur courant |
| `auth_admin` | Authentification administrateur |
| `auth_self_keep` | Auth utilisateur, garde le token |
| `auth_admin_keep` | Auth admin, garde le token |

### 3. Recharger polkit

```bash
# Recharger les policies
sudo systemctl restart polkit

# Vérifier que la policy est chargée
pkaction --verbose --action-id org.xen.installer.run-engine
```

## Configuration pour le développement

### Option 1 : Mode `--no-fork` (recommandé)

Le plus simple pour le développement :

```bash
python -m xen.main --debug --no-fork
```

Avantages :
- Pas besoin de polkit
- Processus unique facile à debugger
- Pas d'authentification requise

### Option 2 : Policy de développement permissive

Créez `/etc/polkit-1/rules.d/99-xen-dev.rules` :

```javascript
// Autoriser l'exécution d'Xen sans authentification pour le développement
// ATTENTION: Ne pas utiliser en production !
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.policykit.exec" &&
        action.lookup("program").indexOf("xen") !== -1) {
        return polkit.Result.YES;
    }
});
```

**Important** : Supprimez ce fichier en production !

### Option 3 : Groupe wheel/sudo sans mot de passe

Dans `/etc/polkit-1/rules.d/50-wheel.rules` :

```javascript
polkit.addRule(function(action, subject) {
    if (subject.isInGroup("wheel")) {
        return polkit.Result.YES;
    }
});
```

## Dépannage

### Erreur : "Engine process died with code 1"

**Causes possibles** :

1. **Pas d'agent polkit GUI**
   ```bash
   # Vérifier si un agent est en cours
   ps aux | grep polkit-agent

   # Installer un agent si nécessaire
   # GNOME: polkit-gnome
   # KDE: polkit-kde-agent
   # Console: pkttyagent
   ```

2. **Policy non chargée**
   ```bash
   # Vérifier la policy
   pkaction --action-id org.xen.installer.run-engine

   # Si erreur, vérifier le fichier XML
   xmllint --noout /usr/share/polkit-1/actions/org.xen.installer.policy
   ```

3. **Environnement Python différent**
   ```bash
   # pkexec utilise l'environnement root, pas votre venv
   # Solution: installer xen système ou utiliser --no-fork
   ```

### Erreur : "Authentication failed"

```bash
# Vérifier les logs polkit
journalctl -u polkit -f

# Tester manuellement
pkexec whoami
```

### Erreur : "No session for PID"

L'agent polkit nécessite une session D-Bus valide :

```bash
# Vérifier la session
echo $DBUS_SESSION_BUS_ADDRESS

# Si vide, démarrer une session
eval $(dbus-launch --sh-syntax)
```

## Intégration Distribution

### Script d'installation de la policy

```bash
#!/bin/bash
# install-polkit-policy.sh

POLICY_FILE="/usr/share/polkit-1/actions/org.xen.installer.policy"

if [[ $EUID -ne 0 ]]; then
   echo "Ce script doit être exécuté en root"
   exit 1
fi

cat > "$POLICY_FILE" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">

<policyconfig>
  <vendor>Xen Installer</vendor>
  <vendor_url>https://github.com/XeroLinux/xen-installer</vendor_url>

  <action id="org.xen.installer.run-engine">
    <description>Run Xen installation engine</description>
    <message>Authentication is required to run the Xen installer</message>
    <defaults>
      <allow_any>yes</allow_any>
      <allow_inactive>yes</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/bin/python3</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
EOF

echo "Policy installée: $POLICY_FILE"
systemctl restart polkit
echo "Polkit redémarré"
```

### Fichier policy pour packaging

Placez dans `packaging/polkit/org.xen.installer.policy` pour inclusion dans les packages distribution.

## Références

- [polkit Reference Manual](https://www.freedesktop.org/software/polkit/docs/latest/)
- [pkexec(1) man page](https://man.archlinux.org/man/pkexec.1)
- [Writing polkit policies](https://wiki.archlinux.org/title/Polkit)
