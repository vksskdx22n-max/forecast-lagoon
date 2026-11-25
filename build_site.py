#!/usr/bin/env python3
"""build_site.py v3.17 - prepara la cartella site/ per GitHub Pages.

Legge Previsioni/previsione_*.json e Previsioni/verifica_*.json, copia le pagine HTML/PDF in site/giorni/,
copia assets/ e scrive site/index.json con le tre zone (Laguna Nord, Centro, Sud) e, per ogni giorno,
la sintesi calcolata qui (semaforo ora per ora, vento, raffiche, direzione prevalente, marea nella finestra,
corrente alla bocca). Nessun valore e' scritto a mano: la pagina index.html legge index.json.

Zona di una previsione: campo "area" del JSON (nord / centro / sud); i JSON senza "area" sono della Laguna Centro.
"""
import os, re, json, glob, shutil, hashlib, math, datetime as dt
from collections import Counter

VERSIONE = "3.75"   # v3.75: verifiche collegate dal contenuto (previsione verificata), stato "in arrivo / non disponibile"; v3.74: puntino giallo = acqua misurata, curva = previsione; v3.73: riga della marea con livello misurato e previsto di adesso; v3.72: livello misurato in una riga sopra il grafico della zona; v3.71: tolto il cerchietto bianco (livello misurato) dai grafici della marea; v3.70: basse maree in verde lime fluo (prima lilla, poco visibile su iPhone); v3.69: marea, alte in bianco con ▲ e basse in lilla con ▼; v3.68: rientrando nell'app si torna sempre alla prima pagina; v3.67: pagina zona, prima tutto il vento (semaforo, ora per ora, grafico, vento medio), poi la marea; v3.66: pagina zona, grafico del vento sotto la tabella (misurato ultime 3 ore + stima prossime 8) al posto di "Vento adesso"; v3.65: in prima pagina il vento incerto solo se la forbice cambia il colore; marea: passato in grigio, fascia sulle ore; v3.64: fasce 05-12, 12-18, 18-24; orari separati di vento e marea; marea sulle 24 ore (pagina zona); nuova prima pagina; v3.63: nel mini-grafico marea il numero dei cm resta sempre centrato sopra il colmo anche quando la linea "adesso" e' vicina (prima, v3.54, si scostava di lato: col numero centrato della v3.62 il Sud finiva a destra mentre Nord/Centro centrati); v3.62: nel mini-grafico marea i cm sono centrati SOPRA il puntino del colmo (prima ancorati a lato: vicino al bordo uscivano a sinistra del colmo, confondendo); didascalia solo "alta HH:MM"; v3.61: nel mini-grafico marea, se la finestra contiene sia un'alta sia una bassa si segna l'alta (prima il primo estremo in ordine di tempo: il Nord, per il ritardo di Saline, segnava la bassa mentre Centro/Sud l'alta); stessa scelta in "Prossimi giorni"; istruzioni con la nota sul ritardo per zona;v3.60: mini-grafico marea, il pallino alta/bassa sul colmo/cavo reale della curva (prima all'ora del bollettino, finiva sulla salita); v3.59: scritte dei nodi del vento sulla cartina piu' grandi (47, prima 37); protezione all'avvio (ritentativo + tasto Ricarica invece di restare su "Caricamento…"); v3.58: banner ADESSO e PREVISIONE più grandi/evidenti, linea di stacco teal marcata; cartina ridotta per non far scrollare fino a Laguna Sud; v3.57: home divisa in due blocchi dichiarati (cartina = ADESSO/misurato; sotto = PREVISIONE, con stacco e pillola); "Previsione" davanti ad "aggiornata alle"; fonti spostate nelle istruzioni, in fondo solo il rimando; v3.56: sulla cartina il vento misurato vale fino a 45 min (prima 20), con l'ora mostrata se vecchio; allerte invariate a 20 min; v3.55: "Prossimi giorni" su riga propria piu' in basso; zona aperta da Prossimi giorni torna a Prossimi giorni (tasto "‹ Prossimi giorni", stessa fascia); v3.54: numero dei cm della marea con contorno scuro e spostato dalla linea "adesso"; "8 kn" invece di "8-8 kn"; Prossimi giorni con domani in cima (previsione vera, cliccabile); v3.53: fascia proposta = quella in corso (se finisce tra piu' di un'ora, altrimenti la prossima); scelta azzerata tornando all'app dopo 2 minuti; v3.52: la cartina della pagina della zona ha il colore del giorno e della fascia scelti; v3.51: allerte vento fino alle 24; v3.50: tre fasce anche nei Prossimi giorni; v3.49: tre fasce della giornata (05-12, 13-18, 19-24) con i bottoni; PDF sintetico
AUTORE = "Ennio Tezze"      # v3.10: mostrato in alto accanto alla versione e in fondo alla pagina
CARTELLA = "Previsioni"
SITO = "site"
ZONE = [
    {"id": "nord", "nome": "Laguna Nord", "bocca": "Lido", "bacino": "bacino di Lido", "tessera": "bordo nord della laguna"},   # v3.26: dove sta Tessera rispetto alla zona
    {"id": "centro", "nome": "Laguna Centro", "bocca": "Malamocco", "bacino": "bacino di Malamocco", "tessera": "circa 15 km"},
    {"id": "sud", "nome": "Laguna Sud", "bocca": "Chioggia", "bacino": "bacino di Chioggia", "tessera": "circa 35 km: indicativa"},
]
ALIAS_AREA = {}            # eventuali nomi di area diversi dagli id delle zone
MARGINE_MAREA_MIN = 60
ORA_DEFINITIVA = 14          # v3.2: previsione generata prima di quest'ora (italiana) = provvisoria del mattino (stessa soglia del workflow)
ADESSO_URL = "https://fd-cpsm-proxy.ennioweb.workers.dev/adesso"   # v3.13: vento misurato adesso, calcolato dal Worker v2.4
# v3.17: allerte vento (Worker v2.5). Il Worker legge questi parametri da index.json: unica fonte delle soglie.
ALLERTA = {"url": "https://fd-cpsm-proxy.ennioweb.workers.dev/allerta",
           "raffica_kn": 25,          # raffica minima in una stazione
           "salita_kn": 8,            # aumento minimo del vento medio ...
           "finestra_min": 15,        # ... in questi minuti
           "pausa_min": 60,           # stessa stazione: al massimo un avviso ogni 60 minuti
           "pausa_globale_min": 30,   # dopo un avviso, 30 minuti senza altri messaggi (stesso fronte)
           "misura_max_min": 20,      # misure piu' vecchie non fanno scattare l'avviso
           "ora_inizio": 5, "ora_fine": 24,        # ora italiana (v3.51: fino alle 24, Worker >= 2.16 con il controllo esteso)
           "controllo_min": 5,        # frequenza del controllo (cron del Worker)
           "rotazione_gradi": 90, "rotazione_min_kn": 8}   # pre-allarme sperimentale: solo registrato, non inviato
