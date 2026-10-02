"""Fenêtre principale de Studio Podcast TTS — moteur Voxtral (API Mistral ou endpoint local)."""
import datetime
import json
import math
import os
import re
import struct
import time
import wave

from PySide6.QtCore import QByteArray, QThread, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from engine import aiwriter, paths, textprep, voxtral

APP_VERSION = "1.5.1"
CONFIG_PATH = os.path.join(paths.appdata_dir(), "config.json")
DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Podcasts")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    """Écriture atomique : un crash pendant l'écriture ne corrompt pas la config."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


# Voxtral lit le texte tel quel : pas de balises d'émotion. Le rythme passe par la
# ponctuation, les locuteurs par « Speaker N: » (retiré du texte lu, une voix chacun).
TAGS = [
    ("Locuteur 1", "Speaker 1: ", "Réplique du locuteur 1 (voix « Locuteur 1 »)"),
    ("Locuteur 2", "Speaker 2: ", "Réplique du locuteur 2 (voix « Locuteur 2 »)"),
    ("Pause …", "... ", "Silence court : points de suspension"),
    ("Nouvelle scène", "\n\n", "Ligne vide : respiration plus longue entre deux paragraphes"),
]

STYLE = """
QMainWindow, QWidget { background:#16181d; color:#e6e6e6; font-family:'Segoe UI'; font-size:13px; }
QGroupBox { border:1px solid #2e323a; border-radius:8px; margin-top:10px; padding-top:8px; font-weight:600; }
QGroupBox::title { subcontrol-origin: margin; left:10px; padding:0 4px; color:#9ecbff; }
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox { background:#1e2127; border:1px solid #343943;
  border-radius:6px; padding:6px; selection-background-color:#3b6ea5; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color:#3b6ea5; }
QComboBox QAbstractItemView { background:#1e2127; selection-background-color:#2b6cb0; }
QPushButton { background:#2b6cb0; border:none; border-radius:6px; padding:8px 14px; font-weight:600; }
QPushButton:hover { background:#3182ce; }
QPushButton:disabled { background:#3a3f47; color:#8a8f98; }
QPushButton#secondary { background:#343943; }
QPushButton#secondary:hover { background:#414854; }
QPushButton#secondary:checked { background:#2b6cb0; }
QPushButton#danger { background:#a03d3d; }
QPushButton#danger:hover { background:#b84a4a; }
QPushButton#danger:disabled { background:#3a3f47; }
QPushButton#accent { background:#7c5cbf; }
QPushButton#accent:hover { background:#8f6fd4; }
QProgressBar { border:1px solid #343943; border-radius:6px; height:14px; text-align:center; }
QProgressBar::chunk { background:#2b6cb0; border-radius:5px; }
QTabWidget::pane { border:1px solid #2e323a; border-radius:6px; }
QTabBar::tab { background:#1e2127; padding:8px 14px; border-top-left-radius:6px; border-top-right-radius:6px; }
QTabBar::tab:selected { background:#2b6cb0; }
QToolTip { background:#1e2127; color:#e6e6e6; border:1px solid #343943; }
QLabel#hint { color:#8a8f98; }
"""

SAMPLE_RATE = 24000
SAME_VOICE = "__same__"


