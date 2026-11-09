# Tableau de bord Power BI — mode d'emploi

## Ouvrir le rapport

Les données sont **intégrées au rapport** (compressées dans les requêtes Power Query) : aucun chemin de fichier à configurer.

1. Double-cliquez sur **`powerbi/churn_dashboard.pbit`** (Power BI Desktop, Windows).
   ⚠️ Dézippez d'abord le projet : un fichier ouvert directement depuis un zip ne fonctionne pas.
2. Les données se chargent toutes seules. Enregistrez ensuite en `.pbix` (**Fichier → Enregistrer sous**).

**Plan B** si le `.pbit` ne s'ouvre pas : ouvrez **`powerbi/source/churn_dashboard.pbip`** (même rapport, format texte),
cliquez sur **Accueil → Actualiser**, puis **Fichier → Exporter → Modèle Power BI** pour obtenir un `.pbit`.

## Contenu du rapport

| Page | Visuels |
|---|---|
| **Vue d'ensemble** | Clients, taux de churn, revenu mensuel perdu, part du revenu perdu · taux de churn par contrat, ancienneté, moyen de paiement, type d'internet · filtre par contrat |
| **Clients à risque** | Clients à risque élevé, clients à contacter, revenu mensuel à risque, gain attendu · répartition par segment · liste des clients triée par probabilité, avec les 2 motifs de départ · filtre par segment |
| **Modèle et campagne** | Profit selon le seuil de ciblage · courbe de gain · importance des variables · comparaison des modèles · seuil et coût d'une offre |

## Tables

| Table | Contenu |
|---|---|
| `clients` | Les 7 043 clients avec leur probabilité de départ (calculée hors échantillon), segment, ciblage, gain attendu et motifs |
| `comparaison_modeles` | ROC AUC et PR AUC des 3 modèles comparés |
| `importance_variables` | Importance par permutation de chaque variable |
| `courbe_profit` | Profit de la campagne pour chaque seuil de ciblage |
| `courbe_gain` | Part des départs captés selon la part des clients contactés |
| `parametres` | Hypothèses de la campagne (coût de l'offre, taux de succès, seuil) |

## Mesures DAX (table `clients`)

```dax
Nb clients                 = COUNTROWS ( clients )
Nb départs                 = SUM ( clients[churn] )
Taux de churn              = DIVIDE ( [Nb départs], [Nb clients] )
Revenu mensuel             = SUM ( clients[facture_mensuelle] )
Revenu mensuel perdu       = CALCULATE ( SUM ( clients[facture_mensuelle] ), clients[churn] = 1 )
Part du revenu perdu       = DIVIDE ( [Revenu mensuel perdu], [Revenu mensuel] )
Probabilité moyenne        = AVERAGE ( clients[probabilite_churn] )
Clients à risque élevé     = CALCULATE ( [Nb clients], clients[segment_risque] = "Élevé" )
Clients ciblés             = CALCULATE ( [Nb clients], clients[cible_campagne] = "Oui" )
Revenu mensuel à risque    = SUMX ( clients, clients[facture_mensuelle] * clients[probabilite_churn] )
Gain attendu de la campagne = CALCULATE ( SUM ( clients[gain_attendu] ), clients[cible_campagne] = "Oui" )
```

## Plan C : recréer le rapport à la main en 15 minutes

Si aucun des deux fichiers ne s'ouvre avec votre version de Power BI :

1. **Accueil → Obtenir les données → Texte/CSV**, et importez les 6 fichiers de `powerbi/data/`.
   Dans l'aperçu, vérifiez que l'**origine du fichier** est *65001 : Unicode (UTF-8)* pour garder les accents.
2. Si les décimales s'affichent mal (ex. `0.5778` lu comme `5778`) : dans Power Query, sur chaque colonne numérique,
   **Modifier le type → Utilisation des paramètres régionaux… → Décimal, Anglais (États-Unis)**.
3. Dans la table `clients`, colonne `tranche_anciennete` : **Outils de colonne → Trier par colonne → tranche_ordre**.
4. Créez les mesures ci-dessus (**Nouvelle mesure**, copier-coller).
5. Reproduisez les visuels listés dans « Contenu du rapport ».

## Mettre à jour les données

Après un nouvel entraînement : **Accueil → Actualiser**. Les CSV sont réécrits au même endroit.
Pour régénérer le rapport lui-même : `python powerbi/build_powerbi.py`.
