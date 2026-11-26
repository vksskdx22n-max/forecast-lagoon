#!/usr/bin/env python3
"""fd_verifica.py v1.26 — verifica automatica delle previsioni di fd_previsione (Fishing Diary).

Confronta il vento previsto per zona con le misure CPSM e accumula gli errori in verifica_archivio.json.
fd_previsione (>= 1.9) usa l'archivio per la correzione di ICON-D2 per zona, fascia di vento previsto
e settore di direzione.

Zone verificate (stazioni CPSM, pagine "temporeale" con le ultime 24 ore):
  Malamocco · Valgrande -> Malamocco Porto (interna, ~3 km dalla bocca lungo il Canale dei Petroli)
  San Leonardo · Melison, Fisolo, Bocca · Alberoni -> non verificate: usano la correzione di Malamocco · Valgrande
  (dal 01/11/2026 riferimenti validi solo Malamocco Porto e Piattaforma: le dighe di Lido e Chioggia
   differiscono fino a 8 nodi tra loro e la loro media non rappresenta Alberoni)
  Laguna Nord (v1.23) -> Laguna Nord (Saline, interna)          previsto = media dei 4 punti di calcolo dell'area nord
  Laguna Sud  (v1.23) -> Chioggia Porto (interna)               previsto = media dei 4 punti di calcolo dell'area sud
    (stessa media del semaforo della previsione). Verifica di CONTROLLO: fd_previsione non applica correzioni a nord e sud,
    le ore servono a decidere le correzioni future. Stazioni diverse da Diga Sud Lido e Diga Sud Chioggia (sulle dighe, escluse).
    Sensore fermo: a Laguna Nord e Chioggia Porto le letture da 5 min con vento medio 0,0 e raffica 0,0 vengono scartate
    (stesso criterio dell'analisi storica 19/08-18/11); l'ora vale solo con almeno 10 letture valide su 12.
  Area della previsione (v1.23): campo "area" del JSON (centro, nord, sud); senza campo = centro.
Onda in mare aperto -> Piattaforma CNR (altezza significativa), confrontata con il modello Open-Meteo Marine
  (cella diversa: confronto indicativo).

Viene chiamato da solo:
  - all'inizio di fd_previsione (comando 0)
  - in fondo al punto 1 della pipeline (rientro dall'uscita)
A mano:                python3 fd_verifica.py
Verifica PROVVISORIA:  python3 fd_verifica.py --provvisoria   (solo ore gia' complete, NON scrive l'archivio)

Vincolo: le pagine CPSM tengono solo le ultime 24 ore. Una previsione si verifica se la sua finestra
(piu' 30 min) e' ancora nella pagina.
Colonne lette dall'intestazione (l'ordine cambia da stazione a stazione); numeri con virgola o punto.
Velocita' in m/s (unita' degli endpoint CPSM; le pagine non la indicano), convertite in nodi.
Orario delle pagine in ora solare (UTC+1): verificato solo d'inverno. DA CONTROLLARE D'ESTATE (CPSM_ORA_SOLARE).
Valore orario = media delle letture da -30 a +30 min (almeno 10 su 12), raffica = massimo.

Pioggia (v1.15): mm in 5 min (pluviometro a vaschetta, scatti da 0,2 mm) a Palazzo Cavalli e Piattaforma CNR;
somma dell'ora PRECEDENTE (come Open-Meteo), almeno 10 letture su 12; "pioggia" = almeno 0,2 mm.

Pioggia osservata in barca (v1.16, riferimento per le zone di pesca): riga nelle note dell'uscita
    PIOGGIA 9:30-10:00 leggera, 10:30-11 debole      oppure      PIOGGIA no
  (orari H, H:MM o H.MM; un orario singolo vale per l'ora che lo contiene), oppure osservazioni_pioggia.json
  nella cartella dello script: {"2026-11-11": "9:30-10:00 leggera, Fisolo"} (stessa sintassi, vince sulle note).
Rifare la verifica di un giorno (entro 24 h dalla fine della finestra):  python3 fd_verifica.py --rifai 2026-11-11

Ore di ripiego (v1.17, previsioni da fd_previsione 1.26): ore senza ICON-D2, stima = ICON-2i senza correzione.
  Non entrano nell'archivio del vento (la correzione e' di ICON-D2); se tutte le ore sono di ripiego la previsione
  viene segnata "non verificabile" e non si riprova.

Semaforo misurato (v1.24): ora per ora dalla media oraria arrotondata all'intero (verde fino a 6, giallo 7-12, rosso da 13,
  come fd_previsione 1.35; v1.19-1.23: soglie 6 e 12 sul valore non arrotondato; le raffiche
  non cambiano il colore). L'archivio si ricalcola con questa regola anche per le ore gia' verificate.
  Verdetto = colore piu' frequente nelle ore, a parita' quello che viene prima (stessa regola di fd_previsione 1.27+).
  La raffica di picco su 5 minuti resta come informazione. Confronto ora per ora con il colore previsto della zona.

Marea della zona (v1.25): curva della zona (marea_propaga, ritardo e quota da Punta Salute; dal JSON della previsione
  con fd_previsione >= 1.43, altrimenti ricalcolata) confrontata con il livello misurato ogni 5 minuti nella finestra
  (Laguna Nord Saline, Malamocco Porto, Chioggia Porto): errore medio e medio assoluto del livello e errore sull'ora
  dell'alta/bassa (previsto - misurato), anche per la curva di Punta Salute non corretta. Archivio: campo "marea".
  v1.34: la previsione CPSM e' confrontata anche con il livello misurato a Punta Salute, nella stessa finestra (campi
  "cpsm_*"; solo se la curva e' la previsione CPSM): separa l'errore del CPSM da quello del trasporto nella zona.
  Una riga per giorno, area e inizio della finestra ("da").

AROME HD in parallelo (v1.26, previsioni da fd_previsione >= 1.44): vento ("ar") e raffica ("raff_ar") di AROME HD salvati
  nelle ore verificate; nel riepilogo errore di ICON-D2, AROME HD e della media AROME HD + ICON-D2 (grezzi, senza correzione),
  sulle stesse ore. Non entra nella correzione.

Uscita: verifica_archivio.json e Previsioni/verifica_<gg-mm-aaaa>_<HHMM-HHMM>[_nord|_sud]_v1.24.html / .pdf
"""
import sys, os, re, json, math, glob, gzip, statistics, subprocess, tempfile, shutil, datetime as dt

VERSIONE = "1.34"   # v1.34: errore della previsione CPSM a Punta Salute sulle stesse finestre della marea di zona; v1.33: nei grafici la stima sta sopra ICON-D2; dove coincidono si vedono entrambi (anello arancione); v1.32: fasce 05-12, 12-18, 18-24 (l'ora di confine entra in archivio una volta sola); gli aggiornamenti a fascia iniziata (_agg) non si verificano; v1.31: verifica e correzione per fascia della giornata (mattino, pomeriggio, sera)
# v1.31: fasce della giornata (scelta di Ennio 23/11/2026). Ogni fascia impara la sua correzione dalle sue ore verificate.
# Finche' le ore non bastano vale la correzione fissa dell'analisi F0 del 23/11/2026 (ICON-D2 del giorno prima, media dei
# 4 punti di calcolo, contro le misure CPSM orarie, 23/08-22/11/2026, prova meta' giorni / altra meta'):
# pomeriggio 13-18 errore medio +0,3 Centro, +0,3 Nord, +0,9 Sud: la correzione non migliora (al Sud peggiora il semaforo) -> 0;
# sera 19-24 +1,2 Centro, +1,2 Nord, +0,9 Sud: errore assoluto 2,30->2,05 / 1,87->1,76 / 2,06->1,92 nodi.
PERIODI = [("mattino", 5, 12), ("pomeriggio", 13, 18), ("sera", 19, 23)]
CORR_PERIODO = {"pomeriggio": ({"centro": 0.0, "nord": 0.0, "sud": 0.0},
                               "analisi F0 23/11/2026: ICON-D2 del giorno prima nel pomeriggio quasi senza errore (+0,3 / +0,3 / +0,9 nodi), nessuna correzione"),
                "sera": ({"centro": 1.2, "nord": 1.2, "sud": 0.9},
                         "analisi F0 23/11/2026: ICON-D2 del giorno prima, ore 19–24, 23/08–22/11/2026, misure CPSM")}
MIN_ORE_CORR_PERIODO = 40        # ore verificate di una fascia di vento prima di usare la correzione misurata (pomeriggio e sera)
FINE_GIORNO = dt.time(23, 59)


def periodo_ora(h):
    """Periodo del giorno di un'ora ("HH:MM" o intero): mattino 05-12, pomeriggio 13-18, sera 19-24 (notte = sera)."""
    h = int(str(h)[:2]) if not isinstance(h, int) else h
    return next((n for n, a, b in PERIODI if a <= h <= b), "sera" if h > 12 else "mattino")


def ora_da_txt(x):
    """v1.31: "24:00" (fine della fascia della sera) -> 23:59."""
    return FINE_GIORNO if x.strip() in ("24:00", "24") else dt.time.fromisoformat(x.strip())


def area_zona(zona):
    return {"Laguna Nord": "nord", "Laguna Sud": "sud"}.get(zona, "centro")
URL_BASE = "https://www.comune.venezia.it/sites/default/files/publicCPSM2/stazioni/temporeale/"
URL_PROXY = "https://fd-cpsm-proxy.ennioweb.workers.dev/misure/"      # v1.22: il Comune blocca i server di GitHub; il proxy Cloudflare no
ORIGINE_PROXY = "https://vksskdx22n-max.github.io"
PAGINE = {"Malamocco Porto": "Malamocco_Porto", "Palazzo Cavalli": "Palazzo_Cavalli",
          "Piattaforma CNR": "Piattaforma", "Laguna Nord": "Laguna_Nord", "Chioggia Porto": "Chioggia_Porto",   # v1.23: + Laguna Nord, Chioggia Porto
          "Punta Salute": "Punta_Salute"}                                       # v1.34: solo livello (previsione CPSM)
ZONE = {  # zona della previsione -> (stazioni mediate, descrizione)
    "Malamocco · Valgrande": (["Malamocco Porto"], "Malamocco Porto (interna, ~3 km dalla bocca, ~1 km dal Canale Valgrande)"),
    "Laguna Nord": (["Laguna Nord"], "Laguna Nord (Saline, interna, 45,4956 N 12,4720 E)"),          # v1.23
    "Laguna Sud": (["Chioggia Porto"], "Chioggia Porto (interna, 45,2325 N 12,2806 E)"),             # v1.23
}
# v1.23: zone che non sono nel JSON della previsione ma si ricavano dalla media dei punti di calcolo dell'area
ZONE_MEDIA = {"Laguna Nord": "nord", "Laguna Sud": "sud"}
# v1.23: stazioni con il sensore del vento che a volte resta fermo a zero: lettura scartata se vento medio e raffica sono 0,0
STAZ_FILTRO_ZERO = {"Laguna Nord", "Chioggia Porto"}
# zone senza stazione di verifica valida: usano la correzione di Malamocco · Valgrande
CORR_DA = {"San Leonardo · Melison": "Malamocco · Valgrande", "Fisolo": "Malamocco · Valgrande", "Bocca · Alberoni": "Malamocco · Valgrande"}
# nomi delle zone nelle previsioni e nell'archivio fino a fd_previsione 1.11 / fd_verifica 1.5
ALIAS = {"Malamocco": "Malamocco · Valgrande", "Bocca Malamocco": "Bocca · Alberoni"}
STAZ_ONDA = "Piattaforma CNR"
STAZ_LIVELLO = "Malamocco Porto"
ARCHIVIO = "verifica_archivio.json"
CARTELLA = "Previsioni"
KN = 1.94384
CPSM_ORA_SOLARE = True
MIN_LETTURE = 10
MIN_ORE_CORR = 10
RACCORDO = 2.0                 # nodi: ampiezza (per lato) del passaggio graduale tra le fasce di correzione
FASCE = [(None, 6.0, "sotto 6"), (6.0, 12.0, "6–12"), (12.0, None, "da 12")]   # fascia del vento PREVISTO da ICON-D2
# v1.13: settori da "Il vento nell'Adriatico settentrionale 1983-2004" (Centro Maree, Comune di Venezia): bora = 22,5-67,5°.
# Le correzioni per settore usano 4 gruppi (con 8 settori da 45° le ore verificate non bastano).
SETTORI = [(22.5, 67.5, "Bora"), (67.5, 202.5, "Levante–Scirocco"), (202.5, 292.5, "Libeccio–Ponente"), (292.5, 22.5, "Tramontana–Maestrale")]
# nomi dei venti: 8 settori da 45° centrati sui punti cardinali (stessa fonte; NE = Grecale-Bora, qui "Bora")
NOMI_VENTO = ["Tramontana", "Bora", "Levante", "Scirocco", "Ostro", "Libeccio", "Ponente", "Maestrale"]
CORR_DEFAULT, CORR_SOGLIA_DEFAULT = 2.2, 4.0
# v1.27: Laguna Nord e Laguna Sud corrette come il Centro (scelta di Ennio del 22/11/2026). Finche' una fascia non ha
# MIN_ORE_CORR_ZONA ore verificate vale la correzione fissa dell'analisi dei 3 mesi (ICON-D2 del giorno prima, media dei
# 4 punti di calcolo, contro le misure CPSM 22/08-21/11/2026, ore 05-12; prova meta' giorni / altra meta': semaforo giusto
# Nord 69% -> 80%, Sud 67% -> 72-75%).
CORR_ZONA = {"Laguna Nord": (1.5, "analisi 3 mesi: ICON-D2 del giorno prima contro Laguna Nord Saline, 92 giorni, 733 ore (22/08–21/11/2026)"),
             "Laguna Sud": (1.4, "analisi 3 mesi: ICON-D2 del giorno prima contro Chioggia Porto, 58 giorni, 453 ore (22/08–21/11/2026)")}
MIN_ORE_CORR_ZONA = {"Laguna Nord": 40, "Laguna Sud": 40}
CORR_DEFAULT_FONTE = "fd_vento_confronto v1.1, 83 misure CPSM Malamocco Porto (31/05–29/10/2026), vento reale 6–12 nodi"
CROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
COLONNE = [("liv", "Liv.Idrom"), ("liv", "L.Idrometrico"), ("dir", "D.Vento"), ("vel", "V.Vento med"), ("max", "V.Vento max"),
           ("onda", "Alt. sign"), ("onda_max", "Alt. max"), ("aria", "Temperatura"), ("pioggia", "Pioggia")]   # "Temp. Acqua" non coincide
