# Relecture des traductions

## Statut par langue

| Langue | Chaînes QML | Origine | Relu par un natif |
|---|---|---|---|
| `fr_FR` | 229/229 | humaine | ✅ |
| `en_US` | 229/229 | source | ✅ |
| `de_DE` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `es_ES` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `it_IT` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `ja_JP` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `ko_KR` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `pt_BR` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `ru_RU` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |
| `zh_CN` | 229/229 | 110 humaines + **119 générées** | ❌ **à relire** |

Les autres locales présentes dans `src/xen/gui/translations/` (`nl_NL`, `pl_PL`,
`pt_PT`, `en_GB`, `cs_CZ`, `sv_SE`, `tr_TR`, `ar_*`, …) sont des **coquilles vides**
(0/229). Elles ne sont pas proposées à l'utilisateur : le sélecteur de langue est
piloté par `config/i18n/*.conf`, et une locale sans traduction retombe sur l'anglais
via `Translator.resolve_locale()`.

## Ce qui a été généré

Les 119 chaînes ajoutées aux 8 langues ci-dessus (116 manquantes + 3 dont
l'attribut `type="unfinished"` empêchait `lrelease` de les publier — dont le bouton
**Install** de l'écran d'accueil) ont été produites automatiquement, en s'alignant sur
la terminologie des 110 chaînes déjà traduites de chaque langue, avec `fr_FR` comme
référence d'intention.

Contrôles automatiques passés : complétude 229/229, intégrité des placeholders
(`%1`, `{var}`), noms propres préservés, `lrelease` sans `unfinished`.

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