# ------------------------------------------------------------------ threads
class TaskThread(QThread):
    """Exécute fn() hors du thread GUI ; le résultat revient par le signal done."""
    done = Signal(bool, object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(True, self.fn())
        except Exception as e:
            self.done.emit(False, str(e) if type(e) is RuntimeError else f"{type(e).__name__}: {e}")


class VoxtralThread(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    done = Signal(bool, str)

    def __init__(self, api_key, text, out_path, model, fmt, bitrate,
                 voices=None, ref_audio_path=None, max_words=280, base_url=None):
        super().__init__()
        self.api_key, self.text, self.out_path = api_key, text, out_path
        self.model, self.fmt, self.bitrate = model, fmt, bitrate
        self.voices, self.ref_audio_path = voices or {}, ref_audio_path
        self.max_words, self.base_url = max_words, base_url
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            client = voxtral.VoxtralClient(self.api_key, self.base_url or None)
            path, duration, n = voxtral.generate_episode(
                client, self.text, self.model, self.out_path, self.fmt, self.bitrate,
                voices=self.voices, ref_audio_path=self.ref_audio_path,
                max_words=self.max_words,
                progress=lambda p, m: self.progress.emit(p, m),
                cancel=lambda: self._cancel, log=self.log.emit)
            self.done.emit(True, json.dumps({"path": path, "duration": duration, "blocks": n}))
        except InterruptedError as e:
            self.done.emit(False, str(e))
        except Exception as e:
            self.done.emit(False, str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}")


class DemoThread(QThread):
    """Mode test local : génère une tonalité sans API (aucune connexion)."""
    progress = Signal(int, str)
    log = Signal(str)
    done = Signal(bool, str)

    def __init__(self, text, out_path, max_words=280):
        super().__init__()
        self.text, self.out_path, self.max_words = text, out_path, max_words
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            n_blocs = len(voxtral.split_script(self.text, self.max_words))
            duration = min(2.0 + len(self.text) / 90.0, 20.0)
            n = int(SAMPLE_RATE * duration)
            frames = bytearray()
            for i in range(n):
                t = i / SAMPLE_RATE
                v = 0.35 * math.sin(2 * math.pi * 220 * t) * math.exp(-t / (duration * 0.8))
                frames += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
                if i % (SAMPLE_RATE // 5) == 0:
                    if self._cancel:
                        raise InterruptedError("Génération annulée")
                    self.progress.emit(min(95, int(95 * i / n)), "Synthèse de test locale…")
            out = voxtral.unique_path(os.path.splitext(self.out_path)[0] + ".wav")
            with wave.open(out, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(bytes(frames))
            self.done.emit(True, json.dumps({"path": out, "duration": duration, "blocks": n_blocs}))
        except Exception as e:
            self.done.emit(False, str(e))


class RewriteThread(QThread):
    """Réécriture IA passage par passage (épisodes entiers) avec progression et annulation."""
    progress = Signal(int, int, str)
    log = Signal(str)
    done = Signal(bool, str)

    def __init__(self, text, base_url, api_key, model, style_prompt, lang, chunk_words):
        super().__init__()
        self.args = (text, base_url, api_key, model, style_prompt, lang)
        self.chunk_words = chunk_words
        self.missing = None   # ModelNotFound éventuel
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            out = aiwriter.rewrite_script(
                *self.args, chunk_words=self.chunk_words,
                progress=lambda i, n, m: self.progress.emit(i, n, m),
                cancel=lambda: self._cancel, log=self.log.emit)
            self.done.emit(True, out)
        except InterruptedError as e:
            self.done.emit(False, str(e))
        except aiwriter.ModelNotFound as e:
            self.missing = e
            self.done.emit(False, str(e))
        except Exception as e:
            self.done.emit(False, str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}")


class PullThread(QThread):
    """Téléchargement d'un modèle Ollama (ollama pull) avec progression."""
    progress = Signal(int, str)
    done = Signal(bool, str)

    def __init__(self, name, base_url):
        super().__init__()
        self.name, self.base_url = name, base_url

    def run(self):
        try:
            aiwriter.pull_ollama_model(
                self.name, self.base_url,
                progress=lambda p, s: self.progress.emit(p if p is not None else -1, s))
            self.done.emit(True, self.name)
        except Exception as e:
            self.done.emit(False, str(e))


def _hint(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setObjectName("hint")
    return lbl


def _fmt_duration(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


# ------------------------------------------------------------------ fenêtre
class MainWindow(QMainWindow):
    REWRITE_LABEL = "🎭  Réécrire en script de podcast expressif"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Studio Podcast TTS {APP_VERSION} — Voxtral")
        self.resize(1240, 820)
        self.setStyleSheet(STYLE)
        self.cfg = load_config()
        self.last_audio = None
        self.threads = set()          # threads en cours (gardés vivants jusqu'à la fin)
        self.gen_thread = None
        self.gen_started = 0.0
        self.voices = []              # [{"id", "name", "languages", "type", "gender"}]
        self.ai_settings = {}         # {code_fournisseur: {"url", "key", "model"}}
        self.ai_provider = "ollama"
        self.rewrite_thread = None

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setSizes([740, 500])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)
        self.status = self.statusBar()

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.on_generate)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save_settings)

        self._restore_settings()
        self._update_count()
        self._log(f"Studio Podcast TTS {APP_VERSION} prêt. Écrivez ou réécrivez votre script "
                  "avec l'Assistant IA, choisissez les voix (onglet Voxtral), puis générez "
                  "(Ctrl+Entrée).")
        if self.edit_apikey.text().strip() or self.edit_api_url.text().strip():
            self.on_refresh_voices(silent=True)

    # ---------------------------------------------------------------- UI gauche
    def _build_left(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        gb = QGroupBox("Script du podcast")
        f = QVBoxLayout(gb)
        tagbar = QHBoxLayout()
        tagbar.addWidget(QLabel("Insérer :"))
        for label, tag, tip in TAGS:
            b = QPushButton(label)
            b.setObjectName("secondary")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, t=tag: self._insert_tag(t))
            tagbar.addWidget(b)
        tagbar.addStretch(1)
        b = QPushButton("✨ Formater")
        b.setObjectName("secondary")
        b.setToolTip("Nombres en lettres, sigles épelés, suppression du markdown/emojis "
                     "et des indications scéniques entre parenthèses")
        b.clicked.connect(self.on_format_text)
        tagbar.addWidget(b)
        f.addLayout(tagbar)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Écrivez ou collez votre texte brut ici…\n\n"
            "Puis utilisez l'Assistant IA (onglet à droite) pour le transformer en "
            "script de podcast expressif, ou écrivez directement :\n\n"
            "Speaker 1: Bienvenue ! Aujourd'hui… nous entrons dans l'histoire.\n"
            "Speaker 2: Et quelle histoire !\n\n"
            "Voxtral lit le texte tel quel : l'émotion passe par la ponctuation "
            "(… ! ?) et par la voix choisie, pas par des balises.")
        self.text.setFont(QFont("Consolas", 11))
        f.addWidget(self.text)
        self.char_count = QLabel()
        self.char_count.setObjectName("hint")
        self.text.textChanged.connect(self._update_count)
        f.addWidget(self.char_count)
        lay.addWidget(gb, 1)

        row = QHBoxLayout()
        self.btn_generate = QPushButton("🎙  Générer le podcast")
        self.btn_generate.setMinimumHeight(42)
        self.btn_generate.setToolTip("Ctrl+Entrée")
        self.btn_generate.clicked.connect(self.on_generate)
        self.btn_cancel = QPushButton("Annuler")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_listen = QPushButton("▶  Écouter")
        self.btn_listen.setObjectName("secondary")
        self.btn_listen.setEnabled(False)
        self.btn_listen.clicked.connect(self.on_listen)
        self.btn_open_dir = QPushButton("📁 Dossier")
        self.btn_open_dir.setObjectName("secondary")
        self.btn_open_dir.clicked.connect(self.on_open_dir)
        row.addWidget(self.btn_generate, 2)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_listen)
        row.addWidget(self.btn_open_dir)
        lay.addLayout(row)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)
        return w

    def _insert_tag(self, tag):
        cur = self.text.textCursor()
        if tag.startswith("Speaker") and cur.positionInBlock() > 0:
            tag = "\n" + tag  # une réplique commence toujours en début de ligne
        cur.insertText(tag)
        self.text.setFocus()

    def _update_count(self):
        t = self.text.toPlainText()
        words = len(t.split())
        if not t.strip():
            self.char_count.setText("0 caractère · 0 mot")
            return
        blocks = len(voxtral.split_script(t, self.spin_maxwords.value()))
        minutes = words / 150  # débit parlé moyen
        self.char_count.setText(
            f"{len(t)} caractères · {words} mots · {blocks} bloc(s) · "
            f"≈ {_fmt_duration(minutes * 60)} d'audio · ≈ {voxtral.estimate_cost(t):.3f} $")

    def _replace_text(self, new_text):
        """Remplace le script en conservant l'historique d'annulation (Ctrl+Z)."""
        cur = self.text.textCursor()
        cur.beginEditBlock()
        cur.select(QTextCursor.Document)
        cur.insertText(new_text)
        cur.endEditBlock()

    # ---------------------------------------------------------------- UI droite
    def _build_right(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        tabs = QTabWidget()
        tabs.addTab(self._build_tab_ai(), "Assistant IA")
        tabs.addTab(self._build_tab_voxtral(), "Voxtral")
        tabs.addTab(self._build_tab_export(), "Export")
        self.tabs = tabs
        lay.addWidget(tabs)

        gb_log = QGroupBox("Journal")
        vl = QVBoxLayout(gb_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        vl.addWidget(self.log)
        lay.addWidget(gb_log, 1)
        return w

    def _build_tab_ai(self):
        tab = QWidget()
        fa = QFormLayout(tab)
        self.combo_ai_provider = QComboBox()
        for p in aiwriter.PROVIDERS:
            self.combo_ai_provider.addItem(p["label"], p["code"])
        fa.addRow("Fournisseur", self.combo_ai_provider)
        self.edit_ai_url = QLineEdit()
        fa.addRow("URL API", self.edit_ai_url)
        key_row = QHBoxLayout()
        self.edit_ai_key = QLineEdit()
        self.edit_ai_key.setEchoMode(QLineEdit.Password)
        b = QPushButton("👁"); b.setObjectName("secondary"); b.setCheckable(True)
        b.setToolTip("Afficher / masquer la clé")
        b.toggled.connect(lambda on: self.edit_ai_key.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.edit_ai_key, 1); key_row.addWidget(b)
        fa.addRow("Clé API", key_row)
        model_row = QHBoxLayout()
        self.combo_ai_model = QComboBox()
        self.combo_ai_model.setEditable(True)
        self.combo_ai_model.lineEdit().setPlaceholderText("nom du modèle…")
        b = QPushButton("📥 Liste"); b.setObjectName("secondary")
        b.setToolTip("Charger la liste réelle des modèles disponibles sur ce fournisseur")
        b.clicked.connect(self.on_list_models)
        self.btn_pull = QPushButton("⬇ Installer"); self.btn_pull.setObjectName("secondary")
        self.btn_pull.setToolTip("Ollama local : télécharge le modèle saisi (« ollama pull »)")
        self.btn_pull.clicked.connect(self.on_pull_model)
        model_row.addWidget(self.combo_ai_model, 1); model_row.addWidget(b); model_row.addWidget(self.btn_pull)
        fa.addRow("Modèle", model_row)
        self.combo_style = QComboBox()
        for code, label, _p in aiwriter.STYLES:
            self.combo_style.addItem(label, code)
        fa.addRow("Style", self.combo_style)
        self.edit_custom_style = QPlainTextEdit()
        self.edit_custom_style.setMaximumHeight(70)
        fa.addRow(self.edit_custom_style)
        self.combo_style.currentIndexChanged.connect(self._on_style_changed)
        self.spin_ai_chunk = QSpinBox()
        self.spin_ai_chunk.setRange(100, 800)
        self.spin_ai_chunk.setSingleStep(50)
        self.spin_ai_chunk.setValue(aiwriter.DEFAULT_CHUNK_WORDS)
        self.spin_ai_chunk.setSuffix(" mots / passage")
        self.spin_ai_chunk.setToolTip("Le texte est réécrit par passages de cette taille, puis "
                                      "réassemblé. Petit modèle local : 250–350 ; gros modèle cloud : 500–800.")
        fa.addRow("Découpage IA", self.spin_ai_chunk)
        self.btn_rewrite = QPushButton(self.REWRITE_LABEL)
        self.btn_rewrite.setObjectName("accent")
        self.btn_rewrite.setMinimumHeight(36)
        self.btn_rewrite.clicked.connect(self.on_rewrite)
        fa.addRow(self.btn_rewrite)
        fa.addRow(_hint("Les épisodes longs sont réécrits passage par passage (aucun contenu "
                        "perdu). Le résultat remplace le texte (Ctrl+Z pour revenir en arrière). "
                        "Ollama local fonctionne hors connexion ; un modèle de 1 milliard de "
                        "paramètres (llama3.2:1b) est trop petit — préférez 7–8 milliards ou plus, "
                        "ou un modèle cloud. Ollama Cloud : clé sur ollama.com/settings/keys."))
        self.combo_ai_provider.currentIndexChanged.connect(self._on_ai_provider)
        return tab

    def _build_tab_voxtral(self):
        tab = QWidget()
        fv = QFormLayout(tab)
        self.edit_apikey = QLineEdit()
        self.edit_apikey.setEchoMode(QLineEdit.Password)
        self.edit_apikey.setPlaceholderText("Clé API Mistral (console.mistral.ai)…")
        key_row = QHBoxLayout(); key_row.addWidget(self.edit_apikey, 1)
        b = QPushButton("👁"); b.setObjectName("secondary"); b.setCheckable(True)
        b.setToolTip("Afficher / masquer la clé")
        b.toggled.connect(lambda on: self.edit_apikey.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(b)
        fv.addRow("Clé API", key_row)
        self.edit_apikey.editingFinished.connect(self._save_settings)

        self.edit_api_url = QLineEdit()
        self.edit_api_url.setPlaceholderText("vide = https://api.mistral.ai (URL locale possible pour vos tests)")
        fv.addRow("URL API", self.edit_api_url)

        self.combo_vox_model = QComboBox()
        for mid, label in voxtral.API_MODELS:
            self.combo_vox_model.addItem(label, mid)
        fv.addRow("Modèle", self.combo_vox_model)

        self.combo_vox_lang = QComboBox()
        for code, label in voxtral.LANGUAGES:
            self.combo_vox_lang.addItem(label, code)
        self.combo_vox_lang.setToolTip("Langue du script : sert au formatage des nombres et à "
                                       "l'Assistant IA (Voxtral détecte la langue lui-même)")
        fv.addRow("Langue", self.combo_vox_lang)

        voice_row = QHBoxLayout()
        self.combo_voice1 = QComboBox()
        self.combo_voice1.setMinimumContentsLength(18)
        self.btn_refresh_voices = QPushButton("🔄"); self.btn_refresh_voices.setObjectName("secondary")
        self.btn_refresh_voices.setToolTip("Actualiser la liste des voix")
        self.btn_refresh_voices.clicked.connect(lambda: self.on_refresh_voices())
        voice_row.addWidget(self.combo_voice1, 1); voice_row.addWidget(self.btn_refresh_voices)
        fv.addRow("Locuteur 1", voice_row)
        self.combo_voice2 = QComboBox()
        self.combo_voice2.setMinimumContentsLength(18)
        self.combo_voice2.setToolTip("Voix des répliques « Speaker 2: » (et suivants)")
        fv.addRow("Locuteur 2", self.combo_voice2)
        self._fill_voice_combos()

        ref_row = QHBoxLayout()
        self.edit_ref_audio = QLineEdit()
        self.edit_ref_audio.setPlaceholderText("Échantillon 5–25 s (≥ 3 s) : remplace la voix du locuteur 1…")
        self.edit_ref_audio.setClearButtonEnabled(True)
        b = QPushButton("…"); b.setObjectName("secondary")
        b.clicked.connect(self.on_pick_ref_audio)
        ref_row.addWidget(self.edit_ref_audio, 1); ref_row.addWidget(b)
        fv.addRow("Clonage", ref_row)

        save_row = QHBoxLayout()
        self.edit_voice_name = QLineEdit()
        self.edit_voice_name.setPlaceholderText("Nom de la nouvelle voix…")
        self.combo_gender = QComboBox()
        self.combo_gender.addItem("Auto", None)
        self.combo_gender.addItem("Femme", "female")
        self.combo_gender.addItem("Homme", "male")
        self.btn_create_voice = QPushButton("💾 Créer"); self.btn_create_voice.setObjectName("secondary")
        self.btn_create_voice.setToolTip("Enregistre l'échantillon de clonage comme voix réutilisable")
        self.btn_create_voice.clicked.connect(self.on_create_voice)
        self.btn_delete_voice = QPushButton("🗑"); self.btn_delete_voice.setObjectName("danger")
        self.btn_delete_voice.setToolTip("Supprimer la voix personnelle sélectionnée (locuteur 1)")
        self.btn_delete_voice.clicked.connect(self.on_delete_voice)
        save_row.addWidget(self.edit_voice_name, 1)
        save_row.addWidget(self.combo_gender); save_row.addWidget(self.btn_create_voice)
        save_row.addWidget(self.btn_delete_voice)
        fv.addRow("Voix perso", save_row)

        self.check_format = QCheckBox("Formatage auto avant synthèse (nombres en lettres, sigles épelés…)")
        self.check_format.setChecked(True)
        fv.addRow(self.check_format)
        self.spin_maxwords = QSpinBox()
        self.spin_maxwords.setRange(50, 300)
        self.spin_maxwords.setValue(voxtral.MAX_WORDS_PER_SEGMENT)
        self.spin_maxwords.setSuffix(" mots max / bloc")
        self.spin_maxwords.valueChanged.connect(self._update_count)
        fv.addRow("Découpage", self.spin_maxwords)

        self.check_demo = QCheckBox("Mode test local (aucune API : tonalité de démonstration)")
        fv.addRow(self.check_demo)

        fv.addRow(_hint("Voxtral : 9 langues, clonage zero-shot, ≈ 0,016 $ / 1000 caractères. "
                        "Le script est découpé en blocs, une voix par locuteur, puis réassemblé "
                        "en un seul fichier. Voxtral n'interprète pas les balises : les "
                        "indications entre parenthèses « (rire) », « (pause) »… sont retirées "
                        "automatiquement avant synthèse."))
        return tab

    def _build_tab_export(self):
        tab = QWidget()
        fe = QFormLayout(tab)
        self.combo_fmt = QComboBox()
        self.combo_fmt.addItem("MP3", "mp3")
        self.combo_fmt.addItem("WAV (sans perte)", "wav")
        self.combo_fmt.currentIndexChanged.connect(
            lambda: self.combo_bitrate.setEnabled(self.combo_fmt.currentData() == "mp3"))
        fe.addRow("Format", self.combo_fmt)
        self.combo_bitrate = QComboBox()
        for br in ("192", "256", "320"):
            self.combo_bitrate.addItem(f"{br} kbps", br)
        self.combo_bitrate.setCurrentIndex(2)
        fe.addRow("Débit MP3", self.combo_bitrate)
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("podcast (la date et l'heure sont ajoutées)")
        fe.addRow("Nom du fichier", self.edit_name)
        orow = QHBoxLayout()
        self.edit_out = QLineEdit()
        b = QPushButton("…"); b.setObjectName("secondary")
        b.clicked.connect(self.on_pick_outdir)
        orow.addWidget(self.edit_out, 1); orow.addWidget(b)
        fe.addRow("Dossier", orow)
        self.check_autoplay = QCheckBox("Écouter automatiquement à la fin de la génération")
        fe.addRow(self.check_autoplay)
        return tab

    # ---------------------------------------------------------------- réglages
    def _restore_settings(self):
        c = self.cfg

        def set_combo(combo, value):
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)

        # fournisseur IA : URL / clé / modèle mémorisés séparément pour chaque fournisseur
        self.ai_settings = {k: dict(v) for k, v in (c.get("ai") or {}).items()}
        if not self.ai_settings and c.get("ai_url"):  # migration d'une ancienne config
            self.ai_settings[c.get("ai_provider") or "ollama"] = {
                "url": c.get("ai_url"), "key": c.get("ai_key", ""), "model": c.get("ai_model", "")}
        self.ai_provider = c.get("ai_provider") if isinstance(c.get("ai_provider"), str) else "ollama"
        self.combo_ai_provider.blockSignals(True)
        set_combo(self.combo_ai_provider, self.ai_provider)
        self.combo_ai_provider.blockSignals(False)
        self.ai_provider = self.combo_ai_provider.currentData()
        self.spin_ai_chunk.setValue(int(c.get("ai_chunk_words", aiwriter.DEFAULT_CHUNK_WORDS)))
        set_combo(self.combo_style, c.get("ai_style"))
        self.edit_custom_style.setPlainText(c.get("ai_custom_style", ""))
        self._on_style_changed()

        self.edit_apikey.setText(c.get("mistral_api_key", ""))
        self.edit_api_url.setText(c.get("voxtral_api_url", ""))
        set_combo(self.combo_vox_model, c.get("vox_model"))
        set_combo(self.combo_vox_lang, c.get("vox_lang", "fr"))
        self.edit_ref_audio.setText(c.get("ref_audio", ""))
        self.check_format.setChecked(c.get("auto_format", True))
        self.spin_maxwords.setValue(int(c.get("max_words", voxtral.MAX_WORDS_PER_SEGMENT)))

        set_combo(self.combo_fmt, c.get("format", "mp3"))
        set_combo(self.combo_bitrate, c.get("bitrate", "320"))
        self.combo_bitrate.setEnabled(self.combo_fmt.currentData() == "mp3")
        self.edit_name.setText(c.get("file_name", ""))
        self.edit_out.setText(c.get("output_dir") or DEFAULT_OUTPUT_DIR)
        self.check_autoplay.setChecked(c.get("autoplay", False))

        self._load_ai_provider()
        if c.get("draft"):
            self.text.setPlainText(c["draft"])
        if c.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(c["geometry"].encode()))

    def _save_settings(self):
        self._store_ai_provider()
        c = self.cfg
        for k in ("ai_url", "ai_key", "ai_model"):
            c.pop(k, None)
        c.update({
            "ai_provider": self.ai_provider,
            "ai": self.ai_settings,
            "ai_chunk_words": self.spin_ai_chunk.value(),
            "ai_style": self.combo_style.currentData(),
            "ai_custom_style": self.edit_custom_style.toPlainText().strip(),
            "mistral_api_key": self.edit_apikey.text().strip(),
            "voxtral_api_url": self.edit_api_url.text().strip(),
            "vox_model": self.combo_vox_model.currentData(),
            "vox_lang": self.combo_vox_lang.currentData(),
            "ref_audio": self.edit_ref_audio.text().strip(),
            "auto_format": self.check_format.isChecked(),
            "max_words": self.spin_maxwords.value(),
            "format": self.combo_fmt.currentData(),
            "bitrate": self.combo_bitrate.currentData(),
            "file_name": self.edit_name.text().strip(),
            "output_dir": self.edit_out.text().strip(),
            "autoplay": self.check_autoplay.isChecked(),
            "draft": self.text.toPlainText(),
            "geometry": bytes(self.saveGeometry().toBase64()).decode(),
        })
        # ne pas perdre la voix mémorisée si la liste n'a pas (encore) été chargée
        if self.voices:
            c["voice1"] = self.combo_voice1.currentData()
            c["voice2"] = self.combo_voice2.currentData()
        try:
            save_config(c)
        except OSError as e:
            self._log(f"⚠ Impossible d'enregistrer les réglages : {e}")

    def closeEvent(self, event):
        running = [t for t in self.threads if t.isRunning()]
        if self.gen_thread in running:
            r = QMessageBox.question(self, "Génération en cours",
                                     "Une génération est en cours. Annuler et quitter ?")
            if r != QMessageBox.Yes:
                event.ignore()
                return
        self._save_settings()
        for t in running:
            if hasattr(t, "cancel"):
                t.cancel()
        for t in running:
            t.wait(3000)  # évite « QThread destroyed while running »
        event.accept()

    # ---------------------------------------------------------------- helpers
    def _log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {msg}")

    def _start(self, thread, on_done):
        """Démarre un thread en le gardant référencé jusqu'à sa fin."""
        self.threads.add(thread)
        thread.done.connect(on_done)
        thread.finished.connect(lambda: self.threads.discard(thread))
        thread.start()
        return thread

    def _run_task(self, fn, on_done):
        return self._start(TaskThread(fn), on_done)

    def _vox_credentials(self, warn=True):
        key = self.edit_apikey.text().strip()
        url = self.edit_api_url.text().strip()
        if not key and not url:
            if warn:
                QMessageBox.warning(self, "Clé API",
                                    "Entrez votre clé API Mistral (onglet Voxtral).\n"
                                    "Elle se crée sur console.mistral.ai → API Keys.")
                self.tabs.setCurrentIndex(1)
                self.edit_apikey.setFocus()
            return None
        self._save_settings()
        return key, url or None

    def _output_dir(self):
        d = self.edit_out.text().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(d, exist_ok=True)
        return d

    def _provider(self, code=None):
        code = code or self.ai_provider
        return next((p for p in aiwriter.PROVIDERS if p["code"] == code), aiwriter.PROVIDERS[0])

    def _store_ai_provider(self):
        """Mémorise l'URL / clé / modèle affichés pour le fournisseur courant."""
        self.ai_settings[self.ai_provider] = {
            "url": self.edit_ai_url.text().strip(),
            "key": self.edit_ai_key.text().strip(),
            "model": self.combo_ai_model.currentText().strip(),
        }

    def _load_ai_provider(self):
        """Affiche les réglages du fournisseur sélectionné (ou ses valeurs par défaut)."""
        p = self._provider()
        saved = self.ai_settings.get(p["code"], {})
        self.edit_ai_url.setText(saved.get("url") or p["url"])
        key = saved.get("key", "")
        if not key and p["code"] == "mistral":  # même clé que Voxtral par défaut
            key = self.edit_apikey.text().strip()
        self.edit_ai_key.setText(key)
        self.edit_ai_key.setPlaceholderText(
            "Clé API requise…" if p["key"] else "Clé API (vide pour Ollama local)…")
        self.combo_ai_model.clear()
        self.combo_ai_model.addItems(p["models"])
        model = saved.get("model") or (p["models"][0] if p["models"] else "")
        self.combo_ai_model.setEditText(model)
        self.btn_pull.setEnabled(p["code"] == "ollama")
        self.edit_ai_url.setReadOnly(p["code"] not in ("custom", "ollama"))
        if p["code"] == "ollama":
            self.combo_ai_model.lineEdit().setPlaceholderText("modèle installé (ex. qwen3:8b)…")
            self.on_list_models(silent=True)  # ne propose que ce qui est réellement installé

    def _on_ai_provider(self, _i):
        self._store_ai_provider()
        self.ai_provider = self.combo_ai_provider.currentData()
        self._load_ai_provider()

    def _on_style_changed(self, *_):
        custom = self.combo_style.currentData() == "custom"
        self.edit_custom_style.setPlaceholderText(
            "Décrivez le style : ton, rythme, intentions…" if custom else
            "Consignes supplémentaires (optionnel) : public visé, durée, angle…")

    # ---------------------------------------------------------------- Assistant IA
    def on_list_models(self, silent=False):
        base = self.edit_ai_url.text().strip()
        if not base:
            if not silent:
                self._log("❌ Entrez d'abord l'URL API.")
            return
        key = self.edit_ai_key.text().strip()
        provider = self.ai_provider
        if not silent:
            self._log(f"Chargement des modèles depuis {base}…")

        def done(ok, result):
            if provider != self.ai_provider:  # le fournisseur a changé entre-temps
                return
            if not ok:
                if silent and provider == "ollama":
                    self._log("ℹ Ollama local non détecté (lancez Ollama pour utiliser l'assistant "
                              "hors connexion) — ou choisissez un fournisseur cloud.")
                else:
                    self._log("❌ Liste des modèles : " + str(result))
                return
            self._apply_model_list(result, silent)
        self._run_task(lambda: aiwriter.list_models(base, key), done)

    def _apply_model_list(self, models, silent=False):
        current = self.combo_ai_model.currentText().strip()
        local = self.ai_provider == "ollama"
        self.combo_ai_model.clear()
        self.combo_ai_model.addItems(models)
        if current in models or (not local and current):
            self.combo_ai_model.setEditText(current)
        elif models:  # Ollama local : modèle absent → premier modèle réellement installé
            self.combo_ai_model.setCurrentIndex(0)
            if current:
                self._log(f"ℹ Le modèle « {current} » n'est pas installé : "
                          f"« {models[0]} » sélectionné à la place.")
        if models:
            if not silent or local:
                self._log(f"✅ {len(models)} modèle(s) disponible(s) : {', '.join(models[:8])}"
                          + ("…" if len(models) > 8 else ""))
            if local and re.search(r"[:\-_](0\.\d+|[1-3])b\b", self.combo_ai_model.currentText().lower()):
                self._log("⚠ Ce modèle est très petit : la qualité de réécriture sera faible. "
                          "Préférez un modèle de 7–8 milliards de paramètres ou plus.")
        elif local:
            sug = ", ".join(self._provider()["suggest"])
            self._log(f"⚠ Aucun modèle installé sur Ollama. Tapez un nom (ex. {sug}) "
                      "puis cliquez ⬇ Installer.")

    def on_pull_model(self):
        name = self.combo_ai_model.currentText().strip()
        if not name:
            QMessageBox.information(self, "Modèle", "Tapez le nom du modèle à installer "
                                    "(ex. llama3.1, mistral, qwen3).")
            return
        base = self.edit_ai_url.text().strip() or "http://localhost:11434/v1"
        self._log(f"⬇ Téléchargement du modèle « {name} » (peut prendre plusieurs minutes)…")
        self.btn_pull.setEnabled(False)
        th = PullThread(name, base)

        def on_prog(pct, status):
            if pct >= 0 and self.gen_thread is None:
                self.progress.setValue(pct)
            self.status.showMessage(f"⬇ {name} : {status}")

        def done(ok, msg):
            self.btn_pull.setEnabled(True)
            if self.gen_thread is None:
                self.progress.setValue(0)
            self.status.clearMessage()
            if ok:
                self._log(f"✅ Modèle « {msg} » installé.")
                self.on_list_models()
            else:
                self._log("❌ Installation du modèle : " + msg)
        th.progress.connect(on_prog)
        self._start(th, done)

    def on_rewrite(self):
        if self.rewrite_thread is not None:  # le bouton sert aussi d'annulation
            self.rewrite_thread.cancel()
            self.btn_rewrite.setEnabled(False)
            self.btn_rewrite.setText("Annulation… (fin du passage en cours)")
            return
        text = self.text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Script", "Écrivez d'abord votre texte brut.")
            return
        base_url = self.edit_ai_url.text().strip()
        model = self.combo_ai_model.currentText().strip()
        key = self.edit_ai_key.text().strip()
        p = self._provider()
        if not base_url or not model:
            QMessageBox.warning(self, "Assistant IA", "URL API et modèle requis "
                                "(📥 Liste pour voir les modèles disponibles).")
            return
        if p["key"] and not key:
            QMessageBox.warning(self, "Assistant IA", f"Entrez votre clé API ({p['label']}).")
            self.edit_ai_key.setFocus()
            return
        self._save_settings()
        style_code = self.combo_style.currentData()
        extra = self.edit_custom_style.toPlainText().strip()
        preset = dict((c, pr) for c, _l, pr in aiwriter.STYLES).get(style_code, "")
        if style_code == "custom":
            if not extra:
                QMessageBox.warning(self, "Style", "Décrivez le style personnalisé.")
                return
            style_prompt = extra
        else:
            style_prompt = preset + (f"\nConsignes supplémentaires : {extra}" if extra else "")
        lang = self.combo_vox_lang.currentData()
        n_chunks = len(aiwriter.split_for_ai(text, self.spin_ai_chunk.value()))
        self.btn_rewrite.setText("⏹  Annuler la réécriture")
        self._log(f"🎭 Réécriture par {model} : {len(text.split())} mots en {n_chunks} passage(s)…")
        started = time.monotonic()
        th = RewriteThread(text, base_url, key, model, style_prompt, lang, self.spin_ai_chunk.value())
        self.rewrite_thread = th

        def on_progress(i, n, msg):
            if self.gen_thread is None:
                self.progress.setValue(int(100 * i / max(1, n)))
            self.status.showMessage(f"🎭 {msg} ({model}) · {_fmt_duration(time.monotonic() - started)}")

        def done(ok, out):
            self.rewrite_thread = None
            self.btn_rewrite.setEnabled(True)
            self.btn_rewrite.setText(self.REWRITE_LABEL)
            if self.gen_thread is None:
                self.progress.setValue(0)
            self.status.clearMessage()
            if ok:
                self._replace_text(out)
                self._log(f"✅ Script réécrit en {_fmt_duration(time.monotonic() - started)} "
                          f"({len(text.split())} → {len(out.split())} mots). Vérifiez-le "
                          "(Ctrl+Z pour annuler), puis générez.")
            else:
                self._log(("⏹ " if "annulée" in str(out) else "❌ Assistant IA : ") + str(out))
                miss = th.missing
                if miss is not None and miss.local:
                    if miss.installed:
                        self._apply_model_list(miss.installed, silent=True)
                    r = QMessageBox.question(
                        self, "Modèle non installé",
                        f"Le modèle « {miss.model} » n'est pas installé sur votre Ollama.\n\n"
                        + (f"Modèles installés : {', '.join(miss.installed)}\n\n" if miss.installed else "")
                        + f"Télécharger « {miss.model} » maintenant (plusieurs Go possibles) ?",
                        QMessageBox.Yes | QMessageBox.No)
                    if r == QMessageBox.Yes:
                        self.combo_ai_model.setEditText(miss.model)
                        self.on_pull_model()
        th.progress.connect(on_progress)
        th.log.connect(self._log)
        self._start(th, done)

    # ---------------------------------------------------------------- voix
    def _fill_voice_combos(self):
        """Remplit les deux listes de voix en conservant / restaurant la sélection."""
        prev1 = self.combo_voice1.currentData() if self.combo_voice1.count() else self.cfg.get("voice1")
        prev2 = self.combo_voice2.currentData() if self.combo_voice2.count() else self.cfg.get("voice2")
        if prev1 is None:
            prev1 = self.cfg.get("voice1")
        if prev2 is None:
            prev2 = self.cfg.get("voice2", SAME_VOICE)
        self.combo_voice1.clear()
        self.combo_voice2.clear()
        if not self.voices:
            self.combo_voice1.addItem("(🔄 pour charger les voix)", None)
        self.combo_voice2.addItem("(même voix que le locuteur 1)", SAME_VOICE)
        for v in self.voices:
            langs = ",".join(v["languages"] or [])
            mark = "★ " if v["type"] == "custom" else ""
            label = f'{mark}{v["name"]}' + (f" [{langs}]" if langs else "")
            self.combo_voice1.addItem(label, v["id"])
            self.combo_voice2.addItem(label, v["id"])
        for combo, prev in ((self.combo_voice1, prev1), (self.combo_voice2, prev2)):
            i = combo.findData(prev)
            if i >= 0:
                combo.setCurrentIndex(i)

    def _auto_pick_voice(self):
        """Choisit une voix correspondant à la langue si aucune n'est sélectionnée."""
        lang = self.combo_vox_lang.currentData()
        for i, v in enumerate(self.voices):
            if lang in (v["languages"] or []):
                return i
        return 0

    def on_refresh_voices(self, silent=False):
        creds = self._vox_credentials(warn=not silent)
        if creds is None:
            return
        key, url = creds
        if not silent:
            self._log("Récupération des voix…")
        self.btn_refresh_voices.setEnabled(False)

        def fetch():
            out = []
            for v in voxtral.VoxtralClient(key, url).list_voices():
                out.append({
                    "id": getattr(v, "id", None) or getattr(v, "slug", None),
                    "name": getattr(v, "name", "?"),
                    "languages": list(getattr(v, "languages", None) or []),
                    "type": getattr(v, "type", None),
                    "gender": getattr(v, "gender", None),
                })
            # voix personnelles d'abord, puis tri alphabétique
            out.sort(key=lambda v: (v["type"] != "custom", v["name"].lower()))
            return out

        def done(ok, items):
            self.btn_refresh_voices.setEnabled(True)
            if not ok:
                self._log("❌ Voix : " + str(items))
                return
            had_selection = self.cfg.get("voice1") or (self.voices and self.combo_voice1.currentData())
            self.voices = items
            self._fill_voice_combos()
            if not had_selection and items:
                self.combo_voice1.setCurrentIndex(self._auto_pick_voice())
            n_custom = sum(1 for v in items if v["type"] == "custom")
            self._log(f"✅ {len(items)} voix disponibles"
                      + (f" dont {n_custom} personnelle(s) ★" if n_custom else "") + ".")
        self._run_task(fetch, done)

    def on_pick_ref_audio(self):
        p, _ = QFileDialog.getOpenFileName(self, "Échantillon vocal (5–25 s idéalement)", "",
                                           "Audio (*.wav *.mp3 *.flac *.ogg *.m4a)")
        if p:
            self.edit_ref_audio.setText(p)

    def on_pick_outdir(self):
        p = QFileDialog.getExistingDirectory(self, "Dossier de sortie", self._output_dir())
        if p:
            self.edit_out.setText(p)

    def on_open_dir(self):
        try:
            os.startfile(self._output_dir())
        except OSError as e:
            self._log(f"❌ Dossier : {e}")

    def on_create_voice(self):
        sample = self.edit_ref_audio.text().strip()
        name = self.edit_voice_name.text().strip()
        if not sample or not os.path.isfile(sample) or not name:
            QMessageBox.warning(self, "Voix", "Choisissez un échantillon audio (champ Clonage) "
                                "et donnez un nom à la voix.")
            return
        creds = self._vox_credentials()
        if creds is None:
            return
        key, url = creds
        lang = self.combo_vox_lang.currentData()
        gender = self.combo_gender.currentData()
        self.btn_create_voice.setEnabled(False)
        self._log(f"Création de la voix « {name} »…")

        def done(ok, v):
            self.btn_create_voice.setEnabled(True)
            if not ok:
                self._log("❌ Création voix : " + str(v))
                return
            vid = getattr(v, "id", None)
            self._log(f"✅ Voix créée : {name}")
            self.cfg["voice1"] = vid
            self.edit_voice_name.clear()
            self.edit_ref_audio.clear()  # la voix sauvegardée remplace le clonage ponctuel
            self.combo_voice1.clear()    # force la sélection de la nouvelle voix
            self.on_refresh_voices(silent=True)
        self._run_task(lambda: voxtral.VoxtralClient(key, url).create_voice(
            name, sample, languages=[lang], gender=gender), done)

    def on_delete_voice(self):
        vid = self.combo_voice1.currentData()
        voice = next((v for v in self.voices if v["id"] == vid), None)
        if not voice or voice["type"] != "custom":
            QMessageBox.information(self, "Voix", "Sélectionnez une voix personnelle (★) "
                                    "dans « Locuteur 1 ». Les voix prédéfinies ne peuvent pas "
                                    "être supprimées.")
            return
        if QMessageBox.question(self, "Supprimer la voix",
                                f"Supprimer définitivement la voix « {voice['name']} » ?") != QMessageBox.Yes:
            return
        creds = self._vox_credentials()
        if creds is None:
            return
        key, url = creds

        def done(ok, msg):
            if not ok:
                self._log("❌ Suppression : " + str(msg))
                return
            self._log(f"✅ Voix « {voice['name']} » supprimée.")
            self.on_refresh_voices(silent=True)
        self._run_task(lambda: voxtral.VoxtralClient(key, url).delete_voice(vid), done)

    def on_format_text(self):
        raw = self.text.toPlainText()
        if not raw.strip():
            return
        cleaned, n_dir = textprep.strip_stage_directions(raw)
        formatted = textprep.format_text(cleaned, self.combo_vox_lang.currentData())
        if formatted != raw.strip():
            self._replace_text(formatted)
            self._log("✨ Texte formaté selon les bonnes pratiques Voxtral"
                      + (f" ({n_dir} indication(s) scénique(s) retirée(s))" if n_dir else "")
                      + ". Ctrl+Z pour annuler.")
        else:
            self._log("Le texte est déjà conforme.")

    # ---------------------------------------------------------------- génération
    def _set_generating(self, on):
        self.btn_generate.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        self.btn_generate.setText("⏳  Génération en cours…" if on else "🎙  Générer le podcast")

    def on_generate(self):
        if self.gen_thread is not None:
            return
        text = self.text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Script", "Écrivez d'abord votre texte.")
            return
        lang = self.combo_vox_lang.currentData()
        fmt = self.combo_fmt.currentData()
        try:
            out_dir = self._output_dir()
        except OSError as e:
            QMessageBox.warning(self, "Dossier de sortie", f"Dossier inaccessible :\n{e}")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = "".join(ch for ch in (self.edit_name.text().strip() or "podcast")
                       if ch not in '<>:"/\\|?*').strip() or "podcast"
        out = os.path.join(out_dir, f"{stem}_{ts}.{fmt}")

        # Voxtral lirait les indications scéniques : on les retire toujours
        text, n_dir = textprep.strip_stage_directions(text)
        if n_dir:
            self._log(f"🧹 {n_dir} indication(s) scénique(s) entre parenthèses retirée(s) "
                      "(Voxtral les lirait à voix haute).")
        if self.check_format.isChecked():
            formatted = textprep.format_text(text, lang)
            if formatted != text:
                self._log("✨ Formatage appliqué (nombres, sigles, symboles).")
                text = formatted

        max_words = self.spin_maxwords.value()
        if self.check_demo.isChecked():
            th = DemoThread(text, out, max_words)
            self._log("Mode test local : génération d'une tonalité de démonstration…")
        else:
            creds = self._vox_credentials()
            if creds is None:
                return
            key, url = creds
            ref = self.edit_ref_audio.text().strip() or None
            if ref and not os.path.isfile(ref):
                QMessageBox.warning(self, "Clonage", f"Échantillon introuvable :\n{ref}")
                return
            v1 = self.combo_voice1.currentData()
            if not v1 and not ref:
                if not self.voices:
                    self._log("Aucune voix chargée : récupération de la liste, puis relancez.")
                    self.on_refresh_voices()
                    return
                self.combo_voice1.setCurrentIndex(self._auto_pick_voice())
                v1 = self.combo_voice1.currentData()
                self._log(f"Voix auto-sélectionnée : {self.combo_voice1.currentText()}")
            voice = next((v for v in self.voices if v["id"] == v1), None)
            if voice and voice["languages"] and lang not in voice["languages"] and not ref:
                self._log(f"⚠ La voix « {voice['name']} » ne déclare pas la langue « {lang} » : "
                          "pour un meilleur rendu, la voix et le texte doivent être dans la même "
                          "langue (une voix d'une autre langue donnera un accent).")
            v2 = self.combo_voice2.currentData()
            voices = {1: v1}
            if v2 and v2 != SAME_VOICE:
                voices.update({n: v2 for n in range(2, 10)})
            th = VoxtralThread(key, text, out, model=self.combo_vox_model.currentData(),
                               fmt=fmt, bitrate=int(self.combo_bitrate.currentData()),
                               voices=voices, ref_audio_path=ref,
                               max_words=max_words, base_url=url)
            who = "échantillon cloné" if ref else self.combo_voice1.currentText()
            self._log(f"🎙 Génération Voxtral ({self.combo_vox_model.currentData()}, "
                      f"voix : {who}) — ≈ {voxtral.estimate_cost(text):.3f} $")

        th.progress.connect(self._on_gen_progress)
        th.log.connect(self._log)
        self.gen_thread = th
        self.gen_started = time.monotonic()
        self.progress.setValue(0)
        self._set_generating(True)
        self._start(th, self._on_gen_done)

    def _on_gen_progress(self, pct, msg):
        self.progress.setValue(pct)
        elapsed = time.monotonic() - self.gen_started
        self.status.showMessage(f"{msg}  ·  {_fmt_duration(elapsed)} écoulées")

    def _on_gen_done(self, ok, msg):
        self.gen_thread = None
        self._set_generating(False)
        elapsed = time.monotonic() - self.gen_started
        if ok:
            info = json.loads(msg)
            self.last_audio = info["path"]
            self.progress.setValue(100)
            self.btn_listen.setEnabled(True)
            self._log(f"✅ Épisode exporté : {info['path']} — {_fmt_duration(info['duration'])} "
                      f"d'audio, {info['blocks']} bloc(s), généré en {_fmt_duration(elapsed)}.")
            self.status.showMessage("Terminé.", 8000)
            if self.check_autoplay.isChecked():
                self.on_listen()
        else:
            self.progress.setValue(0)
            self.status.clearMessage()
            self._log(("⏹ " if "annulée" in msg else "❌ ") + msg)

    def on_cancel(self):
        if self.gen_thread is not None:
            self.gen_thread.cancel()
            self.btn_cancel.setEnabled(False)
            self.status.showMessage("Annulation… (fin du bloc en cours)")

    def on_listen(self):
        if self.last_audio and os.path.exists(self.last_audio):
            os.startfile(self.last_audio)
        else:
            self._log("❌ Fichier introuvable : " + str(self.last_audio))
