#!/usr/bin/env python3
"""controlla_verifiche.py v1.0 — sentinella delle verifiche (dopo la pubblicazione del sito).

1. Collegamenti: ogni verifica dei giorni recenti deve avere il suo pulsante nel sito (site/index.json).
2. Calcolo (non nei giri della sola marea, che non verificano): ogni finestra conclusa da almeno MARGINE_ORE
   deve avere la verifica calcolata.
Esce con errore se qualcosa manca: il giro risulta fallito, il sito resta pubblicato.
Uso: python3 controlla_verifiche.py   (variabile GIRO come nel workflow)
"""
import datetime as dt, glob, json, os, sys

import build_site as B
import fd_verifica as FV

GIORNI = 3              # collegamenti controllati per gli ultimi giorni
MARGINE_ORE = 2         # dopo la fine della finestra: il tempo di un giro che verifica
ORIZZONTE_ORE = 48      # oltre, una verifica mancante e' gia' stata segnalata


def controlla(adesso=None, giro=""):
    adesso = adesso or dt.datetime.now(dt.timezone.utc)
    errori = []
    S = json.load(open(os.path.join(B.SITO, "index.json"), encoding="utf-8"))
    mostrate = {}
    for z in S["zone"]:
        for v in z["voci"]:
            fin = v["finestra"].replace("–", "-").replace(":", "")
            mostrate[(dt.date.fromisoformat(v["data"]), fin, z["id"])] = v
    ver, file_di = {}, set()
    for p in glob.glob(os.path.join(B.CARTELLA, "verifica_*.json")):
        V = B.leggi(p)
        d, fin = B.chiave_verifica(p, V) if V is not None else (None, None)
        if not (d and fin) or V.get("provvisoria"):
            continue
        file_di.add(str(V.get("previsione") or "").split(" · ")[0])
        if (adesso.date() - d).days <= GIORNI:
            ver.setdefault((d, fin, B.zona_di(V)), []).append(p)
    collegate = {os.path.basename(v["verifica"]) for v in mostrate.values() if v.get("verifica")}
    for (d, fin, zona), file in sorted(ver.items()):
        if any(os.path.basename(p)[:-5] + ".html" in collegate for p in file):
            continue
        a, b = B.minuti(fin)      # accettata se la fascia che la sostituisce mostra gia' la propria verifica
        if any(k[0] == d and k[2] == zona and v.get("verifica") and not v.get("verifica_fascia")
               and min(b, B.minuti(k[1])[1]) > max(a, B.minuti(k[1])[0]) for k, v in mostrate.items() if k[1] != fin):
            continue
        errori.append(f"verifica calcolata ma non visibile nel sito: {d} {fin} {zona}")
    if giro != "marea":
        A = json.load(open(FV.ARCHIVIO, encoding="utf-8"))
        esiti = {p["chiave"]: p.get("esito") or "" for p in A["previsioni"]}
        for k, v in sorted(FV.leggi_previsioni(".").items()):
            ore = (adesso - v["a"]).total_seconds() / 3600
            if not MARGINE_ORE <= ore <= ORIZZONTE_ORE:
                continue
            if not esiti.get(k, "").startswith("verificata"):
                errori.append(f"verifica non calcolata: {k} ({esiti.get(k) or 'non ancora in archivio'})")
            elif v["file"] not in file_di and not any(f.startswith(v["file"].rsplit("_v", 1)[0] + "_v") for f in file_di):
                errori.append(f"verifica in archivio ma senza pagina: {k}")
    return errori


if __name__ == "__main__":
    E = controlla(giro=(os.environ.get("GIRO") or "").strip().lower())
    for e in E:
        print("::error::" + e)
    print(f"controlla_verifiche: {len(E)} problemi" if E else "controlla_verifiche: tutte le verifiche sono calcolate e visibili")
    sys.exit(1 if E else 0)
