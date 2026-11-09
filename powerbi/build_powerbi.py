"""
Génère le projet Power BI « churn_dashboard » au format Power BI Project (.pbip).

Un projet .pbip est le format texte officiel de Power BI Desktop :
  churn_dashboard.pbip                      le fichier à ouvrir (double-clic)
  churn_dashboard.SemanticModel/model.bim   le modèle : tables, requêtes Power Query, mesures DAX
  churn_dashboard.Report/report.json        le rapport : pages et visuels

Les données (CSV de powerbi/data) sont intégrées dans les requêtes Power Query :
le rapport ne dépend d'aucun chemin de fichier.

Pour obtenir un modèle .pbit : ouvrir le .pbip, actualiser, puis
Fichier > Exporter > Modèle Power BI.

Lancer :  python powerbi/build_pbip.py
"""

from __future__ import annotations

import base64
import json
import zlib
import shutil
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "churn_dashboard"
DATA_DIR = HERE / "data"


def uid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────
# 1. Modèle de données
# ─────────────────────────────────────────────────────────────
# (nom de colonne, type Power BI, type Power Query, agrégation par défaut, format)
TABLES = {
    "clients": [
        ("id_client", "string", "type text", "none", None),
        ("genre", "string", "type text", "none", None),
        ("senior", "string", "type text", "none", None),
        ("en_couple", "string", "type text", "none", None),
        ("personnes_a_charge", "string", "type text", "none", None),
        ("anciennete_mois", "int64", "Int64.Type", "none", "0"),
        ("tranche_anciennete", "string", "type text", "none", None),
        ("tranche_ordre", "int64", "Int64.Type", "none", "0"),
        ("contrat", "string", "type text", "none", None),
        ("internet", "string", "type text", "none", None),
        ("moyen_paiement", "string", "type text", "none", None),
        ("facture_dematerialisee", "string", "type text", "none", None),
        ("nb_services", "int64", "Int64.Type", "none", "0"),
        ("support_technique", "string", "type text", "none", None),
        ("securite_en_ligne", "string", "type text", "none", None),
        ("facture_mensuelle", "double", "type number", "sum", "#,0.00 $"),
        ("total_facture", "double", "type number", "sum", "#,0 $"),
        ("valeur_annuelle", "double", "type number", "sum", "#,0 $"),
        ("churn", "int64", "Int64.Type", "sum", "0"),
        ("churn_reel", "string", "type text", "none", None),
        ("probabilite_churn", "double", "type number", "none", "0.0 %"),
        ("segment_risque", "string", "type text", "none", None),
        ("cible_campagne", "string", "type text", "none", None),
        ("gain_attendu", "double", "type number", "sum", "#,0 $"),
        ("motif_1", "string", "type text", "none", None),
        ("motif_2", "string", "type text", "none", None),
    ],
    "comparaison_modeles": [
        ("modele", "string", "type text", "none", None),
        ("roc_auc", "double", "type number", "none", "0.000"),
        ("roc_auc_ecart_type", "double", "type number", "none", "0.000"),
        ("pr_auc", "double", "type number", "none", "0.000"),
        ("profit_cv", "double", "type number", "none", "#,0 $"),
        ("retenu", "boolean", "type logical", "none", None),
    ],
    "importance_variables": [
        ("variable", "string", "type text", "none", None),
        ("importance", "double", "type number", "sum", "0.000"),
        ("libelle", "string", "type text", "none", None),
    ],
    "courbe_profit": [
        ("seuil", "double", "type number", "none", "0.00"),
        ("clients_cibles", "int64", "Int64.Type", "sum", "#,0"),
        ("churners_cibles", "int64", "Int64.Type", "sum", "#,0"),
        ("gain", "double", "type number", "sum", "#,0 $"),
        ("cout", "double", "type number", "sum", "#,0 $"),
        ("profit", "double", "type number", "sum", "#,0 $"),
    ],
    "courbe_gain": [
        ("part_clients_contactes", "double", "type number", "none", "0 %"),
        ("part_departs_captes", "double", "type number", "sum", "0 %"),
        ("part_aleatoire", "double", "type number", "sum", "0 %"),
    ],
    "parametres": [
        ("seuil_campagne", "double", "type number", "sum", "0.00"),
        ("cout_offre", "double", "type number", "sum", "#,0 $"),
        ("taux_succes", "double", "type number", "sum", "0 %"),
        ("mois_valeur", "int64", "Int64.Type", "sum", "0"),
    ],
}

SORT_BY = {("clients", "tranche_anciennete"): "tranche_ordre"}
HIDDEN = {("clients", "tranche_ordre")}

