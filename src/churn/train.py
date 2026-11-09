"""
Entraînement du modèle de churn et préparation des données du tableau de bord Power BI.

Lancer :  cd src && python -m churn.train

Étapes :
  1. Séparation apprentissage (80 %) / test (20 %), stratifiée.
  2. Comparaison de 3 modèles en validation croisée (ROC AUC et PR AUC).
  3. Choix du modèle : meilleur ROC AUC, sauf si la régression logistique est à moins de 0,01
     (on la préfère alors : plus simple, plus stable, plus facile à expliquer).
  4. Seuil de ciblage qui maximise le profit de la campagne de rétention (prédictions hors échantillon).
  5. Évaluation sur le test : AUC, rappel, lift, profit comparé à "contacter tout le monde".
  6. Scores de TOUS les clients par validation croisée (chaque client est noté par un modèle qui ne l'a
     jamais vu), motifs de départ, puis export des tables pour Power BI.
"""

from __future__ import annotations

import json
from datetime import date

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score, roc_curve
from sklearn.model_selection import StratifiedKFold, cross_val_predict, cross_validate, train_test_split

from .data import FEATURES, ROOT, TARGET, TENURE_LABELS, load_dataset, summary
from .model import (
    COUT_OFFRE, LABELS, MOIS_VALEUR, SEED, TAUX_SUCCES, best_threshold, campaign_profit, candidate_models,
    explain_many, gain_curve, lift_at, reference_profile, risk_segment,
)

MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"
FIG_DIR = REPORTS_DIR / "figures"
PBI_DIR = ROOT / "powerbi" / "data"
PREFERENCE_MARGIN = 0.01