# v3.26: visibilita' misurata adesso a Venezia Tessera (Worker v2.6, /adesso). Si mostra solo sotto mostra_sotto_m e se il METAR
# non e' piu' vecchio di max_min. Tre livelli scelti da Ennio (21/11): fino a 200 m fitta, fino a 500 m nebbia, fino a 1000 m leggera (1000 compreso dal v3.29).
# v3.29: si mostra anche sopra i 1000 m se il bollettino segnala nebbia (FG, BCFG banchi, PRFG, MIFG, VCFG): "Banchi di nebbia".
VISIBILITA = {"mostra_fino_a_m": 1000, "fitta_fino_a_m": 200, "nebbia_fino_a_m": 500, "max_min": 60}
ORA_MATTINO = "08:30"        # v3.3: ora indicativa della previsione provvisoria (giro Cloudflare delle 08:25), per le istruzioni
ORA_AGGIORNAMENTO = "16:30"  # v3.2: ora indicativa della previsione definitiva (giro Cloudflare delle 16:25), mostrata accanto alla provvisoria
ORA_ATTESA = "17:30"        # v3.0: ora (italiana) entro cui ci si aspetta la previsione di domani; dopo, il sito avvisa del ritardo
ORE_GIRI = ["05:30", "08:30", "11:15", "16:30", "18:30"]   # v3.64: ore indicative degli aggiornamenti del vento (per le istruzioni)
CARD = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]     # minuti di curva di marea mostrati prima e dopo la finestra


