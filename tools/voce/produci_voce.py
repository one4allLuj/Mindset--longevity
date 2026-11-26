#!/usr/bin/env python3
"""
Produzione della voce professionale per la seduta «Respiro Libero».

Usa solo strumenti gratuiti:
  - Gemini TTS (piano gratuito di Google AI Studio) per la voce;
  - un modello Gemini Flash (piano gratuito) per il controllo qualità;
  - numpy + ffmpeg per montaggio, sottofondo musicale e mastering.

Il copione NON è duplicato qui: viene letto da seduta-smettere-di-fumare.html,
così pagina e audio restano sempre allineati (la pagina verifica una firma).

La chiave API non sta nel codice. La aggiunge il proxy dell'ambiente
(segreto di rete con intestazione x-goog-api-key) oppure la variabile GEMINI_API_KEY.

Comandi (dalla radice del repository):
  python3 tools/voce/produci_voce.py voci                 # voci italiane disponibili
  python3 tools/voce/produci_voce.py progetta             # crea le voci "ipnoterapeuta"
  python3 tools/voce/produci_voce.py campioni V1 V2 ...   # un campione per ogni voce
  python3 tools/voce/produci_voce.py genera VOCE          # registra tutte le fasi
  python3 tools/voce/produci_voce.py qa                   # controllo qualità frase per frase
  python3 tools/voce/produci_voce.py rigenera             # rifà le frasi segnalate dal QA
  python3 tools/voce/produci_voce.py monta                # montaggio, sottofondo, mastering
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- percorsi
ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / 'seduta-smettere-di-fumare.html'
WORK = ROOT / 'tools' / 'voce' / 'lavoro'          # ignorato da git
TAKES = WORK / 'riprese'
STATE = WORK / 'stato.json'
OUT_DIR = ROOT / 'audio'
OUT_NAME = 'respiro-libero'

API = 'https://generativelanguage.googleapis.com/v1beta'
TTS_MODEL = os.environ.get('TTS_MODEL', 'gemini-3.8-flash-tts')
QA_MODEL = os.environ.get('QA_MODEL', '')           # vuoto = scelta automatica

SR = 24000          # frequenza delle riprese Gemini
SR_OUT = 48000      # frequenza del master
LEAD_IN = 5.0       # secondi di musica prima della prima parola
TAIL = 14.0         # secondi di musica dopo l'ultima parola

# ---------------------------------------------------------------- regia
# Tono per fase (indice = fase del copione). Frasi brevi: Gemini 3.8 rende
# meglio con indicazioni concise; timbro, età e accento stanno nella voce.
STILI = {
    0: 'calm, warm and welcoming, unhurried',
    1: 'soft, slow and soothing',
    2: 'very slow, soft and deeply soothing',
    3: 'soft, slow and dreamy',
    4: 'calm, slow, warm and quietly confident',
    5: 'calm, slow, warm and quietly confident',
    6: 'calm, slow, warm and quietly confident',
    7: 'soft, slow, with gentle wonder',
    8: 'calm, slow, warm and reassuring',
    9: 'slow, warm and gently uplifting',
    10: 'slow, warm, with quiet joy and conviction',
    11: 'slow, warm, with quiet joy and conviction',
    12: 'warm, gradually brighter and more awake',
}
# Tono di singole frasi (chiave = testo esatto della frase).
STILI_FRASE = {
    'Cinque. Apri gli occhi. Fai un bel respiro… ed eccoti qui.': 'warm, bright and fully awake',
    'Questa è la tua nuova vita. Goditela.': 'warm, bright, with a gentle smile',
}
# Ritocchi del testo letto (il sottotitolo resta quello del copione).
RITOCCHI = {}

# Voci progettate con Voice design (timbro permanente).
VOCI_PROGETTATE = {
    'ipnoterapeuta-donna': {
        'gender': 'female',
        'display_name': 'Respiro Libero - Donna',
        'input': ('A warm, gentle Italian woman in her early forties, a professional '
                  'hypnotherapist with a soft, low, velvety voice and a neutral standard '
                  'Italian accent, speaking intimately close to the microphone.'),
    },
    'ipnoterapeuta-uomo': {
        'gender': 'male',
        'display_name': 'Respiro Libero - Uomo',
        'input': ('A calm Italian man in his late forties, a professional hypnotherapist '
                  'with a deep, warm, slightly breathy baritone and a neutral standard '
                  'Italian accent, speaking intimately close to the microphone.'),
    },
}

# Frasi del campione: mostrano induzione, respiro, persuasione e ancoraggio.
FRASI_CAMPIONE = [
    'Trova una posizione comoda, seduta o sdraiata, con la schiena ben sostenuta.',
    'Fai un respiro lento e profondo. Dentro…',
    "e fuori, piano, lasciando andare tutta l'aria.",
    'Non si può rinunciare a qualcosa che non ti ha mai dato niente.',
    'Da adesso, ogni volta che unirai pollice e indice e farai un respiro profondo, '
    'questa sensazione tornerà, rapida e chiara.',
]


# ================================================================ copione
def load_script():
    html = HTML.read_text('utf-8')
    fasi = re.findall(r"\{\s*titolo:\s*'([^']*)',\s*testo:\s*`([\s\S]*?)`\s*\}", html)
    if not fasi:
        sys.exit('Copione non trovato in ' + str(HTML))
    segs = []
    for fi, (_, testo) in enumerate(fasi):
        for riga in testo.strip().split('\n'):
            riga = riga.strip()
            if not riga:
                continue
            m = re.match(r'^(.*?)\s*\{(\d+(?:\.\d+)?)\}$', riga)
            segs.append({'i': len(segs), 'f': fi,
                         't': m.group(1) if m else riga,
                         'p': float(m.group(2)) if m else 1.2})
    return [t for t, _ in fasi], segs


def fnv(s):
    """FNV-1a 32 bit sul testo UTF-8: la stessa funzione è nella pagina."""
    h = 0x811C9DC5
    for b in s.encode('utf-8'):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f'{h:08x}'


def firma(segs):
    return fnv('\n'.join(s['t'] for s in segs))


def tts_text(seg):
    return RITOCCHI.get(seg['t'], seg['t'])


def tts_style(seg):
    return STILI_FRASE.get(seg['t'], STILI.get(seg['f'], ''))


# ================================================================ stato
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text('utf-8'))
    return {'voce': None, 'voci_progettate': {}, 'riprese': {}, 'qa': {}}


def save_state(st):
    WORK.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), 'utf-8')


def take_key(voice, seg):
    raw = '|'.join([TTS_MODEL, voice, tts_text(seg), tts_style(seg)])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40]


# ================================================================ API
class ApiError(Exception):
    def __init__(self, code, body):
        super().__init__(f'HTTP {code}: {body[:600]}')
        self.code = code
        self.body = body


def api(method, path, body=None, query=None, timeout=600):
    url = API + path
    if query:
        url += '?' + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('GEMINI_API_KEY'):
        headers['x-goog-api-key'] = os.environ['GEMINI_API_KEY']
    for attempt in range(8):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'replace')
            if e.code == 429 and re.search(r'PerDay|per day|daily', txt, re.I):
                raise ApiError(e.code, 'Quota giornaliera gratuita esaurita. Riprova domani. ' + txt)
            if e.code in (429, 500, 502, 503, 504) and attempt < 7:
                m = re.search(r'"retryDelay":\s*"(\d+)', txt)
                wait = int(m.group(1)) + 2 if m else min(90, 8 * 2 ** attempt)
                print(f'   … HTTP {e.code}, riprovo tra {wait}s', flush=True)
                time.sleep(wait)
                continue
            raise ApiError(e.code, txt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < 7:
                print(f'   … rete ({e}), riprovo', flush=True)
                time.sleep(10 * (attempt + 1))
                continue
            raise


def find_audio(obj):
    found = []

    def walk(o):
        if isinstance(o, dict):
            mt = str(o.get('mime_type') or o.get('mimeType') or '')
            if isinstance(o.get('data'), str) and (mt.startswith('audio') or o.get('type') == 'audio'):
                found.append(o['data'])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    if not found:
        raise RuntimeError('Nessun audio nella risposta: ' + json.dumps(obj)[:400])
    return found[-1]


def decode_audio(b):
    if b[:4] == b'RIFF':
        with wave.open(io.BytesIO(b)) as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            pcm = w.readframes(w.getnframes())
        x = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(1)
    else:
        sr = SR
        x = np.frombuffer(b, dtype='<i2').astype(np.float32) / 32768.0
    if sr != SR:
        x = resample(x, sr, SR)
    return x


def resample(x, sr_in, sr_out):
    p = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', str(sr_in), '-ac', '1', '-i', '-',
                        '-af', 'aresample=resampler=soxr', '-ar', str(sr_out), '-f', 'f32le', '-'],
                       input=x.astype('<f4').tobytes(), capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype='<f4').copy()


def tts(items, voice):
    """items: lista di (testo, stile). Restituisce audio mono float32 a SR."""
    content = []
    for text, style in items:
        c = {'type': 'text', 'text': text}
        if style:
            c['annotations'] = [{'type': 'speech_metadata', 'style': style}]
        content.append(c)
    body = {
        'model': TTS_MODEL,
        'input': [{'type': 'user_input', 'content': content}],
        'response_format': {'type': 'audio'},
        'generation_config': {'speech_config': [{'voice': voice}]},
    }
    r = api('POST', '/interactions', body)
    return decode_audio(base64.b64decode(find_audio(r)))


# ================================================================ audio
def write_wav(path, x, sr=SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(x, -1, 1)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype('<i2').tobytes())


def read_wav(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
    return x


def frames_db(x, sr=SR, win=0.02, hop=0.01):
    w, h = int(sr * win), int(sr * hop)
    if len(x) < w:
        x = np.pad(x, (0, w - len(x)))
    n = 1 + (len(x) - w) // h
    idx = np.arange(w)[None, :] + h * np.arange(n)[:, None]
    rms = np.sqrt((x[idx] ** 2).mean(1) + 1e-12)
    return 20 * np.log10(rms), h


def speech_threshold(db):
    loud = db[db > db.max() - 45]
    return max(np.percentile(loud, 70) - 32, -62)


def trim(x, thr=None):
    db, h = frames_db(x)
    if thr is None:
        thr = speech_threshold(db)
    v = np.where(db > thr)[0]
    if not len(v):
        return x
    a = max(0, v[0] * h - int(0.05 * SR))
    b = min(len(x), v[-1] * h + int(0.30 * SR))
    y = x[a:b].copy()
    fi, fo = int(0.012 * SR), int(0.08 * SR)
    y[:fi] *= np.linspace(0, 1, fi)
    y[-fo:] *= np.linspace(1, 0, fo)
    return y


def words(t):
    return len(re.findall(r"\w+", t))


def plausible(seg, dur):
    w = max(1, words(seg['t']))
    rate = w / max(dur, 0.1)
    return 0.6 <= rate <= 3.8 and dur < 4 + w * 1.2


def expected_weight(t):
    """Durata attesa relativa di una frase: lettere + pause della punteggiatura."""
    letters = len(re.findall(r'\w', t))
    return letters + 6 * len(re.findall(r'[,;:]', t)) + 10 * len(re.findall(r'[.!?…]', t))


def split_takes(x, segs):
    """Divide la ripresa di una fase nelle sue frasi.

    Sceglie i tagli tra i silenzi con una programmazione dinamica che premia i
    silenzi lunghi (le <long pause> tra le frasi) e le durate coerenti con la
    lunghezza del testo, così una pausa interna a una frase non viene scambiata
    per un confine.
    """
    n = len(segs)
    db, h = frames_db(x)
    thr = speech_threshold(db)
    quiet = db < thr
    v = np.where(~quiet)[0]
    if not len(v):
        return None, 'nessun parlato'
    if n == 1:
        return [trim(x, thr)], 'ok'
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i))
            start = None
    runs = [r for r in runs if r[0] > v[0] and r[1] <= v[-1] and (r[1] - r[0]) * h / SR >= 0.25]
    m = len(runs)
    if m < n - 1:
        return None, f'{m} pause trovate, ne servono {n - 1}'
    t0, t1 = v[0] * h / SR, v[-1] * h / SR
    mid = [((a + b) / 2) * h / SR for a, b in runs]
    sil = [(b - a) * h / SR for a, b in runs]
    w = np.array([expected_weight(tts_text(sg)) for sg in segs], float)
    # tempo di parlato atteso per frase (le pause scelte sono escluse a posteriori)
    total_sil_guess = sum(sorted(sil, reverse=True)[:n - 1])
    speech = max(1e-3, (t1 - t0) - total_sil_guess)
    exp = w / w.sum() * speech

    def piece_cost(k, a_t, a_sil, b_t, b_sil):
        dur = (b_t - a_t) - a_sil / 2 - b_sil / 2
        r = dur / exp[k]
        return 6.0 * np.log(max(r, 0.05)) ** 2

    INF = float('inf')
    # dp[k][j]: costo migliore con il k-esimo taglio (1..n-1) nella pausa j
    dp = np.full((n, m), INF)
    back = np.full((n, m), -1, int)
    for j in range(m):
        dp[1][j] = piece_cost(0, t0, 0, mid[j], sil[j]) - 2.5 * np.log(sil[j])
    for k in range(2, n):
        for j in range(k - 1, m):
            best, arg = INF, -1
            for i in range(k - 2, j):
                if dp[k - 1][i] == INF:
                    continue
                c = dp[k - 1][i] + piece_cost(k - 1, mid[i], sil[i], mid[j], sil[j])
                if c < best:
                    best, arg = c, i
            if arg >= 0:
                dp[k][j] = best - 2.5 * np.log(sil[j])
                back[k][j] = arg
    best, last = INF, -1
    for j in range(n - 2, m):
        if dp[n - 1][j] == INF:
            continue
        c = dp[n - 1][j] + piece_cost(n - 1, mid[j], sil[j], t1, 0)
        if c < best:
            best, last = c, j
    if last < 0:
        return None, 'nessuna divisione possibile'
    chosen, j = [], last
    for k in range(n - 1, 0, -1):
        chosen.append(j)
        j = back[k][j]
    chosen = sorted(chosen)
    cuts = [0] + [int(mid[j] * SR) for j in chosen] + [len(x)]
    pieces = [trim(x[cuts[k]:cuts[k + 1]], thr) for k in range(n)]
    for sg, pc in zip(segs, pieces):
        if not plausible(sg, len(pc) / SR):
            return None, f'durata anomala nella frase {sg["i"]} ({len(pc) / SR:.1f}s)'
    shortest = min(sil[j] for j in chosen)
    if shortest < 0.45:
        return None, f'pausa tra frasi troppo breve ({shortest:.2f}s)'
    return pieces, 'ok'


# ================================================================ comandi API
def cmd_voci(_args):
    seen = 0
    for lang in ('it-IT', 'it'):
        try:
            r = api('GET', '/voices', query={'language_code': lang, 'page_size': 1000})
        except ApiError as e:
            print('Errore elenco voci:', e)
            continue
        for v in r.get('voices', []):
            seen += 1
            print(json.dumps({k: v.get(k) for k in ('id', 'name', 'display_name', 'displayName', 'gender',
                                                     'pitch', 'persona', 'accent', 'description',
                                                     'context', 'contexts', 'type')
                              if v.get(k)}, ensure_ascii=False))
        if seen:
            break
    print(f'{seen} voci italiane trovate.')


def cmd_progetta(args):
    st = load_state()
    nomi = args.nomi or list(VOCI_PROGETTATE)
    out = WORK / 'progettate'
    for nome in nomi:
        spec = VOCI_PROGETTATE[nome]
        voice = {'model': TTS_MODEL, 'type': 'prompted', 'display_name': spec['display_name'],
                 'gender': spec['gender'], 'language_code': 'it-IT', 'prompted': {'input': spec['input']}}
        r = None
        for attempt in (('POST', '/voices', {'store': True, 'voice': voice}, None),
                        ('POST', '/voices', voice, {'store': 'true'})):
            try:
                r = api(attempt[0], attempt[1], attempt[2], attempt[3])
                break
            except ApiError as e:
                print('  tentativo fallito:', e)
        if not r:
            continue
        vid = r.get('id') or r.get('name', '').split('/')[-1]
        st['voci_progettate'][nome] = vid
        save_state(st)
        print(f'{nome}: {vid}')
        sa = r.get('sample_audio') or r.get('sampleAudio')
        if sa and sa.get('data'):
            write_wav(out / f'{nome}-anteprima.wav', decode_audio(base64.b64decode(sa['data'])))


def resolve_voice(name):
    st = load_state()
    return st['voci_progettate'].get(name, name)


def cmd_campioni(args):
    titles, segs = load_script()
    by_text = {s['t']: s for s in segs}
    sample = [by_text[t] for t in FRASI_CAMPIONE]
    outdir = Path(args.out) if args.out else WORK / 'campioni'
    for name in args.voci:
        voice = resolve_voice(name)
        print(f'▶ Campione: {name}', flush=True)
        items = [(tts_text(s) + ('' if k == len(sample) - 1 else ' <long pause>'), tts_style(s))
                 for k, s in enumerate(sample)]
        try:
            x = tts(items, voice)
        except ApiError as e:
            print('  errore:', e)
            continue
        write_wav(outdir / 'grezzi' / f'{slug(name)}.wav', x)
        pieces, why = split_takes(x, sample)
        if pieces is None:
            print('  (divisione non riuscita:', why, '→ frasi una per una)')
            pieces = [trim(tts([(tts_text(s), tts_style(s))], voice)) for s in sample]
        voice_track, _ = assemble(sample, pieces, lead=3.0, tail=6.0)
        master(voice_track, outdir / f'campione-{slug(name)}.mp3', bitrate='128k')
        print('  ✓', outdir / f'campione-{slug(name)}.mp3')


def cmd_genera(args):
    titles, segs = load_script()
    st = load_state()
    voice_name = args.voce
    voice = resolve_voice(voice_name)
    st['voce'] = voice_name
    fasi = sorted({s['f'] for s in segs}) if not args.fasi else [int(f) for f in args.fasi]
    for fi in fasi:
        fsegs = [s for s in segs if s['f'] == fi]
        if all(st['riprese'].get(str(s['i']), {}).get('key') == take_key(voice, s) for s in fsegs):
            print(f'= Fase {fi + 1} già registrata')
            continue
        print(f'▶ Fase {fi + 1}: {titles[fi]} ({len(fsegs)} frasi)', flush=True)
        items = [(tts_text(s) + ('' if k == len(fsegs) - 1 else ' <long pause>'), tts_style(s))
                 for k, s in enumerate(fsegs)]
        pieces = None
        try:
            x = tts(items, voice)
            write_wav(TAKES / f'fase{fi:02d}-{slug(voice_name)}.wav', x)
            pieces, why = split_takes(x, fsegs)
            if pieces is None:
                print('  divisione non riuscita:', why)
        except ApiError as e:
            print('  errore fase intera:', e)
        if pieces is None:
            print('  → registro le frasi una per una')
            pieces = []
            for s in fsegs:
                pieces.append(trim(tts([(tts_text(s), tts_style(s))], voice)))
        for s, p in zip(fsegs, pieces):
            key = take_key(voice, s)
            f = TAKES / f'f{fi:02d}-s{s["i"]:03d}-{key}.wav'
            write_wav(f, p)
            st['riprese'][str(s['i'])] = {'key': key, 'file': f.name, 'voce': voice_name}
            st['qa'].pop(str(s['i']), None)
        save_state(st)
        print(f'  ✓ {len(fsegs)} frasi', flush=True)


# ================================================================ controllo qualità
def pick_qa_model():
    if QA_MODEL:
        return QA_MODEL
    try:
        r = api('GET', '/models', query={'pageSize': 1000})
        best = None
        for m in r.get('models', []):
            name = m['name'].split('/')[-1]
            if 'generateContent' not in m.get('supportedGenerationMethods', []):
                continue
            mm = re.fullmatch(r'gemini-(\d+(?:\.\d+)?)-flash', name)
            if mm and (best is None or float(mm.group(1)) > best[0]):
                best = (float(mm.group(1)), name)
        if best:
            return best[1]
    except ApiError as e:
        print('Elenco modelli non disponibile:', e)
    return 'gemini-2.5-flash'


QA_PROMPT = """You are a meticulous Italian voice-over director reviewing a professional hypnosis
recording in Italian. The audio contains {n} spoken lines, separated by about one second of
silence, in this exact order:
{lines}

