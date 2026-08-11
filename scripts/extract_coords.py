"""
Dérive la géométrie du tableau d'évaluations d'un livret ECF.

Rien n'est codé en dur : le script *découvre* dans le PDF le nombre
d'activités-types, le nombre de compétences de chacune, les pages concernées,
le nombre de lignes par tableau et la position de chaque case. Un livret d'un
autre titre professionnel passe donc sans modification, tant qu'il sort du même
modèle Word du ministère (MODELE_ECF_LE.docm).

    python extract_coords.py livret.pdf coords.json

One-shot : le résultat est committé, le service n'utilise pas pdfplumber au
runtime. En cas de mise en page inattendue le script s'arrête net avec un
message — il ne devine jamais.

Repère de sortie : celui de reportlab (origine en bas à gauche).
"""
import hashlib
import json
import re
import sys
from collections import defaultdict

import pdfplumber

RE_ACTIVITE = re.compile(r"Activit[ée]-type\s+(\d+)")
RE_COMPLEMENTAIRE = re.compile(r"^[EÉ]valuations\s+compl[ée]mentaires", re.I)
RE_COMPETENCE = re.compile(r"^(\d+)\.\s+(.+?)\s*$")
RE_ENTETE_TABLEAU = re.compile(r"Description des [ée]valuations", re.I)

# Avis final. « Ne pas avoir satisfait… » contient « avoir satisfait… » : la
# négation doit être testée en premier, d'où l'alternance ordonnée.
RE_SATISFAIT = re.compile(r"^☐?\s*(Ne pas avoir|Avoir)\s+satisfait", re.I)
RE_FORMATEURS = re.compile(r"^Formateur\(s\)", re.I)
BLOCS_TEXTE_AVIS = (
    ("points_attention", re.compile(r"^Si le candidat", re.I), 150.0),
    ("a_reevaluer", re.compile(r"^Comp[ée]tences\s+à\s+r[ée][ée]valuer", re.I), 240.0),
)

PAD = 3.0  # marge intérieure des cellules texte, en points


def cluster(values, tol):
    """Regroupe des scalaires proches ; renvoie la moyenne de chaque grappe."""
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def lire_activite(page):
    """-> (numero, intitule, {n: libelle}) ou None si la page n'est pas une fiche."""
    texte = page.extract_text() or ""
    m = RE_ACTIVITE.search(texte)
    if not m:
        return None

    lignes = [l.strip() for l in texte.split("\n")]
    # L'intitulé suit « Activité-type N » sur la même ligne, et déborde parfois
    # sur la suivante.
    i = next(i for i, l in enumerate(lignes) if RE_ACTIVITE.search(l))
    intitule = RE_ACTIVITE.sub("", lignes[i]).strip()
    if i + 1 < len(lignes) and not lignes[i + 1].startswith("Compétences"):
        intitule = f"{intitule} {lignes[i + 1].strip()}".strip()

    # Les compétences sont numérotées entre « Compétences : » et l'en-tête du
    # tableau. S'arrêter à l'en-tête évite de confondre avec les numéros de ligne.
    competences = {}
    dans_bloc = False
    for ligne in lignes:
        if ligne.startswith("Compétences"):
            dans_bloc = True
            continue
        if dans_bloc:
            if RE_ENTETE_TABLEAU.search(ligne):
                break
            mc = RE_COMPETENCE.match(ligne)
            if mc:
                competences[int(mc.group(1))] = mc.group(2)

    if not competences:
        raise SystemExit(f"page {page.page_number} : aucune compétence numérotée trouvée")
    attendu = list(range(1, len(competences) + 1))
    if sorted(competences) != attendu:
        raise SystemExit(
            f"page {page.page_number} : compétences {sorted(competences)}, "
            f"numérotation continue attendue"
        )
    return int(m.group(1)), intitule, competences