STAZ_ARIA_CITTA = "Palazzo Cavalli"
ERR_ARIA = [("mare aperto vs Piattaforma", "mare_prev", "piatt_mis"), ("Mestre vs Palazzo Cavalli", "mestre_prev", "cavalli_mis"),
            ("sull'acqua vs Palazzo Cavalli", "acqua_prev", "cavalli_mis")]


def num(x, d=1):
    return "–" if x is None else f"{x:.{d}f}".replace(".", ",")


def sg(x):
    if x is None:
        return "–"
    x = round(x, 1) + 0.0                      # v1.15: niente "-0,0"
    return ("+" if x > 0 else "") + num(x)


GRADI_CARD = {c: i * 22.5 for i, c in enumerate(['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'])}


def card(g):
    return ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'][int((g % 360) / 22.5 + .5) % 16]


def tz_italia(giorno):
    def ultima_dom(m):
        d = dt.date(giorno.year, m, 31)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    return dt.timezone(dt.timedelta(hours=2 if ultima_dom(3) <= giorno < ultima_dom(10) else 1))


def fascia(d2):
    if d2 is None:                             # v1.17: ora di ripiego senza ICON-D2
        return None
    for lo, hi, nome in FASCE:
        if (lo is None or d2 >= lo) and (hi is None or d2 < hi):
            return nome


VERDE_FINO_A, ROSSO_DA = 6, 13             # v1.24: stessa regola di fd_previsione 1.35 (vento medio arrotondato all'intero)
ORD_SEM = {"verde": 0, "giallo": 1, "rosso": 2}


def semaforo(kn, raff):
    """v1.18: colore di un'ora (stessa regola di fd_previsione.semaforo)."""
    if kn is None:
        return None
    n = int(math.floor(round(kn, 1) + 0.5))   # v1.24: intero come in pagina (6,5 -> 7); le raffiche non contano
    if n >= ROSSO_DA:
        return "rosso"
    return "giallo" if n > VERDE_FINO_A else "verde"


def verdetto(colori):
    """v1.19: colore piu' frequente, a parita' quello che viene prima nelle ore (stessa regola di fd_previsione 1.27+)."""
    c = [x for x in colori if x]
    if not c:
        return None
    n = {x: c.count(x) for x in set(c)}
    return max(n, key=lambda x: (n[x], -c.index(x)))


def sem_ore(o):
    """(previsto, misurato) di un'ora in archivio."""
    return semaforo(o["prev"].get("stima"), o["prev"].get("raff")), semaforo(o["mis"].get("media"), o["mis"].get("raff"))


def gradi(g):
    """v1.13: gradi interi 0-359 (arrotondamento classico): settore, nome e testo usano lo stesso valore."""
    return int((g or 0) % 360 + 0.5) % 360


def settore(g):
    g = gradi(g)
    for lo, hi, nome in SETTORI:
        if (lo <= g < hi) if lo < hi else (g >= lo or g < hi):
            return nome