# Mesures DAX (table, nom, expression, format)
MEASURES = [
    ("clients", "Nb clients", "COUNTROWS ( clients )", "#,0"),
    ("clients", "Nb départs", "SUM ( clients[churn] )", "#,0"),
    ("clients", "Taux de churn", "DIVIDE ( [Nb départs], [Nb clients] )", "0.0 %"),
    ("clients", "Revenu mensuel", "SUM ( clients[facture_mensuelle] )", "#,0 $"),
    ("clients", "Revenu mensuel perdu", "CALCULATE ( SUM ( clients[facture_mensuelle] ), clients[churn] = 1 )", "#,0 $"),
    ("clients", "Part du revenu perdu", "DIVIDE ( [Revenu mensuel perdu], [Revenu mensuel] )", "0.0 %"),
    ("clients", "Probabilité moyenne", "AVERAGE ( clients[probabilite_churn] )", "0.0 %"),
    ("clients", "Clients à risque élevé", "CALCULATE ( [Nb clients], clients[segment_risque] = \"Élevé\" )", "#,0"),
    ("clients", "Clients ciblés", "CALCULATE ( [Nb clients], clients[cible_campagne] = \"Oui\" )", "#,0"),
    ("clients", "Revenu mensuel à risque",
     "SUMX ( clients, clients[facture_mensuelle] * clients[probabilite_churn] )", "#,0 $"),
    ("clients", "Gain attendu de la campagne",
     "CALCULATE ( SUM ( clients[gain_attendu] ), clients[cible_campagne] = \"Oui\" )", "#,0 $"),
]


def embedded_csv(table: str) -> str:
    """Le CSV compressé (deflate) puis encodé en base64 : les données voyagent DANS le rapport."""
    raw = (DATA_DIR / f"{table}.csv").read_bytes()
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.b64encode(comp.compress(raw) + comp.flush()).decode("ascii")


def m_query(table: str, cols) -> list[str]:
    types = ", ".join(f'{{"{c}", {mtype}}}' for c, _, mtype, _, _ in cols)
    return [
        "let",
        # Données intégrées : aucun chemin de fichier, le rapport s'ouvre sur n'importe quel PC
        f'    Donnees = Binary.Decompress(Binary.FromText("{embedded_csv(table)}", BinaryEncoding.Base64), Compression.Deflate),',
        '    Source = Csv.Document(Donnees, [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        "    Entetes = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
        # "en-US" : les CSV utilisent le point décimal, quel que soit le réglage régional du PC
        f'    Types = Table.TransformColumnTypes(Entetes, {{{types}}}, "en-US")',
        "in",
        "    Types",
    ]


def data_model() -> dict:
    tables = []
    for name, cols in TABLES.items():
        columns = []
        for c, dtype, _, summarize, fmt in cols:
            col = {
                "name": c,
                "dataType": dtype,
                "sourceColumn": c,
                "lineageTag": uid(),
                "summarizeBy": summarize,
                "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
            }
            if fmt:
                col["formatString"] = fmt
            if (name, c) in SORT_BY:
                col["sortByColumn"] = SORT_BY[(name, c)]
            if (name, c) in HIDDEN:
                col["isHidden"] = True
            columns.append(col)
        table = {
            "name": name,
            "lineageTag": uid(),
            "columns": columns,
            "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": m_query(name, cols)}}],
            "annotations": [{"name": "PBI_ResultType", "value": "Table"}],
        }
        measures = [
            {"name": m, "expression": expr, "formatString": fmt, "lineageTag": uid()}
            for t, m, expr, fmt in MEASURES if t == name
        ]
        if measures:
            table["measures"] = measures
        tables.append(table)

    return {
        "name": uid(),
        "compatibilityLevel": 1550,
        "model": {
            "culture": "fr-FR",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "fr-FR",
            "tables": tables,
            "annotations": [
                {"name": "PBI_QueryOrder", "value": json.dumps([*TABLES])},
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "0"},
                # Version de Power BI Desktop « auteur » : volontairement ancienne (février 2023)
                # pour que toute version plus récente accepte d'ouvrir le fichier.
                {"name": "PBIDesktopVersion", "value": "2.114.322.0 (23.02)"},
            ],
        },
    }


# ─────────────────────────────────────────────────────────────
# 2. Rapport : pages et visuels
# ─────────────────────────────────────────────────────────────
ALIASES = {t: f"t{i}" for i, t in enumerate(TABLES)}
MEASURE_NAMES = {(t, m) for t, m, _, _ in MEASURES}