Listen carefully to every line and return JSON only:
{{"lines":[{{"n":1,"heard":"exact transcription","text_ok":true,"pronunciation_ok":true,
"natural_italian":true,"artifacts":false,"tone":5,"problem":""}}]}}

Rules:
- text_ok=false if any word is missing, added, repeated or changed (ignore punctuation).
- pronunciation_ok=false for a wrong stress, a mispronounced or garbled word.
- natural_italian=false if it sounds like a non-native or robotic Italian speaker.
- artifacts=true for glitches, clicks, distortion, metallic or robotic sound, strange breaths,
  cut-off words, sudden volume jumps or noise.
- tone from 1 to 5: 5 = calm, warm, slow, soothing and persuasive like a top hypnotherapist;
  1 = flat, rushed, cold or inappropriate.
- problem: short explanation in Italian, empty if everything is fine.
Be strict, but never invent problems you do not hear."""


def wav_bytes(x, sr):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())
    return buf.getvalue()


def qa_phase(model, fsegs, takes):
    gap = np.zeros(int(1.0 * SR), np.float32)
    joined = np.concatenate([np.concatenate([t, gap]) for t in takes])
    audio16 = resample(joined, SR, 16000)
    lines = '\n'.join(f'{k + 1}. «{s["t"]}»' for k, s in enumerate(fsegs))
    body = {
        'contents': [{'role': 'user', 'parts': [
            {'text': QA_PROMPT.format(n=len(fsegs), lines=lines)},
            {'inline_data': {'mime_type': 'audio/wav',
                             'data': base64.b64encode(wav_bytes(audio16, 16000)).decode()}},
        ]}],
        'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0},
    }
    r = api('POST', f'/models/{model}:generateContent', body)
    text = r['candidates'][0]['content']['parts'][0]['text']
    return json.loads(text).get('lines', [])


def local_checks(seg, x):
    issues = []
    dur = len(x) / SR
    if not plausible(seg, dur):
        issues.append(f'durata anomala ({dur:.1f}s per {words(seg["t"])} parole)')
    if np.max(np.abs(x)) >= 0.999:
        issues.append('saturazione')
    db, h = frames_db(x)
    thr = speech_threshold(db)
    quiet = db < thr
    longest, cur = 0, 0
    for q in quiet[5:-25]:
        cur = cur + 1 if q else 0
        longest = max(longest, cur)
    if longest * h / SR > 1.6:
        issues.append(f'silenzio interno di {longest * h / SR:.1f}s')
    return issues


def is_flagged(q):
    return bool(q.get('locale')) or not q.get('text_ok', True) or not q.get('pronunciation_ok', True) \
        or not q.get('natural_italian', True) or q.get('artifacts') or (q.get('tone') or 5) <= 3


def cmd_qa(args):
    titles, segs = load_script()
    st = load_state()
    model = pick_qa_model()
    print('Modello QA:', model)
    fasi = sorted({s['f'] for s in segs}) if not args.fasi else [int(f) for f in args.fasi]
    tot_flag = 0
    for fi in fasi:
        fsegs = [s for s in segs if s['f'] == fi]
        if any(str(s['i']) not in st['riprese'] for s in fsegs):
            print(f'- Fase {fi + 1}: riprese mancanti, salto')
            continue
        takes = [read_wav(TAKES / st['riprese'][str(s['i'])]['file']) for s in fsegs]
        try:
            res = qa_phase(model, fsegs, takes)
        except (ApiError, KeyError, json.JSONDecodeError) as e:
            print(f'- Fase {fi + 1}: QA non riuscito ({e})')
            continue
        for k, s in enumerate(fsegs):
            q = next((r for r in res if r.get('n') == k + 1), {}) if res else {}
            q['locale'] = local_checks(s, takes[k])
            st['qa'][str(s['i'])] = q
            if is_flagged(q):
                tot_flag += 1
                print(f'  ⚠ [{s["i"]}] «{s["t"][:60]}» → {q.get("problem") or ""} '
                      f'{"; ".join(q["locale"])} (tono {q.get("tone")})')
        tones = [st['qa'][str(s['i'])].get('tone') or 0 for s in fsegs]
        print(f'- Fase {fi + 1} {titles[fi]}: tono medio {np.mean(tones):.1f}/5')
        save_state(st)
    print(f'Frasi da rifare: {tot_flag}')


def cmd_rigenera(args):
    titles, segs = load_script()
    st = load_state()
    voice_name = st['voce']
    voice = resolve_voice(voice_name)
    targets = [int(i) for i in args.frasi] if args.frasi else \
        [int(i) for i, q in st['qa'].items() if is_flagged(q)]
    if not targets:
        print('Nessuna frase da rifare.')
        return
    model = pick_qa_model()
    for i in sorted(targets):
        s = segs[i]
        best = None
        for attempt in range(args.tentativi):
            x = trim(tts([(tts_text(s), tts_style(s))], voice))
            try:
                q = qa_phase(model, [s], [x])[0]
            except (ApiError, IndexError, KeyError, json.JSONDecodeError):
                q = {}
            q['locale'] = local_checks(s, x)
            score = (0 if is_flagged(q) else 10) + (q.get('tone') or 0)
            print(f'  [{i}] tentativo {attempt + 1}: punteggio {score} {q.get("problem") or ""}')
            if best is None or score > best[0]:
                best = (score, x, q)
            if not is_flagged(q) and (q.get('tone') or 0) >= 4:
                break
        key = take_key(voice, s)
        f = TAKES / f'f{s["f"]:02d}-s{i:03d}-{key}-r.wav'
        write_wav(f, best[1])
        st['riprese'][str(i)] = {'key': key, 'file': f.name, 'voce': voice_name}
        st['qa'][str(i)] = best[2]
        save_state(st)


# ================================================================ montaggio e mastering
def level(takes):
    """Livella le frasi tra loro (±4 dB) senza appiattire la dinamica."""
    lv = []
    for x in takes:
        db, _ = frames_db(x)
        thr = speech_threshold(db)
        voiced = db[db > thr]
        lv.append(np.mean(voiced) if len(voiced) else -30)
    target = float(np.median(lv))
    return [x * 10 ** (np.clip(target - l, -4, 4) / 20) for x, l in zip(takes, lv)]


def assemble(segs, takes, lead=LEAD_IN, tail=TAIL):
    takes = level(takes)
    times, t = [], lead
    for s, x in zip(segs, takes):
        times.append([round(t, 3), round(t + len(x) / SR, 3)])
        t += len(x) / SR + s['p']
    total = t - segs[-1]['p'] + tail
    out = np.zeros(int(total * SR) + 1, np.float32)
    for (a, _), x in zip(times, takes):
        i = int(round(a * SR))
        out[i:i + len(x)] += x
    return out, times


CHORDS = [  # progressione lenta e aperta in La maggiore
    [55.00, 110.00, 164.81, 277.18, 415.30, 493.88],   # La maj9
    [46.25, 92.50, 138.59, 220.00, 277.18, 329.63],    # Fa# m7
    [36.71, 73.42, 110.00, 185.00, 277.18, 329.63],    # Re maj9
    [41.20, 82.41, 123.47, 185.00, 246.94, 329.63],    # Mi sus2
]


def pad(duration, sr=SR, chord_len=30.0, xfade=12.0, seed=11):
    """Tappeto armonico originale: accordi lenti con chorus e respiro d'ampiezza."""
    rng = np.random.default_rng(seed)
    n = int(duration * sr) + 1
    out = np.zeros((2, n), np.float32)
    k, t0 = 0, -xfade
    while t0 < duration:
        chord = CHORDS[k % len(CHORDS)]
        L = chord_len + xfade
        a, b = max(0, int(t0 * sr)), min(n, int((t0 + L) * sr))
        if b > a:
            tt = (np.arange(a, b) / sr - t0).astype(np.float32)
            env = (np.sin(0.5 * np.pi * np.clip(tt / xfade, 0, 1)) *
                   np.sin(0.5 * np.pi * np.clip((L - tt) / xfade, 0, 1))) ** 2
            sec = np.zeros((2, b - a), np.float32)
            for j, f in enumerate(chord):
                amp = (0.55 if j == 0 else 1.0) / (1 + 0.3 * j)
                lfo = 1 - 0.35 * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(0.015, 0.05) * tt
                                                     + rng.uniform(0, 2 * np.pi)))
                harm = ((1, 1.0),) if j == 0 else ((1, 1.0), (2, 0.22), (3, 0.07))
                pan = 0.5 + (j / (len(chord) - 1) - 0.5) * 0.5
                for h, ha in harm:
                    for det, side in ((0.9989, 0.25), (1.0011, 0.75)):
                        s = np.sin(2 * np.pi * f * h * det * tt + rng.uniform(0, 2 * np.pi)).astype(np.float32)
                        s *= amp * ha * lfo
                        w = 0.5 * side + 0.5 * pan
                        sec[0] += s * (1 - w)
                        sec[1] += s * w
            out[:, a:b] += sec * env
        k += 1
        t0 += chord_len
    out /= np.max(np.abs(out)) + 1e-9
    return out * 0.5


