"""
Pipelines, hypothèses métier de la campagne de rétention, seuil et explications.
Partagé par l'entraînement (train.py) et le scoring (predict.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .data import CATEGORICAL_FEATURES, NUMERIC_FEATURES

SEED = 42

# ─────────────────────────────────────────────────────────────
# Hypothèses de la campagne de rétention (à adapter à l'entreprise)
# ─────────────────────────────────────────────────────────────
COUT_OFFRE = 50.0  # coût d'une offre de rétention par client contacté (remise, appel…), en $
TAUX_SUCCES = 0.30  # part des clients sur le départ qui restent grâce à l'offre
MOIS_VALEUR = 12  # valeur d'un client conservé = 12 mois de facture


def preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("num", StandardScaler(), NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
        ],
        verbose_feature_names_out=False,
    )


def candidate_models() -> dict[str, Pipeline]:
    return {
        "Régression logistique": Pipeline([
            ("prep", preprocessor()),
            ("model", LogisticRegression(C=0.5, max_iter=3000)),
        ]),
        "Forêt aléatoire": Pipeline([
            ("prep", preprocessor()),
            ("model", RandomForestClassifier(n_estimators=400, min_samples_leaf=10, max_features="sqrt",
                                             random_state=SEED, n_jobs=-1)),
        ]),
        "Gradient boosting": Pipeline([
            ("prep", preprocessor()),
            ("model", HistGradientBoostingClassifier(learning_rate=0.05, max_iter=300, max_depth=4,
                                                     l2_regularization=1.0, random_state=SEED)),
        ]),
    }


# ─────────────────────────────────────────────────────────────
# Valeur de la campagne et choix du seuil
# ─────────────────────────────────────────────────────────────
def campaign_profit(y_true, proba, monthly, threshold: float) -> dict:
    """
    On contacte les clients dont la probabilité de départ est >= seuil.
    Gain  : pour chaque client contacté qui allait partir, TAUX_SUCCES × 12 mois de facture sauvés.
    Coût  : COUT_OFFRE pour chaque client contacté (qu'il allait partir ou non).
    """
    y_true, proba, monthly = map(np.asarray, (y_true, proba, monthly))
    target = proba >= threshold
    saved = (TAUX_SUCCES * MOIS_VALEUR * monthly * (target & (y_true == 1))).sum()
    cost = COUT_OFFRE * target.sum()
    return {
        "seuil": float(threshold),
        "clients_cibles": int(target.sum()),
        "churners_cibles": int((target & (y_true == 1)).sum()),
        "gain": float(saved),
        "cout": float(cost),
        "profit": float(saved - cost),
    }


def profit_curve(y_true, proba, monthly) -> pd.DataFrame:
    grid = np.round(np.arange(0.0, 1.0, 0.01), 2)
    return pd.DataFrame([campaign_profit(y_true, proba, monthly, t) for t in grid])


def best_threshold(y_true, proba, monthly) -> tuple[float, pd.DataFrame]:
    curve = profit_curve(y_true, proba, monthly)
    return float(curve.loc[curve["profit"].idxmax(), "seuil"]), curve


def gain_curve(y_true, proba) -> pd.DataFrame:
    """Courbe de gain : en contactant les X % de clients les plus risqués, quelle part des départs capte-t-on ?"""
    order = np.argsort(-np.asarray(proba))
    y_sorted = np.asarray(y_true)[order]
    n, total = len(y_sorted), y_sorted.sum()
    pct = np.arange(1, n + 1) / n
    captured = np.cumsum(y_sorted) / total
    idx = np.unique(np.clip((np.linspace(0.01, 1, 100) * n).astype(int) - 1, 0, n - 1))
    return pd.DataFrame({"part_clients_contactes": pct[idx], "part_departs_captes": captured[idx]})


def lift_at(y_true, proba, share: float) -> float:
    """Lift : le top X % des clients contient combien de fois plus de départs que la moyenne ?"""
    order = np.argsort(-np.asarray(proba))
    k = max(1, int(len(order) * share))
    return float(np.asarray(y_true)[order[:k]].mean() / np.asarray(y_true).mean())


# ─────────────────────────────────────────────────────────────
# Segments et explications
# ─────────────────────────────────────────────────────────────
def risk_segment(p: float) -> str:
    if p >= 0.5:
        return "Élevé"
    if p >= 0.2:
        return "Moyen"
    return "Faible"


def reference_profile(X: pd.DataFrame) -> dict:
    """Le "client type" : médiane des variables numériques, valeur la plus fréquente des catégorielles."""
    ref = {c: float(X[c].median()) for c in NUMERIC_FEATURES}
    ref.update({c: X[c].mode().iloc[0] for c in CATEGORICAL_FEATURES})
    return ref


def _group_of(name: str) -> str:
    """Nom de colonne après one-hot (ex. "contrat_Mensuel") → variable d'origine ("contrat")."""
    for col in sorted(CATEGORICAL_FEATURES, key=len, reverse=True):
        if name.startswith(col + "_"):
            return col
    return name


def contributions(pipeline: Pipeline, X: pd.DataFrame, X_background: pd.DataFrame) -> pd.DataFrame:
    """
    Contribution de chaque variable au risque, en log-odds, par rapport au client MOYEN.
    Pour une régression logistique, c'est exactement la valeur SHAP linéaire :
        contribution(j) = Σ coef_k × (z_k − moyenne(z_k))   sur les colonnes k issues de la variable j
    > 0 : la situation du client sur cette variable AUGMENTE son risque de départ.
    """
    prep, lr = pipeline.named_steps["prep"], pipeline.named_steps["model"]
    names = prep.get_feature_names_out()
    Z, Zb = prep.transform(X), prep.transform(X_background)
    contrib = (Z - Zb.mean(axis=0)) * lr.coef_[0]
    df = pd.DataFrame(contrib, columns=names, index=X.index)
    return df.T.groupby(_group_of).sum().T


def explain_many(pipeline: Pipeline, X: pd.DataFrame, X_background: pd.DataFrame, top: int = 2) -> pd.DataFrame:
    """Les `top` variables qui augmentent le plus le risque de chaque client, formulées en français."""
    contrib = contributions(pipeline, X, X_background)
    out = []
    for idx, row in contrib.iterrows():
        best = row[row > 0.05].sort_values(ascending=False).head(top)  # on ignore les effets négligeables
        reasons = [f"{LABELS[col]} : {fmt(col, X.at[idx, col])}" for col in best.index]
        out.append(reasons + [""] * (top - len(reasons)))
    return pd.DataFrame(out, columns=[f"motif_{k + 1}" for k in range(top)], index=X.index)


LABELS = {
    "anciennete_mois": "Ancienneté",
    "facture_mensuelle": "Facture mensuelle",
    "total_facture": "Total facturé",
    "nb_services": "Services en option",
    "senior": "Senior",
    "en_couple": "En couple",
    "personnes_a_charge": "Personnes à charge",
    "telephone": "Téléphone",
    "lignes_multiples": "Lignes multiples",
    "internet": "Internet",
    "securite_en_ligne": "Sécurité en ligne",
    "sauvegarde_en_ligne": "Sauvegarde en ligne",
    "protection_appareil": "Protection de l'appareil",
    "support_technique": "Support technique",
    "streaming_tv": "Streaming TV",
    "streaming_films": "Streaming films",
    "contrat": "Contrat",
    "facture_dematerialisee": "Facture dématérialisée",
    "moyen_paiement": "Paiement",
}


def fmt(col: str, value) -> str:
    if col == "anciennete_mois":
        return f"{int(value)} mois"
    if col in ("facture_mensuelle", "total_facture"):
        return f"{value:.0f} $"
    if col == "nb_services":
        return str(int(value))
    return str(value)