def leggi(percorso):
    try:
        return json.load(open(percorso, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def data_da_nome(nome):
    m = re.search(r"_(\d\d)-(\d\d)-(\d{4})_", nome)
    return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def ora_mod(p):
    return dt.datetime.fromtimestamp(os.path.getmtime(p), dt.timezone.utc)


def scegli_ultimo(file):
    """Tra piu' file dello stesso giorno, finestra e zona (versioni diverse) tiene il piu' recente."""
    return max(file, key=lambda p: (ora_mod(p), p))


def zona_di(P):
    a = (P or {}).get("area") or "centro"
    return ALIAS_AREA.get(a, a)


def provvisoria(P, giorno=None):
    """v3.2: True se la previsione e' stata generata prima delle ORA_DEFINITIVA (giro del mattino).
    v3.49: solo per i giorni successivi: le fasce di oggi aggiornate al mattino non hanno una "definitiva" dopo."""
    try:
        g = dt.datetime.strptime(P.get("generato") or "", "%d/%m/%Y %H:%M")
    except ValueError:
        return False
    return g.hour < ORA_DEFINITIVA and (giorno is None or g.date() < giorno)


def quando(p):
    """v3.64: ordine tra le previsioni della stessa fascia: ultimo aggiornamento (vento o marea), versione, aggiornamento in corso."""
    P = leggi(p) or {}
    t = [dt.datetime.min]
    for x in (P.get("generato"), (P.get("marea") or {}).get("aggiornata")):
        try:
            t.append(dt.datetime.strptime(x, "%d/%m/%Y %H:%M"))
        except (TypeError, ValueError):
            pass
    return (max(t), P.get("versione") or "", p.endswith("_agg.json"), p)


def sintesi_pdf(giorno, zona):
    """v3.49: PDF sintetico del giorno e della zona (fd_previsione >= 1.50, tre fasce in una pagina), il piu' recente."""
    f = glob.glob(os.path.join(CARTELLA, f"previsione_{giorno.strftime('%d-%m-%Y')}_sintesi{'' if zona == 'centro' else '_' + zona}_v*.pdf"))
    return max(f, key=lambda p: (ora_mod(p), p)) if f else None


def chiave_file(p):
    """(data, finestra, zona) dal nome del file e dal contenuto."""
    base = os.path.basename(p)
    d = data_da_nome(base)
    m = re.search(r"_(\d{4}-\d{4})", base)
    return (d, m.group(1) if m else None)


import marea_propaga as MP   # v3.35: marea della zona (modulo comune con fd_previsione e fd_verifica)


def ore_cambio_colore(ore):
    """v3.65: ore incerte in cui i due estremi della forbice dei modelli hanno colori diversi del semaforo."""
    try:
        import fd_previsione as F
        return [o.get("ora") for o in ore if o.get("incerta") and o.get("forbice")
                and F.semaforo(o["forbice"][0], None) != F.semaforo(o["forbice"][1], None)]
    except Exception as e:
        print(f"build_site: cambio di colore non calcolato ({e})")
        return []


def sintesi(P, giorno):
    """Valori per la pagina della zona, tutti presi dal JSON della previsione."""
    s = {}
    st = P.get("striscia") or {}
    ore = st.get("ore") or []
    s["ore"] = [{"ora": o.get("ora"), "sem": o.get("sem"), "stima": o.get("stima"), "raff": o.get("raff"),
                 "marea": o.get("marea"), "corr": o.get("corr"), "verso": o.get("verso"),
                 "incerta": bool(o.get("incerta")), "forbice": o.get("forbice")} for o in ore]
    # v3.48: ore con previsione incerta (fd_previsione >= 1.48: modelli in disaccordo)
    s["incerta"] = [o.get("ora") for o in ore if o.get("incerta")]
    s["incerta_colore"] = ore_cambio_colore(ore)   # v3.65: in prima pagina solo se la forbice cambia il semaforo
    stime = [o["stima"] for o in ore if isinstance(o.get("stima"), (int, float))]
    raff = [o["raff"] for o in ore if isinstance(o.get("raff"), (int, float))]
    s["vento_min"] = min(stime) if stime else None
    s["vento_max"] = max(stime) if stime else None
    s["raff_max"] = max(raff) if raff else None
    s["alba"], s["tramonto"] = st.get("alba"), st.get("tramonto")
    s["canale"] = st.get("canale")
    # direzione prevalente: la piu' frequente tra tutte le ore di tutte le zone di calcolo
    dirs = Counter()
    for righe in (P.get("vento") or {}).values():
        for r in righe:
            if r.get("dir"):
                dirs[(r.get("dir").split(" ", 1)[-1], r.get("nome"))] += 1
    if dirs:
        (sigla, nome), _ = dirs.most_common(1)[0]
        s["direzione"] = sigla
        s["direzione_nome"] = nome
    # corrente alla bocca: tratti consecutivi con lo stesso verso
    tratti = []
    for o in ore:
        v = o.get("verso")
        if not v:
            continue
        if tratti and tratti[-1]["verso"] == v:
            tratti[-1]["a"] = o.get("ora")
        else:
            tratti.append({"verso": v, "da": o.get("ora"), "a": o.get("ora")})
    s["corrente"] = tratti
    # marea: curve nella finestra +/- margine, estremali della finestra
    M = P.get("marea") or {}
    fascia = M.get("fascia")
    if fascia:
        a, b = fascia[0] - MARGINE_MAREA_MIN, fascia[1] + MARGINE_MAREA_MIN
        s["marea_fascia"] = fascia
        s["marea_curva"] = [p for p in (M.get("curva_cpsm") or M.get("curva") or []) if a <= p[0] <= b]
        s["marea_astro"] = [p for p in (M.get("curva") or []) if a <= p[0] <= b] if M.get("curva_cpsm") else []
    s["marea_fonte"] = M.get("fonte_livello")
    s["marea_eta_h"] = M.get("cpsm_eta_h")
    s["marea_vecchia"] = bool(M.get("cpsm_vecchia"))
    est = []
    for e in (M.get("cpsm") or M.get("estremali") or []):
        m = re.match(r"(\d\d)/(\d\d) (\d\d):(\d\d)", e.get("ora") or "")
        if not m or e.get("cm") is None or (int(m.group(1)), int(m.group(2))) != (giorno.day, giorno.month):
            continue
        minuti = int(m.group(3)) * 60 + int(m.group(4))
        if fascia and fascia[0] - MARGINE_MAREA_MIN <= minuti <= fascia[1] + MARGINE_MAREA_MIN:
            est.append({"tipo": e.get("tipo"), "ora": e["ora"][-5:], "cm": e["cm"], "min": minuti})
    s["marea_estremali"] = est
    # pioggia ora per ora (mm nell'ora e probabilita' per modello) e totali della finestra
    Me = P.get("meteo") or {}
    po = Me.get("pioggia_ore") or []
    s["pioggia_ore"] = po
    tot = {}
    for mod in ("D2", "2i", "EC"):
        val = [r.get("mm_" + mod) for r in po if isinstance(r.get("mm_" + mod), (int, float))]
        tot[mod] = round(sum(val), 1) if val else None
    s["pioggia_tot"] = tot
    prob = [r.get(k) for r in po for k in ("prob_D2", "prob_EC") if isinstance(r.get(k), (int, float))]
    s["pioggia_prob_max"] = max(prob) if prob else None
    s["pioggia_max_oraria"] = Me.get("pioggia")
    # v3.20: rischio nebbia stimato (fd_previsione >= 1.38): sintesi e ore
    s["nebbia"] = Me.get("nebbia")
    s["nebbia_ore"] = Me.get("nebbia_ore") or []
    # v2.2: ora per ora (direzione media vettoriale delle zone, onda in laguna, probabilita' di pioggia), meteo, onda, note
    vento = P.get("vento") or {}
    zone_v = [z for z in (st.get("zone") or list(vento)) if z in vento]
    ol = {r.get("ora", "")[:2]: r.get("v") or {} for r in ((P.get("onda_laguna") or {}).get("ore") or [])}
    pr = {r.get("ora", "")[:2]: r for r in po}
    mare = {r.get("ora", "")[:2]: r for r in ((P.get("mare") or {}).get("righe") or [])}
    det = []
    for o in ore:
        h = o.get("ora")
        dirs = [vento[z][i]["dir_g"] for z in zone_v for i in range(len(vento[z])) if vento[z][i].get("ora", "")[:2] == h and vento[z][i].get("dir_g") is not None]
        dg = None
        if dirs:
            x = sum(math.sin(math.radians(d)) for d in dirs); y = sum(math.cos(math.radians(d)) for d in dirs)
            dg = round(math.degrees(math.atan2(x, y)) % 360)
        onde = [v[0] for v in (ol.get(h) or {}).values() if v and v[0] is not None]
        p = pr.get(h) or {}
        pp = p.get("prob_D2") if p.get("prob_D2") is not None else p.get("prob_EC")
        m = mare.get(h) or {}
        det.append({"ora": h, "sem": o.get("sem"), "stima": o.get("stima"), "raff": o.get("raff"), "dir_g": dg,
                    "dir": CARD[int((dg + 11.25) % 360 // 22.5)] if dg is not None else None,
                    "marea": o.get("marea"), "cm_h": o.get("cm_h"), "corr": o.get("corr"), "verso": o.get("verso"),
                    "onda_lag": max(onde) if onde else None, "pioggia_prob": pp, "pioggia_mm": p.get("mm_D2"),
                    "onda_mare": m.get("h_corr", m.get("h")), "incerta": bool(o.get("incerta")), "forbice": o.get("forbice")})
    s["ore_dettaglio"] = det
    s["acqua"] = P.get("acqua_misurata")           # v3.5: temperatura dell'acqua misurata (fd_previsione >= 1.36)
    s["meteo"] = {k: Me.get(k) for k in ("aria", "aria_acqua", "aria_punto", "pressione", "nuvolosita", "luna", "fonte_meteo")}
    onde_l = [d["onda_lag"] for d in det if d["onda_lag"] is not None]
    onde_m = [d["onda_mare"] for d in det if d["onda_mare"] is not None]
    s["onda_laguna_max"] = max(onde_l) if onde_l else None
    s["onda_mare_min"], s["onda_mare_max"] = (min(onde_m), max(onde_m)) if onde_m else (None, None)
    # v3.6: onda in mare aperto in centimetri interi per la pagina (un solo numero se minimo e massimo coincidono)
    s["onda_mare_cm"] = sorted({round(min(onde_m) * 100), round(max(onde_m) * 100)}) if onde_m else None
    s["note"] = [r.get("testo") for r in (P.get("riflessioni") or []) if r.get("tipo") in ("inferenza", "dato")][:3]
    s["verdetto_testo"] = (P.get("verdetto") or {}).get("testo")
    V = P.get("verdetto") or {}
    s["andamento"] = V.get("andamento_txt")
    s["andamento_fasce"] = V.get("andamento")
    return s


BARRA = """<div id="fl-barra" style="position:sticky;top:0;z-index:9999;display:flex;justify-content:space-between;align-items:center;gap:10px;
padding:calc(env(safe-area-inset-top) + 10px) 14px 10px;background:#06131d;color:#eaf3f7;font:600 15px -apple-system,system-ui,sans-serif">
<a href="../index.html?v=__CODICE__#zona/__ZONA__" style="color:#5fd4d0;text-decoration:none">&lsaquo; Torna a Forecast Lagoon</a>__PDF__</div>"""


# v2.9: sul telefono (app sulla schermata Home) il PDF non si apre dentro l'app, dove non c'e' modo di tornare indietro:
# si apre il menu Condividi (salva in File, WhatsApp, stampa). Il file e' scaricato all'apertura della pagina, cosi' il menu
# parte subito al tocco. Sul computer, o se Condividi non e' disponibile, il link apre il PDF in una nuova scheda.
CONDIVIDI = """<script>(function(){var a=document.getElementById('fl-pdf');if(!a||!navigator.canShare)return;
var tel=matchMedia('(pointer:coarse)').matches||navigator.standalone;if(!tel)return;a.innerHTML='Condividi PDF';var f=null;
fetch(a.href).then(function(r){return r.blob()}).then(function(b){var x=new File([b],a.getAttribute('href'),{type:'application/pdf'});
if(navigator.canShare({files:[x]}))f=x}).catch(function(){});
a.addEventListener('click',function(e){if(!f)return;e.preventDefault();navigator.share({files:[f],title:document.title}).catch(function(){})})})();</script>"""


def codice_sito():
    """Impronta del codice della pagina (cambia a ogni nuova versione): usata per non far riusare copie vecchie."""
    html = open("index.html", encoding="utf-8").read()
    return hashlib.sha1((html + VERSIONE).encode("utf-8")).hexdigest()[:12]


def copia_pagina(sorgente, destinazione, zona, giorno, pdf=None):
    """v2.3: copia la pagina di previsione/verifica aggiungendo in cima la barra per tornare all'app (e il link al PDF)."""
    html = open(sorgente, encoding="utf-8").read()
    link_pdf = (f'<a id="fl-pdf" href="{os.path.basename(pdf)}" target="_blank" rel="noopener" style="color:#cfe0e8;text-decoration:none;'
                f'font-weight:500;font-size:14px">PDF &#8599;</a>' + CONDIVIDI) if pdf else ""
    barra = BARRA.replace("__CODICE__", codice_sito()).replace("__ZONA__", zona).replace("__DATA__", giorno.isoformat()).replace("__PDF__", link_pdf)
    if "<meta name=\"viewport\"" not in html:
        html = html.replace("<head>", "<head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">", 1)
    html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + barra, html, count=1) if re.search(r"<body[^>]*>", html) else barra + html
    open(destinazione, "w", encoding="utf-8").write(html)


def regole():
    """Soglie e parametri mostrati nelle istruzioni del sito, letti da fd_previsione (nessun numero scritto a mano)."""
    try:
        import fd_previsione as F
        return {"verde_fino_a": F.VERDE_FINO_A, "rosso_da": F.ROSSO_DA, "cpsm_ore_max": F.CPSM_ORE_MAX,      # v3.3: semaforo su nodi interi
                "raff_avviso": F.RAFF_AVVISO, "versione_previsione": F.VERSIONE}
    except Exception as e:          # il sito si costruisce lo stesso; le istruzioni omettono i numeri
        print(f"build_site: regole non lette da fd_previsione ({e})")
        return {}


SOGLIE_MAREA = [110, 140]      # v3.64: cm sullo zero di Punta Salute, linee del grafico (come nei grafici del Centro Maree)
VECCHIA_DOPO_ORE = {"vento": 9, "marea": 14}   # v3.64: oltre queste ore la pagina segnala che il dato e' vecchio
GIORNI_24 = 3


def marea_24ore(date, cfg):
    """v3.64: per gli ultimi giorni previsti e per zona, marea 00-24 dall'ultimo bollettino (storico marea_cpsm.json) e
    marea astronomica; se lo storico non copre la giornata resta la sola astronomica. -> ({data: {zona: {...}}}, ora del bollettino)."""
    out, emessa = {}, None
    try:
        import fd_previsione as F
        S = F.storico_cpsm_leggi(".")
        emessa = S.get("emessa")
        for d in date[-GIORNI_24:]:
            G = F.marea_giorno24(d, S, ".")
            if not G:
                continue
            for z in ZONE:
                a = MP.marea_zona(G["astro"], z["id"], d, cfg)
                c = MP.marea_zona(G["cpsm"], z["id"], d, cfg) if G["cpsm"] else None
                if not a:
                    continue
                m = c or a
                out.setdefault(d.isoformat(), {})[z["id"]] = {"stazione": m["stazione"], "curva": m["curva"], "estremi": m["estremi"],
                                                            "astro": a["curva"] if c else None, "emessa": emessa if c else None}
    except Exception as e:          # il sito si costruisce lo stesso, senza il grafico delle 24 ore
        print(f"build_site: marea 24 ore non calcolata ({type(e).__name__}: {str(e)[:120]})")
    return out, emessa


VERIFICA_ORE_MAX = 24      # v3.75: le pagine CPSM tengono 24 ore; oltre, una verifica mancante non si calcola piu'


def chiave_verifica(p, V):
    """v3.75: (data, finestra) della PREVISIONE verificata, letta dal campo "previsione" del contenuto (nome del file della
    previsione). Il nome del file della verifica non conta (fd_verifica scrive 2359 dove la previsione ha 2400).
    Senza il campo: dal nome della verifica."""
    rif = str((V or {}).get("previsione") or "").split(" · ")[0].strip()
    d, fin = chiave_file(rif) if rif.startswith("previsione_") else (None, None)
    if not (d and fin):
        d, fin = chiave_file(p)
        fin = fin.replace("-2359", "-2400") if fin else fin
    return d, fin


def minuti(fin):
    """"1900-2400" -> (1140, 1440)."""
    return tuple(int(x[:2]) * 60 + int(x[2:]) for x in fin.split("-"))


def assegna_verifiche(ver, fasce):
    """v3.75: a quale fascia mostrata va ogni verifica. ver = {(data, fin, zona): file}; fasce = chiavi mostrate.
    Prima la fascia uguale; se la fascia verificata e' stata sostituita (cambio delle fasce), quella dello stesso giorno
    e zona che si sovrappone di piu' e non ha gia' la sua. -> {fascia mostrata: (file, fin verificata)}"""
    out = {k: (ver[k], k[1]) for k in fasce if k in ver}
    for (d, fin, zona) in sorted(k for k in ver if k not in fasce):
        a, b = minuti(fin)
        libere = [(min(b, minuti(k[1])[1]) - max(a, minuti(k[1])[0]), k) for k in fasce
                  if k[0] == d and k[2] == zona and k not in out]
        libere = [x for x in libere if x[0] > 0]
        if libere:
            out[max(libere)[1]] = (ver[(d, fin, zona)], fin)
    return out


def fasce_verificabili():
    """v3.75: finestre che fd_verifica puo' verificare (stessa regola, nessuna copia). None se non si riesce a saperlo."""
    try:
        import fd_verifica as FV
        out = []
        for v in FV.leggi_previsioni(".").values():
            d, fin = chiave_file(v["file"])
            out.append((d, fin, v["area"], v["chiave"]))
        return out
    except Exception as e:
        print(f"build_site: elenco delle finestre verificabili non letto ({type(e).__name__}: {str(e)[:120]})")
        return None


def main():
    shutil.rmtree(SITO, ignore_errors=True)
    os.makedirs(os.path.join(SITO, "giorni"), exist_ok=True)
    gruppi = {}
    for p in glob.glob(os.path.join(CARTELLA, "previsione_*.json")):
        if "giorni_" in os.path.basename(p):
            continue
        d, fin = chiave_file(p)
        P = leggi(p)
        if d and fin and P:
            gruppi.setdefault((d, fin, zona_di(P)), []).append(p)
    ver = {}
    for p in glob.glob(os.path.join(CARTELLA, "verifica_*.json")):
        V = leggi(p)
        d, fin = chiave_verifica(p, V) if V is not None else (None, None)
        if d and fin:
            ver.setdefault((d, fin, zona_di(V)), []).append(p)
    for d, fin, zona in list(gruppi):     # v3.64: passaggio alle nuove fasce, le vecchie dello stesso giorno non si mostrano
        vecchia = {"1200-1800": "1300-1800", "1800-2400": "1900-2400"}.get(fin)
        if vecchia:
            gruppi.pop((d, vecchia, zona), None)
    voci = {z["id"]: [] for z in ZONE}
    assegnate = assegna_verifiche(ver, set(gruppi))                      # v3.75
    verificabili = fasce_verificabili()
    esiti = {p.get("chiave"): p.get("esito") or "" for p in (leggi("verifica_archivio.json") or {}).get("previsioni", [])}
    MZ = MP.carica_config(".")   # v3.33
    for (d, fin, zona), file in sorted(gruppi.items(), key=lambda x: (x[0][0], -int(x[0][1][:4])), reverse=True):   # v3.49: giorno piu' recente prima, fasce in ordine
        if zona not in voci:
            continue
        pj = max(file, key=quando)
        P = leggi(pj)
        voce = {"data": d.isoformat(), "titolo": P.get("titolo"), "finestra": P.get("finestra"),
                "colore": (P.get("verdetto") or {}).get("colore"), "generato": P.get("generato"), "provvisoria": provvisoria(P, d), "emessa": (P.get("generato") or "")[-5:],
                "versione": P.get("versione"), "sintesi": sintesi(P, d),
                "marea_emessa": (P.get("marea") or {}).get("cpsm_emessa"), "marea_aggiornata": (P.get("marea") or {}).get("aggiornata") or P.get("generato")}
        voce["sintesi"]["marea_zona"] = ((P.get("marea") or {}).get("zona")                      # v3.35: dalla previsione (fd_previsione >= 1.43)
                                          or MP.marea_zona(P.get("marea") or {}, zona, d, MZ))           # previsioni vecchie: calcolata qui
        pdf = sintesi_pdf(d, zona) or pj[:-5] + ".pdf"           # v3.49: il PDF sintetico del giorno, se c'e'
        if os.path.exists(pdf):
            voce["previsione_pdf"] = os.path.join("giorni", os.path.basename(pdf))
            shutil.copy(pdf, os.path.join(SITO, voce["previsione_pdf"]))
        else:
            pdf = None
        if os.path.exists(pj[:-5] + ".html"):
            voce["previsione"] = os.path.join("giorni", os.path.basename(pj[:-5] + ".html"))
            copia_pagina(pj[:-5] + ".html", os.path.join(SITO, voce["previsione"]), zona, d, pdf)
        if verificabili is not None:      # v3.75: nessuna previsione verificabile nella fascia, o verifica chiusa senza esito
            a, b = minuti(fin)
            qui = [k for vd, vf, vz, k in verificabili if vd == d and vz == zona and min(b, minuti(vf)[1]) > max(a, minuti(vf)[0])]
            voce["verifica_no"] = not qui or all(k in esiti and not esiti[k].startswith("verificata") for k in qui)
        if (d, fin, zona) in assegnate:
            file_ver, fin_ver = assegnate[(d, fin, zona)]
            vj = scegli_ultimo(file_ver)
            V = leggi(vj) or {}
            vh = vj[:-5] + ".html"
            if os.path.exists(vh):
                destinazione = os.path.join("giorni", os.path.basename(vh))
                vp = vj[:-5] + ".pdf"
                if os.path.exists(vp):
                    shutil.copy(vp, os.path.join(SITO, "giorni", os.path.basename(vp)))
                copia_pagina(vh, os.path.join(SITO, destinazione), zona, d, vp if os.path.exists(vp) else None)
                voce["verifica"] = destinazione
                if fin_ver != fin:         # v3.75: verifica di una fascia sostituita: si dice quali ore copre
                    voce["verifica_fascia"] = "–".join(f"{x[:2]}:{x[2:]}" for x in fin_ver.split("-"))
            voce["verifica_provvisoria"] = bool(V.get("provvisoria"))
            voce["verifica_versione"] = V.get("versione")
        voci[zona].append(voce)
    marea24, marea_emessa = marea_24ore(sorted({d for d, _, _ in gruppi}), MZ)       # v3.64
    zone = []
    for z in ZONE:
        zone.append(dict(z, attiva=bool(voci[z["id"]]), voci=voci[z["id"]]))
    ora = dt.datetime.now(dt.timezone.utc)
    json.dump({"versione_sito": VERSIONE, "autore": AUTORE, "aggiornato_utc": ora.isoformat(timespec="minutes"), "ora_attesa": ORA_ATTESA, "ore_giri": ORE_GIRI, "ora_aggiornamento": ORA_AGGIORNAMENTO, "ora_mattino": ORA_MATTINO, "adesso_url": ADESSO_URL, "allerta": ALLERTA, "visibilita": VISIBILITA, "regole": regole(), "zone": zone, "marea24": marea24, "marea_emessa": marea_emessa, "soglie_marea": SOGLIE_MAREA, "vecchia_dopo_ore": VECCHIA_DOPO_ORE, "verifica_ore_max": VERIFICA_ORE_MAX, "tendenza": leggi("tendenza.json")},
              open(os.path.join(SITO, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # v2.1: la pagina conosce la propria versione e l'impronta del suo codice; versione.json (letto senza cache)
    # dice alla pagina gia' aperta se esiste una versione nuova -> barra "Aggiorna"
    html = open("index.html", encoding="utf-8").read()
    codice = codice_sito()
    html = html.replace("__VERSIONE_SITO__", VERSIONE).replace("__CODICE_SITO__", codice)
    open(os.path.join(SITO, "index.html"), "w", encoding="utf-8").write(html)
    json.dump({"versione_sito": VERSIONE, "codice": codice, "aggiornato_utc": ora.isoformat(timespec="minutes")},
              open(os.path.join(SITO, "versione.json"), "w", encoding="utf-8"))
    if os.path.exists("manifest.json"):
        shutil.copy("manifest.json", os.path.join(SITO, "manifest.json"))
    if os.path.exists("sw.js"):                     # v3.17: service worker delle allerte vento
        shutil.copy("sw.js", os.path.join(SITO, "sw.js"))
    if os.path.isdir("assets"):
        shutil.copytree("assets", os.path.join(SITO, "assets"))
    open(os.path.join(SITO, ".nojekyll"), "w").close()
    print(f"build_site v{VERSIONE}: " + ", ".join(f"{z['id']} {len(voci[z['id']])} giorni" for z in ZONE) + f" in {SITO}/")


if __name__ == "__main__":
    main()
