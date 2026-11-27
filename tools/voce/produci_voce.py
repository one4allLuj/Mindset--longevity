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
MAX_RICHIESTA = 6.5 # minuti di voce stimati per richiesta (il limite del modello è ~10,9)
ASR_DIR = os.environ.get('VOCE_ASR_DIR', '')   # Whisper sherpa-onnx facoltativo per il controllo locale
# Pausa inserita tra una frase e l'altra nella stessa richiesta: deve essere più
# lunga delle pause interne alle frasi («Dieci… il primo passo»). «pause FILE»
# misura quelle reali di una ripresa per decidere se allungarla.
SEPARATORE = os.environ.get('VOCE_SEPARATORE', ' <long pause>')

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
    "Mentre lo guardi, forse ti accorgi che le palpebre diventano un po' più pesanti…":
        'slower and softer, gently heavy, hypnotic and soothing',
    'e quando sono pronte, possono chiudersi da sole.':
        'slower and softer, gently heavy, hypnotic and soothing',
    'Bene.':
        'very soft, low and quietly approving',
    'Fai un respiro lento e profondo. Dentro…':
        'soft, slow and gently breathy, drawing out the final word',
    "e fuori, piano, lasciando andare tutta l'aria.":
        'soft, slow and releasing, like a long exhale',
    'Ancora una volta. Inspira…':
        'soft, slow and gently breathy, inviting a deep inhale',
    'ed espira, ancora più lentamente.':
        'very slow and soft, drawn out like a long exhale',
    "Un'ultima volta. Riempi i polmoni di aria pulita…":
        'soft, slow and spacious, gently breathy, inviting a full inhale',
    "e mentre l'aria esce, lascia uscire con lei anche un po' di tensione.":
        'soft, slow and releasing, letting tension melt away',
    'Uno.':
        'slowest and softest, settled and deeply soothing',
    'Sei in fondo alla scala, in uno stato di rilassamento profondo, piacevole, sicuro.':
        'very slow, soft and deeply settled',
    'Mentre il corpo riposa, la parte più profonda e saggia di te può ascoltare alcune cose semplici. Cose che, in fondo, hai sempre saputo.':
        'soft, slow and intimate, still deeply relaxed',
    'Non si può rinunciare a qualcosa che non ti ha mai dato niente.':
        'slower, quietly certain, letting each word land',
    'È quello che possiamo chiamare il piccolo mostro.':
        'calm and warm, with a soft, gently amused smile',
    'È come mettersi un sassolino nella scarpa, solo per il sollievo di toglierlo.':
        'warm, with a light, knowing smile',
    'Ascolta bene, adesso.':
        'softer and slower, intimate, gently drawing attention',
    'Ora, nel tuo luogo sicuro, immagina davanti a te una nuvola di fumo grigio. Dentro ci sono tutte le vecchie convinzioni: «mi rilassa», «mi aiuta», «senza non ce la faccio».':
        'soft, slow and dreamy, evocative',
    'E adesso arriva un vento fresco, pulito.':
        'soft, slow and dreamy, airy and fresh',
    'Guarda il vento che spinge via il fumo, pezzo dopo pezzo…':
        'soft, slow and dreamy',
    'il grigio si dirada…':
        'soft, slow and dreamy, gently trailing off',
    'e dietro appare un cielo limpido, luminoso.':
        'softly brightening, with gentle wonder',
    'Allunga la mano. Spingi.':
        'soft, slow and gently encouraging',
    'Esci.':
        'soft and inviting, with a gentle sense of release',
    'Non hai dovuto lottare. Non hai usato la forza di volontà. Hai soltanto visto la verità, e la verità ha aperto la porta.':
        'warm, quietly certain, gently proud',
    'E dentro di te puoi dire: «Che bello: non ne ho più bisogno».':
        'warm, with quiet joy on the quoted phrase',
    'In pochi giorni quella vocina diventa più debole. Poi un sussurro. Poi silenzio.':
        'gradually softer and slower, the last words quiet but clear',
    "E sai anche un'altra cosa, con assoluta chiarezza: non esiste «una sigaretta sola».":
        'calm, clear and firm, quietly certain',
    'Una sola sigaretta è proprio il modo in cui la trappola si richiude. È così che ti ha trattenuto per tutto questo tempo.':
        'calm, clear and firm, quietly certain',
    'Così, se un giorno qualcuno ti offrirà una sigaretta, o un pensiero ti suggerirà «solo questa», sorriderai, perché conosci la verità. E dirai, con leggerezza: «No, grazie. Non fumo».':
        'warm and light, with an easy smile',
    '«Non rinuncio a niente. Mi riprendo tutto».':
        'slow, joyful conviction, gentle emphasis on the last word',
    "Unisci delicatamente la punta del pollice e dell'indice di una mano.":
        'calm, slow and clear, a gentle instruction',
    'Ora è il momento di tornare, portando con te tutto quello che hai scoperto.':
        'calm and warm, still soft, beginning to lift',
    'Conterò da uno a cinque. Al cinque aprirai gli occhi, con la mente lucida, piena di energia e di buon umore.':
        'calm and warm, softly beginning to lift',
    'Uno… inizi a risalire, lentamente.':
        'warm, still soft and slow, just beginning to rise',
    'Due… senti di nuovo il peso del corpo, il contatto con la sedia o con il letto.':
        'warm, a little brighter and more present',
    "Tre… muovi un po' le dita delle mani e dei piedi. Il respiro si fa più vivace.":
        'warm and brighter, with more energy, gently awakening',
    "Quattro… l'energia sale, come in una mattina di sole.":
        'bright, energized, warm and lively',
    'Cinque. Apri gli occhi. Fai un bel respiro… ed eccoti qui.':
        'warm, bright and fully awake',
    "Se hai vicino il pacchetto, prendilo e buttalo via, insieme all'accendino. Non con tristezza: con il sollievo di chi si toglie finalmente un sassolino dalla scarpa.":
        'warm, bright and fully awake, light and encouraging',
    'Questa è la tua nuova vita. Goditela.':
        'warm, bright, with a gentle smile',
}
# Ritocchi del testo letto (il sottotitolo resta quello del copione).
RITOCCHI = {
    # Congiuntivo appòggino, accentato sulla terzultima con ò aperta. Senza accento il TTS può l
    'Lascia che le mani si appoggino dove vogliono.':
        'Lascia che le mani si appòggino dove vogliono.',
    # Accento corretto sèparano (/ˈsɛparano/). L'errore 'sepàrano' è frequente e si sentirebbe i
    'La mascella si scioglie. I denti si separano appena. La lingua riposa.':
        'La mascella si scioglie. I denti si sèparano appena. La lingua riposa.',
    # Omografo: qui è l'aggettivo leggère, non il verbo lèggere, che è più frequente. Priorità a
    'Scende lungo le braccia, fino alle mani, fino alla punta delle dita. Forse le senti calde, o leggere, o pesanti. Qualunque sensazione va bene.':
        'Scende lungo le braccia, fino alle mani, fino alla punta delle dita. Forse le senti calde, o leggère, o pesanti. Qualunque sensazione va bene.',
    # Un respiro udibile tra le due frasi rende concreta la suggestione e aiuta l'approfondiment
    "Il petto si muove piano, insieme al respiro. E ogni respiro ti porta un po' più giù, in una calma sempre più profonda.":
        "Il petto si muove piano, insieme al respiro. <breath> E ogni respiro ti porta un po' più giù, in una calma sempre più profonda.",
    # Unisce due ritocchi: la breve pausa di regia dopo la domanda, che lascia affiorare il rico
    "E ricordi il sapore di quella prima sigaretta? Il fumo che graffiava la gola. Forse la tosse, forse un po' di nausea. Il tuo corpo, saggio, ti stava dicendo la verità.":
        "E ricordi il sapore di quella prima sigaretta? <short pause> Il fumo che graffiava la gola. Forse la tosse, forse un po' di nàusea. Il tuo corpo, saggio, ti stava dicendo la verità.",
    # La breve pausa dopo i due punti dà peso alla rivelazione centrale.
    'Quello che rimane è la verità: non ti serve. Non ti è mai servita.':
        'Quello che rimane è la verità: <short pause> non ti serve. Non ti è mai servita.',
    # Lascia il tempo di guardare la porta prima di scoprire che non c'è nessuna serratura.
    "Guarda bene quella porta. Non c'è nessuna serratura. Non c'è mai stata.":
        "Guarda bene quella porta. <short pause> Non c'è nessuna serratura. Non c'è mai stata.",
    # Il primo respiro fuori dalla gabbia, subito dopo 'Esci.'. Va a inizio riga, come da correz
    "Senti l'aria sul viso. Lo spazio intorno a te. La leggerezza.":
        "<breath> Senti l'aria sul viso. Lo spazio intorno a te. La leggerezza.",
    # Accento corretto resìdui. Con lo iato finale -ui il TTS può leggere 'residùi' (voce aggiun
    'Nei prossimi giorni il tuo corpo eliminerà gli ultimi residui di nicotina.':
        'Nei prossimi giorni il tuo corpo eliminerà gli ultimi resìdui di nicotina.',
    # Il respiro risponde all'istruzione esplicita, prende il posto della sigaretta nel momento 
    'Ora arriva un momento di stress: una scadenza, una discussione, un imprevisto. Fai un respiro profondo, la mente è lucida, e lo affronti meglio di come avresti fatto fumando. Perché adesso hai un problema in meno, non uno in più.':
        'Ora arriva un momento di stress: una scadenza, una discussione, un imprevisto. Fai un respiro profondo, <breath> la mente è lucida, e lo affronti meglio di come avresti fatto fumando. Perché adesso hai un problema in meno, non uno in più.',
    # La micro-pausa mette in risalto il ribaltamento da sacrificio a liberazione.
    'E adesso, nel tuo luogo sicuro, è il momento di una scelta semplice. Non un sacrificio: una liberazione.':
        'E adesso, nel tuo luogo sicuro, è il momento di una scelta semplice. Non un sacrificio: <short pause> una liberazione.',
    # Il respiro prima del proposito lo rende solenne e fa da modello all'ascoltatore.
    'Se senti che è giusto per te, ripeti dentro di te, con calma e con gioia: «Da adesso, non fumo più».':
        'Se senti che è giusto per te, ripeti dentro di te, con calma e con gioia: <breath> «Da adesso, non fumo più».',
    # Come nella 54, la domanda invita a sentire qualcosa adesso. La micro-pausa dà il tempo di 
    'Senti la gioia che arriva? Non devi aspettare settimane per sentirti una persona libera. Lo sei già, da questo momento.':
        'Senti la gioia che arriva? <short pause> Non devi aspettare settimane per sentirti una persona libera. Lo sei già, da questo momento.',
    # Momento di ancoraggio: il respiro udibile segue l'istruzione, guida quello dell'ascoltator
    'Fai un respiro profondo, e ripeti dentro di te: «Che bello: non ne ho più bisogno».':
        'Fai un respiro profondo, <breath> e ripeti dentro di te: «Che bello: non ne ho più bisogno».',
    # Il respiro del risveglio accompagna quello dell'ascoltatore prima di 'eccoti qui'. Lo stil
    'Cinque. Apri gli occhi. Fai un bel respiro… ed eccoti qui.':
        'Cinque. Apri gli occhi. Fai un bel respiro… <breath> ed eccoti qui.',
    # Imperativo con due enclitici: il TTS rischia di dire 'goditèla'. È la frase finale, quindi
    'Questa è la tua nuova vita. Goditela.':
        'Questa è la tua nuova vita. Gòditela.',
}

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
# Le riprese e i giudizi sono indicizzati per «chiave»: modello + voce + testo
# letto + stile. Se il copione cambia, si rifanno solo le frasi cambiate.
RICHIESTE = WORK / 'richieste.json'


