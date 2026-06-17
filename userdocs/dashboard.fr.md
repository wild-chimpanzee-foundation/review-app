# Tableau de bord

Le **tableau de bord** (aussi appelé Vue d'ensemble) est la page d'accueil de l'application — ouvrir l'application, ou cliquer sur le bouton accueil/Vue d'ensemble, vous y amène. Il résume l'état du projet actif et constitue le moyen le plus rapide d'accéder aux bonnes vidéos.

![Le tableau de bord, avec les cartes de statistiques, la barre de progression et les observations d'espèces](img/dashboard.jpg)

!!! note
    Le tableau de bord n'apparaît qu'une fois qu'un projet a des vidéos synchronisées. Avant cela, il vous invite à aller dans les Paramètres et à synchroniser un dossier de vidéos. Voir [Premiers pas](getting-started.md).

## Boutons de révision rapide

En haut à droite se trouvent des raccourcis qui ouvrent l'écran de révision avec des filtres déjà appliqués :

- **Revoir les non annotées** — ouvre les vidéos qui restent à annoter.
- **Revoir plus tard** — affiché uniquement si vous avez des vidéos marquées *Revoir plus tard* ; n'ouvre que celles-ci.

## Cartes de statistiques

Une rangée de tuiles récapitulatives pour le projet actif :

| Carte | Signification |
| --- | --- |
| Total vidéos | Vidéos enregistrées dans le projet |
| Caméras | Identifiants de caméra distincts |
| Heures | Durée totale des séquences |
| Annotées | Vidéos annotées, avec pourcentage |
| Vides | Vidéos marquées comme vides |
| Revoir plus tard | Vidéos marquées pour une seconde passe |
| Invalides | Vidéos illisibles ou mal analysées |
| Non analysées | Vidéos pas encore analysées (métadonnées/durée) |

## Vidéos manquantes

Si le projet référence des vidéos qui ne sont plus présentes sur le disque, une **bannière d'avertissement** dépliable liste leurs chemins. Vous pouvez les nettoyer depuis **Paramètres → Dossier vidéo** (voir [Paramètres](settings.md)).

## Progression des annotations

Une barre empilée répartit l'ensemble du projet entre vidéos **vides**, **non vides** et **non annotées**, avec une légende et des décomptes — une vue d'un coup d'œil du travail restant.

## Espèces et comportements

Deux panneaux côte à côte :

- **Observations d'espèces** — chaque espèce enregistrée jusqu'ici, avec le nombre d'observations. Cliquez sur une espèce pour ouvrir l'écran de révision filtré sur celle-ci.
- **Répartition des comportements** — les comportements enregistrés dans le projet, avec décomptes et barre de pourcentage. Cliquez sur un comportement pour filtrer l'écran de révision dessus.

## Récapitulatif par caméra

Une bande déroulable horizontalement de cartes de caméra, chacune montrant une vignette d'exemple, l'identifiant de la caméra, une barre de progression d'annotation et le total de vidéos/heures. Cliquez sur une carte pour revoir les vidéos de cette caméra.

## Récapitulatif des affectations

Lorsque des vidéos sont affectées à des annotateurs (voir [Paramètres → Distribution](settings.md#distribution)), ce panneau liste chaque annotateur avec son pourcentage d'annotation, le nombre de vidéos/caméras, les heures, et une barre de progression vide/non vide/non annotée. Cliquez sur une ligne pour revoir les vidéos affectées à cet annotateur.

## Carte des emplacements

Si des vidéos comportent des coordonnées GPS, une carte interactive place chaque emplacement de caméra avec un marqueur indiquant le nombre de vidéos qu'elle contient.

Suite : [Importer les résultats du modèle](importing.md)
