# Paramètres

La page **Paramètres** regroupe tout ce qui configure l'application et le projet actif. Les contrôles les plus utilisés sont en haut ; les opérations moins fréquentes et destructrices se trouvent sous **Paramètres avancés**.

![Le haut de la page Paramètres : préférences, nom du projet et dossier vidéo](img/settings.jpg)

## Préférences

- **Langue** — basculer l'interface entre l'anglais et le français (recharge l'application).
- **Mode sombre** — activer le thème clair/sombre.

## Projet

Affiché lorsqu'un projet est actif :

- **Nom du projet** — renommer le projet actif.
- **Collection** — regrouper le projet sous une collection nommée, ou le laisser sans catégorie.

## Dossier vidéo

Affiche le dossier sur le disque que le projet actif analyse pour trouver des vidéos.

- **Synchroniser les vidéos** — réanalyser le dossier. Les nouvelles vidéos sont enregistrées ; les vidéos absentes du disque sont signalées. Une fenêtre de progression indique combien ont été analysées, ajoutées et supprimées.
- **Supprimer les vidéos manquantes** — si des vidéos de la base de données n'existent plus sur le disque, un décompte apparaît ici avec un bouton pour supprimer ces enregistrements (confirmation requise).

## Paramètres avancés

Une section dépliable regroupant la configuration plus poussée :

### Espèces du projet

Activez les espèces pertinentes pour votre projet depuis le catalogue global, ou ajoutez des espèces personnalisées. Seules les espèces activées apparaissent dans les contrôles d'annotation. C'est l'élément principal à configurer avant de commencer la révision — voir [Premiers pas](getting-started.md).

### Étiquettes

Gérez les étiquettes pouvant être appliquées aux vidéos. Les étiquettes intégrées (comme `fire`, `nice_shot`, `broken_metadata`) sont toujours disponibles ; vous pouvez ajouter des étiquettes personnalisées ici.

### Détection des vidéos vides

Trois curseurs de seuil de confiance (0.0–1.0) qui contrôlent l'affichage des prédictions de l'IA :

- **Seuil vidéo vide** — niveau de confiance requis pour considérer une vidéo comme vide.
- **Seuil espèces** — probabilité minimale pour qu'une prédiction d'espèce soit affichée.
- **Seuil de détection d'objets** — probabilité minimale pour qu'une détection d'objet soit affichée.

Cliquez sur **Enregistrer** pour appliquer.

### Distribution

Répartissez les vidéos d'un projet entre plusieurs annotateurs afin que chacun revoie sa propre part.

1. Ajoutez les noms des annotateurs à qui répartir.
2. Affectez les caméras aux annotateurs manuellement, ou utilisez **Distribution automatique** pour répartir les caméras équitablement.
3. **Appliquer** pour enregistrer les affectations ; **Réinitialiser** les efface.

Un tableau récapitulatif montre les caméras, vidéos et heures de chaque annotateur.
Ouvrez la page **Distribution** pour transmettre un travail Espèces ou Distance :

1. Choisissez l’**Étape de l’affectation** et un destinataire enregistré :
   - **Espèces** : choisissez sa répartition appliquée ou toutes les vidéos du projet.
   - **Distance** : choisissez une **Espèce cible Distance** confirmée, puis
     **Charger les vidéos Distance admissibles**. Toutes sont sélectionnées par
     défaut ; retirez celles que vous ne souhaitez pas déléguer. Cette sélection
     est indépendante de la répartition Espèces et comprend les tâches manuelles
     ou liées à l’IA, même avec une vidéo manquante. Les vidéos de référence et
     leur calibration sont collectées automatiquement.
     Vous pouvez saisir deux heures locales caméra sur 24 heures : début inclus,
     fin exclue, plages nocturnes possibles. Les tâches sans heure connue sont
     exclues uniquement lorsqu’une plage est définie ; leur nombre est affiché.
     Modifier l’espèce ou les heures efface la sélection : rechargez les vidéos.
   Cliquez sur **Préparer une affectation**. La sélection et l’état enregistré
   sont figés ; les modifications ultérieures ne changent pas ce paquet.
2. Téléchargez l’affectation enregistrée. Pour livrer les vidéos, ouvrez la copie
   des vidéos de l’affectation et choisissez un dossier sur l’ordinateur qui
   exécute l’application, hors des dossiers vidéo du projet. Envoyez le dossier
   créé avec le ZIP. Chaque racine possède son propre sous-dossier. Pour Distance,
   le panneau indique les vidéos affectées + les références = le total des fichiers.
   Les résultats de copie incluent les deux ; les références sont des dépendances,
   pas du travail affecté supplémentaire. Vérifiez les fichiers absents et les échecs ; réessayez dans un autre dossier de sortie
   plutôt que d’écraser une livraison existante.
3. Le destinataire choisit **Ouvrir un paquet** dans le sélecteur de projet,
   charge le ZIP et indique chaque sous-dossier racine pour créer un nouveau projet. Les
   vidéos ne sont pas intégrées au ZIP. Sur le même ordinateur, les dossiers
   vidéo d’origine peuvent être réutilisés. L’ouverture synchronise et analyse
   automatiquement les vidéos de travail et de référence répertoriées, sans
   découvrir les fichiers non répertoriés. En cas d’échec,
   réessayez dans le même formulaire sans créer un autre projet.
4. Après annotation, le destinataire utilise **Télécharger la progression** dans
   Distribution. Le retour contient l’état enregistré de chaque vidéo pour
   l’étape déléguée et, pour Distance, son unique espèce cible, même inchangé,
   inachevé ou vide. L’annotation peut continuer après un retour. Les deux étapes
   restent modifiables localement ; les autres travaux n’entrent pas dans le retour.
5. Dans le projet d’origine, chargez le retour dans **Prévisualiser et accepter le
   retour**. Vérifiez l’étape, l’espèce cible, les nombres et les avertissements.
   Par défaut, **Utiliser l’import** remplace l’état Espèces complet d’une vidéo ou
   l’état complet d’une tâche Distance, y compris les suppressions et corrections
   de l’administrateur. **Conserver l’existant** préserve les unités différentes
   du projet d’origine ; leurs mises à jour retournées ne sont pas appliquées.
   Changer de fichier annule la confirmation ; changer de règle actualise l’aperçu.
   Des modifications du projet destinataire après l’aperçu imposent un nouvel aperçu.

Pour Distance, un changement du contexte Espèces déclenche un avertissement sans
bloquer les tâches du périmètre enregistré. L’acceptation ne restaure ni ne modifie
les annotations Espèces. La calibration de l’administrateur est réutilisée par
vidéo de référence et distance ; les substitutions affichent la vidéo, la distance
et les positions retournée/administrateur, et conservent le masque administrateur.
Les distances des échantillons retournés sont conservées. De nouvelles distances
peuvent partager un horodatage sans remplacer les autres calibrations.

L’import CSV par lot reste indépendant et ajoute des annotations sans aperçu
d’affectation. Le format d’archive **Distance manuelle** reste également séparé :
il ne prend en charge ni les tâches liées à l’IA ni l’identité d’affectation.
Utilisez les paquets d’affectation pour déléguer Distance. **Ouvrir un paquet**
détecte aussi les anciens bundles et explique qu’ils créent un projet indépendant
sans retours d’affectation.

Les affectations apparaissent sur le [tableau de bord](dashboard.md#recapitulatif-des-affectations) et comme filtre d'annotateur sur l'écran Espèces.

### Utilisateurs / annotateurs

Liste toutes les personnes qui se sont connectées comme annotateur, avec leur nombre d'annotations. Vous pouvez supprimer un annotateur sans annotation — les annotateurs ayant des annotations existantes (et votre propre nom actuel) ne peuvent pas être supprimés.

### Gestion de la base de données

- **Sauvegarde** — télécharger un instantané `.db` de toute la base de données.
- **Restauration** — remplacer la base de données actuelle à partir d'une sauvegarde précédente (une sauvegarde de sécurité est d'abord effectuée).
- **Supprimer les annotations du modèle** — supprimer toutes les prédictions de l'IA importées tout en conservant vos annotations manuelles.
- **Réinitialiser la base de données** — tout effacer et repartir de zéro (une sauvegarde est d'abord effectuée). Destructeur — confirmation requise.

## Diagnostics

En bas de la page :

- **Télécharger le journal** — enregistrer `app.log` à envoyer lors d'un signalement de problème.
- **Vérifier les mises à jour** — comparer votre version à la dernière publiée et, si plus récente, proposer un lien de téléchargement (une sauvegarde est effectuée avant la mise à jour).
