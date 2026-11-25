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

## Produzione, passo per passo

Dalla radice del repository:

```bash
python3 tools/voce/produci_voce.py voci                   # voci italiane disponibili
python3 tools/voce/produci_voce.py progetta               # crea le voci "ipnoterapeuta" donna e uomo
python3 tools/voce/produci_voce.py campioni ipnoterapeuta-donna ipnoterapeuta-uomo Sulafat Enceladus
#   → ascolta i campioni in tools/voce/lavoro/campioni/ e scegli la voce
python3 tools/voce/produci_voce.py genera ipnoterapeuta-donna   # registra le 13 fasi
python3 tools/voce/produci_voce.py qa                     # controllo qualità frase per frase
python3 tools/voce/produci_voce.py rigenera               # rifà solo le frasi segnalate
python3 tools/voce/produci_voce.py monta                  # montaggio, sottofondo, mastering
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
