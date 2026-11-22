#!/usr/bin/env python3
"""fd_tendenza.py v1.3 — tendenza dei prossimi giorni per Forecast Lagoon (scelte di Ennio del 22/11/2026).

Due giorni dopo quello della previsione (dopodomani e il giorno dopo), finestra 05-12, per Laguna Nord, Centro e Sud:
colore indicativo (stessa regola del semaforo: media dei punti della zona ora per ora, colore piu' frequente nelle ore),
vento minimo-massimo e direzione prevalente, probabilita' massima di pioggia, alta/bassa marea della zona.

Vento ora per ora: ICON-D2 (al Centro con la correzione misurata di fd_verifica, come la previsione), se manca ICON-2i,
se manca ECMWF; ICON-2i ed ECMWF con la correzione media per zona di tendenza_correzioni.json (v1.1). Pioggia: probabilita' ICON-D2, se manca ECMWF. Marea: astronomica CPSM di Punta Salute
spostata per zona con marea_propaga (senza l'effetto del vento). Una sola richiesta a Open-Meteo.
v1.2: la tendenza impara. Ogni giro salva in tendenza_archivio.json il vento GREZZO previsto ora per ora da ciascun modello
(media dei punti della zona) e, quando il giorno e' passato, lo confronta con le misure CPSM della zona (stesse stazioni e
stessa media oraria di fd_verifica). Le correzioni di ICON-2i ed ECMWF vengono ricalcolate sugli ultimi GIORNI_FINESTRA giorni
verificati: errore medio (misura - previsto), solo se ci sono almeno MIN_GIORNI giorni verificati per quel modello e zona;
altrimenti restano i valori iniziali (analisi del 22/11/2026). Una tendenza per giorno, zona e giorno di emissione: vale l'ultima.
Non tocca la previsione di domani, la verifica e i loro file.

Uso:  python3 fd_tendenza.py AAAA-MM-GG [HH:MM HH:MM]      (AAAA-MM-GG = giorno della previsione, cioe' domani)
Uscita: tendenza.json
"""
import sys, json, math, subprocess, statistics, datetime as dt
import fd_previsione as FP
import marea_propaga as MP

VERSIONE = "1.3"
CORREZIONI = "tendenza_correzioni.json"
ARCHIVIO_T = "tendenza_archivio.json"
GIORNI_FINESTRA = 90           # v1.2: giorni verificati usati per ricalcolare la correzione
MIN_GIORNI = 20                # v1.2: sotto questa soglia resta la correzione iniziale
MAX_CORR = 8.0                 # v1.2: nodi; oltre e' quasi certamente un guasto delle misure, correzione non cambiata
CONSERVA_GIORNI = 150          # v1.2: righe piu' vecchie tolte dall'archivio
ZONA_MIS = {"centro": "Malamocco · Valgrande", "nord": "Laguna Nord", "sud": "Laguna Sud"}   # zone di fd_verifica (stazioni CPSM)
GIORNI = 2
USCITA = "tendenza.json"
NOMI = {"nord": "Laguna Nord", "centro": "Laguna Centro", "sud": "Laguna Sud"}
MODELLI = ["icon_d2", "italia_meteo_arpae_icon_2i", "ecmwf_ifs025"]
NOME_MOD = {"icon_d2": "ICON-D2", "italia_meteo_arpae_icon_2i": "ICON-2i", "ecmwf_ifs025": "ECMWF"}


def carica_correzioni():
    try:
        return json.load(open(CORREZIONI, encoding="utf-8")).get("kn", {})
    except (OSError, ValueError):
        print(f"Tendenza: {CORREZIONI} non leggibile, nessuna correzione per ICON-2i ed ECMWF")
        return {}


# ------------------------------------------------------------------ v1.2: archivio, verifica, correzioni che imparano
def carica_archivio_t():
    try:
        A = json.load(open(ARCHIVIO_T, encoding="utf-8"))
    except (OSError, ValueError):
        A = {}
    A.setdefault("emesse", []); A.setdefault("verificate", [])
    return A


