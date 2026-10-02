"""
Moteur Voxtral TTS — API Mistral (cloud).

- Modèle : voxtral-mini-tts-2603 (9 langues : fr, en, de, es, nl, pt, it, hi, ar)
- Clonage de voix zero-shot : échantillon de 3 s minimum (ref_audio ponctuel
  ou voix sauvegardée via l'API Voices)
- Épisodes entiers : découpage automatique du script en segments < ~300 mots
  (recommandation officielle), synthèse segment par segment, puis concaténation.
- Multi-locuteurs : chaque « Speaker N: » est synthétisé avec sa propre voix ;
  la balise elle-même n'est jamais envoyée à l'API (elle serait lue à voix haute).
"""
import base64
import io
import os
import re
import struct
import time

API_MODELS = [
    ("voxtral-mini-tts-2603", "Voxtral Mini TTS (2603) — rapide, épinglé"),
    ("voxtral-mini-tts-latest", "Voxtral Mini TTS (latest) — dernière version"),
]

LANGUAGES = [
    ("fr", "Français"), ("en", "English"), ("de", "Deutsch"), ("es", "Español"),
    ("nl", "Nederlands"), ("pt", "Português"), ("it", "Italiano"),
    ("hi", "हिन्दी"), ("ar", "العربية"),
]

MAX_WORDS_PER_SEGMENT = 280  # recommandation Mistral : < 300 mots par requête
PRICE_PER_1000_CHARS = 0.016  # USD
SPEAKER_GAP_S = 0.35          # silence inséré entre deux répliques de locuteurs différents
PARAGRAPH_GAP_S = 0.15        # silence entre deux blocs d'un même locuteur
MAX_RETRIES = 3

_SPEAKER_RE = re.compile(r"Speaker\s*(\d+)\s*:", re.I)


# ------------------------------------------------------------------ découpage
def _split_long_sentence(sentence, max_words):
    words = sentence.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def split_script_speakers(text, max_words=MAX_WORDS_PER_SEGMENT):
    """Découpe le script en segments [(locuteur:int, texte)] :
    - rupture à chaque changement de locuteur (« Speaker N: », retiré du texte),
    - regroupement des phrases jusqu'à max_words,
    - une phrase plus longue que max_words est coupée sur les mots."""
    # repère les tours de parole ; le texte avant la première balise = locuteur 1
    turns, speaker, pos = [], 1, 0
    for m in _SPEAKER_RE.finditer(text):
        turns.append((speaker, text[pos:m.start()]))
        speaker, pos = int(m.group(1)), m.end()
    turns.append((speaker, text[pos:]))

    segments = []
    for spk, chunk in turns:
        current, count = [], 0
        for para in re.split(r"\n\s*\n", chunk):
            for s in re.split(r"(?<=[.!?…])\s+", para.strip()):
                s = s.strip()
                if not s:
                    continue
                for piece in _split_long_sentence(s, max_words):
                    w = len(piece.split())
                    if count + w > max_words and current:
                        segments.append((spk, " ".join(current)))
                        current, count = [], 0
                    current.append(piece)
                    count += w
        if current:
            segments.append((spk, " ".join(current)))
    return segments


def split_script(text, max_words=MAX_WORDS_PER_SEGMENT):
    """Compatibilité : liste des textes des segments."""
    return [s for _spk, s in split_script_speakers(text, max_words)]


def estimate_cost(text):
    """Coût approximatif en USD (balises de locuteur exclues)."""
    chars = len(_SPEAKER_RE.sub("", text))
    return chars / 1000 * PRICE_PER_1000_CHARS


# ------------------------------------------------------------------ audio
def _parse_wav(data):
    """Lit un WAV (PCM ou float) sans dépendre du module `wave` : tolère les en-têtes
    de streaming dont la taille de données est 0 ou 0xFFFFFFFF.
    Renvoie (format_tag, channels, sample_rate, bits, pcm_bytes)."""
    if not data or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError("Réponse audio invalide (WAV attendu)")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt ":
            tag, ch, sr, _br, _ba, bits = struct.unpack("<HHIIHH", data[body:body + 16])
            if tag == 0xFFFE and size >= 26:  # WAVE_FORMAT_EXTENSIBLE
                tag = struct.unpack("<H", data[body + 24:body + 26])[0]
            fmt = (tag, ch, sr, bits)
        elif cid == b"data":
            if fmt is None:
                raise RuntimeError("WAV sans en-tête fmt")
            end = len(data) if size in (0, 0xFFFFFFFF) or body + size > len(data) else body + size
            return (*fmt, data[body:end])
        pos = body + size + (size & 1)
    raise RuntimeError("WAV sans données audio")


