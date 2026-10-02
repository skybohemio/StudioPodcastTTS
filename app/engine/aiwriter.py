"""
Assistant IA de réécriture de script de podcast.

Fournisseurs : Ollama local, Ollama Cloud, Kimi/Moonshot, Mistral, OpenAI, Anthropic,
DeepSeek, OpenRouter, Groq, Gemini, ou tout endpoint OpenAI-compatible (URL + clé).

Le texte est réécrit PASSAGE PAR PASSAGE (≈ 350 mots) : envoyer un épisode entier
d'un coup fait résumer le texte par les petits modèles (contexte trop court) et perdre
l'essentiel du contenu. Chaque passage reçoit la fin du précédent pour la continuité,
la longueur de la sortie est contrôlée, et le passage d'origine est conservé si le
modèle l'a trop raccourci.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

# code, libellé, URL par défaut, clé API requise, modèles suggérés
PROVIDERS = [
    {"code": "ollama", "label": "Ollama — local (hors connexion)",
     "url": "http://localhost:11434/v1", "key": False,
     "models": [],  # jamais de noms supposés : on liste ce qui est réellement installé
     "suggest": ["qwen3:8b", "llama3.1:8b", "mistral:7b", "gemma3:12b"]},
    {"code": "ollama_cloud", "label": "Ollama Cloud (clé API)",
     "url": "https://ollama.com/v1", "key": True,
     "models": ["gpt-oss:120b", "deepseek-v3.1:671b", "kimi-k2:1t", "qwen3-coder:480b"]},
    {"code": "moonshot", "label": "Kimi / Moonshot (clé API)",
     "url": "https://api.moonshot.ai/v1", "key": True,
     "models": ["kimi-k2-0905-preview", "kimi-k2-turbo-preview", "moonshot-v1-32k"]},
    {"code": "mistral", "label": "Mistral (clé API)",
     "url": "https://api.mistral.ai/v1", "key": True,
     "models": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"]},
    {"code": "openai", "label": "OpenAI (clé API)",
     "url": "https://api.openai.com/v1", "key": True,
     "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1"]},
    {"code": "anthropic", "label": "Anthropic Claude (clé API)",
     "url": "https://api.anthropic.com/v1", "key": True,
     "models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"]},
    {"code": "deepseek", "label": "DeepSeek (clé API)",
     "url": "https://api.deepseek.com/v1", "key": True, "models": ["deepseek-chat"]},
    {"code": "gemini", "label": "Google Gemini (clé API)",
     "url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": True,
     "models": ["gemini-2.5-flash", "gemini-2.5-pro"]},
    {"code": "groq", "label": "Groq (clé API)",
     "url": "https://api.groq.com/openai/v1", "key": True,
     "models": ["llama-3.3-70b-versatile"]},
    {"code": "openrouter", "label": "OpenRouter (clé API)",
     "url": "https://openrouter.ai/api/v1", "key": True, "models": []},
    {"code": "custom", "label": "OpenAI-compatible (personnalisé)",
     "url": "", "key": False, "models": []},
]

STYLES = [
    ("deutsch", "Laurent Deutsch — « Entrez dans l'histoire » (RTL)",
     "Adopte le style de Laurent Deutsch dans « Entrez dans l'histoire » sur RTL : "
     "ton passionné et complice, phrases rythmées avec des montées dramatiques, "
     "anecdotes concrètes, rhétorique narrative (« Imaginez… », « Figurez-vous que… »), "
     "silences stratégiques matérialisés par des points de suspension, "
     "questions directes à l'auditeur, transitions fluides entre les époques."),
    ("docu", "Documentaire immersif",
     "Ton de documentaire immersif : voix posée, descriptive, cinématographique, "
     "phrases courtes pour les moments forts, plus amples pour le contexte."),
    ("convers", "Podcast conversationnel",
     "Ton de podcast conversationnel : naturel, chaleureux, direct, comme une "
     "discussion entre amis passionnés, avec relances et touches d'humour."),
    ("custom", "Style personnalisé (champ ci-dessous)", ""),
]

LANG_NAMES = {"fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol",
              "nl": "néerlandais", "pt": "portugais", "it": "italien", "hi": "hindi",
              "ar": "arabe"}

DEFAULT_CHUNK_WORDS = 350
MIN_RATIO = 0.6   # en dessous : le modèle a résumé → nouvel essai
FLOOR_RATIO = 0.5  # toujours en dessous après l'essai : on garde le passage d'origine

SYSTEM_PROMPT = ("Tu es un auteur de podcasts primé. Tu réécris fidèlement, sans jamais "
                 "résumer. Tu réponds uniquement par le script demandé, sans préambule ni "
                 "commentaire.")

PROMPT_TEMPLATE = """Réécris le passage de texte brut ci-dessous en script de podcast \
expressif, percutant et intéressant, destiné à être lu à voix haute.

