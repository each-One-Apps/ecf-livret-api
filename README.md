# Livret ECF — API de remplissage

Remplit le **livret d'évaluations passées en cours de formation** (titre professionnel,
document officiel soumis à jury) à partir des soumissions Fillout, via Make et Airtable.

## Pourquoi un dépôt séparé de `apni-bulletin-api`

Le motif technique est le même (FastAPI + overlay reportlab + fusion pypdf), et il aurait été
tentant d'ajouter une route à l'API APNI existante. On ne l'a pas fait : **tout push sur ce
dépôt-là redéploie le service dont dépend le scénario Make `Generate APNI French Course
Subscription File` (6643033)**. Une erreur qui passe le build mais casse au démarrage
emporterait les deux endpoints. Ici, l'ECF ne peut pas faire tomber l'APNI.

## Pourquoi ce dépôt est public

Ce n'est pas un choix d'ouverture : c'est une contrainte du plan Vercel **Hobby**, qui refuse
de lier un dépôt **privé d'organisation** (`409`) et donc de déployer automatiquement sur push.
Le dépôt repassera en privé au passage en plan Pro.

En conséquence, rien d'interne ne doit entrer ici : ni clé, ni identifiant de base, ni donnée
d'apprenant. Le service est **sans état et sans secret** — il reçoit un journal, il renvoie un
PDF — ce qui rend la chose tenable. Les jeux de test utilisent des valeurs fictives de la même
forme que les vraies.

## Le principe : on ne rature pas, on recopie au propre

Le livret n'est **jamais modifié incrémentalement**. À chaque appel, le service repart du
template vierge embarqué et redessine **toutes** les évaluations du journal.

La conséquence est ce qui justifie tout le reste :

- un rejeu du scénario Make ne peut pas écrire deux fois la même ligne ;
- corriger une faute de frappe = corriger le journal dans Airtable et relancer ;
- deux appels avec le même journal produisent le **même fichier, à l'octet près**.

La mémoire vit donc côté appelant (Airtable), pas dans le PDF. Le service, lui, est sans état :
il ne détient aucune clé, ne lit et n'écrit nulle part, et ne connaît que ce qu'on lui envoie.

## API

### `POST /update-ecf-assessment`

Deux façons d'envoyer le journal.

**En texte brut — à privilégier.** Le corps *est* le journal, les paramètres passent par l'URL :

```
POST /update-ecf-assessment?record_id=recAbCdEfGhIjKlMn&livret=TP-00520
Content-Type: text/plain; charset=utf-8

Date : 2026-08-11
Activité : 1. …
…
```

Pourquoi ce mode existe : le journal contient des sauts de ligne, des guillemets et des
apostrophes. L'orchestrateur qui appelle ce service (Make) **n'a pas de fonction
d'échappement JSON** — construire un corps JSON par concaténation de texte y produit du JSON
invalide dès qu'une description tient sur plusieurs lignes. En texte brut, il n'y a rien à
échapper.

**En JSON**, si l'appelant sait sérialiser proprement :

```json
{
  "log": "<journal complet des évaluations>",
  "record_id": "recAbCdEfGhIjKlMn",
  "livret": "TP-00520"
}
```

Les deux formes produisent le même fichier. `livret` est optionnel (défaut `TP-00520`) ;
`record_id` ne sert qu'à la traçabilité et au nom du fichier.

**Réponse `200`** : le PDF en binaire (`application/pdf`), plus deux en-têtes —
`X-ECF-Evaluations` (nombre d'évaluations dessinées) et `X-ECF-Tronquees` (rangs des
descriptions qui ne tenaient pas dans leur cellule).

**`422`** — la demande est bien formée mais refusée : journal illisible, activité inconnue,
compétence hors référentiel, ou livret plein. **`404`** — code de livret inconnu.

Côté Make, `stopOnHttpError: true` fait que le module Airtable suivant ne s'exécute pas : sur
un refus, ni le journal ni le livret ne sont touchés.

### `GET /health`

```json
{"status": "ok", "livrets": ["TP-00520"]}
```

## Le journal

C'est la **concaténation brute des blocs produits par Fillout**, séparés par une ligne de
tirets. Format volontairement conservé tel quel : Make n'a qu'à coller bout à bout l'ancien
journal et les nouveaux blocs, sans construire de JSON ni gérer de séparateur — les blocs
Fillout portent déjà le leur.

```
Date : 2026-08-11
Activité : 1. Contribuer à l'efficacité commerciale d'une unité marchande…
Compétences évaluées : 1, 3
Description des compétences : Test test test
----------
```

- Les valeurs courent jusqu'au libellé suivant : une description sur plusieurs lignes passe.
- L'ordre des libellés est indifférent, les accents et espaces de fin sont tolérés.
- Le préfixe `N.` de `Activité :` désigne la fiche. Sans lui, le bloc est refusé.
- `pedagogical_monitoring_form_comment` est un champ **Rich text** : Airtable peut normaliser
  `----------` en `---`. Le séparateur accepte 3 tirets ou plus.

Ce même champ sert de journal à d'autres formulaires (réflexions AFEST, `▪︎ …`). Le parseur
refuse ce contenu, ce qui empêche le scénario d'écraser la réflexion d'un apprenant si jamais
il visait la mauvaise ligne.

## Ajouter un titre professionnel

La géométrie du tableau n'est **pas codée en dur** : elle est extraite du PDF. Les cases à
cocher sont de vrais glyphes `☐` dans la couche texte, et les filets du tableau sont des
vecteurs — tout est donc mesurable par programme.

```bash
pip install pdfplumber            # dépendance de développement uniquement
python scripts/extract_coords.py livret.pdf ecf/coords_<CODE>.json
cp livret.pdf ecf/template_<CODE>.pdf
```

Le script découvre seul le nombre d'activités-types, le nombre de compétences de chacune, les
pages concernées, le nombre de lignes par tableau et les pages « évaluations complémentaires ».
Un livret d'un autre titre passe sans modification, tant qu'il sort du même modèle Word du
ministère (`MODELE_ECF_LE.docm`). En cas de mise en page inattendue, **il s'arrête avec un
message précis plutôt que de deviner**.

Sortie sur le TP-00520 :

```
activité 1 : 4 compétences | princ p.3 = 5 lignes, compl p.5 = 4 lignes | capacité 9
activité 2 : 4 compétences | princ p.6 = 5 lignes, compl p.8 = 4 lignes | capacité 9
```

⚠️ Le service ne sait pas **quel livret appliquer à quel apprenant** : c'est à l'appelant de
passer `livret`. La règle d'aiguillage reste à définir.

## Capacité

5 lignes par activité (la fiche principale). Au-delà, `422`.

Les pages « évaluations complémentaires » sont déjà cartographiées et portent 4 lignes de plus.
Les activer = passer `BLOCS_AUTORISES` à `("principale", "complementaire")` dans
`ecf/livret.py`.

## Développement

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt pytest httpx
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/uvicorn main:app --reload
```

## Déploiement

Render, Docker auto-détecté, push sur `main`. Le `Dockerfile` **charge tous les livrets au
build** : un template ou une géométrie manquant fait échouer la construction de l'image plutôt
que de livrer un conteneur qui répondrait 404 en production.

Sur le tier gratuit, le cold start peut dépasser le timeout de Make — à surveiller sur le
premier appel de la journée.
