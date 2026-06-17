# Importer les résultats du modèle

Les importations se font sur la page **Import de modèle**, qui comporte des onglets séparés pour les annotations du modèle, les annotations historiques/manuelles, les métadonnées et les bundles.

## Annotations du modèle IA

Deux formats de CSV sont acceptés :

- **Format long** — une ligne par détection, avec les colonnes `video_path` (ou `filepath` / `review_filename` / `original_filepath` / `path`), `annotation_type` (`species`, `blank_non_blank`, `behavior` ou `object_detection`), `model_name`, `value_text`, `value_num`, `probability`, `t_start_sec`, `t_end_sec`.
- **Format large** — une ligne par vidéo, avec des colonnes telles que `top_1_<model>` pour l'espèce prédite, `prob_<model>`, `count_<model>`, et des colonnes de probabilité de vidéo vide. L'application détecte ce format automatiquement et propose une interface de mappage des colonnes.

Un modèle de CSV téléchargeable est disponible sur la page d'import si vous n'êtes pas sûr des colonnes attendues.

### Étapes d'import

1. **Téléversement** de votre CSV. L'application suggère automatiquement un mappage des chemins/colonnes.
2. **Aperçu de correspondance** — indique combien de chemins vidéo correspondent à des vidéos déjà présentes dans le projet, avec un échantillon des chemins non trouvés.
3. **Validation** — vérifie le fichier et indique le nombre de lignes valides/invalides, avec un échantillon de lignes valides.
4. **Revue et mappage des espèces** — toute espèce du CSV absente de la liste d'espèces du projet apparaît comme non mappée. Pour chacune, vous pouvez la mapper vers une espèce existante, l'ajouter comme nouvelle espèce, ou l'ignorer.
5. **Import** — seule cette dernière étape écrit dans la base de données. Les étapes d'aperçu et de validation ne modifient jamais votre projet.

!!! note
    Les lignes correspondant à des espèces laissées non mappées sont **ignorées** lors de l'import, afin que les données importées correspondent exactement à ce qui était affiché dans l'aperçu.

## Annotations historiques / manuelles

Pour réimporter des feuilles de calcul d'annotations exportées précédemment ou provenant d'une source externe. Prend en charge soit des colonnes de chemin séparées (dossier + nom de fichier), soit une colonne de chemin unique, avec des sélecteurs de colonnes pour l'espèce, le comportement, le nombre, l'observateur et l'horodatage. Vous choisissez si l'import doit **remplacer** les annotations existantes ou s'**ajouter** à elles. La même étape de mappage des espèces s'applique, avec en plus une option pour mapper directement vers « vide ».

## Annotations au format de l'application

Si votre CSV est au format d'export propre à l'application (détecté via les colonnes `video_path`/`video_id` + `is_blank`), l'application affiche un résumé à blanc des vidéos trouvées/ignorées et des observations à insérer/mettre à jour/supprimer avant que vous ne confirmiez l'import.

## Import groupé et par bundle

Pour répartir le travail entre plusieurs annotateurs, vous pouvez importer plusieurs fichiers à la fois, ou un seul bundle `.zip` contenant `species.csv`, `tags.csv`, `model_annotations.csv` et `metadata.csv`.

Suite : [Revoir les vidéos](reviewing.md)