Style demandé :
{style}

Position : passage {i} sur {n} d'un même épisode. {position}
{previous}
RÈGLES IMPÉRATIVES :
- N'INVENTE RIEN : n'ajoute AUCUN fait, nom, date, chiffre, lieu ou anecdote absent du texte \
d'origine. Tu changes la forme (rythme, ton, transitions), jamais le fond.
- NE RÉSUME PAS, N'ABRÈGE PAS, NE SAUTE RIEN : conserve TOUS les faits, noms, dates, chiffres \
et idées du passage. Le texte réécrit doit avoir une longueur au moins égale à l'original \
(environ {words} mots ou plus, mais sans dépasser {max_words} mots : pas de remplissage).
- Texte lu tel quel par une voix de synthèse : phrases naturelles et prononçables.
- Nombres, symboles, monnaies et pourcentages EN TOUTES LETTRES (« mille deux cent trente-quatre », pas « 1234 »).
- Abréviations épellées avec des traits d'union (« F-B-I », pas « FBI »), sauf acronymes prononçables (NASA, ONU).
- AUCUN markdown, AUCUN emoji, AUCUN caractère spécial, AUCUNE URL.
- AUCUNE indication scénique entre parenthèses ou crochets (pas de « (rire) », « [pause] », \
« (ton grave) ») : la voix les lirait à voix haute.
- L'émotion et le rythme passent UNIQUEMENT par l'écriture et la ponctuation : virgules pour \
les respirations, points de suspension pour les silences dramatiques, ! et ? pour l'énergie, \
phrases courtes pour la tension, ligne vide entre deux paragraphes.
- Langue de sortie : {lang}.
- Si le passage contient des balises « Speaker 1: » / « Speaker 2: », garde-les en début de \
réplique ; sinon n'en ajoute JAMAIS (texte d'un seul narrateur).
- Aucun titre, aucun commentaire, jamais de « Voici… » : UNIQUEMENT le script de ce passage.

