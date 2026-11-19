#!/usr/bin/env python3
"""build_site.py v2.0 - prepara la cartella site/ per GitHub Pages.

Legge Previsioni/previsione_*.json e Previsioni/verifica_*.json, copia le pagine HTML/PDF in site/giorni/,
copia assets/ e scrive site/index.json con le tre zone (Laguna Nord, Centro, Sud) e, per ogni giorno,
la sintesi calcolata qui (semaforo ora per ora, vento, raffiche, direzione prevalente, marea nella finestra,
corrente alla bocca). Nessun valore e' scritto a mano: la pagina index.html legge index.json.

Zona di una previsione: campo "area" del JSON (nord / centro / sud); i JSON senza "area" sono della Laguna Centro.
"""
import os, re, json, glob, shutil, datetime as dt
from collections import Counter

VERSIONE = "2.0"
CARTELLA = "Previsioni"
SITO = "site"
ZONE = [
    {"id": "nord", "nome": "Laguna Nord", "bocca": "Lido", "bacino": "bacino di Lido"},
    {"id": "centro", "nome": "Laguna Centro", "bocca": "Malamocco", "bacino": "bacino di Malamocco"},
    {"id": "sud", "nome": "Laguna Sud", "bocca": "Chioggia", "bacino": "bacino di Chioggia"},
]
ALIAS_AREA = {}            # eventuali nomi di area diversi dagli id delle zone
MARGINE_MAREA_MIN = 60     # minuti di curva di marea mostrati prima e dopo la finestra


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
    V = P.get("verdetto") or {}
    s["andamento"] = V.get("andamento_txt")
    s["andamento_fasce"] = V.get("andamento")
    return s


def regole():
    """Soglie e parametri mostrati nelle istruzioni del sito, letti da fd_previsione (nessun numero scritto a mano)."""
    try:
        import fd_previsione as F
        return {"soglia_verde": F.SOGLIA_VERDE, "soglia_rosso": F.SOGLIA_ROSSO, "cpsm_ore_max": F.CPSM_ORE_MAX,
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
                "colore": (P.get("verdetto") or {}).get("colore"), "generato": P.get("generato"),
                "versione": P.get("versione"), "sintesi": sintesi(P, d)}
        for estensione, chiave in ((".html", "previsione"), (".pdf", "previsione_pdf")):
            sorgente = pj[:-5] + estensione
            if os.path.exists(sorgente):
                destinazione = os.path.join("giorni", os.path.basename(sorgente))
                shutil.copy(sorgente, os.path.join(SITO, destinazione))
                voce[chiave] = destinazione
        if (d, fin, zona) in ver:
            vj = scegli_ultimo(ver[(d, fin, zona)])
            V = leggi(vj) or {}
            vh = vj[:-5] + ".html"
            if os.path.exists(vh):
                destinazione = os.path.join("giorni", os.path.basename(vh))
                shutil.copy(vh, os.path.join(SITO, destinazione))
                voce["verifica"] = destinazione
            voce["verifica_provvisoria"] = bool(V.get("provvisoria"))
            voce["verifica_versione"] = V.get("versione")
        voci[zona].append(voce)
    zone = []
    for z in ZONE:
        zone.append(dict(z, attiva=bool(voci[z["id"]]), voci=voci[z["id"]]))
    ora = dt.datetime.now(dt.timezone.utc)
    json.dump({"versione_sito": VERSIONE, "aggiornato_utc": ora.isoformat(timespec="minutes"), "regole": regole(), "zone": zone},
              open(os.path.join(SITO, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copy("index.html", os.path.join(SITO, "index.html"))
    if os.path.exists("manifest.json"):
        shutil.copy("manifest.json", os.path.join(SITO, "manifest.json"))
    if os.path.isdir("assets"):
        shutil.copytree("assets", os.path.join(SITO, "assets"))
    open(os.path.join(SITO, ".nojekyll"), "w").close()
    print(f"build_site v{VERSIONE}: " + ", ".join(f"{z['id']} {len(voci[z['id']])} giorni" for z in ZONE) + f" in {SITO}/")


if __name__ == "__main__":
    main()