def impulse_response(sr=SR_OUT, seed=3):
    """Piccola stanza morbida: dà intimità alla voce senza effetto eco."""
    rng = np.random.default_rng(seed)
    n = int(0.9 * sr)
    t = np.arange(n) / sr
    ir = np.zeros((n, 2), np.float32)
    pre = int(0.012 * sr)
    for c in range(2):
        bright = rng.standard_normal(n) * np.exp(-6.9 * t / 0.30)
        dark = np.convolve(rng.standard_normal(n), np.ones(12) / 12, 'same') * np.exp(-6.9 * t / 0.75)
        sig = 0.5 * bright + dark
        sig[:pre] = 0
        ir[:, c] = sig
    ir /= np.max(np.abs(ir))
    return ir


def ffmpeg(*a):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *a], check=True)


def loudness(path):
    p = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(path), '-af',
                        'loudnorm=I=-18:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
                       capture_output=True, text=True)
    return json.loads(p.stderr[p.stderr.rindex('{'):])


def master(voice_track, out_path, bitrate='112k'):
    tmp = WORK / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    dur = len(voice_track) / SR
    (tmp / 'voce.f32').write_bytes(voice_track.astype('<f4').tobytes())
    (tmp / 'pad.f32').write_bytes(pad(dur).T.astype('<f4').tobytes())
    ir = impulse_response()
    with wave.open(str(tmp / 'ir.wav'), 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR_OUT)
        w.writeframes((ir * 32767).astype('<i2').tobytes())

    # 1) voce: pulizia, calore, de-esser, compressione dolce, stanza sottile
    ffmpeg('-f', 'f32le', '-ar', str(SR), '-ac', '1', '-i', tmp / 'voce.f32', '-i', tmp / 'ir.wav',
           '-filter_complex',
           '[0:a]aresample=48000:resampler=soxr,highpass=f=70,'
           'equalizer=f=170:t=q:w=0.9:g=1.5,equalizer=f=3600:t=q:w=1.4:g=-1.5,'
           'equalizer=f=9500:t=h:w=0.7:g=1.0,deesser=i=0.35,'
           'acompressor=threshold=-24dB:ratio=2:attack=12:release=220:makeup=1.6,'
           'pan=stereo|c0=c0|c1=c0,asplit=2[d][w];'
           '[w][1:a]afir=dry=0:wet=1[rv];[rv]volume=0.09[rvq];'
           '[d][rvq]amix=inputs=2:normalize=0[v]',
           '-map', '[v]', '-c:a', 'pcm_f32le', tmp / 'voce.wav')
    # 2) sottofondo: tappeto armonico + onde di rumore marrone, lontani e caldi
    ffmpeg('-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', tmp / 'pad.f32',
           '-f', 'lavfi', '-i', f'anoisesrc=color=brown:sample_rate=48000:amplitude=0.6:duration={dur:.2f}:seed=5',
           '-f', 'lavfi', '-i', f'anoisesrc=color=brown:sample_rate=48000:amplitude=0.6:duration={dur:.2f}:seed=9',
           '-filter_complex',
           '[0:a]aresample=48000:resampler=soxr,highpass=f=40,lowpass=f=1600,volume=1.0[p];'
           '[1:a][2:a]join=inputs=2:channel_layout=stereo,lowpass=f=520,highpass=f=60,'
           "volume='0.55+0.45*sin(2*PI*0.083*t)':eval=frame,volume=0.35[o];"
           '[p][o]amix=inputs=2:normalize=0,'
           f'afade=t=in:d=4,afade=t=out:st={max(0, dur - 10):.2f}:d=10[b]',
           '-map', '[b]', '-c:a', 'pcm_f32le', tmp / 'letto.wav')
    # 3) livelli: voce a -20 LUFS, sottofondo 15 LU sotto la voce
    lv, lb = loudness(tmp / 'voce.wav'), loudness(tmp / 'letto.wav')
    gv = -20 - float(lv['input_i'])
    gb = -35 - float(lb['input_i'])
    ffmpeg('-i', tmp / 'voce.wav', '-i', tmp / 'letto.wav', '-filter_complex',
           f'[0:a]volume={gv:.2f}dB,asplit=2[v][k];[1:a]volume={gb:.2f}dB[b];'
           '[b][k]sidechaincompress=threshold=0.02:ratio=2.5:attack=300:release=2000[bd];'
           '[v][bd]amix=inputs=2:normalize=0[m]',
           '-map', '[m]', '-c:a', 'pcm_f32le', tmp / 'mix.wav')
    # 4) mastering EBU R128: -18 LUFS, picco vero -1.5 dBTP
    m = loudness(tmp / 'mix.wav')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg('-i', tmp / 'mix.wav', '-af',
           f'loudnorm=I=-18:TP=-1.5:LRA=11:measured_I={m["input_i"]}:measured_TP={m["input_tp"]}:'
           f'measured_LRA={m["input_lra"]}:measured_thresh={m["input_thresh"]}:'
           f'offset={m["target_offset"]}:linear=true,aresample=48000',
           '-c:a', 'libmp3lame', '-b:a', bitrate, '-ar', '48000',
           '-metadata', 'title=Respiro Libero - Seduta per smettere di fumare',
           '-metadata', 'artist=MINDSET LABS', out_path)


