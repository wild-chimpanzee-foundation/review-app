# Revoir les vidéos

## Disposition

L'écran de revue comprend :

- Un en-tête avec le sélecteur de projet et la navigation.
- Un panneau latéral gauche avec des filtres (espèce, caméra, étiquettes, statut d'annotation, annotateur, etc.) — entièrement masquable.
- Un lecteur vidéo central avec une barre de position dans la file (curseur, boutons précédent/suivant, et un champ de saut numérique).
- Un panneau latéral droit avec des résumés repliables des prédictions de l'IA (espèce, détection d'objets, vidéo vide) suivis du panneau d'annotation manuelle.

## Métadonnées vidéo

Chaque vidéo affiche son identifiant de caméra, son nom de fichier, son horodatage d'enregistrement, et sa position GPS (si disponible, avec une fenêtre de carte cliquable). Les champs de métadonnées manquants sont signalés.

## Annoter

Pour chaque vidéo, vous pouvez :

- Marquer la vidéo comme vide / non vide.
- Ajouter une ou plusieurs annotations d'espèce — chacune avec une espèce, un filtre de groupe optionnel, une sélection multiple de comportements/étiquettes, et un nombre.
- Cliquer sur une prédiction de l'IA pour la copier directement dans une annotation manuelle.
- Supprimer une annotation, ou utiliser **Effacer les annotations** pour retirer toutes les annotations de la vidéo.
- Activer **Revoir plus tard** pour marquer une vidéo en vue d'un second passage.
- Activer des **étiquettes** sur la vidéo — les étiquettes intégrées incluent `fire`, `nice_shot` et `broken_metadata` ; vous pouvez aussi créer des étiquettes personnalisées.

## Contrôles du lecteur vidéo

Lecture/pause, curseur de défilement avec affichage du temps, vitesse de lecture (0,25x–25x), muet, plein écran, zoom à la molette avec déplacement par glisser-déposer, et curseurs de **luminosité/contraste** (0,5–2,0) avec un bouton de réinitialisation.

## Raccourcis clavier

Une liste complète est disponible depuis la boîte de dialogue d'aide intégrée. Les plus utiles :

| Touche | Action |
| --- | --- |
| Entrée | Valider et passer à la vidéo suivante |
| N / P | Vidéo suivante / précédente |
| M | Activer/désactiver « revoir plus tard » |
| A | Ajouter une espèce |
| C | Effacer les annotations |
| 1–9 | Ajouter la N-ième espèce/objet prédit par l'IA comme annotation manuelle |
| J / K | Sélectionner la carte d'annotation suivante / précédente |
| Tab | Donner le focus au premier champ de la carte sélectionnée |
| X | Supprimer l'annotation sélectionnée |
| T | Donner le focus au champ d'étiquette |

Dans le lecteur vidéo :

| Touche | Action |
| --- | --- |
| Espace | Lecture / pause |
| ← / → | Avancer/reculer de 5s |
| S / D | Diminuer / augmenter la vitesse |
| [ / ] | Diminuer / augmenter la luminosité |
| { / } | Diminuer / augmenter le contraste |
| Z | Réinitialiser le zoom |
| R | Réinitialiser luminosité/contraste |
| F | Plein écran |

Suite : [Exporter les annotations](exporting.md)
