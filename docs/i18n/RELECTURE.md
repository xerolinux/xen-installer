# Relecture des traductions

## Statut par langue

Toutes les langues proposées sur l'écran d'accueil ont une traduction complète :
950 chaînes chacune (écrans QML + messages Python marqués `msg()`), plus les
textes d'accueil et de fin dans `config/i18n/<locale>.conf`.

| Langues | Origine | Relu par un natif |
|---|---|---|
| `en_US` | source | ✅ |
| `fr_FR` | écrans : humaine ; messages Python : générés le 2026-10-23 | ⚠️ messages à relire |
| les 65 autres (`af_ZA` … `zh_TW`) | générées le 2026-10-23 (en partie sur une base humaine pour de, es, it, ja, ko, pt_BR, ru, zh_CN) | ❌ **à relire** |

Une variante régionale sans fichier propre (`fr_BE`, `en_GB`, `es_MX`, `ar_EG`…)
utilise la langue de même famille via `Translator.resolve_locale()` ;
`zh_HK` et `zh_MO` utilisent `zh_TW` (chinois traditionnel).

Contrôles automatiques (`tests/unit/test_translations.py`) : aucune chaîne
`unfinished`, placeholders (`%1`, `{name}`), balises et espaces de début/fin
identiques à la source, et chaque langue proposée a sa traduction.

Arabe, persan et hébreu inversent toute la fenêtre (`LayoutMirroring`).
Le sélecteur de l'écran d'accueil ne propose que les langues dont l'écriture a une
police installée (`QFontDatabase`) : sans `noto-fonts-cjk` sur l'ISO, le chinois, le
japonais et le coréen sont masqués. Ce choix ne change que la langue de l'installeur ;
la langue du système et le clavier se choisissent sur les pages suivantes.

## Priorité de relecture

Xen **efface des disques**. Relire **en priorité** les chaînes de `PartitionView`
et des dialogues de confirmation : un avertissement affaibli ou ambigu dans une
traduction peut faire perdre des données.

Points signalés par les traducteurs, à trancher par un natif :

- **de_DE** : `Swap` laissé tel quel (vs `Auslagerungsspeicher`) ; `Flag(s)` →
  `Markierung(en)` (l'anglicisme reste courant) ; `Powered by` → `Bereitgestellt von`.

## Mettre à jour une traduction

```bash
# éditer src/xen/gui/translations/xen_<locale>.ts puis :
pyside6-lrelease src/xen/gui/translations/xen_<locale>.ts \
  -qm src/xen/gui/translations/xen_<locale>.qm
pytest tests/unit/test_translations.py
```