def extraire_tableau(page, nb_competences, etiquette):
    """Géométrie du tableau d'évaluations d'une page. Nombre de lignes déduit."""
    hauteur = page.height
    boites = [c for c in page.chars if c["text"] == "☐"]
    if not boites:
        raise SystemExit(f"{etiquette} : aucune case à cocher")

    # Les cases de la grille sont les seules alignées sur 3 colonnes à droite ;
    # celles des mentions « Avoir satisfait / Ne pas avoir satisfait » sont
    # isolées dans la marge gauche. On garde les 3 abscisses les plus peuplées.
    par_x = defaultdict(list)
    for b in boites:
        par_x[round(b["x0"], 1)].append(b)
    if len(par_x) < 3:
        raise SystemExit(f"{etiquette} : grille introuvable ({len(par_x)} abscisses)")
    colonnes_x = sorted(sorted(par_x, key=lambda x: -len(par_x[x]))[:3])
    grille = [b for b in boites if round(b["x0"], 1) in colonnes_x]

    if len(grille) % 9:
        raise SystemExit(f"{etiquette} : {len(grille)} cases, multiple de 9 attendu")
    nb_lignes = len(grille) // 9

    # Filets horizontaux traversant le tableau. x0 > 40 écarte le pied de page,
    # qui déborde à gauche.
    filets_h = sorted(
        {round(e["top"], 1) for e in page.edges
         if e["orientation"] == "h" and 40 < e["x0"] < 60 and e["x1"] > 530}
    )
    haut_grille = min(b["top"] for b in grille)
    bas_grille = max(b["bottom"] for b in grille)
    au_dessus = [y for y in filets_h if y < haut_grille]
    if not au_dessus:
        raise SystemExit(f"{etiquette} : aucun filet au-dessus de la grille")
    depart = filets_h.index(au_dessus[-1])
    bornes = filets_h[depart:depart + nb_lignes + 1]
    if len(bornes) != nb_lignes + 1 or bornes[-1] < bas_grille:
        raise SystemExit(f"{etiquette} : bornes de lignes incohérentes ({bornes})")

    # Filets verticaux internes au tableau : ils commencent près de son sommet
    # et descendent jusqu'en bas. Le cadre de page est écarté par x0 > 40.
    filets_v = sorted(cluster(
        [round(e["x0"], 1) for e in page.edges
         if e["orientation"] == "v" and e["x0"] > 40
         and 0 <= bornes[0] - e["top"] < 60 and e["bottom"] > bornes[-1] - 1],
        2.0,
    ))
    if len(filets_v) < 3:
        raise SystemExit(f"{etiquette} : {len(filets_v)} filets verticaux, 3 attendus")
    x_gauche, x_desc_droite, x_date_droite = filets_v[0], filets_v[1], filets_v[2]

    lignes = []
    for i in range(nb_lignes):
        haut, bas = bornes[i], bornes[i + 1]
        dans_ligne = [b for b in grille if haut < b["top"] < bas]
        if len(dans_ligne) != 9:
            raise SystemExit(f"{etiquette} ligne {i + 1} : {len(dans_ligne)} cases, 9 attendues")

        sous_lignes = cluster([b["top"] for b in dans_ligne], 5.0)
        if len(sous_lignes) != 3:
            raise SystemExit(f"{etiquette} ligne {i + 1} : {len(sous_lignes)} sous-lignes")

        # Numérotation imprimée : colonne * 3 + sous-ligne + 1 (1,4,7 / 2,5,8 / 3,6,9)
        cases = {}
        for b in dans_ligne:
            col = min(range(3), key=lambda c: abs(b["x0"] - colonnes_x[c]))
            sous = min(range(3), key=lambda s: abs(b["top"] - sous_lignes[s]))
            cases[col * 3 + sous + 1] = [
                round((b["x0"] + b["x1"]) / 2, 2),
                round(hauteur - (b["top"] + b["bottom"]) / 2, 2),
            ]

        manquantes = [n for n in range(1, nb_competences + 1) if n not in cases]
        if manquantes:
            raise SystemExit(f"{etiquette} ligne {i + 1} : cases {manquantes} introuvables")

        # Le numéro de ligne est imprimé en haut à gauche de la cellule
        # description : on démarre le texte à sa droite pour ne pas l'écraser.
        numeros = [
            ch for ch in page.chars
            if ch["text"].isdigit() and haut < ch["top"] < bas
            and x_gauche < ch["x0"] < x_gauche + 40
        ]
        x_texte = (max(ch["x1"] for ch in numeros) + 5.0) if numeros else (x_gauche + PAD)

        y_bas, y_haut = hauteur - (bas - PAD), hauteur - (haut + PAD)
        lignes.append({
            "desc": [round(x_texte, 2), round(y_bas, 2),
                     round(x_desc_droite - PAD, 2), round(y_haut, 2)],
            "date": [round(x_desc_droite + PAD, 2), round(y_bas, 2),
                     round(x_date_droite - PAD, 2), round(y_haut, 2)],
            "cases": {str(n): cases[n] for n in range(1, nb_competences + 1)},
        })
    return lignes


