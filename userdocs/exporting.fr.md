# Exporter les annotations

Les exports sont disponibles sur la page **Import de modèle**, sous l'onglet **Annotations**.

## Exporter les annotations

Exporte les annotations revues manuellement (humaines) au format CSV.

- Nom de fichier : `annotations_{project_name}_{annotator_name}_{YYYY-MM-DD_HH-MM-SS}.csv`
- Une ligne par observation d'espèce ; les vidéos vides obtiennent une seule ligne avec des champs d'espèce vides.
- Colonnes : `project_name`, `video_path`, `camera_id`, `recorded_at`, `latitude`, `longitude`, `duration_sec`, `assigned_to`, `is_blank`, `review_later`, `is_annotated`, `annotator`, `labeled_at`, `observation_id`, `observation_uuid`, `species` (nom scientifique), `attributes` (clés de comportement/étiquette séparées par des virgules), `count`, `start_sec`, `end_sec`, ainsi qu'une colonne `tag_<key>` (0/1) par étiquette intégrée et une colonne `custom_tags` (clés d'étiquettes personnalisées séparées par des virgules). `observation_uuid` est conservé lors des exportations et permet aux importations répétées en mode ajout de mettre à jour la même observation au lieu de la dupliquer.

## Exporter les annotations IA

Exporte les prédictions brutes du modèle au format CSV.

- Nom de fichier : `ai_annotations_{project_name}_{YYYY-MM-DD_HH-MM-SS}.csv`

## Export par bundle

Toujours depuis la page Import de modèle, vous pouvez exporter un bundle `.zip` contenant la liste d'espèces du projet, les étiquettes, les annotations du modèle et les métadonnées sous forme de CSV séparés. Ceci est destiné à distribuer la configuration d'un projet à d'autres annotateurs — voir [Premiers pas](getting-started.md) pour le flux d'import correspondant.

## Annotations Distance manuelles

Ouvrez **Import de modèle → Distance manuelle**.

1. Choisissez une espèce avec des **étiquettes de révision des espèces confirmées
   manuellement**.
2. Facultativement, saisissez une plage horaire locale de la caméra, par exemple
   **18:00–20:00**. Elle s'applique à tous les jours et vérifie uniquement
   **l'heure de début de la vidéo**, pas le chevauchement. Début inclus, fin
   exclue ; une fin antérieure traverse minuit. Laissez les deux champs vides
   pour toutes les heures.
3. Cliquez sur **Prévisualiser les vidéos**, vérifiez la liste et sélectionnez
   les tâches à exporter. Les vidéos sans heure caméra connue sont signalées
   séparément lorsqu'un filtre horaire est actif. Seules les heures Distance
   vérifiées sont utilisées, jamais les métadonnées du fichier.
4. Téléchargez :
   - Le **ZIP éditable** : tâches entières, individus, alignement,
     échantillons et sauts de visibilité, références calibrées nécessaires,
     relations, achèvement et signets.
   - Le **TSV d'analyse** : une ligne par échantillon enregistré, sauts de
     visibilité compris. Ce tableau seul ne permet pas de restaurer les tâches
     éditables ni les résultats d'absence explicite.

Les affectations ordinaires ne limitent pas la sélection. Les vidéos restent
séparées. Ce premier format refuse les tâches et références liées à l'IA plutôt
que de perdre leur provenance. Les lots de travail ne sont pas encore disponibles.

### Restaurer un ZIP Distance éditable

Les vidéos doivent déjà être importées et synchronisées dans le projet cible.
Sous **Restaurer les annotations Distance**, chargez le ZIP (50 Mio maximum),
associez les dossiers médias sources aux dossiers locaux et vérifiez l'aperçu.
Un unique dossier source et local peut être associé automatiquement.

Choisissez **Remplacer la tâche** ou laissez **Ignorer** pour chaque tâche, puis
appliquez les choix. Remplacer supprime les données actuelles de cette tâche et
restaure ensemble l'alignement, les individus, les échantillons, l'achèvement et
les signets exportés. Les tâches des autres espèces et les étiquettes de révision
des espèces restent inchangées. Les calibrations de référence partagées en
conflit ne sont jamais écrasées silencieusement : ignorez la tâche concernée ou
résolvez ses dépendances séparément.

Une restauration répétée ne duplique pas les données. Le résumé indique les
tâches importées et ignorées. Conservez le ZIP original pour réessayer :
l'application n'archive ni les données ignorées ni le fichier chargé.

Les TSV utilisent UTF-8, des tabulations et le point décimal, indépendamment de
la langue de l'interface. Dans un tableur, utilisez l'import Texte/CSV, choisissez
la tabulation, une locale numérique à point décimal et les identifiants comme
texte. Le double-clic sur un TSV n'est pas fiable entre locales.

Suite : [Paramètres](settings.md)
