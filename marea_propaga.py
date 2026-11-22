"""marea_propaga.py v1.0 — marea della zona da Punta Salute (Forecast Lagoon).

Usato da fd_previsione (>= 1.43, salva la curva nel JSON della previsione), build_site (>= 3.35, ripiego per le
previsioni vecchie) e fd_verifica (>= 1.25, confronto con i livelli misurati).

Ogni semiciclo tra due estremi di Punta Salute viene spostato (ritardo, lineare nel tempo) e alzato/abbassato
(quota, interpolata sul livello: gli estremi restano estremi). Ritardo e quota di ogni estremo dipendono dal tipo
(alta/bassa) e dall'escursione di Punta Salute dall'estremo precedente; coefficienti in marea_zone.json.
Tempi in minuti dalla mezzanotte (ora italiana) del giorno della previsione, livelli in cm.
"""
import os, re, json, datetime as dt

VERSIONE = "1.0"
CONFIG = "marea_zone.json"
MARGINE_MIN = 60          # la curva della zona copre la finestra +/- questo margine (come la curva di Punta Salute nel sito)


def carica_config(cartella="."):
    p = os.path.join(cartella, CONFIG)
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def minuti_estremo(e, giorno):
    """Minuti dalla mezzanotte del giorno (negativi = giorno prima) di un estremo CPSM/astronomico."""
    if isinstance(e.get("min_curva"), (int, float)):
        return e["min_curva"]
    m = re.match(r"(\d\d)/(\d\d) (\d\d):(\d\d)", e.get("ora") or "")
    if not m:
        return None
    try:
        d = dt.date(giorno.year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    if (d - giorno).days > 180:
        d = d.replace(year=d.year - 1)
    elif (giorno - d).days > 180:
        d = d.replace(year=d.year + 1)
    return (d - giorno).days * 1440 + int(m.group(3)) * 60 + int(m.group(4))


def curva_punta_salute(M):
    """Curva [min, cm] e estremi usati per Punta Salute (previsione CPSM se c'e', altrimenti astronomica)."""
    if M.get("curva_cpsm"):
        return M["curva_cpsm"], M.get("cpsm") or [], "previsione CPSM Punta Salute"
    return M.get("curva") or [], M.get("estremali") or [], "marea astronomica Punta Salute"


def marea_zona(M, zona, giorno, cfg):
    """M = blocco "marea" della previsione. Ritorna {stazione, curva, estremi, fascia, fonte, config} o None."""
    Z = (cfg.get("zone") or {}).get(zona)
    fascia = M.get("fascia")
    curva, est, fonte = curva_punta_salute(M)
    if not Z or not fascia or len(curva) < 2 or not est:
        return None
    E = []
    for e in est:
        t = minuti_estremo(e, giorno)
        if t is None or e.get("cm") is None or e.get("tipo") not in ("max", "min"):
            continue
        E.append({"t": t, "cm": float(e["cm"]), "tipo": "alta" if e["tipo"] == "max" else "bassa"})
    E.sort(key=lambda x: x["t"])
    if len(E) < 2:
        return None
    rif = cfg.get("escursione_rif_cm", 60)
    for i, e in enumerate(E):
        esc = abs(e["cm"] - E[i - 1]["cm"]) if i > 0 else abs(E[1]["cm"] - e["cm"])
        c = Z[e["tipo"]]
        k = (esc - rif) / 10
        e["rit"] = c["rit_min"] + c["rit_per_10cm"] * k
        e["dh"] = c["dh_cm"] + c["dh_per_10cm"] * k
    pts = []
    for t, h in curva:
        for a, b in zip(E, E[1:]):
            if a["t"] <= t <= b["t"]:
                f = (t - a["t"]) / (b["t"] - a["t"]) if b["t"] > a["t"] else 0
                g = (h - a["cm"]) / (b["cm"] - a["cm"]) if b["cm"] != a["cm"] else f
                g = min(1.0, max(0.0, g))
                pts.append((t + a["rit"] + f * (b["rit"] - a["rit"]), h + a["dh"] + g * (b["dh"] - a["dh"])))
                break
    pts.sort()
    if len(pts) < 2:
        return None
    out = []
    for t in range(fascia[0] - MARGINE_MIN, fascia[1] + MARGINE_MIN + 1, 10):
        if not (pts[0][0] <= t <= pts[-1][0]):
            continue
        for (t1, h1), (t2, h2) in zip(pts, pts[1:]):
            if t1 <= t <= t2:
                out.append([t, round(h1 + (h2 - h1) * ((t - t1) / (t2 - t1) if t2 > t1 else 0), 1)])
                break
    estremi = []
    for e in E:
        tz = round(e["t"] + e["rit"])
        if fascia[0] - MARGINE_MIN <= tz <= fascia[1] + MARGINE_MIN:
            estremi.append({"tipo": e["tipo"], "min": tz, "ora": f"{(tz // 60) % 24:02d}:{tz % 60:02d}", "cm": round(e["cm"] + e["dh"]),
                            "incertezza_min": Z[e["tipo"]].get("incertezza_min")})
    return {"stazione": Z.get("stazione"), "curva": out, "estremi": estremi, "fascia": fascia, "fonte": fonte,
            "config": cfg.get("versione")}


def interpola(curva, t):
    """Valore della curva [min, cm] al minuto t (None fuori)."""
    for (t1, h1), (t2, h2) in zip(curva, curva[1:]):
        if t1 <= t <= t2:
            return h1 + (h2 - h1) * ((t - t1) / (t2 - t1) if t2 > t1 else 0)
    return None
