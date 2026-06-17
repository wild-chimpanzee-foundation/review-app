# Premiers pas

## Installation

L'application est distribuée sous forme d'exécutable autonome pour Linux, Windows et macOS — aucune installation de Python n'est nécessaire. Téléchargez la version correspondant à votre système depuis la page [Releases](https://github.com/wild-chimpanzee-foundation/review-app/releases) du projet et lancez-la.

L'application nécessite **ffmpeg** installé sur votre système pour la lecture vidéo. S'il n'est pas trouvé, l'assistant de configuration affiche les instructions d'installation pour votre OS :

- macOS : `brew install ffmpeg`
- Windows : via winget, ou un téléchargement manuel
- Linux : `apt install ffmpeg` (ou le gestionnaire de paquets de votre distribution)

## Premier lancement

Au premier démarrage, aucune base de données n'existe encore, donc l'application ouvre directement un **assistant de configuration** :

1. Choisissez votre langue (anglais ou français) — modifiable plus tard dans les Paramètres.
2. Choisissez **Nouveau départ** pour commencer une nouvelle base de données, ou **Restaurer depuis une sauvegarde** pour charger un fichier de sauvegarde `.db` exporté précédemment.

## Projets

Un **projet** est un nom associé à un dossier de vidéos de pièges photographiques sur le disque. L'application parcourt ce dossier de manière récursive (formats pris en charge : mp4, avi, mov, mkv, webm, wmv, flv) et enregistre chaque vidéo trouvée. Vous pouvez avoir plusieurs projets dans une même base de données et basculer entre eux depuis le menu déroulant de l'en-tête.

Il existe deux façons de créer un projet :

- **Manuelle** — saisissez un nom de projet et le chemin de votre dossier vidéo, assignez-le éventuellement à une collection, puis synchronisez.
- **Importation d'un bundle** — téléversez un bundle `.zip` (préparé par un collègue) contenant une liste d'espèces, des étiquettes, des annotations IA et/ou des métadonnées, pointez-le vers votre dossier vidéo local, et l'application importe tout en une seule étape. C'est la méthode recommandée pour distribuer un projet entre plusieurs annotateurs.

!!! note
    Les caméras n'ont pas besoin d'être configurées séparément — l'identifiant de caméra est déduit automatiquement de la structure de vos dossiers lors de la synchronisation.

## Configuration initiale

Une fois le projet créé, l'essentiel à configurer avant de commencer la revue est la **liste d'espèces** :

- Allez dans **Paramètres → Paramètres avancés → Espèces du projet** et activez les espèces pertinentes pour votre projet depuis le catalogue global, ou ajoutez des espèces personnalisées.

Vous pouvez aussi ajuster les **seuils de confiance** utilisés pour déterminer comment les prédictions de l'IA sont affichées (seuils pour vidéo vide, espèces et détection d'objets) dans **Paramètres → Paramètres avancés → Détection des vidéos vides**.

Suite : [Importer les résultats du modèle](importing.md)
