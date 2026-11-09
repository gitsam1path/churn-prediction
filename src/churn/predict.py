"""
Scorer de nouveaux clients avec le modèle entraîné.

Le fichier d'entrée a le même format que les données d'origine (colonnes en anglais, valeurs Yes/No…).
Lancer :  cd src && python -m churn.predict ../data/raw/telco_churn.csv ../reports/scores.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .data import ROOT, clean
from .model import COUT_OFFRE, MOIS_VALEUR, TAUX_SUCCES, explain_many, risk_segment

MODEL_PATH = ROOT / "models" / "model.joblib"


def score(raw: pd.DataFrame, model_path: Path = MODEL_PATH) -> pd.DataFrame:
    bundle = joblib.load(model_path)
    df = clean(raw.assign(Churn=raw.get("Churn", "No")))  # la colonne Churn est facultative pour de nouveaux clients
    X = df[bundle["features"]]
    p = bundle["pipeline"].predict_proba(X)[:, 1]
    out = pd.DataFrame({
        "id_client": df["id_client"],
        "probabilite_churn": p.round(4),
        "segment_risque": [risk_segment(v) for v in p],
        "a_contacter": np.where(p >= bundle["seuil"], "Oui", "Non"),
        "gain_attendu": (p * TAUX_SUCCES * MOIS_VALEUR * df["facture_mensuelle"] - COUT_OFFRE).round(2),
    })
    return out.join(explain_many(bundle["pipeline"], X, bundle["background"])).sort_values(
        "probabilite_churn", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scorer des clients (risque de départ et motifs).")
    parser.add_argument("entree", type=Path, help="CSV des clients, au format des données d'origine")
    parser.add_argument("sortie", type=Path, help="CSV de sortie")
    args = parser.parse_args()
    result = score(pd.read_csv(args.entree))
    result.to_csv(args.sortie, index=False, encoding="utf-8")
    print(f"{len(result)} clients scorés · {(result['a_contacter'] == 'Oui').sum()} à contacter → {args.sortie}")


if __name__ == "__main__":
    main()
