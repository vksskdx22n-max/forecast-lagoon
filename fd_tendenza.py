#!/usr/bin/env python3
"""fd_tendenza.py v1.1 — tendenza dei prossimi giorni per Forecast Lagoon (scelte di Ennio del 22/11/2026).

Due giorni dopo quello della previsione (dopodomani e il giorno dopo), finestra 05-12, per Laguna Nord, Centro e Sud:
colore indicativo (stessa regola del semaforo: media dei punti della zona ora per ora, colore piu' frequente nelle ore),
vento minimo-massimo e direzione prevalente, probabilita' massima di pioggia, alta/bassa marea della zona.

Vento ora per ora: ICON-D2 (al Centro con la correzione misurata di fd_verifica, come la previsione), se manca ICON-2i,
se manca ECMWF; ICON-2i ed ECMWF con la correzione media per zona di tendenza_correzioni.json (v1.1). Pioggia: probabilita' ICON-D2, se manca ECMWF. Marea: astronomica CPSM di Punta Salute
spostata per zona con marea_propaga (senza l'effetto del vento). Una sola richiesta a Open-Meteo.
Non tocca la previsione di domani, la verifica e i loro file.

Uso:  python3 fd_tendenza.py AAAA-MM-GG [HH:MM HH:MM]      (AAAA-MM-GG = giorno della previsione, cioe' domani)
Uscita: tendenza.json
"""
import sys, json, math, subprocess, statistics, datetime as dt
import fd_previsione as FP
import marea_propaga as MP

VERSIONE = "1.1"
CORREZIONI = "tendenza_correzioni.json"
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
            for h in ore:
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
    print(f"Tendenza salvata in {USCITA}")


if __name__ == "__main__":
    main()
