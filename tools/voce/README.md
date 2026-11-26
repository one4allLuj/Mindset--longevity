# Voce professionale di «Respiro Libero»

Questo strumento produce l'audio della seduta `seduta-smettere-di-fumare.html`:
voce professionale, pause esatte del copione, sottofondo musicale originale e
mastering, più i tempi per i sottotitoli sincronizzati nella pagina.

Tutto è **gratuito**:

| Parte | Strumento | Costo |
| --- | --- | --- |
| Voce | Gemini 3.8 Flash TTS, piano gratuito di Google AI Studio | 0 € |
| Controllo qualità | Gemini Flash, piano gratuito | 0 € |
| Sottofondo musicale | generato da `produci_voce.py` (numpy) | 0 € |
| Montaggio e mastering | ffmpeg | 0 € |

Nel piano gratuito Google può usare i testi inviati per migliorare i suoi
prodotti. Non attivare la fatturazione sul progetto se vuoi restare a costo zero.

## Chiave API

1. Crea una chiave in Google AI Studio («Get API key»).
2. Nell'ambiente cloud del progetto aggiungi un **Network secret** con host
   `generativelanguage.googleapis.com`, intestazione `x-goog-api-key`, nessun prefisso.
   In alternativa, in locale: `export GEMINI_API_KEY=...`.

La chiave non va mai scritta nel codice né nei commit.

## Quote e costi

Il piano gratuito di Gemini 3.8 Flash TTS consente **10 richieste al giorno**
(si azzerano a mezzanotte, ora del Pacifico). Per questo lo strumento raggruppa
più fasi in una sola richiesta: la seduta intera richiede **4 richieste**
(`piano` mostra il conto). Se la quota finisce, lo strumento si ferma e
salva i progressi; basta rilanciare lo stesso comando dopo il reset.

Con Google AI Pro si possono riscattare in AI Studio 10 $ al mese di crediti
per l'API (pagina «Google AI plans» della documentazione). Con una chiave a pagamento
non c'è il limite giornaliero e l'intera seduta costa meno di 1 $.

## Produzione, passo per passo

Dalla radice del repository:

```bash
python3 tools/voce/produci_voce.py voci                   # voci italiane disponibili
python3 tools/voce/produci_voce.py progetta               # crea le voci "ipnoterapeuta" (Voice design)
python3 tools/voce/produci_voce.py campioni ipnoterapeuta-donna it-it-csagent-7 it-it-storyteller-1
#   → una richiesta per voce; ascolta i campioni in tools/voce/lavoro/campioni/
python3 tools/voce/produci_voce.py piano ipnoterapeuta-donna    # quante richieste servono
python3 tools/voce/produci_voce.py genera ipnoterapeuta-donna   # registra tutte le fasi (4 richieste)
python3 tools/voce/produci_voce.py qa                     # controllo qualità frase per frase
python3 tools/voce/produci_voce.py rigenera               # rifà insieme le frasi segnalate
python3 tools/voce/produci_voce.py monta                  # montaggio, sottofondo, mastering
```

`genera` divide ogni ripresa nelle singole frasi. Se la divisione non riesce,
la ripresa resta salvata e `dividi` riprova senza nuove richieste.
`--fasi 4 5 6` limita `genera` e `qa` ad alcune fasi (numerate da 1 a 13).

### Controllo locale con Whisper (facoltativo, gratuito)

Oltre al QA di Gemini, ogni frase può essere trascritta in locale e confrontata
con il copione: serve `pip install sherpa-onnx` e un modello Whisper per
sherpa-onnx (per esempio `sherpa-onnx-whisper-turbo` dalle release di
k2-fsa/sherpa-onnx su GitHub). Indica la cartella del modello:

```bash
export VOCE_ASR_DIR=/percorso/sherpa-onnx-whisper-turbo
```

`monta` scrive:

- `audio/respiro-libero.mp3`: la seduta completa (-18 LUFS, picco -1,5 dBTP);
- `audio/respiro-libero-tempi.js`: inizio e fine di ogni frase, per i sottotitoli.

La pagina usa l'audio solo se la firma del copione coincide. Se modifichi il
testo della seduta, rigenera le frasi cambiate (`genera` rifà solo quelle) e
poi `monta`: finché non lo fai, la pagina usa automaticamente la voce del dispositivo.

## Regia

In cima a `produci_voce.py`:

- `STILI`: il tono di ogni fase (rilassamento più lento e morbido, parti
  persuasive calme e sicure, risveglio più luminoso);
- `STILI_FRASE`: il tono di singole frasi;
- `RITOCCHI`: come far leggere una frase senza cambiare il sottotitolo
  (per esempio un accento da correggere);
- `VOCI_PROGETTATE`: la descrizione delle voci create con Voice design.

Le riprese stanno in `tools/voce/lavoro/` (ignorata da git) e vengono
riutilizzate: si rigenera solo ciò che cambia, per non consumare la quota gratuita.