def load_state():
    st = json.loads(STATE.read_text('utf-8')) if STATE.exists() else {}
    for k, v in (('voce', None), ('voci_progettate', {}), ('riprese', {}), ('qa', {}),
                 ('grezzi', {}), ('voci_importate', [])):
        st.setdefault(k, v)
    # formato vecchio (prima del 26/11/2026): voci indicizzate per numero di frase,
    # riconoscibili perché contengono il campo 'key'. Le voci nuove restano intatte.
    if st.get('formato') != 2:
        old_r, old_q = st['riprese'], st['qa']
        st['riprese'], st['qa'] = {}, {}
        for k, e in old_r.items():
            if 'key' in e:
                st['riprese'][e['key']] = {'file': e['file'], 'voce': e.get('voce'), 'i': int(k)}
                if k in old_q:
                    st['qa'][e['key']] = old_q[k]
            else:
                st['riprese'][k] = e
                if k in old_q:
                    st['qa'][k] = old_q[k]
        st['formato'] = 2
    st.pop('richieste', None)
    return st


def save_state(st):
    WORK.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), 'utf-8')
    tmp.replace(STATE)


def take_key(voice, seg):
    raw = '|'.join([TTS_MODEL, voice, tts_text(seg), tts_style(seg)])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


def parse_fasi(valori, n):
    """--fasi: numeri da 1 a n, senza doppioni, in ordine."""
    if not valori:
        return list(range(n))
    try:
        fasi = sorted({int(v) - 1 for v in valori})
    except ValueError:
        sys.exit(f'--fasi vuole numeri da 1 a {n}')
    if fasi[0] < 0 or fasi[-1] >= n:
        sys.exit(f'--fasi vuole numeri da 1 a {n}')
    return fasi


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40]


