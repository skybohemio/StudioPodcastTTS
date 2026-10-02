# Studio Podcast TTS — v1.5.1

Studio de podcast TTS pour Windows 11, basé sur **Voxtral Mini TTS** (API Mistral).
VibeVoice a été entièrement retiré : plus de runtime Python, de torch ni de modèle à télécharger.

## Assistant IA de réécriture
Réécrit votre texte brut en script de podcast expressif (rythme, intentions, relances).
- **Fournisseurs** : Ollama **local** (hors connexion), **Ollama Cloud**, Kimi / Moonshot, Mistral,
  OpenAI, Anthropic Claude, DeepSeek, Google Gemini, Groq, OpenRouter, ou tout endpoint
  OpenAI-compatible. URL, **clé API** et modèle sont mémorisés **séparément pour chaque
  fournisseur** ; 📥 *Liste* charge les vrais modèles disponibles, ⬇ *Installer* télécharge un
  modèle Ollama local. Mistral reprend par défaut la clé de l'onglet Voxtral.
- **Épisodes entiers** : le texte est réécrit **passage par passage** (≈ 350 mots, réglable),
  chaque passage recevant la fin du précédent pour la continuité, puis tout est réassemblé.
  Envoyer un épisode de 30 minutes d'un seul coup à un petit modèle le fait *résumer* le texte.
  La longueur de chaque passage est contrôlée : s'il est trop court, nouvel essai ; si le modèle
  persiste à résumer, le passage d'origine est conservé (jamais de contenu perdu).
- Ollama utilise un contexte de 8192 jetons (sinon il tronque silencieusement l'entrée à 4096).
- Réécriture **annulable**, Ctrl+Z pour revenir au texte d'origine.
- Modèles : évitez les modèles de 1 milliard de paramètres (llama3.2:1b) ; préférez 7–8 milliards
  ou plus en local, ou un modèle cloud.
- Styles : Laurent Deutsch « Entrez dans l'histoire », documentaire immersif, conversationnel,
  ou personnalisé.

## Voxtral Mini TTS (API Mistral, cloud)
Bonnes pratiques officielles intégrées :
- **Découpage en blocs ≤ 300 mots** (réglable 50–300, défaut 280), synthèse bloc par bloc, puis
  **réassemblage en un seul fichier** WAV ou MP3 192/256/320 kbps.
- **Forme prononçable** : nombres, monnaies, pourcentages, heures, ordinaux, chiffres romains en
  toutes lettres ; **sigles épelés** (FBI → F-B-I) ; **pas de formatage riche** (markdown, emojis,
  URL supprimés).
- **Langue de la voix = langue du texte** : un avertissement s'affiche si la voix choisie ne
  déclare pas la langue du script (une voix d'une autre langue donne un accent — le transfert
  cross-lingue est possible mais volontaire).
- Modèle `voxtral-mini-tts-2603` (ou `latest`), 9 langues.
- **Deux locuteurs** : `Speaker 1:` / `Speaker 2:` → une voix chacun (balise jamais lue), court
  silence entre répliques.
- **Clonage de voix zero-shot** : échantillon 5–25 s (≥ 3 s), ou voix sauvegardée réutilisable (★).
- Nouvel essai automatique (réseau / quota), estimation du coût en direct (≈ 0,016 $ / 1000
  caractères), un fichier existant n'est jamais écrasé.
- Voxtral n'interprète **aucune balise** : l'émotion vient de la voix de référence et de la
  ponctuation. Les indications `(rire)`, `[pause]`… sont retirées avant synthèse.
- Clé API stockée localement (`%LOCALAPPDATA%\StudioPodcastTTS\config.json`), jamais transmise
  ailleurs qu'au fournisseur concerné.

## Raccourcis
**Ctrl+Entrée** générer · **Ctrl+S** enregistrer les réglages · **Ctrl+Z** annuler

## Mode test
URL API personnalisée (endpoint local compatible) ou **mode test local** (tonalité de démonstration).

## Installation
Lancer `dist/installer/StudioPodcastTTS-Setup-1.5.1.exe` (sans droits admin).
Anciennes installations : le dossier `%LOCALAPPDATA%\StudioPodcastTTS\runtime` et `models`
(VibeVoice, plusieurs Go) n'est plus utilisé et peut être supprimé.

## Recompiler
```
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist build\StudioPodcastTTS.spec
"C:\Users\bohem\AppData\Local\InnoSetup6\ISCC.exe" build\installer.iss
dist\StudioPodcastTTS\StudioPodcastTTS.exe --selftest
```

## Historique
- **1.5.1** — Ollama local : la liste des modèles *réellement installés* est chargée automatiquement (fin du HTTP 404 « model not found »), proposition d’installer un modèle manquant, `think` désactivé pour les modèles à réflexion (qwen3…), l’IA n’invente plus de faits ni de balises `Speaker`, longueur de sortie plafonnée.
- 1.5.0 — VibeVoice supprimé ; assistant IA : Ollama local + cloud, 11 fournisseurs, clé par
  fournisseur, réécriture par passages (fin du texte tronqué), annulation ; avertissement de langue.
- 1.4.0 — balises jamais lues, 2 voix, clonage corrigé, formateur de texte corrigé, essais auto.