plt.rcParams.update({"figure.dpi": 120, "axes.spines.top": False, "axes.spines.right": False, "font.size": 10})
COLORS = {"Régression logistique": "#2e86ab", "Forêt aléatoire": "#3d9970", "Gradient boosting": "#edae49"}


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> dict:
    for d in (MODELS_DIR, FIG_DIR, PBI_DIR):
        d.mkdir(parents=True, exist_ok=True)

    df = load_dataset()
    train_df, test_df = train_test_split(df, test_size=0.2, stratify=df[TARGET], random_state=SEED)
    X_train, y_train = train_df[FEATURES], train_df[TARGET]
    X_test, y_test = test_df[FEATURES], test_df[TARGET]
    log(f"{len(df)} clients · apprentissage {len(train_df)} · test {len(test_df)} · churn {df[TARGET].mean():.1%}")

    # ── 1. Comparaison des modèles ───────────────────────────────────
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    rows, oof = [], {}
    for name, pipe in candidate_models().items():
        res = cross_validate(pipe, X_train, y_train, cv=cv, scoring=["roc_auc", "average_precision"])
        oof[name] = cross_val_predict(pipe, X_train, y_train, cv=cv, method="predict_proba")[:, 1]
        thr, _ = best_threshold(y_train, oof[name], train_df["facture_mensuelle"])
        rows.append({
            "modele": name,
            "roc_auc": float(res["test_roc_auc"].mean()),
            "roc_auc_ecart_type": float(res["test_roc_auc"].std()),
            "pr_auc": float(res["test_average_precision"].mean()),
            "profit_cv": campaign_profit(y_train, oof[name], train_df["facture_mensuelle"], thr)["profit"],
        })
        log(f"  {name:22s} ROC AUC {rows[-1]['roc_auc']:.3f} ± {rows[-1]['roc_auc_ecart_type']:.3f} · PR AUC {rows[-1]['pr_auc']:.3f}")
    comp = pd.DataFrame(rows).sort_values("roc_auc", ascending=False).reset_index(drop=True)

    best = comp.iloc[0]
    lr_auc = comp.set_index("modele").loc["Régression logistique", "roc_auc"]
    chosen = "Régression logistique" if best["roc_auc"] - lr_auc <= PREFERENCE_MARGIN else best["modele"]
    if chosen != "Régression logistique":  # les motifs (valeurs SHAP linéaires) supposent un modèle linéaire
        raise SystemExit(f"{chosen} est nettement meilleur : adaptez explain_many (SHAP) avant de l'utiliser.")
    reason = "meilleur ROC AUC" if chosen == best["modele"] else f"à moins de {PREFERENCE_MARGIN} du meilleur, plus simple et explicable"
    comp["retenu"] = comp["modele"] == chosen
    log(f"Modèle retenu : {chosen} ({reason})")

    # ── 2. Seuil de ciblage ──────────────────────────────────────────
    threshold, _ = best_threshold(y_train, oof[chosen], train_df["facture_mensuelle"])
    log(f"Seuil de ciblage : {threshold:.2f}")

    # ── 3. Évaluation sur le test ────────────────────────────────────
    final = candidate_models()[chosen].fit(X_train, y_train)
    p_test = final.predict_proba(X_test)[:, 1]
    target = p_test >= threshold
    camp = campaign_profit(y_test, p_test, test_df["facture_mensuelle"], threshold)
    everyone = campaign_profit(y_test, p_test, test_df["facture_mensuelle"], -1)
    metrics = {
        "roc_auc": float(roc_auc_score(y_test, p_test)),
        "pr_auc": float(average_precision_score(y_test, p_test)),
        "seuil": threshold,
        "rappel": float(recall_score(y_test, target)),
        "precision": float(precision_score(y_test, target)),
        "part_clients_cibles": float(target.mean()),
        "lift_top10": lift_at(y_test, p_test, 0.10),
        "lift_top20": lift_at(y_test, p_test, 0.20),
        "profit_campagne": camp["profit"],
        "profit_tout_le_monde": everyone["profit"],
        "clients_cibles": camp["clients_cibles"],
    }
    log(f"Test : ROC AUC {metrics['roc_auc']:.3f} · rappel {metrics['rappel']:.0%} · précision {metrics['precision']:.0%} · "
        f"lift top 10 % {metrics['lift_top10']:.1f} · profit {metrics['profit_campagne']:,.0f} $ "
        f"(tout le monde : {metrics['profit_tout_le_monde']:,.0f} $)")

    # ── 4. Importance des variables ──────────────────────────────────
    perm = permutation_importance(final, X_test, y_test, scoring="roc_auc", n_repeats=10, random_state=SEED, n_jobs=-1)
    importance = (pd.DataFrame({"variable": FEATURES, "importance": perm.importances_mean})
                  .assign(libelle=lambda d: d["variable"].map(LABELS))
                  .sort_values("importance", ascending=False).reset_index(drop=True))

    # ── 5. Scores de tous les clients (hors échantillon) + motifs ────
    X_all, y_all = df[FEATURES], df[TARGET]
    cv10 = StratifiedKFold(n_splits=10, shuffle=True, random_state=SEED)
    p_all = cross_val_predict(candidate_models()[chosen], X_all, y_all, cv=cv10, method="predict_proba")[:, 1]
    reference = reference_profile(X_train)
    log("Calcul des motifs de départ pour chaque client…")
    reasons = explain_many(final, X_all, X_train)
    scored = df.assign(
        probabilite_churn=p_all.round(4),
        segment_risque=[risk_segment(p) for p in p_all],
        cible_campagne=np.where(p_all >= threshold, "Oui", "Non"),
        valeur_annuelle=(MOIS_VALEUR * df["facture_mensuelle"]).round(2),
        gain_attendu=(p_all * TAUX_SUCCES * MOIS_VALEUR * df["facture_mensuelle"] - COUT_OFFRE).round(2),
        churn_reel=np.where(df[TARGET] == 1, "Parti", "Resté"),
        tranche_ordre=df["tranche_anciennete"].map({t: i for i, t in enumerate(TENURE_LABELS)}),
    ).join(reasons)

    # ── 6. Exports ───────────────────────────────────────────────────
    export_powerbi(scored, comp, importance, y_all, p_all, threshold)
    make_figures(y_train, oof, chosen, y_all, p_all, df["facture_mensuelle"], threshold, importance)

    joblib.dump({"pipeline": final, "modele": chosen, "seuil": threshold, "reference": reference,
                 "features": FEATURES, "background": X_train, "version": date.today().isoformat(), "sklearn": sklearn.__version__,
                 "metrics": metrics}, MODELS_DIR / "model.joblib")
    report = {
        "date": date.today().isoformat(),
        "donnees": summary(df),
        "hypotheses_campagne": {"cout_offre": COUT_OFFRE, "taux_succes": TAUX_SUCCES, "mois_valeur": MOIS_VALEUR},
        "comparaison": comp.round(4).to_dict(orient="records"),
        "modele_retenu": chosen,
        "raison": reason,
        "test": metrics,
        "importance": importance.head(10).round(4).to_dict(orient="records"),
        "portefeuille": {
            "clients_cibles": int((p_all >= threshold).sum()),
            "segments": scored["segment_risque"].value_counts().to_dict(),
        },
    }
    (REPORTS_DIR / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("Terminé : models/model.joblib, reports/, powerbi/data/")
    return report


def export_powerbi(scored, comp, importance, y_all, p_all, threshold):
    """Tables lues par le rapport Power BI (séparateur virgule, point décimal, UTF-8)."""
    cols = [
        "id_client", "genre", "senior", "en_couple", "personnes_a_charge", "anciennete_mois", "tranche_anciennete",
        "tranche_ordre",
        "contrat", "internet", "moyen_paiement", "facture_dematerialisee", "nb_services", "support_technique",
        "securite_en_ligne", "facture_mensuelle", "total_facture", "valeur_annuelle", "churn", "churn_reel",
        "probabilite_churn", "segment_risque", "cible_campagne", "gain_attendu", "motif_1", "motif_2",
    ]
    scored[cols].to_csv(PBI_DIR / "clients.csv", index=False, encoding="utf-8")
    comp.round(4).to_csv(PBI_DIR / "comparaison_modeles.csv", index=False, encoding="utf-8")
    importance.round(5).to_csv(PBI_DIR / "importance_variables.csv", index=False, encoding="utf-8")
    from .model import profit_curve

    profit_curve(y_all, p_all, scored["facture_mensuelle"]).round(2).to_csv(PBI_DIR / "courbe_profit.csv", index=False)
    gain_curve(y_all, p_all).round(4).assign(
        part_aleatoire=lambda d: d["part_clients_contactes"]
    ).to_csv(PBI_DIR / "courbe_gain.csv", index=False)
    pd.DataFrame([{"seuil_campagne": threshold, "cout_offre": COUT_OFFRE, "taux_succes": TAUX_SUCCES,
                   "mois_valeur": MOIS_VALEUR}]).to_csv(PBI_DIR / "parametres.csv", index=False)


def make_figures(y_train, oof, chosen, y_all, p_all, monthly, threshold, importance):
    fig, ax = plt.subplots(figsize=(5.5, 5))
    for name, p in oof.items():
        fpr, tpr, _ = roc_curve(y_train, p)
        ax.plot(fpr, tpr, color=COLORS[name], lw=2.5 if name == chosen else 1.3,
                label=f"{name} (AUC {roc_auc_score(y_train, p):.3f})")
    ax.plot([0, 1], [0, 1], "--", color="grey", lw=1)
    ax.set_xlabel("Clients fidèles signalés à tort"); ax.set_ylabel("Départs détectés")
    ax.set_title("Courbes ROC — validation croisée"); ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout(); fig.savefig(FIG_DIR / "roc.png"); plt.close(fig)

    from .model import profit_curve

    curve = profit_curve(y_all, p_all, monthly)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(curve["seuil"], curve["profit"] / 1000, color="#3d9970", lw=2)
    ax.axvline(threshold, color="#d1495b", ls="--")
    best = curve.loc[(curve["seuil"] - threshold).abs().idxmin()]
    ax.annotate(f"seuil {threshold:.2f}\n{int(best['clients_cibles']):,} clients contactés\nprofit {best['profit']/1000:,.0f} k$".replace(",", " "),
                (threshold, best["profit"] / 1000), xytext=(20, -55), textcoords="offset points", color="#d1495b", fontsize=9)
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_xlabel("Seuil de probabilité de départ pour contacter un client")
    ax.set_ylabel("Profit de la campagne (k$)")
    ax.set_title(f"Qui contacter ? Offre à {COUT_OFFRE:.0f} $, {TAUX_SUCCES:.0%} de succès, valeur = {MOIS_VALEUR} mois")
    fig.tight_layout(); fig.savefig(FIG_DIR / "profit_seuil.png"); plt.close(fig)

    g = gain_curve(y_all, p_all)
    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.plot(g["part_clients_contactes"] * 100, g["part_departs_captes"] * 100, color="#2e86ab", lw=2.5, label="Modèle")
    ax.plot([0, 100], [0, 100], "--", color="grey", lw=1, label="Au hasard")
    k = g.iloc[(g["part_clients_contactes"] - 0.2).abs().idxmin()]
    ax.scatter([20], [k["part_departs_captes"] * 100], color="#d1495b", zorder=3)
    ax.annotate(f"20 % des clients → {k['part_departs_captes']:.0%} des départs", (20, k["part_departs_captes"] * 100),
                xytext=(10, -25), textcoords="offset points", fontsize=9)
    ax.set_xlabel("% de clients contactés (les plus risqués d'abord)"); ax.set_ylabel("% des départs captés")
    ax.set_title("Courbe de gain"); ax.legend(loc="lower right")
    fig.tight_layout(); fig.savefig(FIG_DIR / "gain.png"); plt.close(fig)

    top = importance.head(10)[::-1]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.barh(top["libelle"], top["importance"], color="#2e86ab")
    ax.set_xlabel("Baisse du ROC AUC quand on mélange la variable")
    ax.set_title("Ce qui prédit le départ (importance par permutation)")
    fig.tight_layout(); fig.savefig(FIG_DIR / "importance.png"); plt.close(fig)


if __name__ == "__main__":
    main()