# ================================================================ API
class ApiError(Exception):
    def __init__(self, code, body):
        super().__init__(f'HTTP {code}: {body[:600]}')
        self.code = code
        self.body = body


class QuotaError(ApiError):
    """Quota giornaliera esaurita: inutile riprovare prima del reset."""


def oggi_quota():
    """Le quote giornaliere gratuite si azzerano a mezzanotte, ora del Pacifico."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d')
    except Exception:  # zoneinfo assente: approssimazione UTC-8
        return time.strftime('%Y-%m-%d', time.gmtime(time.time() - 8 * 3600))


def richieste_oggi():
    try:
        d = json.loads(RICHIESTE.read_text('utf-8'))
    except (OSError, ValueError):
        d = {}
    return d.get(oggi_quota(), {})


def conta_richiesta(model):
    """Contatore indicativo in un file a sé, così nessun salvataggio di stato lo cancella."""
    try:
        d = json.loads(RICHIESTE.read_text('utf-8'))
    except (OSError, ValueError):
        d = {}
    giorno = d.setdefault(oggi_quota(), {})
    giorno[model] = giorno.get(model, 0) + 1
    WORK.mkdir(parents=True, exist_ok=True)
    RICHIESTE.write_text(json.dumps(d, indent=1), 'utf-8')


def api(method, path, body=None, query=None, timeout=600, tentativi=8, conta=None):
    """conta: nome del modello di cui contare ogni invio (anche fallito, tranne i 429)."""
    url = API + path
    if query:
        url += '?' + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('GEMINI_API_KEY'):
        headers['x-goog-api-key'] = os.environ['GEMINI_API_KEY']
    for attempt in range(tentativi):
        last = attempt == tentativi - 1
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if conta:
                    conta_richiesta(conta)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'replace')
            if conta and e.code != 429:
                conta_richiesta(conta)
            if e.code == 429 and re.search(r'PerDay|per day|daily|requests per day', txt, re.I):
                raise QuotaError(e.code, 'Quota giornaliera esaurita: riprova dopo il reset. ' + txt)
            if e.code in (429, 500, 502, 503, 504) and not last:
                m = re.search(r'"retryDelay":\s*"(\d+)', txt)
                wait = int(m.group(1)) + 2 if m else min(90, 8 * 2 ** attempt)
                print(f'   … HTTP {e.code}, riprovo tra {wait}s', flush=True)
                time.sleep(wait)
                continue
            raise ApiError(e.code, txt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if conta:
                conta_richiesta(conta)
            if not last:
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
    r = api('POST', '/interactions', body, tentativi=3, conta=TTS_MODEL)
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
    if w <= 2:                       # «Bene.», «Uno.», «Quattro…»: possono essere molto allungate
        return 0.2 <= dur <= 4.5
    rate = w / max(dur, 0.1)
    return 0.6 <= rate <= 3.8 and dur < 4 + w * 1.2


def expected_weight(t):
    """Durata attesa relativa di una frase: lettere + pause della punteggiatura."""
    letters = len(re.findall(r'\w', t))
    return letters + 6 * len(re.findall(r'[,;:]', t)) + 10 * len(re.findall(r'[.!?…]', t))


def word_edit(a, b):
    """Distanza di edit tra due liste di parole."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ai != b[j - 1]))
        prev = cur
    return prev[-1]


