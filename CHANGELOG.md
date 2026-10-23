# Changelog

Toutes les modifications notables de ce fork sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased] - Fork XeroLinux

Ce dépôt est un fork local d'Xen, adapté pour cibler exclusivement
XeroLinux (Arch Linux) au lieu de la distribution NixOS d'origine.

### Added

- Btrfs : sous-volume `@snapshots` monté sur `/.snapshots` par défaut, configuré
  selon la procédure Snapper de l'ArchWiki (snapshots hors de `@`, droits 750).
- Space Invaders : 20 envahisseurs geek (disquette, trombone, Pac-Man, BSOD...),
  un nouveau groupe à chaque niveau, de plus en plus menaçants. 10 nouvelles astuces
  sur l'écran d'installation. Partitionnement manuel : « / (root) » dans la liste.
- Interface traduite dans les 67 langues proposées sur l'écran d'accueil
  (écrans et messages Python via `msg()`, traduits à l'affichage et au changement
  de langue) ; arabe, persan et hébreu en miroir ; zh_HK/zh_MO en chinois traditionnel.
- Écran d'accueil : le choix de langue ne change que l'interface (note sous le
  sélecteur) et ne propose que les langues dont une police est installée.
- Écran Partition : carte d'aide (Effacer, À côté, Manuel) tant qu'aucun
  disque n'est choisi, puis pastilles d'infos du disque (type, taille, table,
  partitions, systèmes trouvés, espace libre).
- Job `packagechooser` : sélection de paquets optionnels par groupes,
  configurable via un fichier YAML dédié.
- Job `systemupdate` : mise à jour complète du système (pacman) avant
  l'installation des paquets choisis, avec vérification de connectivité.
- Extension du job `packages` : fusion des paquets optionnels sélectionnés
  et suppression des paquets propres à l'ISO live après installation.
- Configurations `xerolinux.yaml` et `xerolinux-nvidia.yaml` (un seul
  profil par ISO, en ligne comme hors ligne).
- Étape Clavier séparée, avec aperçu du clavier généré depuis la vraie
  keymap (ckbcomp).
- Carte des fuseaux horaires sur l'écran Locale (QtLocation, fournisseurs
  de tuiles Esri, limites de fuseaux timezone-boundary-builder embarquées).
- Partitionnement manuel (avec chiffrement LUKS).
- Option « Installer à côté » : réduit une partition ext4, Btrfs ou NTFS
  et installe dans l'espace libéré, en réutilisant l'ESP existante.
  GRUB (os-prober) ajoute l'autre système au menu de démarrage. Les volumes
  BitLocker et Windows en hibernation (démarrage rapide) sont refusés avec
  la raison affichée.
- Page de partitionnement manuel refaite dans le style Calamares : table
  des partitions, barre de disque, dialogues Créer/Modifier, guide
  intégré et liste de contrôle en direct (racine, ESP). Redimensionnement
  ext4, Btrfs et NTFS ; XFS n'est jamais réduit.
- Fenêtre d'édition des sous-volumes Btrfs (Effacer, Installer à côté et
  Manuel), valeurs par défaut via `btrfs_subvolumes`.
- Option de configuration `min_install_gib` (25 par défaut) : taille
  minimale de la racine.
- Suite de tests sur disques réels dans une VM QEMU/OVMF :
  `python scripts/vmtest/run.py all` (voir `scripts/vmtest/README.md`).
- Mini-jeu sur l'écran d'installation.
- Choix de la langue sur l'écran d'accueil.

### Removed

- Job d'installation NixOS et son générateur de configuration Nix
  (aucun équivalent ni usage pour une cible Arch).
- Branding et thème complets de la distribution d'origine.
- Documentation et historique spécifiques à la distribution d'origine.
- Outillage de build Nix (flake, package Nix) : ce fork se construit et
  se distribue pour Arch/pacman.
- Ancien chemin IPC/engine séparé (`xen.ipc`, `xen.launcher`, options
  `--engine`, `--socket` et `--no-fork`), jamais utilisé par l'application.
- Ancien éditeur manuel à application immédiate (« Apply Changes ») et son
  worker.

### Changed

- Branding (auteur, icône applicative) adapté à XeroLinux.
- Les trois modes de partitionnement produisent un plan unique
  (`xen.partitioning.plan`) exécuté par un seul exécuteur ; validation et
  exécution partagent la même table de capacités des systèmes de fichiers.
- Partition EFI créée à 2000 Mio par défaut (`efi_size_mb`) ; en mode
  manuel, une nouvelle ESP plus petite déclenche un avertissement.
- Plus aucune écriture sur disque avant le clic sur Installer.
- Changer de disque efface aussi la phrase secrète du mode manuel.

### Security

- Action polkit renommée `org.xen.installer.run` : authentification
  administrateur par défaut, sans mot de passe uniquement pour `liveuser`
  en session locale active (règle `49-xen-installer-live.rules`).
- `xen` refuse de démarrer sans root, sauf avec `--dry-run`.

### Fixed

- Btrfs : plus de paires de snapshots snap-pac créées par les scripts de finalisation
  (`SNAP_PAC_SKIP=y`) ; le système démarre avec le seul snapshot « Fresh installation ».
- Clavier : la disposition choisie s'applique à la session live KDE Plasma
  (kxkbrc + notification KConfig, en tant qu'utilisateur de la session sous pkexec),
  y compris si on garde celle proposée, donc la page Utilisateurs l'utilise.
- Lancement natif Wayland : `xen-root-launch` rétablit `WAYLAND_DISPLAY`
  et `XDG_RUNTIME_DIR` (supprimés par pkexec) après validation, au lieu de
  retomber sur XWayland.
