# nomad-server

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="nserver/web/splash-dark.svg">
  <img src="nserver/web/splash-light.svg" alt="nomad-server" width="420">
</picture>

Tanti nodi [NomadNet](https://github.com/markqvist/NomadNet) in un solo processo, con una web UI per seguirli e gestirli.

[![License](https://img.shields.io/badge/license-Reticulum%20License-blue.svg)](LICENSE) · [English](README.md)

![Overview](docs/overview.png)

## Cosa fa

- **Tanti nodi, un processo**: ogni nodo ha la sua cartella, identità, pagine, file e contatori
- **Serve come NomadNet**: pagine `.mu`, pagine eseguibili, liste `.allowed`, download `/file/`, immagini nelle pagine
- **Web UI**: panoramica, statistiche per ora e per giorno, log in diretta, impostazioni, editor delle pagine, tema chiaro e scuro
- **Contatori**: ogni richiesta e ogni byte, segnaposto `{{visits}}` nelle pagine, probabili crawler segnalati
- **Visitatori**: chi è collegato, chi si è identificato, blocco di un peer o di un singolo link, blackhole di un'identità in Reticulum
- **Plugin**: pagine dinamiche in Python dentro il server (guestbook, moduli, dati live), anche su Windows
- **Backup**: uno zip per nodo, export e import dell'identità, cestino dei nodi rimossi
- **Nodi honeypot** (avanzato): pagine esca e trappole nascoste che catturano i crawler
- Gira su Windows, Linux, macOS, Raspberry Pi e Docker; serve solo `rns`

## Avvio rapido

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
pip install rns
python nomad_server.py
```

Apri http://127.0.0.1:8484, premi **Create new node**, metti le pagine in `nodes/<nodo>/pages/` (`index.mu` è la home) e premi **Start**. I nodi nuovi partono fermi, così niente va in rete prima di essere pronto.

Se `rnsd` è acceso, i nodi usano quello; altrimenti nomad-server avvia una sua istanza Reticulum con la config di `~/.reticulum`.

## Modi di avvio

| Come | Comando | Note |
|---|---|---|
| Terminale | `python nomad_server.py` | log a schermo, Ctrl+C lo ferma |
| Finestra app | `python nomad_server.py --app` | la UI in una finestra sua (Edge o Chrome); chiudendola si ferma il server che ha avviato |
| Windows | `start.bat`, `start_hidden.vbs` | il secondo gira senza nessuna finestra |
| Icone | Settings > Shortcuts | desktop e menu app, Windows e desktop Linux |
| Debian, Ubuntu, Raspberry Pi | `bash install.sh` | service systemd all'avvio, vedi [Installer](#installer-debian-ubuntu-raspberry-pi) |
| pipx | `pipx install git+https://github.com/fr33n0w/nomad-server` | poi `nomad-server`; dati in `~/.nomad-server` |
| Docker | `mkdir nomad-data && docker compose up -d` | vedi [Docker](#docker) |

Su una cartella gira un solo server: le stesse identità non vanno mai in rete due volte. Rilanciato con `--app`, apre solo la finestra.

### Installer (Debian, Ubuntu, Raspberry Pi)

```
sudo apt install git python3-pip
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
bash install.sh
```

Va lanciato come utente normale (non root): usa `sudo` solo per installare il service. Fa questo:

1. installa `rns` se manca: nel venv se ce n'è uno (vedi [venv](#in-un-ambiente-virtuale-venv)), altrimenti chiede prima di installarlo per il tuo utente (`pip --user`)
2. se la porta della web UI (8484) è già occupata, ne chiede un'altra
3. chiede se aprire la web UI alla rete locale (`0.0.0.0`); se sì, chiede una password (vuota = nessuna)
4. chiede il nome del primo nodo (solo se non ci sono ancora nodi)
5. chiede se installare il service systemd `nomad-server`: parte all'avvio, col tuo utente, da questa cartella, dopo `rnsd` se esiste `rnsd.service`. Se `nomad-server` è già usato da un'altra cartella, chiede un nome per questa (es. `nomad-server-2`), così più server girano affiancati.

Rilanciarlo non fa danni: nodi, impostazioni e nome del service restano, e il service viene riavviato. Poi (col nome del tuo service):

| Cosa | Comando |
|---|---|
| stato | `python3 nomad_server.py --status` (in un venv: `venv/bin/python3 nomad_server.py --status`) |
| log | `journalctl -u nomad-server -f` |
| stop, avvio, riavvio | `sudo systemctl stop nomad-server` (`start`, `restart`) |
| aggiornare | `git pull`, poi `sudo systemctl restart nomad-server` |
| togliere il service | `sudo systemctl disable --now nomad-server && sudo rm /etc/systemd/system/nomad-server.service && sudo systemctl daemon-reload` |

Sotto systemd `--stop` viene rifiutato (systemd lo farebbe ripartire): usa `systemctl`. Il riavvio dalla web UI funziona.

**rnsd con un altro utente.** Le pagine vengono servite lo stesso, ma `rnsd` rifiuta le richieste di stato degli altri utenti: niente stato di Reticulum nella web UI e niente nomi dei visitatori (lo dice il log). Dai a nomad-server la stessa chiave RPC di `rnsd`. Con l'utente di `rnsd`:

```
python3 -c "import RNS, os; print(RNS.Identity.full_hash(RNS.Identity.from_file(os.path.expanduser('~/.reticulum/storage/transport_identity')).get_private_key()).hex())"
```

poi, in `~/.reticulum/config` dell'utente di nomad-server, sotto `[reticulum]`, aggiungi `rpc_key = <quella chiave>` e riavvia nomad-server. La chiave dà il controllo di `rnsd`: tienila privata.

### In un ambiente virtuale (venv)

Un venv tiene `rns` e le sue librerie dentro la cartella di nomad-server, separati dal Python di sistema. Su Debian, Ubuntu e Raspberry Pi OS prima: `sudo apt install git python3-venv`.

**Con l'installer** (Debian, Ubuntu, Raspberry Pi), per un server che parte all'avvio:

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
python3 -m venv venv
bash install.sh
```

L'installer trova da solo `venv` (o `.venv`) nella cartella, anche se non è attivato: ci installa `rns` e il service gira col python del venv. Rilanciandolo più avanti continua a usare il venv. Funziona anche un venv tenuto altrove (es. `~/venvs/nomad`): attivalo prima di `bash install.sh`. Fuori da un venv non installa niente senza chiedere: se nessun venv è in uso, rispondi `n` e l'installer si ferma senza aver cambiato nulla. Poi è tutto come in [Installer](#installer-debian-ubuntu-raspberry-pi), col python del venv per i comandi:

| Cosa | Comando |
|---|---|
| stato | `venv/bin/python nomad_server.py --status` |
| log | `journalctl -u nomad-server -f` |
| aggiornare | `git pull`, `venv/bin/pip install -U rns`, poi `sudo systemctl restart nomad-server` |

**A mano** (Linux, macOS, Raspberry Pi):

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
python3 -m venv venv
venv/bin/pip install rns
venv/bin/python nomad_server.py
```

Windows:

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
python -m venv venv
venv\Scripts\pip install rns
venv\Scripts\python nomad_server.py
```

Poi come in [Avvio rapido](#avvio-rapido): apri http://127.0.0.1:8484, premi **Create new node**, poi **Start**. Ctrl+C ferma il server.

Usa il python del venv per ogni comando: `venv/bin/python nomad_server.py --status` (anche `--stop`, `--new-node NOME`, `--app`, ...), oppure lancia una volta `source venv/bin/activate` (Windows `venv\Scripts\activate`) nel terminale e poi `python` normale. Per aggiornare: `git pull` e `venv/bin/pip install -U rns`. Le icone create da Settings > Shortcuts usano il venv da sole.

**Come comando**: `venv/bin/pip install git+https://github.com/fr33n0w/nomad-server`, poi `venv/bin/nomad-server` (dati in `~/.nomad-server`).

**Dove gira Reticulum.** Il venv ha sempre bisogno del suo `rns`: è la libreria di cui è fatto nomad-server. Chi fa girare la rete Reticulum lo scegli tu:

- *rnsd già installato per il tuo utente, nomad-server nel venv*: non serve altro. nomad-server si collega al `rnsd` acceso e l'installer lo fa partire dopo `rnsd.service`; quando `rnsd` si riavvia, nomad-server si ricollega da solo. Fai girare nomad-server con lo stesso utente di `rnsd` (altrimenti vedi *rnsd con un altro utente* più sopra) e tieni le due copie di `rns` alla stessa versione (`pip install -U rns` su entrambe).
- *Tutto nel venv, senza rnsd*: nomad-server fa girare Reticulum da solo, con le interfacce di `~/.reticulum/config`. Fuori dal venv non viene installato niente. Mentre gira, altri programmi (NomadNet, rBrowser) possono usarlo come istanza condivisa; restano scollegati mentre nomad-server si riavvia.
- *Tutto nel venv, rnsd dal venv*: `rns` nel venv contiene anche `venv/bin/rnsd`. Fanne un service, poi lancia l'installer (trova `rnsd.service` e parte dopo). Dalla cartella di nomad-server:

```
python3 -m venv venv
venv/bin/pip install rns
sudo tee /etc/systemd/system/rnsd.service >/dev/null <<EOF
[Unit]
Description=Reticulum rnsd (venv)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
ExecStart=$PWD/venv/bin/rnsd --service
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now rnsd
bash install.sh
```

Se la porta della web UI è già occupata (per esempio da un altro nomad-server), il server si ferma con un messaggio: cambia `port` nella sezione `[webui]` di `server.cfg` e rilancialo.

## Riga di comando

`python nomad_server.py [opzioni]`, oppure `nomad-server [opzioni]` se installato con pip o pipx.

**Avvio del server**

| Opzione | |
|---|---|
| *(nessuna)* | avvia, log nel terminale |
| `--app` | apre anche la web UI in una finestra app (Edge o Chrome); chiudendola si ferma il server che ha avviato. Se il server gira già, apre solo la finestra |
| `--service` | silenzioso: nel terminale o nel journal solo avvisi ed errori, log completo in `logs/server.log` |
| `--no-webui` | senza interfaccia web; i comandi da console qui sotto funzionano lo stesso |
| `--verbose` | log di debug di Reticulum |
| `--base DIR` | cartella con `server.cfg`, `nodes/` e `data/`. Default: accanto al programma, `~/.nomad-server` se installato con pip o pipx |

**Comandi al server acceso** (sulla stessa cartella, quindi con la stessa `--base`)

| Opzione | |
|---|---|
| `--status` | nodi, interfacce, visitatori collegati adesso |
| `--json` | con `--status`: i dati grezzi |
| `--log [N]` | ultime N righe del log (60 se N manca) |
| `-f`, `--follow` | con `--log`: continua a mostrare le righe nuove (Ctrl+C per uscire) |
| `--node ID` | con `--log`: solo questo nodo |
| `--stop` | lo ferma, anche se nascosto (rifiutato sotto systemd: usa `systemctl stop nomad-server`) |
| `--block LXMF` | blocca un peer su tutti i nodi |
| `--reason TESTO` | con `--block`: il motivo che gli viene mostrato |
| `--mode MODO` | con `--block`: `page` (default, una pagina col motivo), `silent`, `tarpit` o `maze` |
| `--unblock LXMF` | toglie un peer dalla blocklist |
| `--blocklist` | stampa la blocklist (hash dell'identità, indirizzo LXMF, motivo) |
| `--export-blocklist FILE` | la salva in un file di testo |

**Lavoro sui file** (non serve il server acceso; riavvialo dopo)

| Opzione | |
|---|---|
| `--new-node NOME` | crea la cartella di un nodo, con identità nuova e un `index.mu` di esempio |
| `--set-password` | imposta la password della web UI (vuota = nessuna password) |

**Altro**: `--version`, `-h` / `--help`.

## Web UI

| | |
|---|---|
| ![Stats](docs/stats.png) | ![Logs](docs/logs.png) |
| **Stats**: richieste e traffico per nodo, per ora o giorno, pagine e file più visti, visitatori identificati | **Logs**: in diretta, un colore per nodo, filtri per pagine, visitatori, file e avvisi |

- **Overview**: i nodi con il grafico delle richieste, le interfacce Reticulum, i client collegati adesso
- **Settings**: impostazioni di server, web UI e nodi, identità, backup e ripristino, blocklist, nodi rimossi, icone
- **Edit pages**: gestore di file con editor per ogni nodo; una pagina salvata viene servita subito
- L'indirizzo mostrato nella UI si copia con un clic; sul telefono il layout si adatta

La web UI ascolta su 127.0.0.1:8484. Per raggiungerla da altri dispositivi metti `host = 0.0.0.0` in `server.cfg` (o rispondi sì in `install.sh`) e imposta una password: chi raggiunge la UI può gestire i nodi. Dalla rete, modificare le pagine e gestire le identità richiede la password.

## Nodi

```
nodes/mio-nodo/
  node.cfg      impostazioni (anche dalla web UI)
  identity      la chiave privata del nodo: tienila privata
  pages/        pagine .mu, index.mu è la home
  files/        download, serviti come /file/...
  data/         contatori
  plugin.py     facoltativo, pagine dinamiche
```

Le pagine si leggono a ogni richiesta, quindi una pagina modificata è servita subito. File nuovi o cancellati vengono rilevati ogni 5 minuti (`rescan_interval`), subito con **Rescan pages** o salvandoli nell'editor.

| Impostazione | Default | |
|---|---|---|
| `name` | | nome in NomadNet e negli annunci |
| `enabled` | yes | parte col server |
| `announce_at_start` | yes | dopo un riavvio il nodo è raggiungibile solo quando si annuncia di nuovo |
| `announce_interval` | 360 | minuti, 0 = solo all'avvio |
| `require_identify` | no | solo chi si identifica vede le pagine |
| `requests_per_minute` | 120 | per visitatore, 0 = nessun limite |
| `show_on_pages` | yes | sostituisce `{{visits}}`, `{{visits_total}}`, `{{visits_today}}` |
| `footer.visits` | yes | riga delle visite in fondo alle pagine senza un contatore loro |
| `footer.watermark` | yes | riga "served by nomad-server" |

**Backup node** salva uno zip con identità, impostazioni, pagine, file e contatori; **Restore a node from backup** lo riporta con lo stesso indirizzo. **Import identity** dà a un nodo l'indirizzo di uno esistente. Due nodi dello stesso server non possono mai avere la stessa identità.

## Contatori

Ogni richiesta viene contata, ricaricamenti e visitatori identificati compresi. Una pagina richiesta di nuovo dallo stesso visitatore entro 10 secondi è un *refresh* (i partial che si ricaricano da soli), mostrato a parte. I link che aprono tante pagine in fretta sono segnalati come probabili crawler, e contati lo stesso. Il traffico è contato a parte: contenuto inviato per pagina e file, traffico dei link in entrata e in uscita. I contatori si salvano ogni 30 secondi e quando il server si ferma.

## Pagine dinamiche

- **Pagine eseguibili**, come in NomadNet: un `.mu` con shebang (`#!/usr/bin/env python3`) e `chmod +x` viene eseguito a ogni richiesta e quello che stampa è la pagina. Campi dei moduli e visitatore arrivano come variabili d'ambiente (`field_*`, `var_*`, `link_id`, `remote_identity`). Su Windows funzionano anche le pagine con shebang Python.
- **Plugin**: un `plugin.py` nella cartella del nodo, caricato una volta dentro il server. Nessun processo nuovo per ogni richiesta, funziona anche su Windows e riceve indirizzo LXMF e nome del visitatore.

```python
PATHS = ["/page/guestbook.mu"]

def start(node):      # facoltativo: all'avvio del nodo (thread, generatori)
    pass

def page(path, data, who=None, who_name="", link_id=None):
    # data: campi del modulo (field_*, var_*); who: indirizzo LXMF se il visitatore si è identificato
    return ">Guestbook\n\nCiao " + (who_name or "sconosciuto") + "\n"

def stop():           # facoltativo: allo stop del nodo
    pass
```

I plugin di tutti i nodi condividono un processo Python: dai nomi diversi ai loro moduli di supporto.

## Portare qui un nodo esistente

Un nodo fatto con NomadNet o con `rns_server_page.py` si sposta senza cambiare indirizzo:

1. **Create new node**, poi **Import identity** con il file `identity` del vecchio nodo.
2. Copia le sue `pages/` e `files/`, oppure sostituisci le due cartelle con collegamenti a quelle vecchie se un programma continua a scriverle.
3. Copia il suo `data/visits.json`, se c'è: i vecchi conteggi si sommano ai contatori nuovi, una volta sola.
4. Il codice che faceva pagine dentro il vecchio server diventa un `plugin.py`; i programmi che riscrivono pagine in background possono essere avviati e fermati dal plugin insieme al nodo.
5. Ferma il vecchio server, poi premi **Start**. Mai la stessa identità accesa in due posti.

## Visitatori e blocchi

I peer che si identificano compaiono con l'indirizzo LXMF e, se ne hanno annunciato uno, il nome. **Block** tiene fuori un peer da un nodo o da tutti, con un motivo, in quattro modi: una pagina col motivo, *silent* (scartato), *tarpit* (resta collegato, nessuna risposta: tempo perso per un crawler, nessun byte sulla mesh) o *maze* (solo honeypot). Un link che non si è identificato si può bloccare finché resta collegato. **Export as text** scrive la blocklist con gli hash delle identità.

**Blackhole** aggiunge l'identità di un peer al blackhole di Reticulum, solo a mano: l'istanza Reticulum locale scarta i suoi annunci e le path verso le sue destinazioni, per tutti i programmi che la usano. Per condividerlo, i nodi transport possono pubblicare il loro blackhole (`publish_blackhole`) e gli altri seguirlo (`blackhole_sources`).

## Nodi honeypot

![Honeypot](docs/honeypot.png)

Nascosti dietro la modalità avanzata: clicca cinque volte il logo in **About**. Un honeypot è un nodo pieno di pagine esca credibili, con link trappola che le persone non vedono e i crawler seguono. Chi apre una trappola, apre un link nuovo per ogni pagina o legge troppe pagine troppo in fretta viene preso: per indirizzo LXMF se si è identificato, altrimenti per il resto del suo link. Ai peer presi si può dare un tarpit o un labirinto che non finisce mai e non rifiuta mai una richiesta (pagine finte, file finti da scaricare o entrambi), così il crawler gira finché non si stacca, e lo ricevono anche quando tornano. Finiscono nella blocklist solo se attivi *Put caught visitors in the blocklist*. La scheda Honeypot tiene lo storico delle connessioni, i crawler conosciuti e se tornano, anche su honeypot nuovi. Spegnere la modalità avanzata ferma tutti gli honeypot, che restano fermi finché non li riavvii, e li nasconde da tutte le schede; la blocklist resta com'è.

## Docker

```
mkdir nomad-data
NOMAD_PASSWORD=latuapassword docker compose up -d
```

La UI è sulla porta 8484, tutto il resto in `nomad-data/`: impostazioni, nodi, identità, contatori, log e config di Reticulum (`nomad-data/reticulum/config`, creata al primo avvio: aggiungi un'interfaccia, nelle reti Docker l'AutoInterface non trova nessuno). Il compose gira come utente 1000:1000 così i file restano tuoi, e nei commenti spiega come usare il `rnsd` dell'host. Variabili d'ambiente: `NOMAD_PASSWORD` (password della web UI), `NOMAD_PORT` (porta della UI dentro il container, default 8484), `NOMAD_RNS_CONFIG` (cartella della config Reticulum, default `/data/reticulum`). Comandi da console: `docker compose exec nomad-server python /app/nomad_server.py --base /data --status`. **Stop server** nella UI viene riavviato da Docker; usa `docker compose stop`.

## Risorse

Su un server Debian con tre nodi frequentati e un plugin, il processo usa circa 95 MB: circa 36 MB sono Python e Reticulum, il resto nodi, contatori e i dati del plugin. Un server vuoto usa circa 47 MB.

## Novità

- Risposta maze: pagine, file o entrambi.
- Il maze non rifiuta mai, niente limiti.
- Pagine trappola indicate come trappole, non bloccate.
- Lista peer: prima i nuovi o i vecchi, righe ferme.
- Fino a 60 pagine elencate per connessione.

## Ringraziamenti

- [Reticulum](https://github.com/markqvist/Reticulum) e [NomadNet](https://github.com/markqvist/NomadNet) di Mark Qvist: nomad-server è costruito su Reticulum e serve le pagine come fa NomadNet. Usa la Reticulum License, come Reticulum.
- [rns_server_page](https://github.com/SebastianObi/RNS-Tools/tree/main/rns_server_page) di Sebastian Obele: il server di pagine da cui è nato questo progetto: è partito aggiungendogli contatori, statistiche e web UI, poi è diventato un server a sé. Legge ancora le sue cartelle dei nodi e ne importa il `visits.json`. Il suo avviso MIT è in [LICENSE](LICENSE).

## Licenza

[Reticulum License](LICENSE), come Reticulum.
