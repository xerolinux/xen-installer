# Changelog

Toutes les modifications notables de ce fork sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased] - Fork XeroLinux

Ce dépôt est un fork local d'Xen, adapté pour cibler exclusivement
XeroLinux (Arch Linux) au lieu de la distribution NixOS d'origine.

### Added

- Job `packagechooser` : sélection de paquets optionnels par groupes,
  configurable via un fichier YAML dédié.
- Job `systemupdate` : mise à jour complète du système (pacman) avant
  l'installation des paquets choisis, avec vérification de connectivité.
- Extension du job `packages` : fusion des paquets optionnels sélectionnés
  et suppression des paquets propres à l'ISO live après installation.
- Configurations `xerolinux-online.yaml` et `xerolinux-offline.yaml`.

### Removed

- Job d'installation NixOS et son générateur de configuration Nix
  (aucun équivalent ni usage pour une cible Arch).
- Branding et thème complets de la distribution d'origine.
- Documentation et historique spécifiques à la distribution d'origine.
- Outillage de build Nix (flake, package Nix) — ce fork se construit et
  se distribue pour Arch/pacman.

### Changed

- Branding (auteur, icône applicative) adapté à XeroLinux.