def cmd_monta(args):
    titles, segs = load_script()
    st = load_state()
    missing = [s['i'] for s in segs if str(s['i']) not in st['riprese']]
    if missing:
        sys.exit(f'Mancano {len(missing)} frasi (es. {missing[:5]}): esegui prima «genera».')
    flagged = [i for i, q in st['qa'].items() if is_flagged(q)]
    if flagged and not args.forza:
        print(f'Attenzione: {len(flagged)} frasi segnalate dal QA ({flagged[:8]}). Usa --forza per montare comunque.')
        return
    takes = [read_wav(TAKES / st['riprese'][str(s['i'])]['file']) for s in segs]
    voice_track, times = assemble(segs, takes)
    mp3 = OUT_DIR / f'{OUT_NAME}.mp3'
    master(voice_track, mp3, bitrate=args.bitrate)
    data = {
        'src': f'audio/{OUT_NAME}.mp3',
        'voce': st['voce'],
        'durata': round(len(voice_track) / SR, 2),
        'n': len(segs),
        'firma': firma(segs),
        'segmenti': times,
    }
    js = ('// Generato da tools/voce/produci_voce.py: non modificare a mano.\n'
          'window.RESPIRO_AUDIO = ' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';\n')
    (OUT_DIR / f'{OUT_NAME}-tempi.js').write_text(js, 'utf-8')
    print(f'✓ {mp3} ({mp3.stat().st_size / 1e6:.1f} MB, {data["durata"] / 60:.1f} min)')


# ================================================================ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('voci')
    p = sub.add_parser('progetta')
    p.add_argument('nomi', nargs='*')
    p = sub.add_parser('campioni')
    p.add_argument('voci', nargs='+')
    p.add_argument('--out')
    p = sub.add_parser('genera')
    p.add_argument('voce')
    p.add_argument('--fasi', nargs='*')
    p = sub.add_parser('qa')
    p.add_argument('--fasi', nargs='*')
    p = sub.add_parser('rigenera')
    p.add_argument('frasi', nargs='*')
    p.add_argument('--tentativi', type=int, default=3)
    p = sub.add_parser('monta')
    p.add_argument('--forza', action='store_true')
    p.add_argument('--bitrate', default='112k')
    args = ap.parse_args()
    {'voci': cmd_voci, 'progetta': cmd_progetta, 'campioni': cmd_campioni, 'genera': cmd_genera,
     'qa': cmd_qa, 'rigenera': cmd_rigenera, 'monta': cmd_monta}[args.cmd](args)


if __name__ == '__main__':
    main()