Passage à réécrire :
{text}"""

POSITION_NOTES = {
    "first": "C'est le DÉBUT de l'épisode : tu peux ouvrir par une courte accroche.",
    "middle": "Ce passage est au MILIEU de l'épisode : n'ajoute NI introduction NI conclusion, "
              "enchaîne naturellement.",
    "last": "C'est la FIN de l'épisode : tu peux conclure.",
    "only": "Ce texte est l'épisode complet : tu peux ouvrir par une accroche et conclure.",
}


# ------------------------------------------------------------------ découpage
def split_for_ai(text, max_words=DEFAULT_CHUNK_WORDS):
    """Découpe le texte en passages ≤ max_words, sur les frontières de paragraphes
    puis de phrases (jamais au milieu d'une phrase sauf phrase géante)."""
    units = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        if len(para.split()) <= max_words:
            units.append(para)
            continue
        for s in re.split(r"(?<=[.!?…])\s+", para):
            w = s.split()
            if len(w) <= max_words:
                if s.strip():
                    units.append(s.strip())
            else:
                units += [" ".join(w[i:i + max_words]) for i in range(0, len(w), max_words)]
    chunks, cur, count = [], [], 0
    for u in units:
        w = len(u.split())
        if cur and count + w > max_words:
            chunks.append("\n\n".join(cur))
            cur, count = [], 0
        cur.append(u)
        count += w
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# ------------------------------------------------------------------ réseau
class ModelNotFound(RuntimeError):
    """Le modèle demandé n'existe pas sur le serveur (Ollama : non installé)."""

    def __init__(self, model, installed, local):
        self.model, self.installed, self.local = model, installed, local
        if local:
            msg = f"Le modèle « {model} » n'est pas installé sur votre Ollama."
            msg += (f" Modèles installés : {', '.join(installed)}." if installed
                    else " Aucun modèle n'est installé.")
            msg += " Choisissez un modèle installé (📥 Liste) ou téléchargez celui-ci avec ⬇ Installer."
        else:
            msg = f"Le modèle « {model} » n'existe pas chez ce fournisseur. Cliquez 📥 Liste pour voir les modèles disponibles."
        super().__init__(msg)


def _is_local_ollama(base):
    host = urllib.parse.urlparse(base if "://" in base else "http://" + base).netloc.lower()
    return "ollama.com" not in host


def _is_ollama(base):
    host = urllib.parse.urlparse(base if "://" in base else "http://" + base).netloc.lower()
    return "11434" in host or "ollama" in host


def _root(base):
    return re.sub(r"/v1/?$", "", base.rstrip("/"))


def _headers(api_key, json_body=True):
    h = {"Authorization": f"Bearer {api_key or 'ollama'}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _request(req, timeout):
    """Exécute la requête et convertit les erreurs réseau en messages lisibles."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            err = body.get("error", body)
            detail = err.get("message") if isinstance(err, dict) else str(err)
        except Exception:
            pass
        hint = {401: "clé API invalide ou manquante", 403: "accès refusé",
                404: "URL ou modèle introuvable", 429: "quota / limite de débit atteint"}.get(e.code, "")
        raise RuntimeError(f"HTTP {e.code}" + (f" ({hint})" if hint else "")
                           + (f" : {detail}" if detail else "")) from None
    except urllib.error.URLError as e:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        msg = f"Connexion impossible à {url} ({e.reason})."
        if "11434" in url:
            msg += " Ollama est-il lancé ? (ollama serve)"
        raise RuntimeError(msg) from None
    except TimeoutError:
        raise RuntimeError("Délai dépassé : le modèle met trop de temps à répondre "
                           "(essayez un modèle plus petit ou des passages plus courts).") from None


def chat(base_url, api_key, model, messages, timeout=900, temperature=0.7):
    """Un échange chat. Ollama : API native /api/chat (contexte 8192 jetons, sinon Ollama
    tronque silencieusement l'entrée à 4096) ; sinon OpenAI-compatible /chat/completions."""
    base = base_url.rstrip("/")
    if _is_ollama(base):
        # think=False : les modèles « à réflexion » (qwen3…) répondent sans phase de raisonnement
        body = {"model": model, "messages": messages, "stream": False, "think": False,
                "options": {"num_ctx": 8192, "temperature": temperature, "num_predict": -1}}
        req = urllib.request.Request(_root(base) + "/api/chat",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers=_headers(api_key))
        try:
            data = _request(req, timeout)
        except RuntimeError as e:
            if "HTTP 404" in str(e) and "not found" in str(e).lower():
                try:
                    installed = list_models(base_url, api_key)
                except Exception:
                    installed = []
                raise ModelNotFound(model, installed, _is_local_ollama(base)) from None
            raise
        try:
            return data["message"]["content"] or ""
        except (KeyError, TypeError):
            raise RuntimeError("Réponse inattendue d'Ollama : " + json.dumps(data)[:300]) from None
    body = {"model": model, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    req = urllib.request.Request(base + "/chat/completions",
                                 data=json.dumps(body).encode("utf-8"), headers=_headers(api_key))
    try:
        data = _request(req, timeout)
    except RuntimeError as e:
        if temperature is not None and "temperature" in str(e).lower():
            return chat(base_url, api_key, model, messages, timeout, temperature=None)
        raise
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Réponse inattendue de l'API : " + json.dumps(data)[:300]) from None


def list_models(base_url, api_key="", timeout=15):
    """Liste les modèles disponibles (Ollama : /api/tags ; sinon GET /models)."""
    base = base_url.rstrip("/")
    if _is_ollama(base):
        data = _request(urllib.request.Request(_root(base) + "/api/tags",
                                               headers=_headers(api_key, False)), timeout)
        models = [m for m in (data.get("models") or [])
                  if "completion" in (m.get("capabilities") or ["completion"])]  # sans embeddings
        models.sort(key=lambda m: -(m.get("size") or 0))  # les plus gros d'abord
        return [m["name"] for m in models]  # liste vide possible
    data = _request(urllib.request.Request(base + "/models", headers=_headers(api_key, False)), timeout)
    models = sorted(m["id"].removeprefix("models/") for m in (data.get("data") or []))
    if not models:
        raise RuntimeError("Aucun modèle trouvé sur ce endpoint (vérifiez l'URL et la clé).")
    return models


def pull_ollama_model(name, base_url="http://localhost:11434", progress=None):
    """Télécharge un modèle sur l'Ollama local (POST /api/pull), avec progression."""
    req = urllib.request.Request(_root(base_url) + "/api/pull",
                                 data=json.dumps({"name": name}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=None)
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connexion impossible à Ollama ({getattr(e, 'reason', e)}). "
                           "Ollama est-il installé et lancé ?") from None
    with r:
        for line in r:
            try:
                st = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if "error" in st:
                raise RuntimeError(st["error"])
            if progress:
                pct = int(100 * st.get("completed", 0) / st["total"]) if st.get("total") else None
                progress(pct, st.get("status", ""))
            if st.get("status") == "success":
                return True
    return True


# ------------------------------------------------------------------ réécriture
_PREAMBLE_RE = re.compile(
    r"^(voici|bien sûr|certainement|d'accord|absolument|je n'ai pas|je ne peux|désolé|"
    r"ok\b|okay|here is|here's|sure)", re.I)


def _clean_output(text):
    """Retire raisonnement (<think>), clôtures de code, préambule et crochets scéniques."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    t = re.sub(r"^\s*```[a-z]*\s*\n|\n\s*```\s*$", "", t.strip())
    paras = [p for p in re.split(r"\n\s*\n", t.strip())]
    if paras and _PREAMBLE_RE.match(paras[0].strip()) and (
            paras[0].rstrip().endswith(":") or len(paras[0].split()) < 45):
        paras = paras[1:]
    t = "\n\n".join(paras)
    t = re.sub(r"\[[^\]\n]{0,80}\]", "", t)          # [Pause…], [Transition musicale]…
    t = re.sub(r"\*+", "", t)                           # gras / italique markdown
    t = re.sub(r"^[ \t]*#+[ \t]*", "", t, flags=re.M)   # titres markdown
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def _tail(text, n_words=50):
    return " ".join(text.split()[-n_words:])


def rewrite_chunk(base_url, api_key, model, style_prompt, lang, chunk, i, n, previous_tail="",
                  timeout=900, insist=False):
    position = POSITION_NOTES["only" if n == 1 else "first" if i == 1 else "last" if i == n else "middle"]
    previous = (f"Fin du passage précédent (pour la continuité — ne la répète pas) : "
                f"« {previous_tail} »\n" if previous_tail else "")
    words = len(chunk.split())
    prompt = PROMPT_TEMPLATE.format(style=style_prompt, i=i, n=n, position=position,
                                    previous=previous, words=words, max_words=int(words * 1.5),
                                    lang=LANG_NAMES.get(lang, lang), text=chunk)
    if insist:
        prompt = ("ATTENTION : ta réponse précédente était beaucoup trop courte (résumé). "
                  f"Réécris CHAQUE phrase en développant : {words} mots minimum.\n\n") + prompt
    out = chat(base_url, api_key, model,
               [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
               timeout=timeout)
    return _clean_output(out)


def rewrite_script(text, base_url, api_key, model, style_prompt, lang="fr",
                   chunk_words=DEFAULT_CHUNK_WORDS, progress=None, cancel=None, log=None):
    """Réécrit un texte de n'importe quelle longueur, passage par passage.
    progress(i, n, message) ; cancel() → True pour interrompre."""
    chunks = split_for_ai(text, chunk_words)
    if not chunks:
        raise RuntimeError("Texte vide.")
    n, outs, tail = len(chunks), [], ""
    has_speakers = bool(re.search(r"speaker\s*\d+\s*:", text, re.I))
    for i, chunk in enumerate(chunks, 1):
        if cancel and cancel():
            raise InterruptedError("Réécriture annulée")
        if progress:
            progress(i - 1, n, f"Passage {i}/{n}…")
        in_w = len(chunk.split())
        out = rewrite_chunk(base_url, api_key, model, style_prompt, lang, chunk, i, n, tail)
        if in_w >= 40 and len(out.split()) < MIN_RATIO * in_w:
            if log:
                log(f"⚠ Passage {i}/{n} trop court ({len(out.split())} mots pour {in_w}) : nouvel essai…")
            if cancel and cancel():
                raise InterruptedError("Réécriture annulée")
            out2 = rewrite_chunk(base_url, api_key, model, style_prompt, lang, chunk, i, n, tail,
                                 insist=True)
            if len(out2.split()) > len(out.split()):
                out = out2
            if len(out.split()) < FLOOR_RATIO * in_w:
                if log:
                    log(f"⚠ Passage {i}/{n} : le modèle résume au lieu de réécrire — texte "
                        "d'origine conservé pour ce passage. Essayez un modèle plus grand.")
                out = chunk
        if not has_speakers:  # texte à un seul narrateur : pas de balises inventées
            out = re.sub(r"(?im)^[ \t]*speaker\s*\d+\s*:[ \t]*", "", out)
        outs.append(out)
        tail = _tail(out)
    if progress:
        progress(n, n, "Terminé")
    return "\n\n".join(outs)