def _filets_larges(page, au_dessus_de=0.0):
    return sorted(
        {round(e["top"], 1) for e in page.edges
         if e["orientation"] == "h" and e["x0"] < 50 and (e["x1"] - e["x0"]) > 150
         and e["top"] > au_dessus_de}
    )


def _cadre_sous(page, ancre_top, hauteur_max, etiquette, quoi):
    """Cadre de saisie ouvert par le filet situé juste sous une ancre textuelle."""
    filets = _filets_larges(page, ancre_top)
    if not filets:
        raise SystemExit(f"{etiquette} : aucun filet sous « {quoi} »")
    haut = filets[0]

    # Le cadre est fermé par ses montants verticaux : ce sont eux qui donnent
    # sa hauteur réelle et sa largeur, pas le filet horizontal suivant.
    montants = [e for e in page.edges
                if e["orientation"] == "v" and 30 < e["x0"] < 570
                and abs(e["top"] - haut) < 6 and (e["bottom"] - e["top"]) > 30]
    if len(montants) < 2:
        raise SystemExit(f"{etiquette} : montants du cadre « {quoi} » introuvables")

    bas = min(max(e["bottom"] for e in montants), haut + hauteur_max)
    cotes = sorted({round(e["x0"], 1) for e in montants})
    return haut, bas, cotes[0], cotes[-1]


def extraire_avis(pages, page_fiche, page_suite, etiquette):
    """Zones de l'avis final : cases résultat, textes libres, tableau formateurs.

    Le résultat de l'activité 2 est à cheval sur deux pages (« Avoir satisfait »
    en bas de la fiche, « Ne pas avoir satisfait » en haut de la suivante) : on
    cherche donc chaque case sur les deux pages plutôt que de le supposer.
    """
    avis = {"resultat": {}}

    for idx in (page_fiche, page_suite):
        page = pages[idx]
        hauteur = page.height
        lignes = page.extract_text_lines()
        for case in [c for c in page.chars if c["text"] == "☐" and c["x0"] < 100]:
            proche = [l for l in lignes if l["top"] - 4 <= case["top"] <= l["bottom"] + 4]
            if not proche:
                continue
            libelle = proche[0]["text"].lstrip("☐ ").strip()
            m = RE_SATISFAIT.match(libelle)
            if not m:
                continue
            cle = "non_satisfait" if m.group(1).lower().startswith("ne pas") else "satisfait"
            avis["resultat"][cle] = {
                "page": idx,
                "centre": [round((case["x0"] + case["x1"]) / 2, 2),
                           round(hauteur - (case["top"] + case["bottom"]) / 2, 2)],
                "libelle": libelle,
            }

    manquantes = [c for c in ("satisfait", "non_satisfait") if c not in avis["resultat"]]
    if manquantes:
        raise SystemExit(f"{etiquette} : cases résultat {manquantes} introuvables")

    # Textes libres et tableau formateurs vivent sur la page qui suit la fiche.
    page = pages[page_suite]
    hauteur = page.height
    lignes = page.extract_text_lines()

    for cle, motif, hauteur_max in BLOCS_TEXTE_AVIS:
        ancre = next((l for l in lignes if motif.match(l["text"].strip())), None)
        if ancre is None:
            raise SystemExit(f"{etiquette} : bloc « {cle} » introuvable")
        haut, bas, x0, x1 = _cadre_sous(page, ancre["top"], hauteur_max, etiquette, cle)
        avis[cle] = {
            "page": page_suite,
            "cadre": [round(x0 + PAD, 2), round(hauteur - (bas - PAD), 2),
                      round(x1 - PAD, 2), round(hauteur - (haut + PAD), 2)],
        }

    ancre = next((l for l in lignes if RE_FORMATEURS.match(l["text"].strip())), None)
    if ancre is None:
        raise SystemExit(f"{etiquette} : tableau formateurs introuvable")
    filets_h = _filets_larges(page, ancre["top"])[:3]
    if len(filets_h) < 3:
        raise SystemExit(f"{etiquette} : tableau formateurs incomplet ({filets_h})")
    colonnes = sorted(
        {round(e["x0"], 1) for e in page.edges
         if e["orientation"] == "v" and 30 < e["x0"] < 570
         and e["top"] <= filets_h[0] + 5 and e["bottom"] >= filets_h[-1] - 5}
    )
    if len(colonnes) < 4:
        raise SystemExit(f"{etiquette} : colonnes du tableau formateurs : {colonnes}")

    mots = page.extract_words()

    def apres_libelle(x_gauche, x_droite, haut, bas):
        """Démarre le texte à droite du « Nom » / « Date » imprimé dans la cellule."""
        dedans = [w for w in mots
                  if x_gauche <= w["x0"] < x_droite and haut <= w["top"] < bas]
        return (max(w["x1"] for w in dedans) + 6.0) if dedans else (x_gauche + PAD)

    avis["formateurs"] = []
    for i in range(2):
        haut, bas = filets_h[i], filets_h[i + 1]
        y_bas, y_haut = hauteur - (bas - PAD), hauteur - (haut + PAD)
        avis["formateurs"].append({
            "page": page_suite,
            "nom": [round(apres_libelle(colonnes[0], colonnes[1], haut, bas), 2),
                    round(y_bas, 2), round(colonnes[1] - PAD, 2), round(y_haut, 2)],
            "date": [round(apres_libelle(colonnes[1], colonnes[2], haut, bas), 2),
                     round(y_bas, 2), round(colonnes[2] - PAD, 2), round(y_haut, 2)],
            "visa": [round(colonnes[2] + PAD, 2), round(y_bas, 2),
                     round(colonnes[3] - PAD, 2), round(y_haut, 2)],
        })
    return avis