def nome_vento(g):
    return NOMI_VENTO[int((gradi(g) + 22.5) // 45) % 8]


def dir_txt(g):
    """v1.13: '38° NE · Bora' (gradi di provenienza, rosa a 16 punti, nome del vento)."""
    return "–" if g is None else f"{gradi(g)}° {card(gradi(g))} · {nome_vento(g)}"


def settore_ora(x):
    """v1.13: settore di un'ora in archivio dalla direzione MISURATA (prima: direzione prevista da ICON-D2)."""
    g = (x.get("mis") or {}).get("dir_g")
    return settore(g if g is not None else x["prev"].get("dir_g", 0))


def media_dir(ds):
    return math.degrees(math.atan2(sum(math.sin(math.radians(d)) for d in ds), sum(math.cos(math.radians(d)) for d in ds))) % 360


# ------------------------------------------------------------------ misure CPSM
def _f(x):
    try:
        return float(re.sub(r"<[^>]*>", "", x).strip().replace(",", "."))
    except ValueError:
        return None


def leggi_pagina(txt):
    """Righe {t, liv, dir, vel, max, onda, onda_max} da una pagina temporeale CPSM (colonne dall'intestazione)."""
    th = re.findall(r"<th[^>]*>(.*?)</th>", txt, re.S)
    pos = {}
    for k, chiave in COLONNE:
        if k in pos:                          # v1.25: prima colonna trovata (il livello ha due nomi)
            continue
        for i, h in enumerate(th):
            if chiave.lower() in re.sub(r"<[^>]*>", " ", h).lower():
                pos[k] = i
                break
    out = []
    for riga in re.findall(r"<tr>(.*?)</tr>", txt, re.S):
        td = re.findall(r"<td[^>]*>(.*?)</td>", riga, re.S)
        if not td or not re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", td[0].strip()):
            continue
        t = dt.datetime.fromisoformat(td[0].strip())
        tz = dt.timezone(dt.timedelta(hours=1)) if CPSM_ORA_SOLARE else tz_italia(t.date())
        r = {"t": t.replace(tzinfo=tz)}
        for k, i in pos.items():
            r[k] = _f(td[i]) if i < len(td) else None
        out.append(r)
    return sorted(out, key=lambda r: r["t"])


def scarica_misure(html_dir=None):
    """{stazione: righe}. html_dir: cartella con le pagine salvate (per le prove)."""
    M = {}
    for nome, pagina in PAGINE.items():
        if html_dir:
            p = os.path.join(html_dir, pagina + ".html")
            txt = open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""
        else:
            # alcune pagine CPSM arrivano compresse (gzip): --compressed e, per sicurezza, decompressione a mano
            txt = ""
            for url, extra in ((URL_PROXY + pagina, ["-H", "Origin: " + ORIGINE_PROXY]), (URL_BASE + pagina + ".html", [])):   # v1.22: prima il proxy, poi il sito diretto
                r = subprocess.run(["curl", "-s", "--compressed", "-m", "30", *extra, url], capture_output=True)
                b = r.stdout if r.returncode == 0 else b""
                if b[:2] == b"\x1f\x8b":
                    try:
                        b = gzip.decompress(b)
                    except OSError:
                        b = b""
                txt = b.decode("utf-8", errors="replace")
                if "<th" in txt:                  # pagina vera (non la pagina di controllo anti-bot)
                    break
        M[nome] = leggi_pagina(txt)
        if nome in STAZ_FILTRO_ZERO:            # v1.23: sensore fermo -> lettura senza vento (le altre colonne restano)
            for r in M[nome]:
                if r.get("vel") == 0 and r.get("max") == 0:
                    r["vel"] = r["max"] = r["dir"] = None
                    r["zero"] = True
    return M


def misura_ora(righe, centro, campo="vel"):
    w = [r for r in righe if centro - dt.timedelta(minutes=30) <= r["t"] < centro + dt.timedelta(minutes=30) and r.get(campo) is not None]
    if len(w) < MIN_LETTURE:
        return None
    if campo != "vel":
        return {"media": round(statistics.mean(r[campo] for r in w), 2), "n": len(w)}
    raff = [r["max"] for r in w if r.get("max") is not None]
    dirs = [r["dir"] for r in w if r.get("dir") is not None]
    return {"media": round(statistics.mean(r["vel"] for r in w) * KN, 1), "raff": round(max(raff) * KN, 1) if raff else None,
            "dir_g": round(media_dir(dirs)) if dirs else None, "n": len(w)}


def misura_pioggia(righe, centro):
    """v1.15: mm caduti nell'ora precedente a centro (letture da 5 min con t in (centro-60 min, centro]); None se < MIN_LETTURE."""
    w = [r["pioggia"] for r in righe if centro - dt.timedelta(minutes=60) < r["t"] <= centro and r.get("pioggia") is not None]
    return round(sum(w), 1) if len(w) >= MIN_LETTURE else None


SOGLIA_PIOGGIA = 0.2           # mm/h: un solo scatto del pluviometro
OSSERVAZIONI = "osservazioni_pioggia.json"
MASTER = "diario_pesca_MASTER.json"
TEST_USCITA = 1779811089369


def _min(h, m):
    return int(h) * 60 + int(m or 0)


def leggi_fasce_pioggia(testo):
    """v1.16: 'PIOGGIA no' -> []; 'PIOGGIA 9:30-10:00 leggera, 10.30-11' -> [(570, 600), (630, 660)]; nessuna riga -> None."""
    m = re.search(r"PIOGGIA\b\s*:?\s*([^\n]*)", testo or "")
    if not m:
        return None
    seg = m.group(1).strip()
    if re.match(r"(?i)no\b", seg):
        return []
    T = r"(\d{1,2})(?:[:.](\d{2}))?"
    fasce = [(_min(a, b), _min(c, d)) for a, b, c, d in re.findall(T + r"\s*[-–]\s*" + T, seg)]
    if not fasce:
        fasce = [(_min(a, b),) * 2 for a, b in re.findall(T, seg)[:6]]
    return fasce


def osservazioni_pioggia(cartella="."):
    """{data_iso: (fasce, testo)} dalle note delle uscite nel master e da osservazioni_pioggia.json (vince il file)."""
    O = {}
    p = os.path.join(cartella, MASTER)
    if os.path.exists(p):
        try:
            for u in json.load(open(p, encoding="utf-8")).get("uscite") or []:
                f = leggi_fasce_pioggia(u.get("note"))
                if f is not None and u.get("id") != TEST_USCITA and u.get("data"):
                    O[str(u["data"])[:10]] = (f, "note dell'uscita")
        except (OSError, ValueError):
            pass
    p = os.path.join(cartella, OSSERVAZIONI)
    if os.path.exists(p):
        try:
            for g, t in json.load(open(p, encoding="utf-8")).items():
                f = leggi_fasce_pioggia("PIOGGIA " + str(t))
                if f is not None:
                    O[g] = (f, OSSERVAZIONI)
        except (OSError, ValueError):
            pass
    return O


def pioggia_osservata(O, data, ora):
    """True/False se c'e' un'osservazione per quel giorno (ora = fine dell'ora: 10:00 = dalle 09 alle 10), altrimenti None."""
    if data not in O:
        return None
    h = int(ora[:2]) * 60
    return any(a <= h and b > h - 60 for a, b in O[data][0])


def applica_osservazioni(A, cartella="."):
    O = osservazioni_pioggia(cartella)
    for x in A.get("pioggia", []):
        x["oss"] = pioggia_osservata(O, x["data"], x["ora"])
    return O
FASCE_PROB = [(0, 20, "sotto 20%"), (20, 50, "20–50%"), (50, 101, "da 50%")]


def misura_zona(M, zona, centro):
    """Media tra le stazioni della zona (vento medio e direzione mediati, raffica = massimo)."""
    ms = [misura_ora(M.get(s, []), centro) for s in ZONE[zona][0]]
    if not ms or any(m is None for m in ms):
        return None
    raff = [m["raff"] for m in ms if m["raff"] is not None]
    dirs = [m["dir_g"] for m in ms if m["dir_g"] is not None]
    return {"media": round(statistics.mean(m["media"] for m in ms), 1), "raff": max(raff) if raff else None,
            "dir_g": round(media_dir(dirs)) if dirs else None, "n": min(m["n"] for m in ms),
            "stazioni": {s: m["media"] for s, m in zip(ZONE[zona][0], ms)}}


# ------------------------------------------------------------------ archivio
def carica_archivio(cartella="."):
    p = os.path.join(cartella, ARCHIVIO)
    A = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"ore": [], "previsioni": []}
    A.setdefault("onda", [])
    A.setdefault("aria", [])
    A.setdefault("pioggia", [])
    A.setdefault("marea", [])                  # v1.25
    for o in A["ore"]:                         # fino a fd_previsione 1.19 "raff" era la raffica di ICON-D2 senza correzione
        o["prev"].setdefault("raff_d2", o["prev"].get("raff"))
    for o in A["ore"]:                         # archivio v1.0/v1.1: solo zona Malamocco; nomi vecchi -> nuovi
        o["zona"] = ALIAS.get(o.get("zona", "Malamocco"), o.get("zona", "Malamocco"))
    return A


def salva_archivio(A, cartella="."):
    A["versione"] = VERSIONE
    json.dump(A, open(os.path.join(cartella, ARCHIVIO), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _corr_fascia(fa, se, d2, A, zona, periodo="mattino"):
    """Correzione di UNA fascia di ICON-D2: misurata (settore, poi tutta la fascia) o fissa.
    v1.31: solo con le ore verificate dello stesso periodo del giorno (mattino, pomeriggio, sera)."""
    ore = [o for o in A["ore"] if o["zona"] == zona and o["fascia_d2"] == fa and periodo_ora(o["ora"]) == periodo]
    ore_s = [o for o in ore if settore_ora(o) == se]
    mn = MIN_ORE_CORR_PERIODO if periodo != "mattino" else MIN_ORE_CORR_ZONA.get(zona, MIN_ORE_CORR)   # v1.27, v1.31
    rp = "" if periodo == "mattino" else f" ({periodo})"
    if len(ore_s) >= mn:
        c = statistics.mean(o["mis"]["media"] - o["prev"]["d2"] for o in ore_s)
        return round(c, 1), f"misurata{rp}, ICON-D2 {fa} nodi con vento {se}, {len(ore_s)} ore verificate", len(ore_s)
    if len(ore) >= mn:
        c = statistics.mean(o["mis"]["media"] - o["prev"]["d2"] for o in ore)
        return round(c, 1), f"misurata{rp}, ICON-D2 {fa} nodi, {len(ore)} ore verificate", len(ore)
    if periodo in CORR_PERIODO:                                 # v1.31: pomeriggio e sera, correzione fissa dell'analisi F0
        cv, f0 = CORR_PERIODO[periodo]
        return cv[area_zona(zona)], f"fissa {periodo} ({f0}); ICON-D2 {fa} nodi: {len(ore)} ore verificate su {mn} richieste", len(ore)
    if zona in CORR_ZONA:                                        # v1.27: Nord e Sud, correzione fissa dei 3 mesi
        c0, f0 = CORR_ZONA[zona]
        return c0, f"fissa ({f0}); ICON-D2 {fa} nodi: {len(ore)} ore verificate su {mn} richieste", len(ore)
    # v1.11: correzione fissa graduale, 0 fino a 3 nodi di ICON-D2 e piena (+2,2) da 5 nodi (prima: scatto a 4 nodi)
    c = round(CORR_DEFAULT * min(1.0, max(0.0, (d2 - (CORR_SOGLIA_DEFAULT - 1)) / 2)), 1)
    return c, f"fissa ({CORR_DEFAULT_FONTE}); ICON-D2 {fa} nodi: {len(ore)} ore verificate su {MIN_ORE_CORR} richieste", len(ore)


def correzione(d2, dir_g, cartella=".", zona="Malamocco · Valgrande", periodo="mattino"):
    """(correzione_nodi, testo_fonte, n_ore) da sommare a ICON-D2 per la zona.
    v1.9: vicino ai confini tra le fasce (6 e 12 nodi, +-RACCORDO nodi) la correzione passa gradualmente
    da quella della fascia sotto a quella della fascia sopra: niente salti, la stima cresce con ICON-D2."""
    zona = CORR_DA.get(ALIAS.get(zona, zona), ALIAS.get(zona, zona))
    A = carica_archivio(cartella)
    se = settore(dir_g)
    fa = fascia(d2)
    c, fonte, n = _corr_fascia(fa, se, d2, A, zona, periodo)
    for i in range(len(FASCE) - 1):
        b = FASCE[i][1]
        if b - RACCORDO <= d2 < b + RACCORDO:
            c1, _, _ = _corr_fascia(FASCE[i][2], se, d2, A, zona, periodo)
            c2, _, _ = _corr_fascia(FASCE[i + 1][2], se, d2, A, zona, periodo)
            if c1 != c2:
                t = (d2 - (b - RACCORDO)) / (2 * RACCORDO)
                c = round(c1 + (c2 - c1) * t, 1)
                fonte = (f"graduale tra fascia {FASCE[i][2]} ({'+' if c1 >= 0 else ''}{num(c1)}) e fascia {FASCE[i + 1][2]} "
                         f"({'+' if c2 >= 0 else ''}{num(c2)}) nodi; fascia di ICON-D2 {fonte}")
            break
    return c, f"{zona}: {fonte}", n


def _onde_settore(dir_g, cartella):
    se = settore(dir_g)
    def g_onda(x):   # v1.13: gradi misurati (dal 1.13), altrimenti dalla sigla salvata
        return x["dir_mis_g"] if x.get("dir_mis_g") is not None else GRADI_CARD.get(x.get("dir_mis"))
    return se, [x for x in carica_archivio(cartella)["onda"] if g_onda(x) is not None
                and settore(g_onda(x)) == se and x.get("prev")]


def correzione_onda_fascia(dir_g, cartella="."):
    """v1.12: (min, max, giorni) del RAPPORTO medio giornaliero onda misurata / modello nello stesso settore;
    None se le ore verificate sono meno di MIN_ORE_CORR. Il rapporto scala con l'onda: con poco vento resta piccola."""
    se, o = _onde_settore(dir_g, cartella)
    if len(o) < MIN_ORE_CORR:
        return None
    g = {}
    for x in o:
        g.setdefault(x["data"], []).append(x["mis"] / x["prev"])
    m = [statistics.mean(v) for v in g.values()]
    return round(min(m), 2), round(max(m), 2), len(g)


def correzione_onda(dir_g, cartella="."):
    """v1.12: (rapporto_medio, testo_fonte, n_ore): onda corretta = modello x rapporto (misurata / modello alla
    Piattaforma CNR, stesso settore del vento). None se le ore verificate sono meno di MIN_ORE_CORR."""
    se, o = _onde_settore(dir_g, cartella)
    if len(o) < MIN_ORE_CORR:
        return None, f"onda con vento {se}: {len(o)} ore verificate su {MIN_ORE_CORR} richieste", len(o)
    r = statistics.mean(x["mis"] / x["prev"] for x in o)
    return round(r, 2), f"onda corretta con vento {se}: misurata alla Piattaforma CNR = modello × {num(r, 2)} in media, {len(o)} ore verificate", len(o)


def correzione_raffiche(dir_g, cartella=".", zona="Malamocco · Valgrande", periodo="mattino"):
    """v1.12: (correzione_nodi, testo, n_ore) da sommare alla raffica di ICON-D2, misurata a Malamocco Porto nello stesso
    settore; None se le ore verificate sono meno di MIN_ORE_CORR."""
    zona = CORR_DA.get(ALIAS.get(zona, zona), ALIAS.get(zona, zona))
    se = settore(dir_g)
    o = [x for x in carica_archivio(cartella)["ore"] if x["zona"] == zona and settore_ora(x) == se and periodo_ora(x["ora"]) == periodo   # v1.31
         and x["prev"].get("raff_d2") is not None and x["mis"].get("raff") is not None]
    if len(o) < MIN_ORE_CORR:
        return None, f"raffiche con vento {se}: {len(o)} ore verificate su {MIN_ORE_CORR} richieste", len(o)
    c = statistics.mean(x["mis"]["raff"] - x["prev"]["raff_d2"] for x in o)
    return round(c, 1), f"raffiche misurate a Malamocco Porto con vento {se}: ICON-D2 {'+' if c >= 0 else ''}{num(c)} nodi, {len(o)} ore verificate", len(o)


def errori_fascia(nome_fascia, cartella=".", zona="Malamocco · Valgrande"):
    zona = CORR_DA.get(ALIAS.get(zona, zona), ALIAS.get(zona, zona))
    o = [x for x in carica_archivio(cartella)["ore"] if x["zona"] == zona and x["fascia_d2"] == nome_fascia]
    if not o:
        return 0, None, None
    return len(o), statistics.mean(x["prev"]["d2"] - x["mis"]["media"] for x in o), statistics.mean(x["prev"]["stima"] - x["mis"]["media"] for x in o)


def riepilogo(A):
    righe = []
    for zona in ZONE:
        oz = [x for x in A["ore"] if x["zona"] == zona]
        if not oz:
            continue
        righe.append(f"{zona} ({len(oz)} ore):")
        for _, _, fa in FASCE:
            o = [x for x in oz if x["fascia_d2"] == fa]
            if o:
                righe.append(f"  ICON-D2 {fa} nodi: {len(o)} ore · errore ICON-D2 {sg(statistics.mean(x['prev']['d2'] - x['mis']['media'] for x in o))}"
                             f" · errore stima {sg(statistics.mean(x['prev']['stima'] - x['mis']['media'] for x in o))} nodi")
        oa = [x for x in oz if x["prev"].get("ar") is not None and x["prev"].get("d2") is not None]   # v1.26: AROME HD in parallelo
        if oa:
            e = lambda f_: (statistics.mean(f_(x) - x["mis"]["media"] for x in oa), statistics.mean(abs(f_(x) - x["mis"]["media"]) for x in oa))
            (bd, ad), (ba, aa), (bm, am) = e(lambda x: x["prev"]["d2"]), e(lambda x: x["prev"]["ar"]), e(lambda x: (x["prev"]["ar"] + x["prev"]["d2"]) / 2)
            righe.append(f"  AROME HD in parallelo ({len(oa)} ore, senza correzione): errore medio / assoluto ICON-D2 {sg(bd)} / {num(ad)} · "
                         f"AROME HD {sg(ba)} / {num(aa)} · media dei due {sg(bm)} / {num(am)} nodi")
        cs = [sem_ore(x) for x in oz]
        cs = [c for c in cs if None not in c]
        if cs:                                      # v1.18
            ok = sum(p == m for p, m in cs); calmo = sum(ORD_SEM[p] < ORD_SEM[m] for p, m in cs)
            righe.append(f"  semaforo orario: colore azzeccato in {ok} ore su {len(cs)} · previsto più calmo in {calmo} · più ventoso in {len(cs) - ok - calmo}")
        for _, _, se in SETTORI:
            o = [x for x in oz if settore_ora(x) == se]
            if o:
                righe.append(f"  vento {se} (misurato): {len(o)} ore · errore ICON-D2 {sg(statistics.mean(x['prev']['d2'] - x['mis']['media'] for x in o))} nodi")
    if A["onda"]:
        e = [x["prev"] - x["mis"] for x in A["onda"]]
        righe.append(f"Onda in mare aperto ({len(e)} ore): errore modello {sg(statistics.mean(e))} m (cella modello diversa dalla piattaforma)")
    if A["aria"]:
        righe.append("Aria (previsto − misurato, °C): " + " · ".join(
            f"{nome} {sg(statistics.mean(x[p] - x[m] for x in s))} ({len(s)} ore)" for nome, p, m in ERR_ARIA
            for s in [[x for x in A["aria"] if x.get(p) is not None and x.get(m) is not None]] if s))
    po = [x for x in A.get("pioggia", []) if x.get("oss") is not None and x.get("prob_D2") is not None]
    if po:
        parti = []
        for lo, hi, nome in FASCE_PROB:
            s = [x for x in po if lo <= x["prob_D2"] < hi]
            if s:
                parti.append(f"ICON-D2 {nome}: {len(s)} {'ora' if len(s) == 1 else 'ore'}, piovuto in {sum(bool(x['oss']) for x in s)}")
        righe.append(f"Pioggia osservata in barca ({len(po)} ore): " + " · ".join(parti))
    pg = [x for x in A.get("pioggia", []) if x.get("cavalli_mm") is not None and x.get("prob_D2") is not None]
    if pg:
        parti = []
        for lo, hi, nome in FASCE_PROB:
            s = [x for x in pg if lo <= x["prob_D2"] < hi]
            if s:
                parti.append(f"ICON-D2 {nome}: {len(s)} {'ora' if len(s) == 1 else 'ore'}, piovuto in {sum(x['cavalli_mm'] >= SOGLIA_PIOGGIA for x in s)}")
        e = [x["mm_D2"] - x["cavalli_mm"] for x in pg if x.get("mm_D2") is not None]
        righe.append(f"Pioggia a Palazzo Cavalli ({len(pg)} ore): " + " · ".join(parti)
                     + (f" · errore mm ICON-D2 {sg(statistics.mean(e))}" if e else ""))
    return righe


# ------------------------------------------------------------------ previsioni da verificare
def area_di(P, nome_file=""):
    """v1.23: area della previsione dal campo "area" del JSON; altrimenti dal nome del file; altrimenti centro."""
    if P.get("area"):
        return P["area"]
    m = re.search(r"_\d{4}-\d{4}_([a-z]+)_v[\d.]+\.json$", nome_file)
    return m.group(1) if m else "centro"


def _media(xs, d=1):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), d) if xs else None


def righe_zona(P, zona):
    """Righe orarie del vento previsto per una zona di verifica. v1.23: per Laguna Nord e Laguna Sud media dei punti di
    calcolo dell'area (stessa media del semaforo); un'ora con un punto senza ICON-D2 vale come ripiego."""
    if zona in P["vento"]:
        return P["vento"][zona]
    if ZONE_MEDIA.get(zona) != P.get("_area"):
        return None
    punti = [z for z in (P.get("zone_vento") or []) if z in P["vento"]]
    if not punti:
        return None
    out = []
    for rs in zip(*(P["vento"][z] for z in punti)):
        if len({r["ora"] for r in rs}) != 1:
            return None
        if any(r.get("d2") is None or r.get("ripiego") for r in rs):
            out.append({"ora": rs[0]["ora"], "d2": None, "ripiego": True})
            continue
        dirs = [r["dir_g"] for r in rs if r.get("dir_g") is not None]
        st, d2 = _media(r["stima"] for r in rs), _media(r["d2"] for r in rs)
        out.append({"ora": rs[0]["ora"], "stima": st, "d2": d2,
                    "i2": _media(r.get("i2") for r in rs) if all(r.get("i2") is not None for r in rs) else None,
                    "ec": _media(r.get("ec") for r in rs) if all(r.get("ec") is not None for r in rs) else None,
                    "raff": _media(r.get("raff") for r in rs), "raff_d2": _media(r.get("raff_d2", r.get("raff")) for r in rs),
                    "ar": _media(r.get("ar") for r in rs) if all(r.get("ar") is not None for r in rs) else None,            # v1.26
                    "raff_ar": _media(r.get("raff_ar") for r in rs) if all(r.get("raff_ar") is not None for r in rs) else None,
                    "corr": round(st - d2, 1), "dir_g": round(media_dir(dirs)) if dirs else None, "punti": punti,
                    "prudenza": any(r.get("prudenza") for r in rs)})                                              # v1.30
    return out


def leggi_previsioni(cartella):
    scelte = {}
    for f in glob.glob(os.path.join(cartella, CARTELLA, "previsione_*.json")):
        if f.endswith("_agg.json"):          # v1.32: aggiornamento fatto a fascia iniziata, non e' la previsione da verificare
            continue
        try:
            P = json.load(open(f, encoding="utf-8"))
            P["vento"] = {ALIAS.get(z, z): r for z, r in P["vento"].items()}
            P["_area"] = area_di(P, f)
            g = dt.datetime.strptime(re.search(r"previsione_(\d\d-\d\d-\d{4})", f).group(1), "%d-%m-%Y").date()
            da_s, a_s = P["finestra"].replace("-", "–").split("–")
            tz = tz_italia(g)
            da = dt.datetime.combine(g, ora_da_txt(da_s), tz)
            a = dt.datetime.combine(g, ora_da_txt(a_s), tz)          # v1.31: "24:00" = 23:59
            gen = dt.datetime.strptime(P["generato"], "%d/%m/%Y %H:%M").replace(tzinfo=tz_italia(g))
            if not any(righe_zona(P, z) for z in ZONE):
                continue
        except Exception:
            continue
        if gen > da:
            continue
        # v1.14: l'area fa parte della chiave. v1.23: area dal JSON (prima: dal nome del file, e senza nome valeva "sud",
        # che era il vecchio nome del centro: centro e sud dello stesso giorno sarebbero finiti sulla stessa chiave).
        # Il centro resta senza suffisso, come le chiavi gia' in archivio.
        area = P["_area"]
        chiave = f"{g.isoformat()} {P['finestra']}" + ("" if area == "centro" else f" {area}")
        agg_marea = (P.get("marea") or {}).get("aggiornata") or ""          # v1.32: a parita' di vento vale la marea piu' recente
        agg_marea = agg_marea[6:10] + agg_marea[3:5] + agg_marea[:2] + agg_marea[10:]
        if chiave not in scelte or (gen, agg_marea) > (scelte[chiave]["gen"], scelte[chiave]["agg_marea"]):
            scelte[chiave] = {"file": os.path.basename(f), "giorno": g, "da": da, "a": a, "gen": gen, "P": P, "area": area, "chiave": chiave, "agg_marea": agg_marea}
    for v in list(scelte.values()):      # v1.32: passaggio alle nuove fasce: dove c'e' la nuova non si verifica anche la vecchia
        vecchia = {12: 13, 18: 19}.get(v["da"].hour)
        if vecchia:
            for k, w in list(scelte.items()):
                if w["giorno"] == v["giorno"] and w["area"] == v["area"] and w["da"].hour == vecchia:
                    del scelte[k]
    return scelte


STAZ_MAREA = {"centro": "Malamocco Porto", "nord": "Laguna Nord", "sud": "Chioggia Porto"}   # v1.25: livello misurato per area
STAZ_CPSM = "Punta Salute"     # v1.34: stazione a cui si riferisce la previsione di marea del CPSM
MIN_LIVELLI = 60               # letture da 5 min nella finestra 05-12 (85 possibili)
MIN_LIVELLI_QUOTA = 0.7        # v1.31: fasce piu' corte (13-18, 19-24): almeno il 70 % delle letture possibili


ESTREMO_TOLL_CM = 2.0          # v1.29: tratto "in colmo" = livello (media mobile) entro 2 cm dal massimo/minimo
ESTREMO_MEDIA_PUNTI = 7        # v1.29: media mobile su 15 letture (75 min) per togliere le oscillazioni brevi (prima 7 letture, 35 min)


def _estremo_misurato(serie, t0, tipo, raggio=120):
    """Estremo (alta/bassa) della serie [(min, cm)] entro +-raggio min da t0, su media mobile 75 min (v1.29; prima 35).
    v1.29: ora = centro del tratto continuo PIU' LUNGO in cui il livello resta entro ESTREMO_TOLL_CM dal massimo (alta)
    o dal minimo (bassa); cm = estremo di quel tratto. Prima: punto piu' alto con interpolazione parabolica, che prendeva
    oscillazioni brevi (Chioggia Porto 22/11/2026: punta di 95 cm alle 06:50, poi pianoro 89-92 cm 07:40-10:30)."""
    k_ = ESTREMO_MEDIA_PUNTI
    xs = [p for p in serie if t0 - raggio - 60 <= p[0] <= t0 + raggio + 60]
    if len(xs) < 2 * k_ + 10:
        return None
    sm = []
    for i in range(k_, len(xs) - k_):
        w = xs[i - k_:i + k_ + 1]
        if w[-1][0] - w[0][0] <= 10 * k_ + 10:
            sm.append((xs[i][0], statistics.mean(x[1] for x in w)))
    sm = [p for p in sm if t0 - raggio <= p[0] <= t0 + raggio]
    if len(sm) < 10:
        return None
    alta = tipo == "alta"
    ye = max(p[1] for p in sm) if alta else min(p[1] for p in sm)
    dentro = [(alta and p[1] >= ye - ESTREMO_TOLL_CM) or (not alta and p[1] <= ye + ESTREMO_TOLL_CM) for p in sm]
    tratti, i = [], 0
    while i < len(sm):
        if dentro[i]:
            j = i
            while j + 1 < len(sm) and dentro[j + 1] and sm[j + 1][0] - sm[j][0] <= 10:
                j += 1
            tratti.append((i, j))
            i = j + 1
        else:
            i += 1
    a_, b_ = max(tratti, key=lambda r: (sm[r[1]][0] - sm[r[0]][0], r[1] - r[0]))
    if a_ == 0 or b_ == len(sm) - 1:
        return None                           # tratto sul bordo della finestra: non e' quello cercato
    seg = [p[1] for p in sm[a_:b_ + 1]]
    return {"min": round((sm[a_][0] + sm[b_][0]) / 2), "cm": round(max(seg) if alta else min(seg), 1),
            "durata_min": sm[b_][0] - sm[a_][0]}


def _errore_cpsm(righe, g0, f, ps, fonte_ps, min_l, MP):
    """v1.34: previsione CPSM contro il livello misurato a Punta Salute nella finestra f; {} se la curva e' solo
    astronomica o le letture sono troppo poche."""
    if "CPSM" not in (fonte_ps or ""):
        return {}
    e = []
    for r in righe:
        t = round((r["t"] - g0).total_seconds() / 60)
        if r.get("liv") is None or not f[0] <= t <= f[1]:
            continue
        p = MP.interpola(ps, t)
        if p is not None:
            e.append(p - r["liv"] * 100)
    if len(e) < min_l:
        return {}
    return {"cpsm_n": len(e), "cpsm_err_medio": round(statistics.mean(e), 1),
            "cpsm_err_ass": round(statistics.mean(abs(x) for x in e), 1)}


def verifica_marea(v, M, cfg):
    """v1.25: confronto marea della zona prevista / misurata nella finestra. Ritorna un dict o None."""
    import marea_propaga as MP
    P, area, g = v["P"], v["area"], v["giorno"]
    Mm = P.get("marea") or {}
    mz = Mm.get("zona") or MP.marea_zona(Mm, area, g, cfg)
    staz = STAZ_MAREA.get(area)
    if not mz or not staz:
        return None
    f = mz["fascia"]
    g0 = dt.datetime.combine(g, dt.time(0), tz_italia(g))
    mis = [(round((r["t"] - g0).total_seconds() / 60), r["liv"] * 100) for r in M.get(staz, []) if r.get("liv") is not None]
    win = [p for p in mis if f[0] <= p[0] <= f[1]]
    min_l = min(MIN_LIVELLI, int(MIN_LIVELLI_QUOTA * ((f[1] - f[0]) // 5 + 1)))     # v1.31
    if len(win) < min_l:
        return None
    ps, est_ps, fonte_ps = MP.curva_punta_salute(Mm)
    ez, ep = [], []
    for t, h in win:
        a, b = MP.interpola(mz["curva"], t), MP.interpola(ps, t)
        if a is not None and b is not None:
            ez.append(a - h); ep.append(b - h)
    if len(ez) < min_l:
        return None
    out = {"data": g.isoformat(), "area": area, "stazione": staz, "generato": v["gen"].strftime("%Y-%m-%d %H:%M"),
           "config": mz.get("config"), "fonte": mz.get("fonte"), "n": len(ez),
           "liv_err_medio": round(statistics.mean(ez), 1), "liv_err_ass": round(statistics.mean(abs(x) for x in ez), 1),
           "liv_err_medio_ps": round(statistics.mean(ep), 1), "liv_err_ass_ps": round(statistics.mean(abs(x) for x in ep), 1),
           "mis_medio": round(statistics.mean(h for _, h in win), 1), "estremo": None, "da": f[0]}
    out.update(_errore_cpsm(M.get(STAZ_CPSM, []), g0, f, ps, fonte_ps, min_l, MP))     # v1.34
    e = next((x for x in mz.get("estremi") or [] if f[0] <= x["min"] <= f[1]), None)
    if e:
        m = _estremo_misurato(mis, e["min"], e["tipo"])
        if m:
            tps = [MP.minuti_estremo(x, g) for x in est_ps if x.get("tipo") == ("max" if e["tipo"] == "alta" else "min")]
            tps = [t for t in tps if t is not None and abs(t - e["min"]) <= 180]
            t_ps = min(tps, key=lambda t: abs(t - e["min"])) if tps else None
            hh = lambda t: f"{(t // 60) % 24:02d}:{t % 60:02d}"
            out["estremo"] = {"tipo": e["tipo"], "prev_min": e["min"], "prev_ora": e["ora"], "prev_cm": e["cm"],
                              "mis_min": m["min"], "mis_ora": hh(m["min"]), "mis_cm": round(m["cm"]),
                              "err_min": e["min"] - m["min"], "err_cm": round(e["cm"] - m["cm"]),
                              "ps_min": t_ps, "ps_ora": hh(t_ps) if t_ps is not None else None,
                              "err_ps_min": (t_ps - m["min"]) if t_ps is not None else None}
    return out


def verifica_pendenti(cartella=".", html_dir=None, stampa_pdf=True, adesso=None, provvisoria=False):
    """Verifica le previsioni concluse non ancora in archivio. Ritorna righe di testo.
    provvisoria=True: finestre gia' iniziate, solo ore complete, archivio non modificato."""
    adesso = adesso or dt.datetime.now(dt.timezone.utc)
    A = carica_archivio(cartella)
    O = applica_osservazioni(A, cartella)
    fatte = {p["chiave"] for p in A["previsioni"]}
    lim = (lambda v: v["da"] + dt.timedelta(minutes=30) <= adesso) if provvisoria else (lambda v: v["a"] + dt.timedelta(minutes=30) <= adesso)
    pend = [v for k, v in leggi_previsioni(cartella).items() if k not in fatte and lim(v)]
    if not pend:
        return [f"fd_verifica v{VERSIONE}: nessuna previsione da verificare" + (" (nessuna finestra iniziata da almeno 30 min)" if provvisoria else "")]
    M = scarica_misure(html_dir)
    import marea_propaga as MP
    MPCFG = MP.carica_config(cartella)             # v1.25
    inizio = min((r[0]["t"] for r in M.values() if r), default=None)
    if inizio is None:
        return [f"fd_verifica v{VERSIONE}: pagine CPSM non raggiungibili, verifica rinviata"]
    log = []
    for v in sorted(pend, key=lambda x: (x["da"], x["area"] != "centro")):
        P, chiave = v["P"], v["chiave"]
        risultati, ore_tot, onda_v, aria_v = [], [], [], []
        n_rip, n_d2 = 0, 0                      # v1.17: ore di ripiego (senza ICON-D2) e ore con ICON-D2
        zeri = {}                               # v1.23: letture a zero scartate nella finestra, per stazione
        for zona in ZONE:
            rz = righe_zona(P, zona)
            if not rz:
                continue
            for s in ZONE[zona][0]:
                if s in STAZ_FILTRO_ZERO:
                    zeri[s] = sum(1 for x in M.get(s, []) if x.get("zero") and v["da"] - dt.timedelta(minutes=30) <= x["t"] < v["a"] + dt.timedelta(minutes=30))
            ore_z = []
            for r in rz:
                if r.get("d2") is None or r.get("ripiego"):     # v1.17: niente ICON-D2 da verificare
                    n_rip += 1
                    continue
                n_d2 += 1
                centro = dt.datetime.combine(v["giorno"], dt.time(int(r["ora"][:2]), 0), tz_italia(v["giorno"]))
                if centro + dt.timedelta(minutes=30) > adesso:
                    continue
                m = misura_zona(M, zona, centro)
                if not m:
                    continue
                ore_z.append({"data": v["giorno"].isoformat(), "ora": r["ora"], "zona": zona, "stazione": ZONE[zona][1],
                              "generato": v["gen"].strftime("%Y-%m-%d %H:%M"), "anticipo_h": round((centro - v["gen"]).total_seconds() / 3600, 1),
                              "prev": {**{k: r.get(k) for k in ("stima", "d2", "i2", "ec", "raff", "dir_g") + (("punti",) if "punti" in r else ())
                                          + (("ar", "raff_ar") if r.get("ar") is not None else ())
                                          + (("prudenza",) if r.get("prudenza") else ())},   # v1.26: AROME HD in parallelo; v1.30: prudenza
                                       "raff_d2": r.get("raff_d2", r.get("raff")), "corr": r.get("corr", round(r["stima"] - r["d2"], 1))},
                              "mis": m, "fascia_d2": fascia(r["d2"]), "settore_d2": settore(r.get("dir_g", 0)),
                              "sem_prev": semaforo(r.get("stima"), r.get("raff")), "sem_mis": semaforo(m["media"], m["raff"])})
            if ore_z:
                risultati.append((zona, ore_z)); ore_tot += ore_z
        for r in ((P.get("mare") or {}).get("righe") or []):
            centro = dt.datetime.combine(v["giorno"], dt.time(int(r["ora"][:2]), 0), tz_italia(v["giorno"]))
            if centro + dt.timedelta(minutes=30) > adesso:
                continue
            m = misura_ora(M.get(STAZ_ONDA, []), centro, "onda")
            if m:
                mv = misura_ora(M.get(STAZ_ONDA, []), centro)
                onda_v.append({"data": v["giorno"].isoformat(), "ora": r["ora"], "prev": r["h"], "mis": m["media"],
                               "vento_prev": r.get("vento"), "vento_mis": mv["media"] if mv else None,
                               "dir_mis": card(mv["dir_g"]) if mv and mv["dir_g"] is not None else None,
                               "dir_mis_g": round(mv["dir_g"]) if mv and mv["dir_g"] is not None else None,
                               "dir_mis_txt": f"{gradi(mv['dir_g'])}° {card(gradi(mv['dir_g']))}" if mv and mv["dir_g"] is not None else "–",
                               "nome_mis": nome_vento(mv["dir_g"]) if mv and mv["dir_g"] is not None else ""})
        for r in ((P.get("meteo") or {}).get("aria_ore") or []):
            centro = dt.datetime.combine(v["giorno"], dt.time(int(r["ora"][:2]), 0), tz_italia(v["giorno"]))
            if centro + dt.timedelta(minutes=30) > adesso:
                continue
            mp = misura_ora(M.get(STAZ_ONDA, []), centro, "aria")
            mc = misura_ora(M.get(STAZ_ARIA_CITTA, []), centro, "aria")
            if mp or mc:
                aria_v.append({"data": v["giorno"].isoformat(), "ora": r["ora"], "mare_prev": r.get("mare"), "mestre_prev": r.get("mestre"),
                               "acqua_prev": r.get("acqua"), "piatt_mis": round(mp["media"], 1) if mp else None,
                               "cavalli_mis": round(mc["media"], 1) if mc else None})
        v["aria_v"] = aria_v
        pioggia_v = []
        for r in ((P.get("meteo") or {}).get("pioggia_ore") or []):       # v1.15 (previsioni da fd_previsione 1.24)
            centro = dt.datetime.combine(v["giorno"], dt.time(int(r["ora"][:2]), 0), tz_italia(v["giorno"]))
            if centro + dt.timedelta(minutes=30) > adesso:
                continue
            mc, mp = misura_pioggia(M.get(STAZ_ARIA_CITTA, []), centro), misura_pioggia(M.get(STAZ_ONDA, []), centro)
            if mc is not None or mp is not None:
                pioggia_v.append({"data": v["giorno"].isoformat(), "ora": r["ora"], **{k: r.get(k) for k in ("prob_D2", "prob_EC", "mm_D2", "mm_2i", "mm_EC")},
                                  "cavalli_mm": mc, "piatt_mm": mp, "oss": pioggia_osservata(O, v["giorno"].isoformat(), r["ora"])})
        v["pioggia_v"] = pioggia_v
        v["zeri"] = zeri
        try:                                    # v1.25: marea della zona (solo verifica definitiva)
            v["marea_v"] = None if provvisoria else verifica_marea(v, M, MPCFG)
        except Exception as ex:
            v["marea_v"] = None
            log.append(f"  {chiave}: verifica marea non riuscita ({type(ex).__name__}: {str(ex)[:80]})")
        if not ore_tot:
            if n_rip and not n_d2:              # v1.17: tutte le ore di ripiego, non si riprova
                if not provvisoria:
                    A["previsioni"].append({"chiave": chiave, "file": v["file"], "esito": "non verificabile (ICON-D2 assente in tutte le ore)"})
                log.append(f"  {chiave}: non verificabile, ICON-D2 assente in tutte le ore (stima = ICON-2i)")
            elif provvisoria:
                log.append(f"  {chiave}: ancora nessuna ora completa")
            elif v["a"] < inizio:
                A["previsioni"].append({"chiave": chiave, "file": v["file"], "esito": "persa (misure non piu' nelle pagine CPSM)"})
                log.append(f"  {chiave}: persa, misure CPSM non piu' disponibili (le pagine tengono 24 ore)")
            else:
                log.append(f"  {chiave}: misure CPSM incomplete, riprovo al prossimo lancio")
            continue
        if not provvisoria:
            # v1.23: le ore del vento si sostituiscono sempre (la chiave contiene la zona, le aree non si toccano tra loro);
            # onda, aria e pioggia sono le stesse misure per tutte le aree: le scrive il centro, le altre solo se mancano
            # v1.32: l'ora di confine (12 e 18) e' gia' in archivio dalla fascia precedente: non la si sovrascrive
            def doppia(o, presenti, chiave_o):
                return int(o["ora"][:2]) == v["da"].hour and v["da"].hour in (12, 18) and chiave_o(o) in presenti
            gia = {(x["data"], x["ora"], x["zona"]) for x in A["ore"]}
            ore_tot = [o for o in ore_tot if not doppia(o, gia, lambda o: (o["data"], o["ora"], o["zona"]))]
            gia2 = {n: {(x["data"], x["ora"]) for x in A[n]} for n in ("onda", "aria", "pioggia")}
            onda_v = [o for o in onda_v if not doppia(o, gia2["onda"], lambda o: (o["data"], o["ora"]))]
            aria_v = [o for o in aria_v if not doppia(o, gia2["aria"], lambda o: (o["data"], o["ora"]))]
            pioggia_v = [o for o in pioggia_v if not doppia(o, gia2["pioggia"], lambda o: (o["data"], o["ora"]))]
            chiavi = {(o["data"], o["ora"], o["zona"]) for o in ore_tot}
            A["ore"] = [x for x in A["ore"] if (x["data"], x["ora"], x["zona"]) not in chiavi] + ore_tot
            if v["area"] == "centro":
                ch_o = {(o["data"], o["ora"]) for o in onda_v}
                A["onda"] = [x for x in A["onda"] if (x["data"], x["ora"]) not in ch_o] + onda_v
                ch_a = {(o["data"], o["ora"]) for o in aria_v}
                A["aria"] = [x for x in A["aria"] if (x["data"], x["ora"]) not in ch_a] + aria_v
                ch_p = {(o["data"], o["ora"]) for o in pioggia_v}
                A["pioggia"] = [x for x in A["pioggia"] if (x["data"], x["ora"]) not in ch_p] + pioggia_v
            else:      # v1.14: un'altra area non sostituisce onda, aria e pioggia gia' in archivio (le misure sono le stesse)
                gia = {(x["data"], x["ora"]) for x in A["onda"]}
                A["onda"] += [o for o in onda_v if (o["data"], o["ora"]) not in gia]
                gia = {(x["data"], x["ora"]) for x in A["aria"]}
                A["aria"] += [o for o in aria_v if (o["data"], o["ora"]) not in gia]
                gia = {(x["data"], x["ora"]) for x in A["pioggia"]}
                A["pioggia"] += [o for o in pioggia_v if (o["data"], o["ora"]) not in gia]
            if v.get("marea_v"):                # v1.25: una riga per giorno e area; v1.34: e per finestra
                mv = v["marea_v"]
                k = (mv["data"], mv["area"], mv["da"])
                A["marea"] = [x for x in A["marea"] if (x["data"], x["area"], x.get("da")) != k] + [mv]
            A["previsioni"].append({"chiave": chiave, "file": v["file"], "esito": f"verificata, {len(ore_tot)} ore",
                                    "verificata": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
        log.append(f"  {chiave}{' PROVVISORIA' if provvisoria else ''}:" + (f" {n_rip} ore di ripiego senza ICON-D2 escluse" if n_rip else ""))
        for zona, oz in risultati:
            oa = [o for o in oz if o["prev"].get("ar") is not None]          # v1.26
            if oa:
                log.append(f"    {zona} AROME HD (in parallelo): {len(oa)} ore · errore AROME HD {sg(statistics.mean(o['prev']['ar'] - o['mis']['media'] for o in oa))} · "
                           f"media AROME+D2 {sg(statistics.mean((o['prev']['ar'] + o['prev']['d2']) / 2 - o['mis']['media'] for o in oa))} nodi")
            log.append(f"    {zona}: {len(oz)} ore · misurato {num(statistics.mean(o['mis']['media'] for o in oz))} nodi · "
                       f"errore stima {sg(statistics.mean(o['prev']['stima'] - o['mis']['media'] for o in oz))} · "
                       f"errore ICON-D2 {sg(statistics.mean(o['prev']['d2'] - o['mis']['media'] for o in oz))} nodi")
        mv = v.get("marea_v")
        if mv:
            e = mv.get("estremo")
            log.append(f"    Marea {mv['stazione']}: livello errore medio {sg(mv['liv_err_medio'])} cm (Punta Salute non corretta {sg(mv['liv_err_medio_ps'])})"
                       + (f" · {e['tipo']} prevista {e['prev_ora']}, misurata {e['mis_ora']}, errore {e['err_min']:+d} min"
                          + (f" (Punta Salute {e['err_ps_min']:+d})" if e.get('err_ps_min') is not None else "") if e else ""))
        if mv and "cpsm_err_ass" in mv:                  # v1.34
            log.append(f"    Marea CPSM a Punta Salute: errore medio {sg(mv['cpsm_err_medio'])} cm, medio assoluto {num(mv['cpsm_err_ass'])} cm")
        for s, nz in zeri.items():
            if nz:
                log.append(f"    {s}: {nz} letture da 5 min a zero scartate (sensore fermo)")
        if onda_v:
            log.append(f"    Onda mare aperto: {len(onda_v)} ore · misurata {num(statistics.mean(o['mis'] for o in onda_v), 2)} m · "
                       f"prevista {num(statistics.mean(o['prev'] for o in onda_v), 2)} m")
        for nome, p, m in ERR_ARIA:
            s = [x for x in aria_v if x.get(p) is not None and x.get(m) is not None]
            if s:
                log.append(f"    Aria {nome}: {len(s)} ore · misurata {num(statistics.mean(x[m] for x in s))} °C · "
                           f"errore previsione {sg(statistics.mean(x[p] - x[m] for x in s))} °C")
        if any(x.get("oss") is not None for x in pioggia_v):
            log.append(f"    Pioggia osservata in barca ({O[v['giorno'].isoformat()][1]}): ore con pioggia "
                       + (", ".join(x["ora"] for x in pioggia_v if x.get("oss")) or "nessuna"))
        pc = [x for x in pioggia_v if x.get("cavalli_mm") is not None]
        if pc:
            log.append(f"    Pioggia Palazzo Cavalli: {len(pc)} ore · misurata {num(sum(x['cavalli_mm'] for x in pc))} mm · "
                       f"ore con pioggia {sum(x['cavalli_mm'] >= SOGLIA_PIOGGIA for x in pc)}")
        try:
            log.append(f"    report: {report(cartella, v, risultati, onda_v, M, A, stampa_pdf, provvisoria)}")
        except Exception as ex:
            log.append(f"    report non creato: {ex}")
    if provvisoria:
        return [f"fd_verifica v{VERSIONE}: verifica PROVVISORIA, archivio non modificato ({len(A['ore'])} ore verificate)"] + log
    salva_archivio(A, cartella)
    return [f"fd_verifica v{VERSIONE}: archivio {len(A['ore'])} ore verificate"] + log + ["  " + r for r in riepilogo(A)]


def andamento(A, zona="Malamocco · Valgrande"):
    """Errori della stima (previsione corretta) e di ICON-D2 nel tempo e per ora del mattino (previsto − misurato, nodi)."""
    oz = [o for o in A["ore"] if o["zona"] == zona and o["prev"].get("stima") is not None and o["prev"].get("d2") is not None]
    def blocco(chiave, gruppo):
        e_s = [o["prev"]["stima"] - o["mis"]["media"] for o in gruppo]; e_d = [o["prev"]["d2"] - o["mis"]["media"] for o in gruppo]
        return {"k": chiave, "n": len(gruppo), "stima": round(statistics.mean(e_s), 1), "d2": round(statistics.mean(e_d), 1),
                "ass_stima": round(statistics.mean(abs(x) for x in e_s), 1), "ass_d2": round(statistics.mean(abs(x) for x in e_d), 1)}
    giorni = [blocco(dt.date.fromisoformat(g).strftime("%d/%m"), [o for o in oz if o["data"] == g]) for g in sorted({o["data"] for o in oz})]
    ore = [blocco(f"{h}:00", [o for o in oz if o["ora"][:2] == h]) for h in sorted({o["ora"][:2] for o in oz})]
    return {"zona": zona, "stazione": ZONE[zona][1], "giorni": giorni, "ore": ore, "tot": blocco("tutte", oz) if oz else None}


def _stampa(base):
    chrome = next((c for c in [os.environ.get("FD_CHROME"), CROME_MAC, shutil.which("google-chrome"), shutil.which("chromium")] if c and os.path.exists(c)), None)
    if chrome:
        with tempfile.TemporaryDirectory() as prof:
            try:
                subprocess.run([chrome, "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking", f"--user-data-dir={prof}",
                                "--window-size=560,900", "--hide-scrollbars", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                                "--virtual-time-budget=4000", f"--print-to-pdf={os.path.abspath(base + '.pdf')}", "file://" + os.path.abspath(base + ".html")],
                               capture_output=True, timeout=90)
            except subprocess.TimeoutExpired:
                pass


def report_andamento(cartella=".", stampa_pdf=True):
    """Pagina con il solo andamento delle verifiche (python3 fd_verifica.py --andamento)."""
    A = carica_archivio(cartella)
    D = {"versione": VERSIONE, "provvisoria": False, "ore_fino": "", "titolo": "Andamento delle verifiche",
         "previsione": f"Archivio: {len(A['ore'])} ore verificate · aggiornato {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}",
         "sem_prev": "", "fascia": [0, 0], "zone": [], "onda": [], "liv": [], "aria": [], "andamento": andamento(A), "archivio": riepilogo(A),
         "note": NOTE_ANDAMENTO, "fonti": f"Misure: CPSM Comune di Venezia (Malamocco Porto) · fd_verifica v{VERSIONE}",
         "verde_fino_a": VERDE_FINO_A, "rosso_da": ROSSO_DA,
         "generato": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
    os.makedirs(os.path.join(cartella, CARTELLA), exist_ok=True)
    base = os.path.join(cartella, CARTELLA, f"verifica_andamento_v{VERSIONE}")
    open(base + ".html", "w", encoding="utf-8").write(HTML.replace("__DATI__", json.dumps(D, ensure_ascii=False).replace("</", "<\\/")))
    if stampa_pdf:
        _stampa(base)
    return base + (".pdf" if os.path.exists(base + ".pdf") else ".html")


NOTE_ANDAMENTO = [{"tipo": "dato", "testo": "Errore = previsto − misurato a Malamocco Porto (media oraria), in nodi: positivo = previsione troppo alta, negativo = troppo bassa. "
                                            "Stima = la previsione con la correzione in uso quel giorno (fissa o misurata), come era stata emessa."},
                  {"tipo": "inferenza", "testo": "Se la correzione funziona, la linea rossa (stima) resta più vicina a zero di quella arancio (ICON-D2 senza correzione). "
                                                 "Con pochi giorni e quasi solo bora l'andamento è indicativo."}]


# ------------------------------------------------------------------ report di confronto
NEBBIA_ARCHIVIO = "nebbia_archivio.json"


def _metar_metri(raw):
    """Visibilita' prevalente in metri dal bollettino METAR (gruppo a 4 cifre dopo il vento; 9999/CAVOK = oltre 10 km)."""
    if not raw:
        return None
    if " CAVOK" in raw:
        return 10000
    m = re.search(r"KT(?: \d{3}V\d{3})? (\d{4})(?:NDV)?(?: |$)", raw)
    return int(m.group(1)) if m else None


def nebbia_verifica(cartella, P, giorno):
    """v1.28: nebbia prevista ora per ora accanto alla visibilita' misurata a Venezia Tessera (METAR di nebbia_archivio.json).
    Per ogni ora: METAR tra 30 min prima e 30 min dopo (ora italiana -> UTC), visibilita' minima. Solo da vedere, nessun punteggio."""
    Me = P.get("meteo") or {}
    N, O = Me.get("nebbia"), Me.get("nebbia_ore") or []
    if not N or not O:
        return []
    try:
        mis = json.load(open(os.path.join(cartella, NEBBIA_ARCHIVIO), encoding="utf-8")).get("misure") or []
    except (OSError, ValueError):
        mis = []
    tz = tz_italia(giorno)
    pr = N.get("prob_max") is not None
    out = []
    for o in O:
        c = dt.datetime.combine(giorno, dt.time(int(o["ora"][:2]), 0), tz).astimezone(dt.timezone.utc)
        vv, wx = [], []
        for x in mis:
            try:
                t = dt.datetime.strptime(x["t"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
            except (KeyError, ValueError):
                continue
            if c - dt.timedelta(minutes=30) <= t < c + dt.timedelta(minutes=30):
                m = _metar_metri(x.get("raw"))
                if m is not None:
                    vv.append(m)
                if x.get("wx"):
                    wx.append(x["wx"])
        p = o.get("prob")
        cat = (0 if p is None else 2 if p >= N.get("soglia_alta", 50) else 1 if p >= N.get("soglia_min", 20) else 0) if pr else (o.get("livello") or 0)
        out.append({"ora": o["ora"], "cat": cat, "vis_m": min(vv) if vv else None, "wx": " ".join(dict.fromkeys(wx)) or None})
    return out if any(x["vis_m"] is not None for x in out) else []


def report(cartella, v, risultati, onda_v, M, A, stampa_pdf=True, provvisoria=False):
    g, P = v["giorno"], v["P"]
    g0 = dt.datetime.combine(g, dt.time(0), tz_italia(g))
    def err(oz, k, ref="media"):
        e = [o["prev"][k] - o["mis"][ref] for o in oz if o["prev"].get(k) is not None and o["mis"].get(ref) is not None]
        return {"medio": round(statistics.mean(e), 1), "ass": round(statistics.mean(abs(x) for x in e), 1)} if e else None
    zone = []
    for zona, oz in risultati:
        staz = ZONE[zona][0]
        serie = []
        for s in staz:     # serie da 5 min: media tra le stazioni della zona negli istanti comuni
            for r in M.get(s, []):
                if v["da"] - dt.timedelta(hours=2) <= r["t"] <= v["a"] + dt.timedelta(hours=2) and r.get("vel") is not None:
                    serie.append((r["t"], r["vel"], r.get("max") or r["vel"]))
        per_t = {}
        for t, a_, b_ in serie:
            per_t.setdefault(t, []).append((a_, b_))
        serie = [[round((t - g0).total_seconds() / 60), round(statistics.mean(x[0] for x in xs) * KN, 1), round(max(x[1] for x in xs) * KN, 1)]
                 for t, xs in sorted(per_t.items()) if len(xs) == len(staz)]
        win = [p for p in serie if v["da"].hour * 60 + v["da"].minute <= p[0] <= v["a"].hour * 60 + v["a"].minute]
        m_med = statistics.mean(p[1] for p in win) if win else None
        r_max = max(p[2] for p in win) if win else None
        cs = [sem_ore(o) for o in oz]                   # v1.18: semafori orari, stessa regola della previsione
        zone.append({"zona": zona, "stazione": ZONE[zona][1], "serie": serie,
                     "sintesi": {"media": round(m_med, 1) if m_med else None, "raff": r_max,
                                 "sem_mis": verdetto(c[1] for c in cs), "sem_prev_zona": verdetto(c[0] for c in cs),
                                 "ok": sum(p == m for p, m in cs if p and m), "n": sum(1 for p, m in cs if p and m),
                                 "rosse_mis": [o["ora"][:2] for o, c in zip(oz, cs) if c[1] == "rosso"],
                                 "rosse_prev": [o["ora"][:2] for o, c in zip(oz, cs) if c[0] == "rosso"]},
                     "ore": [{"ora": o["ora"], "mis": o["mis"]["media"], "raff_mis": o["mis"]["raff"],
                              "dir_mis": f"{gradi(o['mis']['dir_g'])}° {card(gradi(o['mis']['dir_g']))}" if o["mis"]["dir_g"] is not None else "–",
                              "nome_mis": nome_vento(o["mis"]["dir_g"]) if o["mis"]["dir_g"] is not None else "",
                              "dir_prev": f"{gradi(o['prev']['dir_g'])}° {card(gradi(o['prev']['dir_g']))}" if o["prev"].get("dir_g") is not None else "–",
                              "nome_prev": nome_vento(o["prev"]["dir_g"]) if o["prev"].get("dir_g") is not None else "",
                              "set_mis": settore_ora(o), "set_prev": settore(o["prev"].get("dir_g", 0)),
                              "sem_prev": sem_ore(o)[0], "sem_mis": sem_ore(o)[1],
                              **{k: o["prev"].get(k) for k in ("stima", "d2", "i2", "ec", "ar", "raff", "raff_d2", "corr", "prudenza")}} for o in oz],   # v1.30: AROME HD e prudenza
                     "errori": [dict(nome=n, **e) for n, e in [("Stima usata", err(oz, "stima")), ("ICON-D2", err(oz, "d2")), ("ICON-2i", err(oz, "i2")),
                                                               ("ECMWF", err(oz, "ec")), ("AROME HD (in parallelo)", err(oz, "ar") if any(o["prev"].get("ar") is not None for o in oz) else None), ("Raffiche ICON-D2", err(oz, "raff_d2", "raff")),
                                                               ("Raffiche stimate", err(oz, "raff", "raff") if any(o["prev"].get("raff") != o["prev"].get("raff_d2") for o in oz) else None)] if e]})
    liv = []
    Pm = P.get("marea") or {}
    fonte_liv = Pm.get("fonte_livello", "astronomica")
    eta_liv = Pm.get("cpsm_eta_h")
    ast = os.path.join(cartella, f"marea_astronomica_{g.year}.json")
    if os.path.exists(ast) and risultati:
        AM = json.load(open(ast, encoding="utf-8"))
        t0 = dt.datetime.fromisoformat(AM["t0_utc"].replace("Z", "+00:00"))
        for o in risultati[0][1]:
            c = dt.datetime.combine(g, dt.time(int(o["ora"][:2])), tz_italia(g))
            r = [x for x in M.get(STAZ_LIVELLO, []) if x["t"] == c and x.get("liv") is not None]
            k = (c - t0).total_seconds() / 60 / AM["passo_min"]; i = int(k)
            if r and 0 <= i < len(AM["cm"]) - 1:
                a_cm = AM["cm"][i] + (AM["cm"][i + 1] - AM["cm"][i]) * (k - i)
                mis_cm = r[0]["liv"] * 100
                pv = {}
                if Pm.get("curva"):                        # v1.20: livello previsto dalla fonte usata (CPSM se c'era, altrimenti astronomica)
                    cur = Pm.get("curva_cpsm") or Pm["curva"]
                    mm_ = int(o["ora"][:2]) * 60
                    q = next((p[1] for p in cur if p[0] == mm_), None)
                    if q is not None:
                        pv = {"prev": round(q), "scarto_prev": round(q - mis_cm)}
                liv.append({"ora": o["ora"], "mis": round(mis_cm), "astro": round(a_cm), "scarto": round(mis_cm - a_cm), **pv})
    if liv and not provvisoria and v.get("area", "centro") == "centro" and any("prev" in l for l in liv):    # v1.23: solo il centro    # v1.20: archivio livelli per valutare l'eta' del bollettino CPSM
        A["livello"] = [x for x in A.get("livello", []) if x["data"] != g.isoformat()] + [
            {"data": g.isoformat(), "ora": l["ora"], "mis": l["mis"], "astro": l["astro"], "prev": l.get("prev"), "fonte": fonte_liv, "eta_h": eta_liv} for l in liv]
    area = v.get("area", "centro")
    nota_misure = {"tipo": "dato", "testo": "Misure CPSM ogni 5 min in m/s, convertite in nodi; valore orario = media da −30 a +30 min, raffica = massimo nell'ora."}
    nota_settori = ("Settori del vento (Centro Maree, Comune di Venezia): Bora 23–67°, Tramontana–Maestrale 293–22°, Levante–Scirocco 68–202°, "
                    "Libeccio–Ponente 203–292°. In archivio il settore di ogni ora viene dalla direzione misurata alla stazione della zona; "
                    "in previsione dalla direzione di ICON-D2. Sotto la direzione misurata: quella prevista (in rosso se di un altro settore).")
    if area == "centro":
        note = [nota_misure,
                {"tipo": "inferenza", "testo": "San Leonardo · Melison, Fisolo e Bocca · Alberoni: nessuna stazione di verifica valida; non verificate, "
                                               "usano la correzione di Malamocco · Valgrande. Riferimenti validi: Malamocco Porto (vento) e Piattaforma CNR (onda e aria); aria anche Palazzo Cavalli."},
                {"tipo": "dato", "testo": f"Correzione misurata per zona quando una fascia di ICON-D2 ha almeno {MIN_ORE_CORR} ore verificate; prima vale +{num(CORR_DEFAULT)} nodi (graduale tra 3 e 5 nodi di ICON-D2)."},
                {"tipo": "dato", "testo": nota_settori}]
        staz_vento = ["Malamocco Porto"]
        avviso = ""
    else:                                       # v1.23: nord e sud, verifica di controllo
        staz_vento = [s_ for zona, _ in risultati for s_ in ZONE[zona][0]]
        punti = next((o["prev"].get("punti") for _, oz in risultati for o in oz if o["prev"].get("punti")), None) or P.get("zone_vento") or []
        corretta = any(abs(o["prev"].get("corr") or 0) > 0 for _, oz in risultati for o in oz)   # v1.27
        zn = [z for z, _ in risultati]
        note = [nota_misure,
                ({"tipo": "dato", "testo": f"Stima = ICON-D2 + correzione misurata della zona: fissa (+{num(CORR_ZONA[zn[0]][0])} nodi, {CORR_ZONA[zn[0]][1]}) finché una fascia di ICON-D2 non ha "
                                           f"{MIN_ORE_CORR_ZONA.get(zn[0], MIN_ORE_CORR)} ore verificate, poi calcolata dalle verifiche per fascia e direzione. Le raffiche non sono corrette."}
                 if corretta and zn and zn[0] in CORR_ZONA else
                 {"tipo": "dato", "testo": f"Verifica di controllo: nessuna correzione applicata alla previsione (stima = ICON-D2 così com'è). "
                                           f"Le ore verificate servono a decidere in seguito la correzione del vento per {', '.join(zn)}."}),
                {"tipo": "dato", "testo": f"Previsto della zona = media dei punti di calcolo ({', '.join(punti)}), la stessa media che decide il semaforo; "
                                          f"misurato = {', '.join(ZONE[z][1] for z, _ in risultati)}."},
                {"tipo": "inferenza", "testo": "Una stazione rappresenta un solo punto del bacino: il confronto con la media dei punti di calcolo è indicativo."},
                {"tipo": "dato", "testo": nota_settori}]
        zz = {s_: n_ for s_, n_ in (v.get("zeri") or {}).items() if n_}
        if any(s_ in STAZ_FILTRO_ZERO for s_ in staz_vento):
            note.insert(3, {"tipo": "dato", "testo": "Sensore fermo: le letture da 5 min con vento medio 0,0 e raffica 0,0 sono scartate; un'ora vale con almeno "
                                                     f"{MIN_LETTURE} letture valide su 12. "
                                                     + ("Scartate in questa finestra: " + ", ".join(f"{s_} {n_}" for s_, n_ in zz.items()) + "." if zz else "Nessuna lettura scartata in questa finestra.")})
        avviso = "" if corretta else " · Verifica di controllo: nessuna correzione applicata alla previsione"
    D = {"versione": VERSIONE, "provvisoria": provvisoria, "ore_fino": max(o["ora"] for _, oz in risultati for o in oz), "area": area,
         "titolo": f"{P['titolo']} · {P['finestra']}", "previsione": f"{v['file']} · generata {v['gen'].strftime('%d/%m %H:%M')}{avviso}",
         "sem_prev": P["verdetto"]["colore"], "fascia": [v["da"].hour * 60 + v["da"].minute, v["a"].hour * 60 + v["a"].minute],
         "zone": zone, "onda": onda_v, "liv": liv, "liv_fonte": fonte_liv, "liv_eta_h": eta_liv, "aria": v.get("aria_v") or [], "pioggia": v.get("pioggia_v") or [], "marea_zona": v.get("marea_v"),
         "andamento": andamento(A, risultati[0][0]), "archivio": riepilogo(A), "note": note,
         "nebbia": nebbia_verifica(cartella, P, g),   # v1.28
         "fonti": f"Misure: CPSM Comune di Venezia, dati.venezia.it, CC BY ({', '.join(dict.fromkeys(staz_vento + ['Malamocco Porto', 'Piattaforma CNR', 'Palazzo Cavalli']))}) · "
                  f"visibilità: METAR Venezia Tessera (AviationWeather.gov) · "
                  f"Previsione: fd_previsione (dati meteo Open-Meteo.com, CC BY 4.0) · fd_verifica v{VERSIONE}",
         "verde_fino_a": VERDE_FINO_A, "rosso_da": ROSSO_DA,
         "generato": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
    base = os.path.join(cartella, CARTELLA, f"verifica_{g.strftime('%d-%m-%Y')}_{v['da'].strftime('%H%M')}-{v['a'].strftime('%H%M')}"
                                            f"{'' if area == 'centro' else '_' + area}{'_PROVVISORIA' if provvisoria else ''}_v{VERSIONE}")
    json.dump(D, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(base + ".html", "w", encoding="utf-8").write(HTML.replace("__DATI__", json.dumps(D, ensure_ascii=False).replace("</", "<\\/")))
    if stampa_pdf:
        _stampa(base)
    return os.path.basename(base + (".pdf" if os.path.exists(base + ".pdf") else ".html"))


HTML = r"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verifica previsione</title>
<style>
:root{--bg:#f1f5f9;--card:#fff;--bd:#d8e0ea;--tx:#1e293b;--mu:#64748b;--ac:#3b82c4;--soft:#f1f5f9;--verde:#16a34a;--giallo:#ca8a04;--rosso:#dc2626;--vs:#dcfce7;--gs:#fef3c7;--rs:#fee2e2}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.45 -apple-system,"Inter","Segoe UI",Roboto,sans-serif}
.wrap{max-width:560px;margin:0 auto;padding:16px}
.hero{border-radius:14px;padding:22px;color:#fff;background:linear-gradient(135deg,#1e3a6b,#2c5aa0 60%,#4a8fd8)}
.hero .k{font-size:12px;letter-spacing:.14em;opacity:.85;text-transform:uppercase}.hero h1{font:400 26px/1.15 Georgia,serif;margin:10px 0 8px}.hero .z{opacity:.9;font-size:14px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px;margin-top:16px}
.card h2{margin:0 0 14px;font-size:13px;letter-spacing:.1em;color:var(--ac);text-transform:uppercase}
.card h3{margin:16px 0 6px;font-size:13px;color:var(--mu);font-weight:600}
.verd{display:flex;gap:16px;align-items:center}.dot{flex:0 0 76px;width:76px;height:76px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:11px;text-transform:uppercase;text-align:center;line-height:1.3}
.dot.verde{background:var(--verde)}.dot.giallo{background:var(--giallo)}.dot.rosso{background:var(--rosso)}
.nota{color:var(--mu);font-size:13px;margin-top:10px}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th{color:var(--mu);font-weight:500;font-size:12px;text-align:right;padding:6px 3px}th:first-child,td:first-child{text-align:left}
td{padding:7px 3px;border-top:1px solid #e5eaf0;text-align:right}
td.dir{font-size:12px;white-space:nowrap}.dnome{font-size:11px;color:var(--mu)}.dprev{font-size:9.5px;color:var(--mu);line-height:1.25;max-width:none}.dprev.fuori{color:var(--rosso);font-weight:600}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-weight:700}.pill.verde{background:var(--vs);color:var(--verde)}.pill.giallo{background:var(--gs);color:var(--giallo)}.pill.rosso{background:var(--rs);color:var(--rosso)}
.pos{color:#a16207}.neg{color:#1e40af}
.leg{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--mu);margin-top:6px}.leg i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:4px}
.arch{font:12px/1.6 ui-monospace,Menlo,monospace;color:var(--tx);white-space:pre-wrap}
.r{display:flex;gap:10px;margin:10px 0;align-items:flex-start}.tag{flex:0 0 auto;font-size:11px;font-weight:700;letter-spacing:.06em;padding:3px 8px;border-radius:6px;text-transform:uppercase}
.tag.dato{background:#dbeafe;color:#1e40af}.tag.inferenza{background:#fef3c7;color:#a16207}.tag.opinione{background:#ede9fe;color:#6d28d9}
.fonti{color:var(--mu);font-size:12px;text-align:center;margin:18px 8px 8px}
</style></head><body><div class="wrap" id="app"></div>
<script id="dati" type="application/json">__DATI__</script>
<script>
const D=JSON.parse(document.getElementById('dati').textContent);
const h=(t,a={},...c)=>{const e=document.createElement(t);for(const[k,v]of Object.entries(a))k==='cls'?e.className=v:k==='html'?e.innerHTML=v:e.setAttribute(k,v);c.flat().forEach(x=>x!=null&&e.append(x));return e;};
const n=(x,d=1)=>x==null?'–':Number(x).toFixed(d).replace('.',',');const sg=(x,d=1)=>{if(x==null)return'–';const p=10**d,r=Math.round(x*p)/p||0;return(r>0?'+':'')+n(r,d);};
const SV=D.verde_fino_a,SR=D.rosso_da,sem=(m,r)=>{const k=Math.round(Math.round(m*10)/10);return k>=SR?'rosso':k>SV?'giallo':'verde'};
const app=document.getElementById('app');
app.append(h('div',{cls:'hero'},h('div',{cls:'k'},(D.provvisoria?'Verifica PROVVISORIA (ore fino alle '+D.ore_fino+')':'Verifica previsione')+' · v'+D.versione),h('h1',{},D.titolo),h('div',{cls:'z'},D.previsione)));
function grafico(Z){const se=Z.serie;if(!se.length)return null;const W=520,H=210,L=30,R=10,T=14,B=26,x0=Math.min(...se.map(p=>p[0])),x1=Math.max(...se.map(p=>p[0]));
 const ymax=Math.ceil(Math.max(...se.map(p=>p[2]),...Z.ore.map(o=>Math.max(o.raff||0,o.stima||0)))/5)*5||5;
 const X=v=>L+(v-x0)/((x1-x0)||1)*(W-L-R),Y=v=>T+(ymax-v)/ymax*(H-T-B);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="font:10px sans-serif;fill:#64748b">`;
 s+=`<rect x="${X(D.fascia[0])}" y="${T}" width="${X(D.fascia[1])-X(D.fascia[0])}" height="${H-T-B}" fill="#dbe7f6" opacity=".6"/>`;
 for(let y=0;y<=ymax;y+=5)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="#e2e8f0"/><text x="4" y="${Y(y)+3}">${y}</text>`;
 [6,12].forEach(y=>s+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="${y===12?'#dc2626':'#ca8a04'}" stroke-dasharray="4 3" opacity=".7"/>`);
 for(let m=Math.ceil(x0/60)*60;m<=x1;m+=60)s+=`<text x="${X(m)-6}" y="${H-8}">${String(m/60).padStart(2,'0')}</text>`;
 s+=`<polyline fill="none" stroke="#94a3b8" stroke-width="1" points="${se.map(p=>X(p[0])+','+Y(p[2])).join(' ')}"/>`;
 s+=`<polyline fill="none" stroke="#1e3a6b" stroke-width="2" points="${se.map(p=>X(p[0])+','+Y(p[1])).join(' ')}"/>`;
 const pt=(k,c,r)=>Z.ore.forEach(o=>{if(o[k]==null)return;const m=+o.ora.slice(0,2)*60;s+=`<circle cx="${X(m)}" cy="${Y(o[k])}" r="${r}" fill="${c}"/>`;});
 const U=Z.ore.filter(o=>o.stima!=null&&o.d2!=null&&Math.abs(o.stima-o.d2)<0.05);
 pt('d2','#f59e0b',3.5);pt('ec','#7c3aed',3.5);pt('stima','#dc2626',4);
 U.forEach(o=>{const m=+o.ora.slice(0,2)*60;s+=`<circle class="uguali" cx="${X(m)}" cy="${Y(o.stima)}" r="6.5" fill="none" stroke="#f59e0b" stroke-width="2.5"/>`;});
 return h('div',{},h('div',{html:s+'</svg>'}),U.length?h('div',{cls:'nota',html:'<b style="color:#dc2626">●</b> con anello <b style="color:#f59e0b">arancione</b>: stima uguale a ICON-D2 (nessuna correzione '+(U.length===Z.ore.length?'in questa fascia':'in quelle ore')+').'}):null);}
D.zone.forEach(Z=>{const S=Z.sintesi,c=h('div',{cls:'card'},h('h2',{},'💨 '+Z.zona),h('div',{cls:'nota',style:'margin:-8px 0 10px'},'Misura: '+Z.stazione));
 if(S.sem_mis)c.append(h('div',{cls:'verd'},h('div',{cls:'dot '+S.sem_mis,html:'misurato<br>'+S.sem_mis}),h('div',{},
   `${Z.zona}: previsto ${S.sem_prev_zona||'–'}, misurato ${S.sem_mis} (colore più frequente nelle ore, stessa regola). Colore azzeccato in ${S.n?S.ok+' ore su '+S.n:'–'}. `
   +`Ore rosse misurate: ${S.rosse_mis.length?S.rosse_mis.join(', '):'nessuna'}; previste: ${S.rosse_prev.length?S.rosse_prev.join(', '):'nessuna'}. `
   +`Vento medio nella finestra ${n(S.media)} nodi, raffica di picco (5 min) ${n(S.raff)}. Verdetto della previsione (media delle zone): ${D.sem_prev}.`)));
 c.append(grafico(Z),h('div',{cls:'leg',html:'<span><i style="background:#1e3a6b"></i>misurato (5 min)</span><span><i style="background:#94a3b8"></i>raffica misurata</span><span style="color:#dc2626">● <b>stima</b></span><span style="color:#f59e0b">● <b>ICON-D2</b></span><span style="color:#7c3aed">● <b>ECMWF</b></span><span>fascia azzurra = finestra prevista · tratteggio: 6 e 12 nodi</span>'}));
 const t=h('table',{},h('tr',{},...['Ora','Misur.','Dir.','Stima','D2','2i','EC','AR','Raff. mis.','Raff. prev.'].map(x=>h('th',{},x))));
 Z.ore.forEach(o=>t.append(h('tr',{},h('td',{},o.ora),h('td',{},h('span',{cls:'pill '+sem(o.mis,o.raff_mis)},n(o.mis,0))),h('td',{cls:'dir'},o.dir_mis,h('div',{cls:'dnome'},o.nome_mis),h('div',{cls:'dprev'+(o.set_mis===o.set_prev?'':' fuori')},'prev. '+o.dir_prev,h('br'),o.nome_prev)),
  h('td',{},o.sem_prev?h('span',{cls:'pill '+o.sem_prev},n(o.stima,0)):n(o.stima,0)),h('td',{},n(o.d2)),h('td',{},n(o.i2)),h('td',{},n(o.ec)),h('td',{},n(o.ar)),h('td',{},n(o.raff_mis)),h('td',{},n(o.raff)))));
 c.append(h('h3',{},'Ora per ora (nodi)'),t,h('div',{cls:'nota'},'Colori: Misur. = semaforo misurato dell’ora (media oraria), Stima = semaforo previsto per la zona; stessa regola della previsione sul vento medio arrotondato all’intero: verde fino a '+D.verde_fino_a+' nodi, giallo da '+(D.verde_fino_a+1)+' a '+(D.rosso_da-1)+', rosso da '+D.rosso_da+'; le raffiche non cambiano il colore. Raff. prev. = raffica stimata (corretta). AR = AROME HD, in parallelo: non usato nella stima.'));
 const te=h('table',{},h('tr',{},h('th',{},'Modello'),h('th',{},'Errore medio'),h('th',{},'Errore assoluto')));
 Z.errori.forEach(e=>te.append(h('tr',{},h('td',{},e.nome),h('td',{cls:e.medio>0?'pos':'neg'},sg(e.medio)),h('td',{},n(e.ass)))));
 c.append(h('h3',{},'Errore dei modelli (previsto − misurato, nodi)'),te);app.append(c);});
if(D.onda.length){const cv=D.onda.some(o=>o.vento_prev!=null);
 const t=h('table',{},h('tr',{},...['Ora','Onda mis.','Onda prev.','Errore'].concat(cv?['Vento mis.','Vento D2','Dir. mis.']:[]).map(x=>h('th',{},x))));
 D.onda.forEach(o=>t.append(h('tr',{},h('td',{},o.ora),h('td',{},n(o.mis,2)+' m'),h('td',{},n(o.prev,2)+' m'),h('td',{},sg(o.prev-o.mis,2)+' m'),
  ...(cv?[h('td',{},n(o.vento_mis)),h('td',{},n(o.vento_prev)),h('td',{cls:'dir'},o.dir_mis_txt||o.dir_mis||'–',h('div',{cls:'dnome'},o.nome_mis||''))]:[]))));
 app.append(h('div',{cls:'card'},h('h2',{},'〰️ Onda in mare aperto'),t,h('div',{cls:'nota'},'Misurata: Piattaforma CNR (onda = altezza significativa; vento in nodi). Onda prevista: Open-Meteo Marine, cella più vicina alla costa: confronto indicativo. Vento previsto: ICON-D2 senza correzione sul punto della piattaforma.')));}
if(D.aria.length){const ta=h('table',{},h('tr',{},...['Ora','Piattaf. mis.','Mare aperto prev.','P. Cavalli mis.','Mestre prev.','Sull’acqua prev.'].map(x=>h('th',{},x))));
 D.aria.forEach(o=>ta.append(h('tr',{},h('td',{},o.ora),h('td',{},n(o.piatt_mis)),h('td',{},n(o.mare_prev)),h('td',{},n(o.cavalli_mis)),h('td',{},n(o.mestre_prev)),h('td',{},n(o.acqua_prev)))));
 const em=(p,m)=>{const s=D.aria.filter(o=>o[p]!=null&&o[m]!=null);return s.length?sg(Math.round(s.reduce((a,o)=>a+o[p]-o[m],0)/s.length*10)/10)+' °C':'–';};
 const te=h('table',{},h('tr',{},h('th',{},'Confronto (previsto − misurato)'),h('th',{},'Errore medio')),
  ...[['Mare aperto (ICON-D2) − Piattaforma','mare_prev','piatt_mis'],['Mestre (ICON-D2) − Palazzo Cavalli','mestre_prev','cavalli_mis'],['Sull’acqua Malamocco (ICON-D2) − Palazzo Cavalli','acqua_prev','cavalli_mis']].map(([t,p,m])=>h('tr',{},h('td',{},t),h('td',{},em(p,m)))));
 app.append(h('div',{cls:'card'},h('h2',{},'🌡️ Aria'),ta,te,h('div',{cls:'nota'},'°C. Misure CPSM: Piattaforma CNR (mare aperto, 8 miglia al largo) e Palazzo Cavalli (centro città, tetto a 15 m, ~12 km da Malamocco: probabilmente più caldo che in barca). Previsioni: ICON-D2 a 2 m. Nessuna misura dell’aria nelle zone di pesca: confronto indicativo.')));}
if((D.pioggia||[]).length){const PO=D.pioggia,N=PO.length,W=380,H=270,L=58,R=6,T=26,B=92,ec=PO.some(r=>r.prob_EC!=null);
 const colP=v=>v==null?['#f1f5f9','#64748b']:v<20?['#e6f1fb','#0c447c']:v<40?['#b5d4f4','#0c447c']:v<60?['#85b7eb','#042c53']:v<80?['#378add','#fff']:['#185fa5','#fff'];
 const bw=Math.min(56,(W-L-R)/N*0.55),X=i=>L+(i+0.5)*(W-L-R)/N,Y=v=>T+(100-v)/100*(H-T-B),mm=x=>x==null?'–':n(x);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="font:11px sans-serif;fill:#64748b">`;
 [0,50,100].forEach(v=>s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${v?'#e2e8f0':'#c3c2b7'}"${v===50?' stroke-dasharray="4 3"':''}/><text x="2" y="${Y(v)+4}">${v}%</text>`);
 const OS=PO.some(r=>r.oss!=null);PO.forEach((r,i)=>{const v=r.prob_D2,c=colP(v),x=X(i),pv=OS?!!r.oss:(r.cavalli_mm!=null&&r.cavalli_mm>=0.2);
  if(v!=null)s+=`<rect x="${x-bw/2}" y="${Y(v)}" width="${bw}" height="${Math.max(1,Y(0)-Y(v))}" rx="4" fill="${c[0]}" stroke="#85b7eb" stroke-width=".5"/><text x="${x}" y="${Y(v)-6}" text-anchor="middle" style="font-weight:700;fill:#1e293b;font-size:12px">${v}%</text>`;
  if(ec&&r.prob_EC!=null)s+=`<line x1="${x-bw/2-4}" x2="${x+bw/2+4}" y1="${Y(r.prob_EC)}" y2="${Y(r.prob_EC)}" stroke="#7c3aed" stroke-width="2"/>`;
  s+=`<text x="${x}" y="${Y(0)+15}" text-anchor="middle" style="fill:#1e293b;font-weight:700">${r.ora}</text>`;
  s+=`<rect x="${x-24}" y="${Y(0)+22}" width="48" height="17" rx="8" fill="${pv?'#dbeafe':'#f1f5f9'}"/><text x="${x}" y="${Y(0)+34}" text-anchor="middle" style="font-size:11px;font-weight:700;fill:${pv?'#1e40af':'#64748b'}">${pv?'pioggia':'asciutto'}</text>`;
  [r.mm_D2,r.cavalli_mm,r.piatt_mm].forEach((val,j)=>s+=`<text x="${x}" y="${Y(0)+54+j*13}" text-anchor="middle" style="font-size:11px">${mm(val)}</text>`);});
 s+=`<text x="2" y="${Y(0)+34}" style="font-size:11px">${OS?'In barca':'Cavalli'}</text><text x="2" y="${Y(0)+54}" style="font-size:11px">mm prev.</text><text x="2" y="${Y(0)+67}" style="font-size:11px">Cavalli</text><text x="2" y="${Y(0)+80}" style="font-size:11px">Piattaf.</text></svg>`;
 app.append(h('div',{cls:'card'},h('h2',{},'🌧 Pioggia'),h('div',{html:s}),h('div',{cls:'nota'},'Barre: probabilità ICON-D2 di più di 0,1 mm'+(ec?'; linea viola: ECMWF':'')+'. Etichetta: '+(OS?'osservazione in barca (riga PIOGGIA nelle note dell’uscita): riferimento per le zone di pesca.':'misura a Palazzo Cavalli, "pioggia" da 0,2 mm (un solo scatto del pluviometro).')+' Sotto: mm previsti da ICON-D2 e misurati a Palazzo Cavalli e alla Piattaforma CNR. Ogni ora = ora precedente (08:00 = dalle 07 alle 08). Palazzo Cavalli è a ~12 km dalle zone di pesca: confronto indicativo; la pioviggine sotto 0,2 mm/h non viene registrata.')));}
if((D.nebbia||[]).length){const TX=['non probabile','poco probabile','probabile'],CO=['#e2e8f0','#facc15','#475569'],TC=['#334155','#334155','#fff'];   // v1.28
 const vis=m=>m==null?'–':m>=10000?'oltre 10 km':m>=1000?n(m/1000,1)+' km':m+' m',vc=m=>m==null?'':m<=200?'font-weight:700;color:#7f1d1d':m<=1000?'font-weight:700':'';
 const tn=h('table',{},h('tr',{},h('th',{},'Ora'),h('th',{},'Nebbia prevista'),h('th',{},'Visibilità Tessera'),h('th',{},'Fenomeni')));
 D.nebbia.forEach(r=>tn.append(h('tr',{},h('td',{},r.ora),h('td',{},h('span',{style:'display:inline-block;padding:1px 8px;border-radius:9px;background:'+CO[r.cat]+';color:'+TC[r.cat]},TX[r.cat])),h('td',{style:vc(r.vis_m)},vis(r.vis_m)),h('td',{},r.wx||''))));
 app.append(h('div',{cls:'card'},h('h2',{},'🌫 Nebbia'),tn,h('div',{cls:'nota'},'Nebbia prevista: scenari dei modelli (a parole, come nel sito). Visibilità: minima dei bollettini METAR di Venezia Tessera tra mezz’ora prima e mezz’ora dopo (sotto 1000 m = nebbia). Tessera è sul bordo nord della laguna, a 15–35 km dalle zone: confronto indicativo, nessun punteggio. FG nebbia, BR foschia, BCFG banchi, RA pioggia.')));}
if(D.liv.length){const hp=D.liv.some(l=>l.prev!=null),sg=v=>(v>0?'+':'')+v+' cm';
 const tl=h('table',{},h('tr',{},h('th',{},'Ora'),h('th',{},'Misurato'),h('th',{},'Astronomica'),h('th',{},'Scarto astr.'),...(hp?[h('th',{},'Previsto ('+D.liv_fonte+')'),h('th',{},'Scarto prev.')]:[])));
 D.liv.forEach(l=>tl.append(h('tr',{},h('td',{},l.ora),h('td',{},l.mis+' cm'),h('td',{},l.astro+' cm'),h('td',{},sg(l.scarto)),...(hp?[h('td',{},l.prev!=null?l.prev+' cm':'–'),h('td',{},l.scarto_prev!=null?sg(l.scarto_prev):'–')]:[]))));
 app.append(h('div',{cls:'card'},h('h2',{},'🌊 Livello · Malamocco Porto'),tl,h('div',{cls:'nota'},'Scarto astr. = misurato − astronomica; Scarto prev. = previsto − misurato. Astronomica CPSM di Punta Salute: lo scarto include la differenza tra le stazioni.'+(D.liv_eta_h!=null?' Bollettino CPSM di '+D.liv_eta_h+' h prima della fine della finestra.':''))));}
if(D.marea_zona){const Z=D.marea_zona,E=Z.estremo,sc=x=>(x>0?'+':'')+String(x).replace('.',','),tm=t=>(t==null?'–':(t>0?'+':'')+t+' min');   // v1.25
 const tz=h('table',{},h('tr',{},h('th',{},''),h('th',{},'Curva della zona'),h('th',{},'Punta Salute non corretta')));
 if(E)tz.append(h('tr',{},h('td',{},'Ora '+(E.tipo==='alta'?"dell'alta":'della bassa')),h('td',{},E.prev_ora+' · misurata '+E.mis_ora+' · errore '+tm(E.err_min)),h('td',{},E.ps_ora?E.ps_ora+' · errore '+tm(E.err_ps_min):'–')));
 if(E)tz.append(h('tr',{},h('td',{},'Livello '+(E.tipo==='alta'?"dell'alta":'della bassa')),h('td',{},E.prev_cm+' cm · misurato '+E.mis_cm+' cm'),h('td',{},'')));
 tz.append(h('tr',{},h('td',{},'Livello nella finestra'),h('td',{},'errore medio '+sc(Z.liv_err_medio)+' cm · medio assoluto '+String(Z.liv_err_ass).replace('.',',')+' cm'),h('td',{},'errore medio '+sc(Z.liv_err_medio_ps)+' cm · medio assoluto '+String(Z.liv_err_ass_ps).replace('.',',')+' cm')));
 const cp=Z.cpsm_err_ass==null?null:h('div',{cls:'nota'},'Previsione CPSM controllata a Punta Salute, stessa finestra: errore medio '+sc(Z.cpsm_err_medio)+' cm · medio assoluto '+String(Z.cpsm_err_ass).replace('.',',')+' cm.');   // v1.34
 app.append(h('div',{cls:'card'},h('h2',{},'🌊 Marea della zona · '+Z.stazione),tz,...(cp?[cp]:[]),h('div',{cls:'nota'},'Errore = previsto − misurato. Curva della zona: '+Z.fonte+' spostata con ritardo e quota della stazione (coefficienti v'+(Z.config||'–')+', archivio CPSM). Misurato: livello ogni 5 minuti, ora dell’estremo dalla media mobile di 35 minuti. Con maree piccole il colmo è piatto e l’ora è poco precisa.')));}
const AN=D.andamento;
function graf(righe,serie,tipo){const W=520,H=200,L=30,R=10,T=12,B=26,N=righe.length;if(!N)return null;
 const v=righe.flatMap(r=>serie.map(s=>r[s[0]]));const ym=Math.max(1,Math.ceil(Math.max(...v.map(Math.abs))));const y0=tipo==='ass'?0:-ym;
 const X=i=>L+28+(N>1?i/(N-1):.5)*(W-L-R-56),Y=x=>T+(ym-x)/(ym-y0)*(H-T-B);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="font:10px sans-serif;fill:#64748b">`;
 for(let y=y0;y<=ym;y+=Math.max(1,Math.round(ym/4)))s+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="${y===0?'#64748b':'#e2e8f0'}"/><text x="2" y="${Y(y)+3}">${y>0&&tipo!=='ass'?'+':''}${y}</text>`;
 righe.forEach((r,i)=>s+=`<text x="${X(i)-12}" y="${H-8}">${r.k}</text>`);
 if(tipo==='barre'){const bw=Math.min(26,(W-L-R)/N/3);righe.forEach((r,i)=>serie.forEach(([k,c],j)=>{const x=X(i)-bw*1.05+j*bw*1.1,y=Y(Math.max(0,r[k])),hh=Math.abs(Y(r[k])-Y(0));s+=`<rect x="${x}" y="${y}" width="${bw}" height="${hh}" fill="${c}"/>`;}));}
 else serie.forEach(([k,c,w])=>{s+=`<polyline fill="none" stroke="${c}" stroke-width="${w}" points="${righe.map((r,i)=>X(i)+','+Y(r[k])).join(' ')}"/>`;righe.forEach((r,i)=>s+=`<circle cx="${X(i)}" cy="${Y(r[k])}" r="3.5" fill="${c}"/>`);});
 return h('div',{html:s+'</svg>'});}
function tabAn(righe,col){const t=h('table',{},h('tr',{},...[col,'Ore','Stima','D2','|Stima|','|D2|'].map(x=>h('th',{},x))));
 righe.forEach(r=>t.append(h('tr',{},h('td',{},r.k),h('td',{},r.n),h('td',{cls:r.stima>0?'pos':'neg'},sg(r.stima)),h('td',{cls:r.d2>0?'pos':'neg'},sg(r.d2)),h('td',{},n(r.ass_stima)),h('td',{},n(r.ass_d2)))));return t;}
const LEGA='<span><i style="background:#dc2626;height:4px"></i><b>stima</b> (previsione corretta)</span><span><i style="background:#f59e0b"></i>ICON-D2 senza correzione</span>';
if(AN&&AN.giorni&&AN.giorni.length){
 app.append(h('div',{cls:'card'},h('h2',{},'📈 Andamento giorno per giorno · '+AN.zona),
  h('h3',{},'Errore assoluto medio (nodi): più basso = previsione migliore'),graf(AN.giorni,[['ass_stima','#dc2626',2.6],['ass_d2','#f59e0b',1.8]],'ass'),h('div',{cls:'leg',html:LEGA}),
  h('h3',{},'Errore medio con segno (nodi): sopra zero previsione troppo alta, sotto troppo bassa'),graf(AN.giorni,[['d2','#f59e0b',3.6],['stima','#dc2626',1.8]],'segno'),h('div',{cls:'leg',html:LEGA}),
  tabAn(AN.giorni,'Giorno'),
  AN.tot?h('div',{cls:'nota'},`Tutte le ${AN.tot.n} ore: errore assoluto medio stima ${n(AN.tot.ass_stima)} nodi, ICON-D2 ${n(AN.tot.ass_d2)} nodi. Misura: ${AN.stazione}.`):null));
 app.append(h('div',{cls:'card'},h('h2',{},'🕐 Errore per ora del mattino'),
  h('h3',{},'Errore medio con segno (nodi), tutte le verifiche'),graf(AN.ore,[['stima','#dc2626'],['d2','#f59e0b']],'barre'),h('div',{cls:'leg',html:LEGA}),
  tabAn(AN.ore,'Ora'),h('div',{cls:'nota'},'Ogni ora = media da −30 a +30 minuti. Le ore con poche verifiche (colonna Ore) sono poco affidabili.')));}
app.append(h('div',{cls:'card'},h('h2',{},'📚 Archivio verifiche'),h('div',{cls:'arch'},D.archivio.join('\n')||'–')));
const cr=h('div',{cls:'card'},h('h2',{},'🧭 Note'));D.note.forEach(r=>cr.append(h('div',{cls:'r'},h('span',{cls:'tag '+r.tipo},r.tipo),h('div',{},r.testo))));app.append(cr);
app.append(h('div',{cls:'fonti'},D.fonti+' · generato '+D.generato));
const st=document.createElement('style');st.textContent=`@page{size:560px ${document.documentElement.scrollHeight+4}px;margin:0}`;document.head.append(st);
</script></body></html>"""


def main():
    cartella = os.path.dirname(os.path.abspath(__file__))
    hd = sys.argv[sys.argv.index("--html-dir") + 1] if "--html-dir" in sys.argv else None
    prov = "--provvisoria" in sys.argv
    if "--andamento" in sys.argv:
        p = report_andamento(cartella)
        print(f"fd_verifica v{VERSIONE}: andamento -> {os.path.basename(p)}")
        if shutil.which("open"):
            subprocess.run(["open", p])
        return
    if "--rifai" in sys.argv:                  # v1.16: rifà la verifica di un giorno (le misure CPSM coprono 24 h)
        g = sys.argv[sys.argv.index("--rifai") + 1]
        A = carica_archivio(cartella)
        n0 = len(A["previsioni"])
        A["previsioni"] = [x for x in A["previsioni"] if not x["chiave"].startswith(g)]
        salva_archivio(A, cartella)
        print(f"fd_verifica v{VERSIONE}: {n0 - len(A['previsioni'])} previsioni del {g} tolte dall'elenco delle verificate, le rifaccio")
    righe = verifica_pendenti(cartella, html_dir=hd, provvisoria=prov)
    for r in righe:
        print(r)
    if prov and shutil.which("open"):
        rep = [r.split("report: ", 1)[1] for r in righe if "report: " in r]
        if rep:
            subprocess.run(["open", os.path.join(cartella, CARTELLA, rep[-1])])


if __name__ == "__main__":
    main()
