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

### Affectations

La répartition et la transmission se trouvent dans **Données & affectations →
Affectations**, et non dans les Paramètres. La liste compacte affiche les
affectations préparées. Sélectionnez une ligne pour enregistrer un dossier de
livraison ou examiner le périmètre figé. Les identifiants et horodatages précis
sont dans **Détails techniques**.

1. Choisissez **Affecter le travail** et l’étape de révision. Ajoutez les noms des
   annotateurs ici si nécessaire.
   - **Espèces** : utilisez la répartition automatique ou ajustez les destinataires.
     Les répartitions existantes sont conservées exactement, même si une caméra
     est partagée entre plusieurs personnes. La répartition automatique sélectionne
     les vidéos non annotées ayant une caméra. Ajustez la répartition initiale
     pour inclure manuellement les vidéos annotées, manquantes ou sans caméra.
     La préparation utilise toujours la répartition affichée, sans raccourci pour
     toutes les vidéos ni étape distincte d’application.
   - **Distance** : choisissez une espèce cible confirmée et son destinataire.
     Les vidéos correspondantes sont chargées et sélectionnées automatiquement.
     Le bouton d’options de chaque ligne permet d’examiner ou réduire la sélection. Ajoutez une ligne pour une
     autre espèce cible : chaque espèce produit une affectation et un projet de
     travail distincts. Les tâches manuelles et liées à l’IA sont prises en charge,
     indépendamment de la répartition Espèces. Les références et calibrations
     nécessaires sont collectées automatiquement. Les heures locales caméra
     facultatives utilisent le format 24 heures : début inclus, fin exclue,
     plages nocturnes possibles. Les heures inconnues sont exclues uniquement
     avec une plage. Modifier les filtres efface et recharge la sélection.
2. Cliquez sur **Vérifier les affectations**, vérifiez le travail regroupé par
   annotateur, puis **Préparer les affectations**. Chaque périmètre est figé. La
   répartition Espèces est appliquée pendant la préparation. Les lignes sont
   préparées séparément : les réussites restent enregistrées si une autre échoue,
   et réessayer ignore les réussites. Les modifications avant préparation restent
   un brouillon ; une répartition ultérieure ne change pas les ZIP enregistrés.
3. Sélectionnez une affectation et **Enregistrer le dossier de livraison**.
   Choisissez un dossier sur l’ordinateur exécutant l’application, hors des vidéos
   du projet. **Parcourir…** ouvre le sélecteur système ; le chemin reste aussi
   saisissable manuellement. Sous Linux, le sélecteur nécessite Zenity ou KDialog.
   Le nouveau dossier contient **assignment.zip**. L’option **Inclure
   les vidéos** copie aussi le travail et les références nécessaires dans des
   sous-dossiers par racine. Laissez-la désactivée si les dossiers d’origine sont
   accessibles au destinataire. Sélectionnez plusieurs affectations pour enregistrer
   leurs dossiers ensemble. Les résultats affichent une entrée compacte par
   destinataire ; dépliez-la pour voir les chemins et détails techniques.
   Vérifiez les fichiers manquants et les échecs ; réessayez dans un autre dossier
   sans écraser une livraison.
4. Le destinataire choisit **Ouvrir un paquet** dans le sélecteur de projet,
   charge le ZIP et indique les dossiers vidéo pour créer un nouveau projet.
   Les vidéos ne sont pas intégrées au ZIP. L’ouverture synchronise et analyse
   uniquement les fichiers répertoriés, sans découvrir de fichiers supplémentaires.
   Une synchronisation échouée peut être réessayée sans créer un autre projet.
5. Le destinataire utilise **Télécharger la progression** dans **Affectations**
   (également accessible depuis la vue d’ensemble). Le retour inclut tout l’état
   enregistré dans le périmètre, même inchangé, inachevé ou vide. L’annotation
   peut continuer. Les deux étapes restent modifiables localement ; les autres
   travaux n’entrent pas dans le retour.
6. Dans le projet d’origine, choisissez **Accepter le travail retourné**. Chargez
   le retour et examinez l’aperçu avant d’accepter. **Utiliser l’import** remplace
   l’état complet des vidéos Espèces ou tâches Distance différentes, y compris
   les suppressions et corrections de l’administrateur. **Conserver l’existant**
   préserve le travail local différent. Changer de fichier annule la confirmation ;
   changer de règle actualise l’aperçu. Les modifications du projet imposent un
   nouvel aperçu.


Les listes de vidéos permettent la recherche, le tri par colonne et les filtres
pertinents par caméra, disponibilité, rôle ou action du retour. Les filtres
Distance ne modifient pas la sélection hors filtre ; utilisez explicitement les
boutons pour sélectionner ou désélectionner les vidéos correspondantes.

**Retirer l’affectation** la masque et désactive les livraisons après confirmation,
sans supprimer vidéos, annotations, projets de travail ou fichiers déjà enregistrés.
Les retours futurs restent acceptables : l’instantané initial est conservé en
interne pour validation et son espace dans la base n’est donc pas libéré.

Pour Distance, un changement du contexte Espèces déclenche un avertissement sans
bloquer les tâches du périmètre enregistré. L’acceptation ne restaure ni ne modifie
les annotations Espèces. La calibration de l’administrateur est réutilisée par
vidéo de référence et distance ; les substitutions affichent la vidéo, la distance
et les positions retournée/administrateur, et conservent le masque administrateur.
Les distances des échantillons retournés sont conservées. De nouvelles distances
peuvent partager un horodatage sans remplacer les autres calibrations.

L’import CSV par lot est dans **Données & affectations → Ajouter des données → Annotations existantes**. Il reste indépendant et ajoute les annotations dès l'envoi, sans aperçu d'affectation. La restauration **Distance manuelle** est dans le même espace et reste séparée :
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