def lit(text: str) -> dict:
    return {"expr": {"Literal": {"Value": "'" + text.replace("'", "''") + "'"}}}


def true() -> dict:
    return {"expr": {"Literal": {"Value": "true"}}}


def field(table: str, name: str, agg: int | None = None) -> tuple[dict, str]:
    """Champ d'une requête de visuel. agg : 0 = somme ; None = colonne ou mesure telle quelle."""
    src = {"SourceRef": {"Source": ALIASES[table]}}
    if (table, name) in MEASURE_NAMES:
        return {"Measure": {"Expression": src, "Property": name}, "Name": f"{table}.{name}"}, f"{table}.{name}"
    col = {"Column": {"Expression": src, "Property": name}}
    if agg is None:
        return {**col, "Name": f"{table}.{name}", "NativeReferenceName": name}, f"{table}.{name}"
    ref = f"Sum({table}.{name})"
    return {"Aggregation": {"Expression": col, "Function": agg}, "Name": ref, "NativeReferenceName": name}, ref


class Page:
    def __init__(self, title: str):
        self.title = title
        self.visuals: list[dict] = []

    def add(self, vtype: str, x, y, w, h, title: str, roles: dict, order_by=None, objects=None, show_title=True):
        selects, projections, tables = [], {}, set()
        for role, items in roles.items():
            projections[role] = []
            for table, name, *agg in items:
                sel, ref = field(table, name, agg[0] if agg else None)
                if ref not in [s["Name"] for s in selects]:
                    selects.append(sel)
                projections[role].append({"queryRef": ref, **({"active": True} if role in ("Category", "Values") and vtype == "slicer" else {})})
                tables.add(table)
        query = {"Version": 2, "From": [{"Name": ALIASES[t], "Entity": t, "Type": 0} for t in sorted(tables)], "Select": selects}
        if order_by:
            table, name, direction = order_by
            sel, _ = field(table, name)
            expr = {k: v for k, v in sel.items() if k in ("Measure", "Column")}
            query["OrderBy"] = [{"Direction": direction, "Expression": expr}]
        vc_objects = {"title": [{"properties": {"show": true() if show_title else {"expr": {"Literal": {"Value": "false"}}},
                                                "text": lit(title)}}]}
        single = {
            "visualType": vtype,
            "projections": projections,
            "prototypeQuery": query,
            "drillFilterOtherVisuals": True,
            "vcObjects": vc_objects,
        }
        if objects:
            single["objects"] = objects
        name = uuid.uuid4().hex[:20]
        z = len(self.visuals) * 1000
        config = {"name": name, "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
                  "singleVisual": single}
        self.visuals.append({"x": x, "y": y, "z": z, "width": w, "height": h,
                             "config": json.dumps(config, ensure_ascii=False), "filters": "[]"})


LABELS_ON = {"labels": [{"properties": {"show": true()}}]}


