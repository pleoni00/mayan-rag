# Mayan + MinIO + OpenSearch: PoC con chiamate HTTP native

**Mayan usa l’immagine originale.** MinIO è il suo storage S3. I workflow nativi inviano chiamate HTTP a un componente Python esterno, che aggiorna OpenSearch. Nessuna app Django aggiunta, patch ai worker, lettura diretta del database Mayan o migrazione custom.

```bash
cd mayan
bash scripts/start.sh
bash scripts/test.sh
```

Servono Docker Compose, Python 3 e Internet per il primo download. Credenziali e `WEBHOOK_TOKEN` sono in `.env`; modello e stato delle lavorazioni in `.private/`.

## Flusso

1. `start.sh` avvia i servizi e configura via API REST il workflow nativo `Events PoC HTTP`, associato al tipo documentale `Events PoC`, con avvio automatico.
2. L’ingresso iniziale nel workflow invia la notifica di creazione. Gli eventi su file, versioni, completamento parsing/OCR e ACL attivano transizioni che inviano altre notifiche.
3. L’azione nativa `HTTP Request` esegue una `POST` a `http://events:8080/events`, sulla rete Docker interna, autenticata con `WEBHOOK_TOKEN`.
4. Il ricevitore salva la richiesta e risponde `202`. Un thread separato recupera contenuto, versione attiva e ACL dalle API Mayan e aggiorna OpenSearch.

Il componente **non interroga periodicamente il registro degli eventi**. Ritenta soltanto lavorazioni già ricevute, quando il testo del documento non è ancora pronto. Le modifiche ACL aggiornano i metadati senza generare nuovi embedding.

Ogni notifica contiene `document_id`, `workflow_instance_id`, `delivery_id` e `verb`. `delivery_id` identifica la voce del log del workflow Mayan; `verb` identifica l’evento configurato sulla transizione. Lo stato delle richieste ricevute e delle lavorazioni è in `.private/events/events.json`; gli identificativi della configurazione nativa sono in `.private/events/workflow.json`.

## I cinque test

1. Caricamento e rilettura di tre documenti TXT attraverso Mayan, configurato con MinIO.
2. ACL native con due utenti: A vede 1 e 2, B vede 2 e 3.
3. Le chiamate HTTP dei workflow attivano estrazione del testo via API, chunking, embedding locale e scrittura OpenSearch. Il test controlla anche la risposta `202` registrata da Mayan.
4. Una modifica ACL aggiorna i metadati dei chunk; ID, testo e vettori restano invariati e il contatore degli embedding non aumenta.
5. Nuovo file/versione e rollback tramite API native: cambia il contenuto indicizzato, rimangono disponibili i file precedenti.

Il test non chiama direttamente la pipeline. I risultati sono in `evidence/test.log` e `evidence/test.json`, con le notifiche elaborate. Ogni esecuzione crea tre nuovi documenti e due utenti di prova, che restano in Mayan. Le letture GET tollerano reset di connessione temporanei; upload e altre scritture non vengono ripetuti automaticamente.

**Stato del collaudo: 5/5 test superati con le chiamate HTTP native**, compresi ricezione delle notifiche, risposta `202` registrata da Mayan, modifica ACL senza nuovi embedding e rollback della versione.

## Limiti del PoC

Le verifiche riguardano ACL dirette sul documento, non ereditarietà o modifiche ai gruppi. Non è prevista un’importazione retroattiva dell’archivio. Se si azzerano i volumi Docker, va azzerato anche lo stato locale in `.private/events` prima di riavviare il PoC.

L’azione HTTP nativa non garantisce la riconsegna automatica se il ricevitore è irraggiungibile. Il workflow conserva l’ultima risposta in `last_callback`; gli errori di esecuzione sono consultabili nei log delle azioni Mayan. Il componente esterno conserva le richieste accettate per riprenderne l’elaborazione dopo un riavvio.

## Codice essenziale

- `external/configure.py`: configura i workflow nativi tramite REST.
- `external/worker.py`: riceve le POST ed esegue la pipeline esterna.
- `external/client.py`: client delle API Mayan e OpenSearch.
- `external/test.py`: cinque verifiche sui servizi reali.
- `Dockerfile`: costruisce esclusivamente il componente esterno.
- `compose.yaml`: Mayan originale, MinIO, OpenSearch, componente esterno, PostgreSQL e Redis necessari a Mayan.

Mayan: <http://localhost:8010/>. MinIO, OpenSearch e ricevitore HTTP restano sulla rete Docker interna. Il progetto Compose è `mayan-poc`; i volumi dei precedenti tentativi non vengono riutilizzati né cancellati.