def salva_archivio_t(A, oggi):
    lim = (oggi - dt.timedelta(days=CONSERVA_GIORNI)).isoformat()
    A["emesse"] = [e for e in A["emesse"] if e["data"] >= lim]
    A["verificate"] = [v for v in A["verificate"] if v["data"] >= lim]
    A["versione"] = VERSIONE
    json.dump(A, open(ARCHIVIO_T, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def verifica_tendenze(A, adesso):
    """Confronta le tendenze emesse con le misure CPSM (pagine delle ultime 24 h). Ritorna il numero di zone-giorno verificate."""
    try:
        import fd_verifica as FV
    except ImportError:
        print("Tendenza: fd_verifica non disponibile, verifica rinviata")
        return 0
    fatte = {(v["data"], v["area"], v["emessa"]) for v in A["verificate"]}
    pend = []
    for e in A["emesse"]:
        g = dt.date.fromisoformat(e["data"])
        fine = dt.datetime.combine(g, dt.time(int(max(e["ore"])), 0), FV.tz_italia(g)) + dt.timedelta(minutes=30)
        if (e["data"], e["area"], e["emessa"]) not in fatte and fine <= adesso and (adesso - fine) < dt.timedelta(hours=23):
            pend.append(e)
    if not pend:
        return 0
    M = FV.scarica_misure()
    if not any(M.values()):
        print("Tendenza: pagine CPSM non raggiungibili, verifica della tendenza rinviata")
        return 0
    n = 0
    for e in pend:
        g = dt.date.fromisoformat(e["data"])
        ore = []
        for h, mod in sorted(e["ore"].items()):
            m = FV.misura_zona(M, ZONA_MIS[e["area"]], dt.datetime.combine(g, dt.time(int(h), 0), FV.tz_italia(g)))
            if m:
                ore.append({"ora": h, "mis": m["media"], "prev": mod})
        if len(ore) < len(e["ore"]) // 2:
            continue                                   # misure insufficienti (sensore fermo, pagina parziale)
        A["verificate"].append({"data": e["data"], "area": e["area"], "emessa": e["emessa"], "anticipo_g": e["anticipo_g"], "ore": ore})
        n += 1
        err = {MOD: round(statistics.mean(o["mis"] - o["prev"][MOD] for o in ore if o["prev"].get(MOD) is not None), 1)
               for MOD in MODELLI if any(o["prev"].get(MOD) is not None for o in ore)}
        print(f"  verifica tendenza {e['data']} {NOMI[e['area']]} (emessa {e['emessa']}, {len(ore)} ore): errore medio grezzo "
              + ", ".join(f"{NOME_MOD[k]} {v:+.1f} kn" for k, v in err.items()))
    return n


def aggiorna_correzioni(A, oggi):
    """Ricalcola le correzioni di ICON-2i ed ECMWF dalle tendenze verificate; scrive tendenza_correzioni.json."""
    try:
        C = json.load(open(CORREZIONI, encoding="utf-8"))
    except (OSError, ValueError):
        return
    C.setdefault("iniziali", json.loads(json.dumps(C.get("kn", {}))))
    lim = (oggi - dt.timedelta(days=GIORNI_FINESTRA)).isoformat()
    stato = {}
    for MOD in ("ecmwf_ifs025", "italia_meteo_arpae_icon_2i"):
        for area in ("centro", "nord", "sud"):
            per_giorno = {}
            for v in A["verificate"]:
                if v["area"] != area or v["data"] < lim:
                    continue
                d = [o["mis"] - o["prev"][MOD] for o in v["ore"] if o["prev"].get(MOD) is not None]
                if d:
                    per_giorno.setdefault(v["data"], []).append(statistics.mean(d))
            giorni = len(per_giorno)
            base = C["iniziali"].get(MOD, {}).get(area)
            if giorni >= MIN_GIORNI:
                val = round(statistics.mean(statistics.mean(x) for x in per_giorno.values()), 1)
                if abs(val) <= MAX_CORR:
                    C["kn"].setdefault(MOD, {})[area] = val
                    stato.setdefault(MOD, {})[area] = {"fonte": "misure", "giorni": giorni}
                    continue
                print(f"Tendenza: correzione {NOME_MOD[MOD]} {NOMI[area]} {val:+.1f} kn fuori limite, non usata")
            if base is not None:
                C["kn"].setdefault(MOD, {})[area] = base
            stato.setdefault(MOD, {})[area] = {"fonte": "iniziale", "giorni": giorni}
    C["stato"] = stato
    C["aggiornato"] = oggi.isoformat()
    json.dump(C, open(CORREZIONI, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Correzioni della tendenza: " + " · ".join(
        f"{NOME_MOD[m]} " + ", ".join(f"{NOMI[a].split()[-1]} {C['kn'][m][a]:+.1f} ({st['fonte']}, {st['giorni']} g)" for a, st in v.items())
        for m, v in stato.items()))


def punti_aree():
    centro = [p for p in FP.PUNTI if p[0] in FP.ZONE_VENTO]
    return {"centro": centro, "nord": FP.AREE["nord"]["zone"], "sud": FP.AREE["sud"]["zone"]}


def scarica(punti, g0, g1):
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={','.join(str(p[1]) for p in punti)}&longitude={','.join(str(p[2]) for p in punti)}"
           "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation_probability"
           f"&models={','.join(MODELLI)}&wind_speed_unit=kn&timezone=Europe%2FRome"
           f"&start_date={g0.isoformat()}&end_date={g1.isoformat()}")
    for _ in range(2):
        r = subprocess.run(["curl", "-s", "-m", "45", url], capture_output=True, text=True)
        try:
            R = json.loads(r.stdout)
        except ValueError:
            continue
        if isinstance(R, dict):
            R = [R]
        if isinstance(R, list) and len(R) == len(punti) and all("hourly" in x for x in R):
            return R
    return None


def media_dir(gs):
    s = sum(math.sin(math.radians(g)) for g in gs); c = sum(math.cos(math.radians(g)) for g in gs)
    return (math.degrees(math.atan2(s, c)) + 360) % 360


def marea_giorno(area, g, da, a, cfg):
    """Prima alta/bassa della zona nella finestra (marea astronomica + propagazione), o None."""
    try:
        MA = FP.Marea(f"marea_astronomica_{g.year}.json")
    except OSError:
        return None
    tz = FP.tz_italia(g)
    ms0 = dt.datetime(g.year, g.month, g.day, tzinfo=tz).timestamp() * 1000
    curva = []
    for m in range(max(0, da - 120), min(1440, a + 120) + 1, 10):
        v = MA.cm_at(ms0 + m * 60000)
        if v is not None:
            curva.append([m, round(v, 1)])
    est = [{"tipo": t, "min_curva": round((ms - ms0) / 60000), "cm": cm} for t, ms, cm in MA.estremali(ms0 - 18 * 3600000, ms0 + 36 * 3600000)]
    mz = MP.marea_zona({"curva": curva, "estremali": est, "fascia": [da, a]}, area, g, cfg)
    if not mz:
        return None
    e = next((x for x in mz["estremi"] if da <= x["min"] <= a), None)
    return {"tipo": e["tipo"], "ora": e["ora"], "cm": e["cm"]} if e else None


def main():
    arg = [x for x in sys.argv[1:] if not x.startswith("--")]
    base = dt.date.fromisoformat(arg[0]) if arg else dt.date.today() + dt.timedelta(days=1)
    da_t = dt.time.fromisoformat(arg[1]) if len(arg) > 1 else dt.time(5, 0)
    a_t = dt.time.fromisoformat(arg[2]) if len(arg) > 2 else dt.time(12, 0)
    da, a = da_t.hour * 60 + da_t.minute, a_t.hour * 60 + a_t.minute
    giorni = [base + dt.timedelta(days=i) for i in range(1, GIORNI + 1)]
    adesso = dt.datetime.now(dt.timezone.utc)
    oggi = adesso.astimezone(FP.tz_italia(adesso.date())).date()
    AT = carica_archivio_t()
    try:                                                    # v1.2: verifica e correzioni, mai bloccanti
        nv = verifica_tendenze(AT, adesso)
        aggiorna_correzioni(AT, oggi)
        if nv:
            salva_archivio_t(AT, oggi)
    except Exception as ex:
        print(f"Tendenza: verifica non riuscita ({ex}), si usano le correzioni attuali")
    print(f"fd_tendenza v{VERSIONE} — {giorni[0].strftime('%d/%m')}–{giorni[-1].strftime('%d/%m/%Y')} {da_t.strftime('%H:%M')}–{a_t.strftime('%H:%M')}")
    PA = punti_aree()
    punti = [p for ps in PA.values() for p in ps]
    R = scarica(punti, giorni[0], giorni[-1])
    if not R:
        print("Tendenza: Open-Meteo non risponde, tendenza.json non aggiornato")
        return
    idx = {p[0]: i for i, p in enumerate(punti)}
    cfg = MP.carica_config(".")
    CK = carica_correzioni()
    ore = [h for h in range(24) if da <= h * 60 <= a]
    out = {"versione": VERSIONE, "generato": dt.datetime.now(FP.tz_italia(dt.date.today())).strftime("%Y-%m-%d %H:%M"),
           "base": base.isoformat(), "finestra": f"{da_t.strftime('%H:%M')}–{a_t.strftime('%H:%M')}", "giorni": []}
    for g in giorni:
        G = {"data": g.isoformat(), "zone": []}
        for area in ("nord", "centro", "sud"):
            st_ore, dirs, prob, usati, corr = [], [], [], {}, {}
            grezzi = {}                                     # v1.2: {ora: {modello: media grezza dei punti}}
            for h in ore:
                t0 = f"{g.isoformat()}T{h:02d}:00"
                for m in MODELLI:
                    xs = []
                    for z, _, _ in PA[area]:
                        H = R[idx[z]]["hourly"]
                        if t0 in H["time"]:
                            x = (H.get(f"wind_speed_10m_{m}") or [None] * len(H["time"]))[H["time"].index(t0)]
                            if x is not None:
                                xs.append(x)
                    if len(xs) == len(PA[area]):
                        grezzi.setdefault(f"{h:02d}", {})[m] = round(statistics.mean(xs), 1)
                t = f"{g.isoformat()}T{h:02d}:00"
                vals = []
                for z, _, _ in PA[area]:
                    H = R[idx[z]]["hourly"]
                    i = H["time"].index(t) if t in H["time"] else None
                    if i is None:
                        continue
                    for m in MODELLI:
                        v, d = (H.get(f"wind_speed_10m_{m}") or [None] * (i + 1))[i], (H.get(f"wind_direction_10m_{m}") or [None] * (i + 1))[i]
                        if v is None or d is None:
                            continue
                        if m == "icon_d2" and area == "centro" and not FP.riparata(z, d):
                            v = v + (FP.FV.correzione(v, d, ".", FP.zona_corr(z))[0] if FP.FV else 0.0)
                            corr[m] = True
                        elif m == "icon_d2" and FP.FV and ZONA_MIS[area] in getattr(FP.FV, "CORR_ZONA", {}):   # v1.3: Nord e Sud
                            v = v + FP.FV.correzione(v, d, ".", ZONA_MIS[area])[0]
                            corr[m] = True
                        elif m != "icon_d2" and CK.get(m, {}).get(area):
                            v = max(0.0, v + CK[m][area]); corr[m] = True
                        vals.append((v, d)); usati[m] = usati.get(m, 0) + 1
                        break
                    p = (H.get("precipitation_probability_icon_d2") or [None] * (i + 1))[i]
                    if p is None:
                        p = (H.get("precipitation_probability_ecmwf_ifs025") or [None] * (i + 1))[i]
                    if p is not None:
                        prob.append(p)
                if vals and len(vals) == len(PA[area]):
                    st_ore.append(statistics.mean(v for v, _ in vals)); dirs += [d for _, d in vals]
            if grezzi:
                AT["emesse"] = [e for e in AT["emesse"] if not (e["data"] == g.isoformat() and e["area"] == area and e["emessa"] == oggi.isoformat())]
                AT["emesse"].append({"data": g.isoformat(), "area": area, "emessa": oggi.isoformat(),
                                     "anticipo_g": (g - oggi).days, "ore": grezzi})
            if len(st_ore) < len(ore) // 2:
                G["zone"].append({"id": area, "nome": NOMI[area], "disponibile": False})
                continue
            colori = [FP.semaforo(v, None) for v in st_ore]
            col = max(set(colori), key=lambda c: (colori.count(c), -colori.index(c)))
            fonte = max(usati, key=usati.get)
            G["zone"].append({"id": area, "nome": NOMI[area], "disponibile": True, "colore": col,
                              "vento_min": FP.arrotonda(min(st_ore)), "vento_max": FP.arrotonda(max(st_ore)),
                              "dir": FP.card(media_dir(dirs)) if dirs else None,
                              "pioggia": round(max(prob)) if prob else None,
                              "marea": marea_giorno(area, g, da, a, cfg),
                              "modello": NOME_MOD[fonte], "corretto": bool(corr.get(fonte))})
        out["giorni"].append(G)
        print(f"  {g.strftime('%d/%m')}: " + " · ".join(f"{z['nome']} " + (f"{z['colore']} {z['vento_min']}–{z['vento_max']} kn ({z['modello']})" if z.get("disponibile") else "non disponibile") for z in G["zone"]))
    json.dump(out, open(USCITA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    salva_archivio_t(AT, oggi)
    print(f"Tendenza salvata in {USCITA}")


if __name__ == "__main__":
    main()
