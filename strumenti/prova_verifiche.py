#!/usr/bin/env python3
"""prova_verifiche.py v1.0 — prova dei collegamenti previsione/verifica, prima di pubblicare.
Costruisce il sito in una cartella temporanea con previsioni e verifiche di prova (tutte le fasce e zone, compreso il
cambio di fasce) e controlla pulsanti e sentinella. Uso (dalla radice del repository): python3 strumenti/prova_verifiche.py
"""
import datetime as dt, glob, json, os, shutil, subprocess, sys, tempfile

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZONE = ("centro", "nord", "sud")
# (fascia della previsione verificata, ora finale nel nome della verifica, fascia mostrata nel sito)
CASI = [("0500-1200", "1200", "0500-1200"), ("1200-1800", "1800", "1200-1800"), ("1800-2400", "2359", "1800-2400"),
        ("1900-2400", "2359", "1900-2400"), ("1300-1800", "1800", "1200-1800")]


def prepara(cartella):
    for f in glob.glob(os.path.join(RADICE, "*.py")) + glob.glob(os.path.join(RADICE, "*.json")) + [os.path.join(RADICE, x) for x in ("index.html", "sw.js")]:
        shutil.copy(f, cartella)
    os.makedirs(os.path.join(cartella, "Previsioni"))


def suff(z):
    return "" if z == "centro" else "_" + z


def modello(z):
    """Una previsione vera della zona, da cui copiare la struttura."""
    for f in sorted(glob.glob(os.path.join(RADICE, "Previsioni", "previsione_*_0500-1200*_v*.json")), reverse=True):
        P = json.load(open(f, encoding="utf-8"))
        if (P.get("area") or "centro") == z and not f.endswith("_agg.json"):
            return P
    raise SystemExit(f"prova_verifiche: nessuna previsione della zona {z} da copiare")


def scrivi(cartella, nome, dati):
    base = os.path.join(cartella, "Previsioni", nome)
    json.dump(dati, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False)
    open(base + ".html", "w", encoding="utf-8").write("<html><body></body></html>")


def previsione(cartella, M, giorno, fin, z, agg=False):
    P = dict(M, finestra="–".join(f"{x[:2]}:{x[2:]}" for x in fin.split("-")),
             generato=(giorno - dt.timedelta(days=1)).strftime("%d/%m/%Y") + " 17:00")
    nome = f"previsione_{giorno.strftime('%d-%m-%Y')}_{fin}{suff(z)}_v9.9{'_agg' if agg else ''}"
    scrivi(cartella, nome, P)
    return nome + ".json"


def main():
    errori = []
    with tempfile.TemporaryDirectory() as T:
        prepara(T)
        oggi = dt.date.today()
        attesi, archivio = [], []
        for i, (fin, fine_nome, mostrata) in enumerate(CASI):
            g = oggi - dt.timedelta(days=1 if i < 3 else (2 if fin == "1900-2400" else 3))
            for z in ZONE:
                rif = previsione(T, modello(z), g, fin, z)
                if mostrata != fin:      # fascia sostituita da quella nuova, fatta a fascia iniziata
                    previsione(T, modello(z), g, mostrata, z, agg=True)
                nome = f"verifica_{g.strftime('%d-%m-%Y')}_{fin[:4]}-{fine_nome}{suff(z)}_v9.9"
                scrivi(T, nome, {"area": z, "provvisoria": False, "previsione": rif + " · generata"})
                attesi.append((z, g.isoformat(), mostrata, nome, fin))
                chiave = f"{g.isoformat()} {fin[:2]}:00–{fin[5:7]}:00" + ("" if z == "centro" else " " + z)
                archivio.append({"chiave": chiave, "file": rif, "esito": "verificata, 6 ore"})
        json.dump({"previsioni": archivio, "ore": []}, open(os.path.join(T, "verifica_archivio.json"), "w"))
        subprocess.run([sys.executable, "build_site.py"], cwd=T, check=True, stdout=subprocess.DEVNULL)
        S = json.load(open(os.path.join(T, "site", "index.json"), encoding="utf-8"))
        voci = {(z["id"], v["data"], v["finestra"].replace("–", "-").replace(":", "")): v for z in S["zone"] for v in z["voci"]}
        for z, g, mostrata, nome, fin in attesi:
            v = voci.get((z, g, mostrata)) or {}
            if os.path.basename(v.get("verifica") or "") != nome + ".html":
                errori.append(f"{g} {mostrata} {z}: pulsante mancante o sbagliato ({v.get('verifica')})")
            if bool(v.get("verifica_fascia")) != (mostrata != fin):
                errori.append(f"{g} {mostrata} {z}: indicazione delle ore verificate sbagliata ({v.get('verifica_fascia')})")
        sentinella = lambda: subprocess.run([sys.executable, "-c", "import controlla_verifiche as C,sys;print(*C.controlla(giro='marea'),sep=chr(10))"],
                                            cwd=T, check=True, capture_output=True, text=True).stdout.strip()
        if sentinella():
            errori.append("la sentinella segnala problemi su un sito corretto: " + sentinella())
        for v in S["zone"][0]["voci"]:       # collegamento tolto: la sentinella deve accorgersene
            v.pop("verifica", None)
        json.dump(S, open(os.path.join(T, "site", "index.json"), "w", encoding="utf-8"))
        if "non visibile" not in sentinella():
            errori.append("la sentinella non si accorge di un pulsante mancante")
    for e in errori:
        print("::error::" + e)
    print(f"prova_verifiche: {len(errori)} errori" if errori else f"prova_verifiche: {len(attesi)} collegamenti corretti, sentinella funzionante")
    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
