# Exporter les annotations

Ouvrez **Importation / Exportation → Exporter les résultats**. Choisissez un jeu de données dans la liste ; sa description, son périmètre et ses commandes de téléchargement/copie s'affichent à côté. Les formats CSV/TSV et les règles d'éligibilité restent inchangés.

## Exporter les annotations

Exporte les annotations revues manuellement (humaines) au format CSV.

- Nom de fichier : `annotations_{project_name}_{annotator_name}_{YYYY-MM-DD_HH-MM-SS}.csv`
- Une ligne par observation d'espèce ; les vidéos vides obtiennent une seule ligne avec des champs d'espèce vides.
- Colonnes : `project_name`, `video_path`, `camera_id`, `recorded_at`, `latitude`, `longitude`, `duration_sec`, `assigned_to`, `is_blank`, `review_later`, `is_annotated`, `annotator`, `labeled_at`, `observation_id`, `observation_uuid`, `species` (nom scientifique), `attributes` (clés de comportement/étiquette séparées par des virgules), `count`, `start_sec`, `end_sec`, ainsi qu'une colonne `tag_<key>` (0/1) par étiquette intégrée et une colonne `custom_tags` (clés d'étiquettes personnalisées séparées par des virgules). `observation_uuid` est conservé lors des exportations et permet aux importations répétées en mode ajout de mettre à jour la même observation au lieu de la dupliquer.

## Exporter les annotations IA

Exporte les prédictions brutes du modèle au format CSV.

- Nom de fichier : `ai_annotations_{project_name}_{YYYY-MM-DD_HH-MM-SS}.csv`

## Livraison des affectations

Utilisez **Importation / Exportation → Affectations** pour préparer les affectations, puis **Enregistrer le dossier de livraison**. Cette action écrit `assignment.zip` et copie éventuellement les vidéos nécessaires dans un nouveau dossier sur l’ordinateur exécutant l’application ; ce n’est pas un téléchargement par le navigateur. La barre de progression compte les fichiers traités, et le résultat indique les copies, fichiers manquants et échecs. Transmettez le dossier au destinataire. L’ouverture des affectations reçues et des anciens bundles reste dans le sélecteur de projet — voir [Premiers pas](getting-started.md).

Pour les affectations contenant des données de déploiement, comme des métadonnées du catalogue ou des liens de dossiers, utilisez chez le destinataire une version de l’application prenant en charge le format d’affectation v2. Une version limitée au format v1 ne peut pas ouvrir ces affectations. Les versions compatibles avec le format v2 ouvrent également les affectations v1. Une affectation Distance contenant des mesures liées à des images de référence supprimées nécessite la prise en charge du format v3.

Les annotateurs téléchargent un ZIP de **progression** depuis **Affectations** dans leur projet de travail. Le coordinateur l’examine et l’accepte dans le projet d’origine. Pour Espèces, les règles sont **Ajouter** (ajout/mise à jour par UUID d’observation, sans suppression des observations absentes), **Conserver l’existant** ou **Utiliser l’import**. Distance utilise uniquement les deux dernières règles, par tâche entière. Voir [Affectations](settings.md#affectations) pour les détails.

## Annotations Distance manuelles

Ouvrez **Importation / Exportation → Exporter les résultats**, puis choisissez **Mesures de distance** (TSV).

1. Choisissez une espèce avec des **étiquettes de révision des espèces confirmées
   manuellement**.
2. Facultativement, saisissez une plage horaire locale de la caméra, par exemple
   **18:00–20:00**. Elle s'applique à tous les jours et vérifie uniquement
   **l'heure de début de la vidéo**, pas le chevauchement. Début inclus, fin
   exclue ; une fin antérieure traverse minuit. Laissez les deux champs vides
   pour toutes les heures.
3. Cliquez sur **Prévisualiser les vidéos**. Toutes les tâches correspondantes prises en charge sont sélectionnées ; ouvrez la liste uniquement pour les examiner ou réduire la sélection. Les vidéos sans heure caméra connue sont signalées
   séparément lorsqu'un filtre horaire est actif. Seules les heures Distance
   vérifiées sont utilisées, jamais les métadonnées du fichier.
4. Téléchargez le **TSV d'analyse** : une ligne par échantillon enregistré, sauts
   de visibilité compris. Ce tableau ne permet pas de restaurer les tâches
   éditables ni les résultats d'absence explicite.

Les affectations ordinaires ne limitent pas la sélection. Cet export d'analyse
prend uniquement en charge les tâches manuelles. Utilisez les lots d'affectation
pour les travaux Distance modifiables, manuels ou liés à l'IA.

Les TSV utilisent UTF-8, des tabulations et le point décimal, indépendamment de
la langue de l'interface. Dans un tableur, utilisez l'import Texte/CSV, choisissez
la tabulation, une locale numérique à point décimal et les identifiants comme
texte. Le double-clic sur un TSV n'est pas fiable entre locales.

Suite : [Paramètres](settings.md)
