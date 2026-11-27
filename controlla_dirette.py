#!/usr/bin/env python3
"""controlla_dirette.py v1.0 — sentinella dei dati in diretta e delle allerte (a ogni giro, dopo la pubblicazione).
Legge il Worker: /adesso (vento, livello, onda, pressione), /allerta/stato e i dati aperti del vento usati dalle allerte.
Il blocco anti-bot delle letture programmate non e' un errore: le allerte usano i dati aperti, che qui vengono controllati.
Esce con errore se manca qualcosa: il giro risulta fallito, il sito resta pubblicato. La visibilita' non e' controllata.
"""
import datetime as dt, json, subprocess, sys, time

URL = "https://fd-cpsm-proxy.ennioweb.workers.dev"
ZONE = (("nord", "Nord"), ("centro", "Centro"), ("sud", "Sud"))
STAZIONI_ALLERTE = {"1028": "Laguna Nord", "1038": "Diga Sud Lido", "1032": "Malamocco Porto", "1021": "Piattaforma",
                    "1024": "Diga Sud Chioggia", "1033": "Chioggia Porto"}
VECCHI_MIN = 60                 # dati aperti del vento piu' vecchi: allerte senza misure
TENTATIVI, PAUSA = 3, 20


def leggi(percorso):
    r = subprocess.run(["curl", "-s", "--compressed", "-m", "30", URL + percorso], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def esamina(scarica, adesso):
    errori = []
    J = scarica("/adesso")
    if not isinstance(J, dict) or "zone" not in J:
        errori.append("Dati in diretta: il Worker non risponde a /adesso")
    else:
        for k, nome in ZONE:
            z = (J["zone"] or {}).get(k) or {}
            if not z.get("disponibile"):
                errori.append(f"Dati in diretta: vento {nome} non disponibile")
            if not (z.get("livello") or {}).get("disponibile"):
                errori.append(f"Dati in diretta: marea {nome} non disponibile")
        for k, nome in (("onda", "onda"), ("pressione", "pressione")):
            if not (J.get(k) or {}).get("disponibile"):
                errori.append(f"Dati in diretta: {nome} non disponibile")
    S = scarica("/allerta/stato")
    if not isinstance(S, dict) or not S.get("versione"):
        errori.append("Allerte: stato non leggibile")
    V = scarica("/vento")
    if not isinstance(V, list):
        errori.append("Allerte: dati aperti del vento non leggibili")
    else:
        ferme = []
        for ident, nome in STAZIONI_ALLERTE.items():
            x = next((r for r in V if str(r.get("ID_stazione")) == ident), None)
            try:                                  # data in ora solare (UTC+1)
                t = dt.datetime.strptime(x["data"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone(dt.timedelta(hours=1)))
                vecchio = not x.get("valore") or (adesso - t).total_seconds() > VECCHI_MIN * 60
            except (TypeError, KeyError, ValueError):
                vecchio = True
            if vecchio:
                ferme.append(nome)
        if ferme:
            errori.append("Allerte: dati aperti del vento fermi o assenti per " + ", ".join(ferme))
    return errori


def controlla(scarica=leggi, orologio=lambda: dt.datetime.now(dt.timezone.utc), pausa=PAUSA):
    """Un errore vale solo se resta fino all'ultimo tentativo."""
    for i in range(TENTATIVI):
        errori = esamina(scarica, orologio())
        if not errori:
            break
        if i < TENTATIVI - 1:
            time.sleep(pausa)
    return errori


if __name__ == "__main__":
    E = controlla()
    for e in E:
        print("::error::" + e)
    print(f"controlla_dirette: {len(E)} problemi" if E else "controlla_dirette: dati in diretta e allerte regolari")
    sys.exit(1 if E else 0)