def allinea_asr(x, segs, min_sil=0.3, max_span=18):
    """Divide una ripresa lunga allineandola al testo, non alle pause.

    La ripresa viene spezzata in tutti i suoi silenzi; ogni pezzo viene trascritto
    con Whisper e una programmazione dinamica assegna pezzi consecutivi alle frasi
    del copione minimizzando la distanza tra parole trascritte e parole scritte.
    Funziona anche quando le pause tra le frasi sono corte quanto quelle interne.
    Restituisce (pezzi, errore medio per parola) oppure (None, motivo).
    """
    n = len(segs)
    db, h = frames_db(x)
    thr = speech_threshold(db)
    quiet = db < thr
    v = np.where(~quiet)[0]
    if not len(v):
        return None, 'nessun parlato'
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if start > v[0] and (i - start) * h / SR >= min_sil:
                runs.append((start, i))
            start = None
    bounds = [0] + [((a + b) // 2) * h for a, b in runs] + [len(x)]
    chunks = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    C = len(chunks)
    if C < n:
        return None, f'{C} pezzi per {n} frasi'
    heard = [norm_words(asr(trim(x[a:b], thr)) or '') for a, b in chunks]
    ref = [norm_words(sg['t']) for sg in segs]
    INF = float('inf')
    dp = [[INF] * (C + 1) for _ in range(n + 1)]
    back = [[-1] * (C + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for k in range(n):
        rem = n - k - 1                       # frasi ancora da assegnare dopo questa
        for c in range(C):
            if dp[k][c] == INF:
                continue
            acc = []
            for c2 in range(c + 1, min(C - rem, c + max_span) + 1):
                acc = acc + heard[c2 - 1]
                cost = dp[k][c] + word_edit(ref[k], acc)
                if cost < dp[k + 1][c2]:
                    dp[k + 1][c2] = cost
                    back[k + 1][c2] = c
    if dp[n][C] == INF:
        return None, 'allineamento impossibile'
    cuts, c = [C], C
    for k in range(n, 0, -1):
        c = back[k][c]
        cuts.append(c)
    cuts = cuts[::-1]                          # indici di pezzo: frase k = pezzi cuts[k]..cuts[k+1]
    pieces = [trim(x[chunks[cuts[k]][0]:chunks[cuts[k + 1] - 1][1]], thr) for k in range(n)]
    # quota di frasi che corrispondono al testo (un'allucinazione di Whisper su una
    # parola isolata, o una frase improvvisata da Gemini, non devono invalidare tutto)
    buone = 0
    for k in range(n):
        udito = [w for c in range(cuts[k], cuts[k + 1]) for w in heard[c]]
        if word_edit(ref[k], udito) <= max(1, 0.3 * len(ref[k])):
            buone += 1
    return pieces, 1 - buone / n


def split_takes(x, segs):
    """Divide la ripresa di una fase nelle sue frasi.

    Con Whisper disponibile la divisione segue il testo (allinea_asr); altrimenti
    segue le pause e le durate attese.

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
    if get_asr():
        pieces, err = allinea_asr(x, segs)
        if pieces is not None and err <= 0.2:
            strane = [sg['i'] for sg, pc in zip(segs, pieces) if not plausible(sg, len(pc) / SR)]
            why = f'ok (allineata al testo, {1 - err:.0%} delle frasi corrisponde)'
            if strane:
                why += f'; durata insolita da controllare: {strane}'
            return pieces, why
        print(f'  allineamento al testo non riuscito ({err if pieces is None else f"corrisponde solo il {1 - err:.0%}"}): '
              'divido con le pause', flush=True)
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
    rifinito = False
    if get_asr():
        nuovi = rifinisci_tagli(x, segs, mid, chosen, thr)
        rifinito, chosen = nuovi != chosen, nuovi
    cuts = [0] + [int(mid[j] * SR) for j in chosen] + [len(x)]
    pieces = [trim(x[cuts[k]:cuts[k + 1]], thr) for k in range(n)]
    strane = [sg['i'] for sg, pc in zip(segs, pieces) if not plausible(sg, len(pc) / SR)]
    if len(strane) > max(1, n // 10):
        return None, f'durata anomala in {len(strane)} frasi (es. {strane[:4]})'
    shortest = min(sil[j] for j in chosen)
    if shortest < 0.45 and not get_asr():
        return None, f'pausa tra frasi troppo breve ({shortest:.2f}s)'
    why = 'ok' + (' (tagli corretti con Whisper)' if rifinito else '')
    scelti = set(chosen)
    altre = [sil[j] for j in range(m) if j not in scelti]
    if altre and shortest < 1.15 * max(altre):
        why += (f'; divisione incerta: pausa tra frasi più breve {shortest:.2f}s, '
                f'pausa interna più lunga {max(altre):.2f}s')
    if strane:
        why += f'; durata insolita da controllare: {strane}'
    return pieces, why


def rifinisci_tagli(x, segs, mid, chosen, thr, raggio=3):
    """Sposta i tagli dove la trascrizione locale coincide meglio con il copione.

    Serve quando una pausa interna («Dieci… il primo passo») è lunga quanto la
    pausa tra due frasi: si provano le pause vicine e si tiene il taglio che
    riduce gli errori di trascrizione delle due frasi confinanti.
    """
    n, ch, cache = len(segs), list(chosen), {}

    def err(k, a_j, b_j):
        key = (k, a_j, b_j)
        if key not in cache:
            a = 0 if a_j is None else int(mid[a_j] * SR)
            b = len(x) if b_j is None else int(mid[b_j] * SR)
            ref = segs[k]['t']
            cache[key] = wer(ref, asr(trim(x[a:b], thr)) or '') * max(1, len(norm_words(ref)))
        return cache[key]

    def bounds(k):
        return (None if k == 0 else ch[k - 1]), (None if k == n - 1 else ch[k])

    errs = [err(k, *bounds(k)) for k in range(n)]
    for _ in range(2):
        changed = False
        for b in range(n - 1):
            if errs[b] + errs[b + 1] < 1:
                continue
            lo = ch[b - 1] + 1 if b > 0 else 0
            hi = ch[b + 1] - 1 if b + 1 < n - 1 else len(mid) - 1
            prev_cut = None if b == 0 else ch[b - 1]
            next_cut = None if b + 1 == n - 1 else ch[b + 1]
            best_e, best_j = errs[b] + errs[b + 1], ch[b]
            for j in range(max(lo, ch[b] - raggio), min(hi, ch[b] + raggio) + 1):
                if j == ch[b]:
                    continue
                e = err(b, prev_cut, j) + err(b + 1, j, next_cut)
                if e < best_e - 0.5:
                    best_e, best_j = e, j
            if best_j != ch[b]:
                ch[b] = best_j
                changed = True
                errs[b], errs[b + 1] = err(b, *bounds(b)), err(b + 1, *bounds(b + 1))
        if not changed:
            break
    return ch


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


# Voci create con Voice design sul progetto della chiave (gli id non sono segreti:
# funzionano solo con la chiave). Servono se lo stato locale viene perso.
VOCI_SALVATE = {
    'ipnoterapeuta-donna': 'voice_0n9pveczr6t2',
}


def resolve_voice(name):
    st = load_state()
    return st['voci_progettate'].get(name) or VOCI_SALVATE.get(name, name)


def cmd_campioni(args):
    """Un campione per voce: una sola richiesta ciascuno, senza divisione in frasi."""
    titles, segs = load_script()
    by_text = {s['t']: s for s in segs}
    sample = [by_text[t] for t in FRASI_CAMPIONE]
    outdir = Path(args.out) if args.out else WORK / 'campioni'
    for name in args.voci:
        voice = resolve_voice(name)
        print(f'▶ Campione: {name}', flush=True)
        try:
            x = tts(items_for(sample), voice)
        except QuotaError as e:
            sys.exit(f'Quota giornaliera esaurita: {e}')
        except ApiError as e:
            print('  errore:', e)
            continue
        write_wav(outdir / 'grezzi' / f'{slug(name)}.wav', x)
        x = trim(x)
        track = np.concatenate([np.zeros(int(3.0 * SR), np.float32), x, np.zeros(int(6.0 * SR), np.float32)])
        master(track, outdir / f'campione-{slug(name)}.mp3', bitrate='128k')
        print('  ✓', outdir / f'campione-{slug(name)}.mp3')


def est_seconds(seg):
    """Durata stimata di una frase letta lentamente, più la pausa che la segue."""
    t = tts_text(seg)
    return words(t) / 1.75 + 0.35 * len(re.findall(r'[,;:…]', t)) + 1.5


def gruppi_fasi(segs, fasi, max_s):
    """Raggruppa fasi consecutive in richieste di durata stimata <= max_s."""
    groups, cur, tot = [], [], 0.0
    for fi in fasi:
        d = sum(est_seconds(s) for s in segs if s['f'] == fi)
        if cur and (tot + d > max_s or fi != cur[-1] + 1):
            groups.append(cur)
            cur, tot = [], 0.0
        cur.append(fi)
        tot += d
    if cur:
        groups.append(cur)
    return groups


def items_for(segs):
    return [(tts_text(s) + ('' if k == len(segs) - 1 else SEPARATORE), tts_style(s))
            for k, s in enumerate(segs)]


def nome_ripresa(s, key, tag=''):
    """Nome unico: una ripresa nuova non sovrascrive mai il file di una precedente."""
    return f'f{s["f"]:02d}-s{s["i"]:03d}-{key}{tag}-{time.time_ns() % 10**12:012d}.wav'


def salva_pezzi(st, segs, pieces, voice, voice_name, sovrascrivi=True):
    salvate = 0
    for s, pc in zip(segs, pieces):
        key = take_key(voice, s)
        if not sovrascrivi and key in st['riprese']:
            continue
        f = TAKES / nome_ripresa(s, key)
        write_wav(f, pc)
        st['riprese'][key] = {'file': f.name, 'voce': voice_name, 'i': s['i']}
        st['qa'].pop(key, None)
        salvate += 1
    return salvate


def ripresa(st, voice, s):
    return st['riprese'].get(take_key(voice, s))


def giudizio(st, voice, s):
    return st['qa'].get(take_key(voice, s), {})


def fasi_da_fare(segs, st, voice, fasi):
    return [fi for fi in fasi
            if not all(take_key(voice, s) in st['riprese'] for s in segs if s['f'] == fi)]


def divisione_dubbia(why):
    return 'incerta' in why or 'insolita' in why


def registra_grezzo(st, raw, segs, voice, voice_name, motivo):
    """Una ripresa pagata ma non ancora divisa: resta recuperabile con «dividi»."""
    st['grezzi'][raw.name] = {'frasi': [s['i'] for s in segs], 'keys': [take_key(voice, s) for s in segs],
                              'voce': voice_name, 'motivo': motivo}
    save_state(st)


def cmd_piano(args):
    titles, segs = load_script()
    st = load_state()
    voice_name = args.voce or st.get('voce')
    fasi = list(range(len(titles)))
    todo = fasi_da_fare(segs, st, resolve_voice(voice_name), fasi) if voice_name else fasi
    groups = gruppi_fasi(segs, todo, args.max_minuti * 60)
    print(f'Voce: {voice_name or "(da scegliere)"} · fasi da registrare: {len(todo)} · richieste: {len(groups)}')
    for g in groups:
        gs = [s for s in segs if s['f'] in g]
        print(f'  fasi {g[0] + 1}-{g[-1] + 1}: {len(gs)} frasi, ~{sum(est_seconds(s) for s in gs) / 60:.1f} min')
    print(f'Richieste TTS inviate oggi ({oggi_quota()}, ora del Pacifico): '
          f'{richieste_oggi().get(TTS_MODEL, 0)} (conteggio indicativo)')


def cmd_genera(args):
    titles, segs = load_script()
    st = load_state()
    voice_name = args.voce
    voice = resolve_voice(voice_name)
    st['voce'] = voice_name
    save_state(st)
    fasi = parse_fasi(args.fasi, len(titles))
    todo = fasi_da_fare(segs, st, voice, fasi)
    for fi in sorted(set(fasi) - set(todo)):
        print(f'= Fase {fi + 1} già registrata')
    groups = gruppi_fasi(segs, todo, args.max_minuti * 60)
    print(f'Richieste previste: {len(groups)}', flush=True)
    for g in groups:
        gsegs = [s for s in segs if s['f'] in g]
        nomi = ', '.join(titles[fi] for fi in g)
        print(f'▶ Fasi {g[0] + 1}-{g[-1] + 1} ({nomi}): {len(gsegs)} frasi', flush=True)
        try:
            x = tts(items_for(gsegs), voice)
        except QuotaError as e:
            print('  Quota giornaliera esaurita: i progressi sono salvati, riprendi dopo il reset.')
            print('  ', str(e)[:300])
            return
        except ApiError as e:
            print('  errore:', e)
            continue
        raw = TAKES / f'gruppo-f{g[0] + 1:02d}-f{g[-1] + 1:02d}-{slug(voice_name)}-{int(time.time())}.wav'
        write_wav(raw, x)
        registra_grezzo(st, raw, gsegs, voice, voice_name, 'da dividere')
        print(f'  ripresa: {len(x) / SR / 60:.1f} min', flush=True)
        pieces, why = split_takes(x, gsegs)
        print('  divisione:', why, flush=True)
        if pieces is None:
            st['grezzi'][raw.name]['motivo'] = why
            save_state(st)
            print('  → ripresa salvata: riprova con «dividi» (nessuna nuova richiesta)')
            continue
        n = salva_pezzi(st, gsegs, pieces, voice, voice_name, sovrascrivi=False)
        if divisione_dubbia(why):
            st['grezzi'][raw.name]['motivo'] = why
            print(f'  ripresa tenuta come {raw.name}: con Whisper (VOCE_ASR_DIR) «dividi {raw.name} --forza» '
                  'la ridivide senza nuove richieste')
        else:
            del st['grezzi'][raw.name]
        save_state(st)
        print(f'  ✓ {n} frasi salvate', flush=True)
        controllo_asr(gsegs, pieces)


def cmd_dividi(args):
    """Riprova a dividere le riprese salvate ma non ancora divise (nessuna richiesta all'API)."""
    titles, segs = load_script()
    st = load_state()
    if not st['grezzi']:
        print('Nessuna ripresa da dividere.')
        return
    if args.forza and not args.nome:
        sys.exit('--forza vale per una ripresa precisa: «dividi NOME --forza» (i nomi li stampa «dividi»).')
    if args.nome and args.nome not in st['grezzi']:
        sys.exit(f'Nessuna ripresa da dividere chiamata {args.nome}. Disponibili: {", ".join(st["grezzi"])}')
    for name, info in list(st['grezzi'].items()):
        if args.nome and name != args.nome:
            continue
        voice = resolve_voice(info['voce'])
        by_key = {take_key(voice, s): s for s in segs}
        keys = info.get('keys')
        if not keys or any(k not in by_key for k in keys):
            print(f'{name}: il copione è cambiato da quando è stata registrata, la salto')
            continue
        gsegs = [by_key[k] for k in keys]
        if not args.forza and all(k in st['riprese'] for k in keys):
            print(f'{name}: tutte le sue frasi hanno già una ripresa (per sostituirle: «dividi {name} --forza»)')
            continue
        pieces, why = split_takes(read_wav(TAKES / name), gsegs)
        print(f'{name}: {why}')
        if pieces is not None:
            n = salva_pezzi(st, gsegs, pieces, voice, info['voce'], sovrascrivi=args.forza)
            if divisione_dubbia(why):
                info['motivo'] = why
            else:
                del st['grezzi'][name]
            save_state(st)
            print(f'  {n} frasi salvate')
            controllo_asr(gsegs, pieces)


def load_audio(path):
    """Qualsiasi file audio (wav, mp3, m4a...) → mono float32 a SR, tramite ffmpeg."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype='<f4').copy()


def cmd_importa(args):
    """Importa le fasi registrate a mano (es. in AI Studio): fase01.wav … fase13.wav."""
    titles, segs = load_script()
    st = load_state()
    voice_name = args.voce
    voice = resolve_voice(voice_name)
    st['voce'] = voice_name
    if voice == voice_name and voice_name not in st['voci_importate']:
        st['voci_importate'].append(voice_name)   # etichetta di una voce non disponibile nell'API
    files = {}
    for f in sorted(Path(args.cartella).iterdir()):
        m = re.match(r'fase\s*0*(\d+)', f.stem, re.I)
        if m and f.suffix.lower() in ('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.aac'):
            fi = int(m.group(1)) - 1
            if 0 <= fi < len(titles):
                files[fi] = f
    if not files:
        sys.exit('Nessun file faseNN trovato in ' + args.cartella)
    for fi, f in sorted(files.items()):
        fsegs = [s for s in segs if s['f'] == fi]
        x = load_audio(f)
        raw = TAKES / f'importata-f{fi + 1:02d}-{slug(voice_name)}-{int(time.time())}.wav'
        write_wav(raw, x)
        registra_grezzo(st, raw, fsegs, voice, voice_name, 'da dividere')
        pieces, why = split_takes(x, fsegs)
        print(f'Fase {fi + 1} {titles[fi]}: {len(x) / SR / 60:.1f} min → {why}', flush=True)
        if pieces is None:
            st['grezzi'][raw.name]['motivo'] = why
            save_state(st)
            continue
        salva_pezzi(st, fsegs, pieces, voice, voice_name)
        if divisione_dubbia(why):
            st['grezzi'][raw.name]['motivo'] = why
        else:
            del st['grezzi'][raw.name]
        save_state(st)
        controllo_asr(fsegs, pieces)
    mancanti = sorted(set(range(len(titles))) - set(files))
    if mancanti:
        print('Fasi non ancora importate:', ', '.join(str(m + 1) for m in mancanti))


def cmd_pause(args):
    """Misura le pause di una ripresa: servono a scegliere il SEPARATORE."""
    x = load_audio(args.file)
    db, h = frames_db(x)
    thr = speech_threshold(db)
    quiet = db < thr
    v = np.where(~quiet)[0]
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if start > v[0]:
                runs.append((i - start) * h / SR)
            start = None
    runs = sorted((r for r in runs if r >= 0.25), reverse=True)
    print(f'{len(x) / SR:.1f} s di audio, {len(runs)} pause di almeno 0,25 s')
    print('Le più lunghe:', ', '.join(f'{r:.2f}' for r in runs[:args.n]))
    if args.frasi and len(runs) >= args.frasi:
        tra, interna = runs[args.frasi - 2], runs[args.frasi - 1]
        print(f'Con {args.frasi} frasi: la più breve tra frasi è ~{tra:.2f}s, la più lunga interna ~{interna:.2f}s '
              f'→ margine {tra / max(interna, 0.01):.2f} (sotto 1,3 conviene allungare il SEPARATORE)')


# ================================================================ controllo locale (Whisper)
_ASR = None


def get_asr():
    """Riconoscitore Whisper locale (sherpa-onnx), se configurato con VOCE_ASR_DIR."""
    global _ASR
    if _ASR is not None or not ASR_DIR:
        return _ASR or None
    try:
        import sherpa_onnx
        d = Path(ASR_DIR)
        enc = sorted(d.glob('*encoder*.onnx'))
        dec = sorted(d.glob('*decoder*.onnx'))
        tok = sorted(d.glob('*tokens.txt'))
        pick = lambda fs: next((f for f in fs if 'int8' in f.name), fs[0])
        _ASR = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(pick(enc)), decoder=str(pick(dec)), tokens=str(tok[0]),
            language='it', task='transcribe', num_threads=max(1, (os.cpu_count() or 2) - 1))
    except Exception as e:
        print('Controllo locale non disponibile:', e)
        _ASR = False
    return _ASR or None


NUMERI = ['zero', 'uno', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 'otto', 'nove', 'dieci']


def norm_words(t):
    import unicodedata
    t = unicodedata.normalize('NFD', t.lower())
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return [NUMERI[int(w)] if w.isdigit() and int(w) <= 10 else w for w in re.findall(r"[a-z0-9]+", t)]


def wer(ref, hyp):
    r, h = norm_words(ref), norm_words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[:], i
        for j in range(1, len(h) + 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(1, len(r))


def asr(x):
    rec = get_asr()
    if not rec:
        return None
    out = []
    x = resample(x, SR, 16000)
    for a in range(0, len(x), 16000 * 28):
        st = rec.create_stream()
        st.accept_waveform(16000, x[a:a + 16000 * 28].astype(np.float32))
        rec.decode_stream(st)
        out.append(st.result.text.strip())
    return ' '.join(out)


def controllo_asr(segs, takes, soglia=0.2):
    """Trascrive ogni frase in locale e segnala quelle che non corrispondono al copione."""
    if not get_asr():
        return {}
    res, cattive = {}, 0
    for s, x in zip(segs, takes):
        heard = asr(x)
        w = wer(s['t'], heard)
        if words(s['t']) < 4:
            w = min(w, soglia)   # Whisper è inaffidabile su frasi di 1-3 parole: non bastano per scartarle
        res[s['i']] = {'heard': heard, 'wer': round(w, 2)}
        if w > soglia:
            cattive += 1
            print(f'  ⚠ [{s["i"]}] WER {w:.2f}: «{s["t"][:50]}» → «{heard[:60]}»')
    print(f'  controllo locale: {len(segs) - cattive}/{len(segs)} frasi corrispondono al copione', flush=True)
    return res


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
    r = api('POST', f'/models/{model}:generateContent', body, tentativi=4)
    text = r['candidates'][0]['content']['parts'][0]['text']
    d = json.loads(text)
    lines = d.get('lines', []) if isinstance(d, dict) else d if isinstance(d, list) else []
    return [x for x in lines if isinstance(x, dict)]


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


GEMINI_FIELDS = ('heard', 'text_ok', 'pronunciation_ok', 'natural_italian', 'artifacts', 'tone', 'problem', 'gemini')


def is_flagged(q):
    return bool(q.get('locale')) or not q.get('text_ok', True) or not q.get('pronunciation_ok', True) \
        or not q.get('natural_italian', True) or bool(q.get('artifacts')) or (q.get('tone') or 5) <= 3 \
        or (q.get('asr') or {}).get('wer', 0) > 0.2


def punteggio(q):
    if not q:
        return -1
    return (0 if is_flagged(q) else 10) + (q.get('tone') or 0) - 5 * (q.get('asr') or {}).get('wer', 0)


def valuta(model, fsegs, takes):
    """QA di Gemini (se c'è quota) + controlli locali + Whisper locale (se configurato).

    Restituisce (giudizi, stato) con stato 'ok', 'quota' o 'errore'. Un giudizio
    ha gemini=True solo se Gemini ha davvero valutato quella frase.
    """
    stato = 'ok'
    try:
        res = qa_phase(model, fsegs, takes)
    except QuotaError:
        print('  quota del modello QA esaurita: solo controlli locali')
        res, stato = [], 'quota'
    except Exception as e:
        print(f'  QA Gemini non riuscito ({str(e)[:120]})')
        res, stato = [], 'errore'
    try:
        asr_res = controllo_asr(fsegs, takes)
    except Exception as e:
        print(f'  controllo locale non riuscito ({str(e)[:120]})')
        asr_res = {}
    res = [r for r in res if isinstance(r, dict)] if isinstance(res, list) else []
    out = []
    for k, s in enumerate(fsegs):
        g = next((r for r in res if r.get('n') == k + 1), None)
        q = {f: g[f] for f in GEMINI_FIELDS if g and f in g}
        q['gemini'] = g is not None
        q['locale'] = local_checks(s, takes[k])
        if s['i'] in asr_res:
            q['asr'] = asr_res[s['i']]
        out.append(q)
    return out, stato


def cmd_qa(args):
    titles, segs = load_script()
    st = load_state()
    voice = resolve_voice(st['voce'])
    model = pick_qa_model()
    print('Modello QA:', model)
    tot_flag, senza = 0, 0
    for fi in parse_fasi(args.fasi, len(titles)):
        fsegs = [s for s in segs if s['f'] == fi]
        if any(ripresa(st, voice, s) is None for s in fsegs):
            print(f'- Fase {fi + 1}: riprese mancanti o di un testo precedente, salto')
            continue
        takes = [read_wav(TAKES / ripresa(st, voice, s)['file']) for s in fsegs]
        qs, stato = valuta(model, fsegs, takes)
        for s, q in zip(fsegs, qs):
            key = take_key(voice, s)
            old = st['qa'].get(key, {})
            if not q['gemini'] and old.get('gemini'):
                q.update({f: old[f] for f in GEMINI_FIELDS if f in old})   # il giudizio vecchio resta valido
            st['qa'][key] = q
            senza += not q.get('gemini')
            if is_flagged(q):
                tot_flag += 1
                print(f'  ⚠ [{s["i"]}] «{s["t"][:60]}» → {q.get("problem") or ""} '
                      f'{"; ".join(q["locale"])} (tono {q.get("tone")}, WER {(q.get("asr") or {}).get("wer")})')
        tones = [st['qa'][take_key(voice, s)].get('tone') or 0 for s in fsegs]
        print(f'- Fase {fi + 1} {titles[fi]}: tono medio {np.mean(tones):.1f}/5')
        save_state(st)
        if stato == 'quota':
            print('Quota del controllo Gemini esaurita: riprendi «qa» dopo il reset.')
            break
    print(f'Frasi da rifare: {tot_flag}' + (f' · senza giudizio Gemini: {senza}' if senza else ''))


def cmd_rigenera(args):
    """Rifà le frasi segnalate raggruppandole: una richiesta per gruppo, non una per frase.

    Usa sempre la voce della sessione (quella di «genera»): una ripresa con un'altra
    voce non verrebbe mai montata.
    """
    titles, segs = load_script()
    st = load_state()
    voice_name = st['voce']
    if not voice_name:
        sys.exit('Nessuna voce: esegui prima «genera VOCE».')
    if voice_name in st['voci_importate']:
        sys.exit(f'«{voice_name}» è una voce registrata a mano: rifai quelle frasi in AI Studio e importale di nuovo, '
                 'oppure registra la seduta con una voce dell\'API usando «genera VOCE».')
    voice = resolve_voice(voice_name)
    esplicite = bool(args.frasi)
    if esplicite:
        try:
            targets = sorted({int(i) for i in args.frasi})
        except ValueError:
            sys.exit(f'Le frasi si indicano con il loro numero, da 0 a {len(segs) - 1}')
        if targets[0] < 0 or targets[-1] >= len(segs):
            sys.exit(f'Le frasi si indicano con il loro numero, da 0 a {len(segs) - 1}')
    else:
        targets = [s['i'] for s in segs if ripresa(st, voice, s) and is_flagged(giudizio(st, voice, s))]
    if not targets:
        print('Nessuna frase da rifare.')
        return
    model = pick_qa_model()
    max_s = args.max_minuti * 60
    escluse = set()          # frasi da non rimandare all'API in questo comando
    for rnd in range(args.tentativi):
        if esplicite and rnd == 0:
            todo = list(targets)
        else:
            todo = [i for i in targets if i not in escluse and is_flagged(giudizio(st, voice, segs[i]))]
        if not todo:
            break
        batches, cur, tot = [], [], 0.0
        for i in todo:
            d = est_seconds(segs[i])
            if cur and tot + d > max_s:
                batches.append(cur)
                cur, tot = [], 0.0
            cur.append(segs[i])
            tot += d
        if cur:
            batches.append(cur)
        print(f'Giro {rnd + 1}: {len(todo)} frasi in {len(batches)} richieste', flush=True)
        for bsegs in batches:
            try:
                x = tts(items_for(bsegs), voice)
            except QuotaError:
                print('  Quota giornaliera esaurita: riprendi dopo il reset.')
                return
            except ApiError as e:
                print('  errore:', e)
                escluse.update(s['i'] for s in bsegs)
                continue
            raw = TAKES / f'rigenera-s{bsegs[0]["i"]:03d}-s{bsegs[-1]["i"]:03d}-r{rnd + 1}-{int(time.time())}.wav'
            write_wav(raw, x)
            registra_grezzo(st, raw, bsegs, voice, voice_name, 'rifacimento da valutare')
            pieces, why = split_takes(x, bsegs)
            print('  divisione:', why, flush=True)
            if pieces is None or (divisione_dubbia(why) and not get_asr()):
                st['grezzi'][raw.name]['motivo'] = why
                save_state(st)
                escluse.update(s['i'] for s in bsegs)
                print(f'  → ripresa conservata come {raw.name}, frasi lasciate come sono. '
                      'Se è un problema di pause, allunga VOCE_SEPARATORE o configura VOCE_ASR_DIR.')
                continue
            qs, stato = valuta(model, bsegs, pieces)
            for s, pc, q in zip(bsegs, pieces, qs):
                key = take_key(voice, s)
                old = st['qa'].get(key, {})
                nuova = key not in st['riprese']
                if not q['gemini'] and not nuova:
                    alt = TAKES / nome_ripresa(s, key, f'-alt{rnd + 1}')
                    write_wav(alt, pc)
                    escluse.add(s['i'])
                    esito = f'senza giudizio Gemini: tenuta la precedente (alternativa in {alt.name})'
                elif nuova or (esplicite and not is_flagged(q)) or punteggio(q) > punteggio(old):
                    f = TAKES / nome_ripresa(s, key, f'-r{rnd + 1}')
                    write_wav(f, pc)
                    st['riprese'][key] = {'file': f.name, 'voce': voice_name, 'i': s['i']}
                    st['qa'][key] = q
                    esito = 'sostituita'
                else:
                    esito = 'tenuta la precedente'
                print(f'  [{s["i"]}] {esito} (punteggio {punteggio(q):.1f} contro {punteggio(old):.1f})')
            del st['grezzi'][raw.name]
            save_state(st)
            if stato != 'ok':
                print('Il controllo di Gemini non è disponibile: mi fermo per non spendere richieste '
                      'su frasi che non posso valutare.')
                return


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
    voice = resolve_voice(st['voce'])
    mancanti = [s['i'] for s in segs if ripresa(st, voice, s) is None]
    if mancanti:
        sys.exit(f'Mancano {len(mancanti)} frasi del copione attuale (es. {mancanti[:6]}): '
                 'esegui prima «genera» (rifà solo quelle).')
    qs = [giudizio(st, voice, s) for s in segs]
    segnalate = [s['i'] for s, q in zip(segs, qs) if is_flagged(q)]
    senza = [s['i'] for s, q in zip(segs, qs) if not q.get('gemini')]
    if (segnalate or senza) and not args.forza:
        if segnalate:
            print(f'Attenzione: {len(segnalate)} frasi segnalate dal controllo ({segnalate[:8]}).')
        if senza:
            print(f'Attenzione: {len(senza)} frasi senza giudizio di Gemini ({senza[:8]}): esegui «qa».')
        print('Usa --forza per montare comunque.')
        return
    takes = [read_wav(TAKES / ripresa(st, voice, s)['file']) for s in segs]
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
    p = sub.add_parser('piano')
    p.add_argument('voce', nargs='?')
    p.add_argument('--max-minuti', type=float, default=MAX_RICHIESTA)
    p = sub.add_parser('genera')
    p.add_argument('voce')
    p.add_argument('--fasi', nargs='*', help='numeri di fase da 1 a 13')
    p.add_argument('--max-minuti', type=float, default=MAX_RICHIESTA)
    p = sub.add_parser('dividi')
    p.add_argument('nome', nargs='?', help='una ripresa precisa (i nomi li stampa «dividi»)')
    p.add_argument('--forza', action='store_true', help='con NOME: sostituisce anche frasi che hanno già una ripresa')
    p = sub.add_parser('pause')
    p.add_argument('file')
    p.add_argument('--frasi', type=int, help='quante frasi contiene la ripresa')
    p.add_argument('-n', type=int, default=12)
    p = sub.add_parser('importa')
    p.add_argument('cartella')
    p.add_argument('--voce', default='aistudio-donna')
    p = sub.add_parser('qa')
    p.add_argument('--fasi', nargs='*', help='numeri di fase da 1 a 13')
    p = sub.add_parser('rigenera')
    p.add_argument('frasi', nargs='*', help='numeri di frase (da 0); vuoto = quelle segnalate dal controllo')
    p.add_argument('--tentativi', type=int, default=2)
    p.add_argument('--max-minuti', type=float, default=MAX_RICHIESTA)
    p = sub.add_parser('monta')
    p.add_argument('--forza', action='store_true')
    p.add_argument('--bitrate', default='112k')
    args = ap.parse_args()
    {'voci': cmd_voci, 'progetta': cmd_progetta, 'campioni': cmd_campioni, 'piano': cmd_piano,
     'genera': cmd_genera, 'dividi': cmd_dividi, 'importa': cmd_importa, 'pause': cmd_pause, 'qa': cmd_qa, 'rigenera': cmd_rigenera,
     'monta': cmd_monta}[args.cmd](args)


if __name__ == '__main__':
    main()
