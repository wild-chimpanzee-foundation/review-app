# Exporter les annotations

Les exports sont disponibles sur la page **Import de modèle**, sous l'onglet **Annotations**.

## Exporter les annotations

Exporte les annotations revues manuellement (humaines) au format CSV.

- Nom de fichier : `annotations_{project_name}_{annotator_name}_{YYYY-MM-DD_HH-MM-SS}.csv`
- Une ligne par observation d'espèce ; les vidéos vides obtiennent une seule ligne avec des champs d'espèce vides.
- Colonnes : `project_name`, `video_path`, `camera_id`, `recorded_at`, `latitude`, `longitude`, `duration_sec`, `assigned_to`, `is_blank`, `review_later`, `is_annotated`, `annotator`, `labeled_at`, `observation_id`, `species` (nom scientifique), `attributes` (clés de comportement/étiquette séparées par des virgules), `count`, `start_sec`, `end_sec`, ainsi qu'une colonne `tag_<key>` (0/1) par étiquette intégrée et une colonne `custom_tags` (clés d'étiquettes personnalisées séparées par des virgules).

## Exporter les annotations IA

Exporte les prédictions brutes du modèle au format CSV.

- Nom de fichier : `ai_annotations_{project_name}_{YYYY-MM-DD_HH-MM-SS}.csv`

## Export par bundle

Toujours depuis la page Import de modèle, vous pouvez exporter un bundle `.zip` contenant la liste d'espèces du projet, les étiquettes, les annotations du modèle et les métadonnées sous forme de CSV séparés. Ceci est destiné à distribuer la configuration d'un projet à d'autres annotateurs — voir [Premiers pas](getting-started.md) pour le flux d'import correspondant.
