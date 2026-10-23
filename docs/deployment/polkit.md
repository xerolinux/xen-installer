# Configuration Polkit pour Xen

Xen s'exécute en **un seul processus root** : l'interface QML et les Jobs
(partitionnement, montage, installation, bootloader) tournent dans le même
processus Python. L'élévation passe par `pkexec` et une action polkit dédiée.

## Chaîne de lancement

```
xen.desktop
  └── /usr/bin/xen-polkit                    (utilisateur)
        └── pkexec --disable-internal-agent /usr/lib/xen/xen-root-launch \
                   "$WAYLAND_DISPLAY" "$XDG_RUNTIME_DIR" "$@"
              └── /usr/lib/xen/xen-root-launch   (root)
                    └── exec /usr/bin/xen "$@"
```

`pkexec` efface `WAYLAND_DISPLAY` et `XDG_RUNTIME_DIR`, ce qui ferait
retomber l'application sur XWayland. `xen-polkit` les passe donc en
arguments, et `xen-root-launch` ne les exporte qu'après validation :

- `PKEXEC_UID` est un entier ;
- `WAYLAND_DISPLAY` est un simple nom de socket (pas de `/`) ;
- `XDG_RUNTIME_DIR` vaut exactement `/run/user/$PKEXEC_UID`, est un
  répertoire (pas un lien symbolique) appartenant à cet utilisateur ;
- le socket `$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY` existe.

Il exporte alors aussi `QT_QPA_PLATFORM="wayland;xcb"`. Sinon, rien n'est
exporté et Qt utilise X11 via `DISPLAY`/`XAUTHORITY`, que `pkexec` conserve
grâce à `org.freedesktop.policykit.exec.allow_gui`. Ne jamais remplacer ce
mécanisme par `pkexec env ...` : l'action polkit porterait alors sur `env`
et autoriserait n'importe quelle commande.

`xen` refuse de démarrer sans root, sauf avec `--dry-run`.

## Action polkit

Fichier : `data/polkit/org.xen.installer.policy`, installé dans
`/usr/share/polkit-1/actions/`.

```xml
<action id="org.xen.installer.run">
  <defaults>
    <allow_any>auth_admin</allow_any>
    <allow_inactive>auth_admin_keep</allow_inactive>
    <allow_active>auth_admin_keep</allow_active>
  </defaults>
  <annotate key="org.freedesktop.policykit.exec.path">/usr/lib/xen/xen-root-launch</annotate>
  <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
</action>
```

Par défaut, lancer Xen exige une authentification administrateur : `xen`
accepte `--config`, et les commandes d'une configuration s'exécutent en root.

## Live ISO sans mot de passe

Fichier : `data/polkit/49-xen-installer-live.rules`, installé dans
`/usr/share/polkit-1/rules.d/`.

```javascript
polkit.addRule(function(action, subject) {
    if (action.id == "org.xen.installer.run" &&
        subject.user == "liveuser" &&
        subject.local && subject.active) {
        return polkit.Result.YES;
    }
});
```

Seul `liveuser`, dans une session locale et active, est dispensé
d'authentification. Le paquet `xen-app` (règle comprise) est retiré du
système installé par le job `packages` (liste `remove` de
`config/examples/xerolinux*.yaml`), donc la règle ne persiste pas après
l'installation.

## Vérification

```bash
pkaction --verbose --action-id org.xen.installer.run
xmllint --noout /usr/share/polkit-1/actions/org.xen.installer.policy
journalctl -u polkit -b
```

Sur le live ISO, `xen-polkit` doit ouvrir Xen sans demander de mot de
passe, et `xen --platform-info` lancé de la même façon doit indiquer le
plugin `wayland` sous une session Wayland.

## Développement

Pas besoin de polkit pour travailler sur l'interface :

```bash
PYTHONPATH=src python -m xen.main --dry-run --config config/examples/xerolinux.yaml
```

`--dry-run` n'exécute aucune commande système et lève l'exigence root.

## Dépannage

### Aucune fenêtre d'authentification

Un agent polkit graphique doit tourner dans la session (KDE :
`polkit-kde-agent`). `xen-polkit` passe `--disable-internal-agent`, donc
`pkexec` n'utilise jamais d'agent texte.

### L'application démarre sous XWayland

`xen-root-launch` a refusé les valeurs reçues. Vérifier dans la session
utilisateur que `WAYLAND_DISPLAY` est défini, que `XDG_RUNTIME_DIR` vaut
`/run/user/$(id -u)` et que le socket existe. Le paquet `qt6-wayland` doit
être installé.

## Références

- [polkit Reference Manual](https://www.freedesktop.org/software/polkit/docs/latest/)
- [pkexec(1)](https://man.archlinux.org/man/pkexec.1)
- [Polkit (ArchWiki)](https://wiki.archlinux.org/title/Polkit)