def _build_wav(tag, channels, sr, bits, pcm):
    block = channels * bits // 8
    out = io.BytesIO()
    out.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE")
    out.write(b"fmt " + struct.pack("<IHHIIHH", 16, tag, channels, sr, sr * block, block, bits))
    out.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
    return out.getvalue()


def concat_wav(chunks):
    """Concatène des WAV de même format. `chunks` : liste de (wav_bytes, silence_avant_s).
    Renvoie (wav_bytes, durée_s)."""
    if not chunks:
        raise RuntimeError("Aucun segment audio à assembler (script vide ?)")
    params, parts = None, []
    for data, gap in chunks:
        tag, ch, sr, bits, pcm = _parse_wav(data)
        if params is None:
            params = (tag, ch, sr, bits)
        elif (tag, ch, sr, bits) != params:
            raise RuntimeError("Segments audio de formats différents : impossible de les assembler")
        block = ch * bits // 8
        if parts and gap > 0:
            parts.append(b"\x00" * (int(sr * gap) * block))
        parts.append(pcm[:len(pcm) - len(pcm) % block])
    pcm = b"".join(parts)
    tag, ch, sr, bits = params
    return _build_wav(tag, ch, sr, bits, pcm), len(pcm) / (sr * ch * bits // 8)


def to_mp3(wav_bytes, bitrate):
    import imageio_ffmpeg
    import subprocess
    import tempfile
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        tmp_in = f.name
    tmp_out = tmp_in + ".mp3"
    try:
        p = subprocess.run([ffmpeg, "-y", "-i", tmp_in, "-codec:a", "libmp3lame",
                            "-b:a", f"{bitrate}k", tmp_out], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode != 0:
            raise RuntimeError("Échec encodage MP3 : " + p.stderr.decode(errors="ignore")[-400:])
        with open(tmp_out, "rb") as f:
            return f.read()
    finally:
        for p_ in (tmp_in, tmp_out):
            try:
                os.remove(p_)
            except OSError:
                pass


def unique_path(path):
    """Évite d'écraser un fichier existant : podcast.mp3 → podcast (2).mp3."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


# ------------------------------------------------------------------ client API
def _is_transient(exc):
    code = getattr(exc, "status_code", None)
    if code is not None:
        return code == 429 or code >= 500
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "connect", "network", "remoteprotocol", "noresponse"))


class VoxtralClient:
    def __init__(self, api_key, base_url=None):
        from mistralai.client import Mistral
        kwargs = {"api_key": api_key or "local", "timeout_ms": 180_000}
        if base_url:
            kwargs["server_url"] = base_url.rstrip("/")
        self.client = Mistral(**kwargs)

    def list_voices(self):
        """Toutes les voix (préréglées + personnelles), pagination comprise."""
        items, offset, page = [], 0, 100
        while True:
            resp = self.client.audio.voices.list(limit=page, offset=offset)
            batch = list(getattr(resp, "items", None) or [])
            items.extend(batch)
            offset += len(batch)
            total = getattr(resp, "total", None)
            if not batch or len(batch) < page or (total is not None and offset >= total):
                return items

    def create_voice(self, name, sample_path, languages=None, gender=None):
        with open(sample_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        kwargs = {"name": name, "sample_audio": b64,
                  "sample_filename": os.path.basename(sample_path)}
        if languages:
            kwargs["languages"] = languages
        if gender:
            kwargs["gender"] = gender
        return self.client.audio.voices.create(**kwargs)

    def delete_voice(self, voice_id):
        return self.client.audio.voices.delete(voice_id=voice_id)

    def synthesize_segment(self, text, model, response_format="wav",
                           voice_id=None, ref_audio_b64=None):
        # l'API déduit la langue automatiquement (pas de paramètre `language`)
        kwargs = {"model": model, "input": text, "response_format": response_format}
        if voice_id:
            kwargs["voice_id"] = voice_id
        elif ref_audio_b64:
            kwargs["ref_audio"] = ref_audio_b64
        resp = self.client.audio.speech.complete(**kwargs)
        data = getattr(resp, "audio_data", None) or getattr(resp, "audio", None)
        if isinstance(data, str):
            return base64.b64decode(data)
        if not data:
            raise RuntimeError("L'API n'a renvoyé aucun audio")
        return data


def generate_episode(client, text, model, out_path, fmt, bitrate, voices=None,
                     ref_audio_path=None, progress=None, cancel=None,
                     max_words=MAX_WORDS_PER_SEGMENT, log=None):
    """Génère un épisode complet : découpage en blocs ≤ max_words, synthèse par bloc
    (une voix par locuteur), puis réassemblage en UN SEUL fichier final.

    voices : {numéro_locuteur: voice_id}. Le locuteur 1 utilise l'échantillon
    `ref_audio_path` s'il est fourni (clonage ponctuel). Un locuteur sans voix
    attribuée reprend la voix du locuteur 1."""
    voices = voices or {}
    ref_b64 = None
    if ref_audio_path:
        with open(ref_audio_path, "rb") as f:
            ref_b64 = base64.b64encode(f.read()).decode()

    def voice_for(spk):
        if spk == 1 and ref_b64:
            return None, ref_b64
        vid = voices.get(spk) or voices.get(1)
        if vid is None and ref_b64:
            return None, ref_b64
        return vid, None

    segments = split_script_speakers(text, max_words=max_words)
    if not segments:
        raise RuntimeError("Le script est vide après nettoyage : rien à synthétiser.")
    n_spk = len({s for s, _ in segments})
    if progress:
        progress(2, f"{len(segments)} bloc(s) de ≤ {max_words} mots"
                    + (f", {n_spk} locuteurs" if n_spk > 1 else "") + " à synthétiser…")
    chunks, prev_spk = [], None
    for i, (spk, seg) in enumerate(segments):
        vid, ref = voice_for(spk)
        for attempt in range(MAX_RETRIES + 1):
            if cancel and cancel():
                raise InterruptedError("Génération annulée")
            try:
                # demande WAV pour concaténer proprement, conversion finale ensuite
                audio = client.synthesize_segment(seg, model, response_format="wav",
                                                  voice_id=vid, ref_audio_b64=ref)
                break
            except Exception as e:
                if attempt >= MAX_RETRIES or not _is_transient(e):
                    raise RuntimeError(f"Bloc {i + 1}/{len(segments)} : {type(e).__name__}: {e}") from e
                wait = 2 ** (attempt + 1)
                if log:
                    log(f"⚠ Bloc {i + 1} : erreur temporaire ({type(e).__name__}), "
                        f"nouvel essai dans {wait} s…")
                for _ in range(wait * 10):
                    if cancel and cancel():
                        raise InterruptedError("Génération annulée")
                    time.sleep(0.1)
        gap = 0 if prev_spk is None else (SPEAKER_GAP_S if spk != prev_spk else PARAGRAPH_GAP_S)
        chunks.append((audio, gap))
        prev_spk = spk
        if progress:
            progress(2 + int(94 * (i + 1) / len(segments)),
                     f"Bloc {i + 1}/{len(segments)} synthétisé"
                     + (f" (locuteur {spk})" if n_spk > 1 else ""))
    wav_bytes, duration = concat_wav(chunks)  # réassemblage de tous les blocs
    if progress:
        progress(97, "Assemblage et export du fichier…")
    if fmt == "mp3":
        data = to_mp3(wav_bytes, bitrate)
        out_path = os.path.splitext(out_path)[0] + ".mp3"
    else:
        data = wav_bytes
        out_path = os.path.splitext(out_path)[0] + ".wav"
    out_path = unique_path(out_path)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path, duration, len(segments)