def report_pages() -> list[Page]:
    # ── Page 1 : vue d'ensemble ──────────────────────────────────────
    p1 = Page("Vue d'ensemble")
    cards = [("Nb clients", "Clients"), ("Taux de churn", "Taux de churn"),
             ("Revenu mensuel perdu", "Revenu mensuel perdu"), ("Part du revenu perdu", "Part du revenu perdu")]
    for i, (m, t) in enumerate(cards):
        p1.add("card", 20 + i * 250, 20, 235, 110, t, {"Values": [("clients", m)]})
    p1.add("slicer", 1030, 20, 230, 110, "Contrat", {"Values": [("clients", "contrat")]})
    charts = [
        ("contrat", "Taux de churn par contrat", 20, 145, "clusteredColumnChart"),
        ("tranche_anciennete", "Taux de churn par ancienneté", 650, 145, "clusteredColumnChart"),
        ("moyen_paiement", "Taux de churn par moyen de paiement", 20, 430, "clusteredBarChart"),
        ("internet", "Taux de churn par type d'internet", 650, 430, "clusteredBarChart"),
    ]
    for col, title, x, y, vt in charts:
        p1.add(vt, x, y, 610, 270, title, {"Category": [("clients", col)], "Y": [("clients", "Taux de churn")]},
               objects=LABELS_ON)

    # ── Page 2 : clients à risque ────────────────────────────────────
    p2 = Page("Clients à risque")
    cards = [("Clients à risque élevé", "Clients à risque élevé"), ("Clients ciblés", "Clients à contacter"),
             ("Revenu mensuel à risque", "Revenu mensuel à risque"), ("Gain attendu de la campagne", "Gain attendu de la campagne")]
    for i, (m, t) in enumerate(cards):
        p2.add("card", 20 + i * 250, 20, 235, 110, t, {"Values": [("clients", m)]})
    p2.add("slicer", 1030, 20, 230, 110, "Segment de risque", {"Values": [("clients", "segment_risque")]})
    p2.add("donutChart", 20, 145, 380, 555, "Clients par segment de risque",
           {"Category": [("clients", "segment_risque")], "Y": [("clients", "Nb clients")]}, objects=LABELS_ON)
    p2.add("tableEx", 415, 145, 845, 555, "Clients à contacter en priorité",
           {"Values": [("clients", "id_client"), ("clients", "probabilite_churn"), ("clients", "segment_risque"),
                       ("clients", "contrat"), ("clients", "anciennete_mois"), ("clients", "facture_mensuelle", 0),
                       ("clients", "motif_1"), ("clients", "motif_2")]},
           order_by=("clients", "probabilite_churn", 2))

    # ── Page 3 : modèle et campagne ──────────────────────────────────
    p3 = Page("Modèle et campagne")
    p3.add("lineChart", 20, 20, 620, 330, "Profit de la campagne selon le seuil de ciblage",
           {"Category": [("courbe_profit", "seuil")], "Y": [("courbe_profit", "profit", 0)]})
    p3.add("lineChart", 655, 20, 605, 330, "Courbe de gain : part des départs captés",
           {"Category": [("courbe_gain", "part_clients_contactes")],
            "Y": [("courbe_gain", "part_departs_captes", 0), ("courbe_gain", "part_aleatoire", 0)]})
    p3.add("clusteredBarChart", 20, 365, 620, 335, "Ce qui prédit le départ (importance)",
           {"Category": [("importance_variables", "libelle")], "Y": [("importance_variables", "importance", 0)]},
           order_by=("importance_variables", "importance", 2))
    p3.add("tableEx", 655, 365, 605, 200, "Modèles comparés (validation croisée)",
           {"Values": [("comparaison_modeles", "modele"), ("comparaison_modeles", "roc_auc"),
                       ("comparaison_modeles", "pr_auc"), ("comparaison_modeles", "retenu")]})
    p3.add("card", 655, 580, 295, 120, "Seuil de ciblage", {"Values": [("parametres", "seuil_campagne", 0)]})
    p3.add("card", 965, 580, 295, 120, "Coût d'une offre", {"Values": [("parametres", "cout_offre", 0)]})
    return [p1, p2, p3]


def layout() -> dict:
    sections = []
    for i, page in enumerate(report_pages()):
        sections.append({
            "id": i,
            "name": f"ReportSection{uuid.uuid4().hex[:20]}",
            "displayName": page.title,
            "filters": "[]",
            "ordinal": i,
            "visualContainers": page.visuals,
            "config": "{}",
            "displayOption": 1,
            "width": 1280,
            "height": 720,
        })
    return {
        "id": 0,
        "resourcePackages": [],
        "sections": sections,
        "config": json.dumps({"version": "5.32", "themeCollection": {}, "activeSectionIndex": 0}),
        "layoutOptimization": 0,
    }




# ─────────────────────────────────────────────────────────────
# 3. Écriture du projet .pbip
# ─────────────────────────────────────────────────────────────
def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def _platform(kind: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": kind, "displayName": NAME},
        "config": {"version": "2.0", "logicalId": uid()},
    }


def build(root: Path = HERE) -> Path:
    model_dir = root / f"{NAME}.SemanticModel"
    report_dir = root / f"{NAME}.Report"
    for d in (model_dir, report_dir):
        if d.exists():
            shutil.rmtree(d)

    # Modèle sémantique : model.bim (format TMSL) + pointeur definition.pbism (version 1.0 = model.bim)
    _write(model_dir / "model.bim", data_model())
    _write(model_dir / "definition.pbism", {"version": "1.0", "settings": {}})
    _write(model_dir / ".platform", _platform("SemanticModel"))

    # Rapport : report.json (format classique) + pointeur definition.pbir (version 1.0 = report.json)
    _write(report_dir / "report.json", layout())
    _write(report_dir / "definition.pbir", {
        "version": "1.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}, "byConnection": None},
    })
    _write(report_dir / ".platform", _platform("Report"))

    pbip = root / f"{NAME}.pbip"
    _write(pbip, {"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                  "settings": {"enableAutoRecovery": True}})
    return pbip


if __name__ == "__main__":
    print(f"Projet Power BI créé : {build()}")
