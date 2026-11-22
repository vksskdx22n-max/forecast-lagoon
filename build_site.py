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

VERSIONE = "3.31"
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
           "ora_inizio": 5, "ora_fine": 20,        # ora italiana
           "controllo_min": 5,        # frequenza del controllo (cron del Worker)
           "rotazione_gradi": 90, "rotazione_min_kn": 8}   # pre-allarme sperimentale: solo registrato, non inviato
# v3.26: visibilita' misurata adesso a Venezia Tessera (Worker v2.6, /adesso). Si mostra solo sotto mostra_sotto_m e se il METAR
# non e' piu' vecchio di max_min. Tre livelli scelti da Ennio (21/11): fino a 200 m fitta, fino a 500 m nebbia, fino a 1000 m leggera (1000 compreso dal v3.29).
# v3.29: si mostra anche sopra i 1000 m se il bollettino segnala nebbia (FG, BCFG banchi, PRFG, MIFG, VCFG): "Banchi di nebbia".
VISIBILITA = {"mostra_fino_a_m": 1000, "fitta_fino_a_m": 200, "nebbia_fino_a_m": 500, "max_min": 60}
ORA_MATTINO = "08:30"        # v3.3: ora indicativa della previsione provvisoria (giro Cloudflare delle 08:25), per le istruzioni
ORA_AGGIORNAMENTO = "16:30"  # v3.2: ora indicativa della previsione definitiva (giro Cloudflare delle 16:25), mostrata accanto alla provvisoria
ORA_ATTESA = "17:30"        # v3.0: ora (italiana) entro cui ci si aspetta la previsione di domani; dopo, il sito avvisa del ritardo
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


def provvisoria(P):
    """v3.2: True se la previsione e' stata generata prima delle ORA_DEFINITIVA (giro del mattino)."""
    try:
        return dt.datetime.strptime(P.get("generato") or "", "%d/%m/%Y %H:%M").hour < ORA_DEFINITIVA
    except ValueError:
        return False


def chiave_file(p):
    """(data, finestra, zona) dal nome del file e dal contenuto."""
    base = os.path.basename(p)
    d = data_da_nome(base)
    m = re.search(r"_(\d{4}-\d{4})", base)
    return (d, m.group(1) if m else None)


def sintesi(P, giorno):
    """Valori per la pagina della zona, tutti presi dal JSON della previsione."""
    s = {}
    st = P.get("striscia") or {}
    ore = st.get("ore") or []
    s["ore"] = [{"ora": o.get("ora"), "sem": o.get("sem"), "stima": o.get("stima"), "raff": o.get("raff"),
                 "marea": o.get("marea"), "corr": o.get("corr"), "verso": o.get("verso")} for o in ore]
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
                    "onda_mare": m.get("h_corr", m.get("h"))})
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
        d, fin = chiave_file(p)
        V = leggi(p)
        if d and fin and V is not None:
            ver.setdefault((d, fin, zona_di(V)), []).append(p)
    voci = {z["id"]: [] for z in ZONE}
    for (d, fin, zona), file in sorted(gruppi.items(), reverse=True):
        if zona not in voci:
            continue
        pj = scegli_ultimo(file)
        P = leggi(pj)
        voce = {"data": d.isoformat(), "titolo": P.get("titolo"), "finestra": P.get("finestra"),
                "colore": (P.get("verdetto") or {}).get("colore"), "generato": P.get("generato"), "provvisoria": provvisoria(P), "emessa": (P.get("generato") or "")[-5:],
                "versione": P.get("versione"), "sintesi": sintesi(P, d)}
        pdf = pj[:-5] + ".pdf"
        if os.path.exists(pdf):
            voce["previsione_pdf"] = os.path.join("giorni", os.path.basename(pdf))
            shutil.copy(pdf, os.path.join(SITO, voce["previsione_pdf"]))
        else:
            pdf = None
        if os.path.exists(pj[:-5] + ".html"):
            voce["previsione"] = os.path.join("giorni", os.path.basename(pj[:-5] + ".html"))
            copia_pagina(pj[:-5] + ".html", os.path.join(SITO, voce["previsione"]), zona, d, pdf)
        if (d, fin, zona) in ver:
            vj = scegli_ultimo(ver[(d, fin, zona)])
            V = leggi(vj) or {}
            vh = vj[:-5] + ".html"
            if os.path.exists(vh):
                destinazione = os.path.join("giorni", os.path.basename(vh))
                vp = vj[:-5] + ".pdf"
                if os.path.exists(vp):
                    shutil.copy(vp, os.path.join(SITO, "giorni", os.path.basename(vp)))
                copia_pagina(vh, os.path.join(SITO, destinazione), zona, d, vp if os.path.exists(vp) else None)
                voce["verifica"] = destinazione
            voce["verifica_provvisoria"] = bool(V.get("provvisoria"))
            voce["verifica_versione"] = V.get("versione")
        voci[zona].append(voce)
    zone = []
    for z in ZONE:
        zone.append(dict(z, attiva=bool(voci[z["id"]]), voci=voci[z["id"]]))
    ora = dt.datetime.now(dt.timezone.utc)
    json.dump({"versione_sito": VERSIONE, "autore": AUTORE, "aggiornato_utc": ora.isoformat(timespec="minutes"), "ora_attesa": ORA_ATTESA, "ora_aggiornamento": ORA_AGGIORNAMENTO, "ora_mattino": ORA_MATTINO, "adesso_url": ADESSO_URL, "allerta": ALLERTA, "visibilita": VISIBILITA, "regole": regole(), "zone": zone},
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