def main(chemin_pdf, chemin_sortie):
    with open(chemin_pdf, "rb") as f:
        empreinte = hashlib.sha256(f.read()).hexdigest()

    activites = {}
    with pdfplumber.open(chemin_pdf) as pdf:
        pages = pdf.pages
        fiches = []
        for i, page in enumerate(pages):
            lu = lire_activite(page)
            if lu:
                fiches.append((i, lu))
        if not fiches:
            raise SystemExit("aucune fiche « Activité-type N » trouvée")

        for rang, (idx, (numero, intitule, competences)) in enumerate(fiches):
            nb = len(competences)
            blocs = [{
                "type": "principale",
                "page": idx,
                "lignes": extraire_tableau(pages[idx], nb, f"activité {numero} (p.{idx + 1})"),
            }]

            # Pages « Évaluations complémentaires » situées avant la fiche suivante.
            # Le marqueur doit être le TITRE de la page : la fiche de résultats
            # renvoie elle-même « voir évaluations complémentaires ci-après ».
            fin = fiches[rang + 1][0] if rang + 1 < len(fiches) else len(pages)
            for j in range(idx + 1, fin):
                lignes_page = [
                    l.strip() for l in (pages[j].extract_text() or "").split("\n") if l.strip()
                ]
                if lignes_page and RE_COMPLEMENTAIRE.match(lignes_page[0]):
                    blocs.append({
                        "type": "complementaire",
                        "page": j,
                        "lignes": extraire_tableau(
                            pages[j], nb, f"activité {numero} compl. (p.{j + 1})"
                        ),
                    })

            activites[str(numero)] = {
                "intitule": intitule,
                "competences": {str(k): v for k, v in sorted(competences.items())},
                "blocs": blocs,
                "avis": extraire_avis(
                    pages, idx, idx + 1, f"activité {numero} avis (p.{idx + 2})"
                ),
            }

        donnees = {
            "template_sha256": empreinte,
            "page_size": [round(pages[0].width, 2), round(pages[0].height, 2)],
            "activites": activites,
        }

    with open(chemin_sortie, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)

    print(f"OK — {chemin_sortie} écrit (template sha256 {empreinte[:12]}…)")
    for num, act in sorted(activites.items(), key=lambda kv: int(kv[0])):
        detail = ", ".join(
            f"{b['type'][:5]} p.{b['page'] + 1} = {len(b['lignes'])} lignes" for b in act["blocs"]
        )
        capacite = sum(len(b["lignes"]) for b in act["blocs"])
        print(f"  activité {num} : {len(act['competences'])} compétences | "
              f"{detail} | capacité {capacite}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
