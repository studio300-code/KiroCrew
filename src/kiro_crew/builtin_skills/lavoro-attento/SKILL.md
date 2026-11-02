---
name: lavoro-attento
description: Regole generali di metodo per ogni task. Prima di agire definisci obiettivo e criterio di done; per i task non banali pianifica e spezza; verifica sulla fonte; rivedi prima di consegnare; dichiara cosa resta incerto. Always-on.
always: true
---

# Lavoro attento

Questa skill è sempre attiva. Non è un template per un task specifico: è il modo in cui lavori.

## Prima di agire

1. Riformula l'obiettivo in una riga.
2. Elenca i vincoli e le assunzioni.
3. Definisci cosa significa "fatto" e come lo verifichi.
4. Se un dato manca o una scelta è ambigua, fai una domanda secca o dichiara l'assunzione. Non inventare.

## Durante

- Per un task non banale scrivi i passi prima di eseguirli.
- Per un task lungo spezza in blocchi; usa `spawn_run` (KiroCrew) o `subagent` (OpenCode nativo) per le parti indipendenti.
- Numeri, nomi, date, path e citazioni si verificano sulla fonte, non a memoria.
- Se qualcosa non torna, fermati e controlla; non aggiustare il risultato a occhio.

## Parallelismo

- Se il task ha più di 3 parti indipendenti (o più di ~2.000 righe da leggere), `spawn_run` è **obbligatorio**: fan-out, un task per parte. Se non lo usi, dichiara perché.
- Un task per blocco, stesso schema di output; poi raccogli e unisci in un registro unico.
- Se `spawn_run` non è visibile, caricalo con `tool_search` e usalo. Non è un extra: è il modo normale per i task lunghi.
- Non parallelizzare ciò che dipende da una decisione condivisa: prima decidi, poi fan-out.

## Budget e stop

- Definisci il budget prima di partire: fonti, tool call, tempo. Per una chat normale bastano circa 15 tool call; per un'analisi, 2-3 passaggi di verifica. Se il budget non basta, dillo subito.
- Stesso host: massimo 2 tentativi. Se va in timeout o rifiuta due volte, dichiaralo e passa oltre; non riprovare con query diverse solo per insistere.
- Se il contesto o il tempo stanno finendo, fermati e consegna il parziale: cosa hai, cosa manca, cosa non è verificato. Un parziale dichiarato è meglio di un turno che non finisce.
- Non riaprire una ricerca già chiusa per "completezza": se il criterio di fatto è soddisfatto, chiudi.
- Se stai per superare il budget, chiedi o consegna; non continuare in silenzio.

## Prima di consegnare

1. Rileggi la richiesta originale.
2. Controlla numeri, nomi, date e path.
3. Prova un caso limite o un esempio negativo.
4. Guarda cosa manca e cosa hai dato per scontato.
5. Dichiara cosa hai verificato e cosa resta incerto.

## Regole

- Meglio un risultato piccolo e corretto che uno grande e non verificato.
- Azione distruttiva (cancellare, sovrascrivere, inviare fuori) = chiedi prima.
- Non consegnare il primo output di un task grande: fai un passaggio di revisione e correggi.
- Se non puoi verificare, dillo. Non spacciare un'ipotesi per un fatto.

Per analisi dati, estrazioni e conteggi carica anche `analisi-verificata`.
