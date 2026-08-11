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

**Réponse `200`** : le format de pièce jointe attendu par Airtable, plus deux en-têtes —
`X-ECF-Evaluations` (nombre d'évaluations dessinées) et `X-ECF-Tronquees` (rangs des
descriptions qui ne tenaient pas dans leur cellule).

```json
{
  "attachment": [{"url": "https://…/livret.pdf?j=eNqFz8EK…", "filename": "livret_ecf_rec….pdf"}],
  "journal": "Date : 11/08/2026\nActivité : 1. …\n----------\n",
  "evaluations": 2,
  "doublons_ignores": [3]
}
```

`attachment` va dans le champ pièce jointe. **`journal` est à réécrire à la source** : c'est le
journal canonique, dédoublonné et trié, toujours relisible par le service. Sans cette
réécriture, un émetteur qui renvoie son historique complet à chaque envoi fait doubler le champ
à chaque soumission.

Ajouter `?format=pdf` pour récupérer le binaire directement — pratique en test.

### `GET /livret.pdf?j=<journal compressé>`

Régénère le livret. C'est l'URL qu'Airtable télécharge.

**Pourquoi ce détour.** Un champ *attachment* Airtable ne se remplit pas avec des octets : il
faut lui donner une URL, qu'il va chercher lui-même. Pousser le binaire échoue — `422 Invalid
attachment object`, ou `413` si le fichier est gros.

Restait à savoir *où* héberger le PDF. Plutôt que d'ajouter un stockage et un jeton à un service
qui n'a aucun secret, l'URL pointe vers **ce même service** et porte le journal compressé
(zlib + base64url). Le rendu étant déterministe, le `GET` reproduit le fichier à l'octet près.
Aucun état, aucune expiration, aucun ménage à faire.

En pratique deux évaluations tiennent dans une URL de ~300 caractères. Au-delà de 8 000
caractères encodés, le service refuse en `422` plutôt que de fabriquer une URL que personne
n'acceptera.

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

## Page de garde et pied de page

Deux natures d'information, séparées parce qu'elles ne changent pas au même rythme.

**Constantes du titre** — `ecf/libelles_<CODE>.json` : dates d'arrêté, de J.O., d'effet, et les
colonnes « Date JO » / « Date de mise à jour » répétées sur les 10 pages. Identiques pour tous
les candidats, donc versionnées avec le template. **Un champ vide n'écrit rien** : jamais de
texte de remplissage sur un document soumis à jury.

**Informations du candidat** — un bloc `Candidat` dans le journal, comme les évaluations :

```
Candidat :
Civilité : Mme
Nom : MARTÍNEZ
Prénom : Sofia
Date de naissance : 14/03/1998
Organisme de formation : each One
Lieu de formation : Paris 18e
----------
```

La civilité coche la case correspondante ; une valeur autre que Mme ou M. est refusée plutôt
que devinée. Le bloc peut arriver partiellement rempli : seuls les champs fournis sont écrits.

## Corriger

Le journal est l'original, le PDF n'en est qu'une projection : on corrige le journal, jamais le
document.

**Une évaluation se corrige en la renvoyant sur sa ligne.** Le bloc porte un champ
`Ligne : 1..5` : c'est l'auteur qui désigne sa place dans le tableau. Renvoyer une évaluation
sur une ligne déjà occupée la **remplace**. Les trous sont permis — choisir la ligne 3 quand 1
et 2 sont vides est un choix, pas une erreur. Signalé par `X-ECF-Lignes-Remplacees`.

Les évaluations enregistrées avant ce champ n'ont pas de numéro : elles reçoivent leur place
d'après leur ordre chronologique, puis les blocs numérotés écrasent celle qu'ils réclament.

**L'avis final se refait.** Il n'y en a qu'un par activité-type : si l'appelant en renvoie un
second, il **remplace** le précédent. C'est l'ordre d'arrivée qui tranche, pas la date saisie —
un avis refait le jour même porterait la même date. Le remplacement est signalé par l'en-tête
`X-ECF-Avis-Remplaces` et dans les logs.

⚠️ **Ne pas rejouer un appel ancien** si l'appelant compose son journal avec l'existant : il
réinjecterait la version périmée à côté de la corrigée. Pour régénérer, renvoyer le journal
courant seul.

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
