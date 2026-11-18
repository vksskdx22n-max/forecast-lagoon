#!/usr/bin/env python3
"""build_site.py v1.0 - prepara la cartella site/ per GitHub Pages.

Legge Previsioni/previsione_*.json e Previsioni/verifica_*.json, copia le pagine HTML in site/ e scrive site/index.json
(elenco delle uscite, dalla piu' recente). Nessun valore e' scritto a mano: la pagina index.html legge index.json.
"""
import os, re, json, glob, shutil, datetime as dt

VERSIONE = "1.0"
CARTELLA = "Previsioni"
SITO = "site"


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
    """Tra piu' file dello stesso giorno e finestra (versioni diverse) tiene il piu' recente."""
    return max(file, key=lambda p: (ora_mod(p), p))


def main():
    shutil.rmtree(SITO, ignore_errors=True)
    os.makedirs(os.path.join(SITO, "giorni"), exist_ok=True)
    gruppi = {}
    for p in glob.glob(os.path.join(CARTELLA, "previsione_*.json")):
        if "giorni_" in os.path.basename(p):
            continue
        d = data_da_nome(os.path.basename(p))
        m = re.search(r"_(\d{4}-\d{4})", os.path.basename(p))
        if d and m:
            gruppi.setdefault((d, m.group(1)), []).append(p)
    ver = {}
    for p in glob.glob(os.path.join(CARTELLA, "verifica_*.json")):
        d = data_da_nome(os.path.basename(p))
        m = re.search(r"_(\d{4}-\d{4})", os.path.basename(p))
        if d and m:
            ver.setdefault((d, m.group(1)), []).append(p)
    voci = []
    for (d, fin), file in sorted(gruppi.items(), reverse=True):
        pj = scegli_ultimo(file)
        P = leggi(pj)
        if not P:
            continue
        voce = {"data": d.isoformat(), "titolo": P.get("titolo"), "finestra": P.get("finestra"),
                "colore": (P.get("verdetto") or {}).get("colore"), "generato": P.get("generato"),
                "versione": P.get("versione")}
        for estensione, chiave in ((".html", "previsione"), (".pdf", "previsione_pdf")):
            sorgente = pj[:-5] + estensione
            if os.path.exists(sorgente):
                destinazione = os.path.join("giorni", os.path.basename(sorgente))
                shutil.copy(sorgente, os.path.join(SITO, destinazione))
                voce[chiave] = destinazione
        if (d, fin) in ver:
            vj = scegli_ultimo(ver[(d, fin)])
            V = leggi(vj) or {}
            vh = vj[:-5] + ".html"
            if os.path.exists(vh):
                destinazione = os.path.join("giorni", os.path.basename(vh))
                shutil.copy(vh, os.path.join(SITO, destinazione))
                voce["verifica"] = destinazione
            voce["verifica_provvisoria"] = bool(V.get("provvisoria"))
            voce["verifica_versione"] = V.get("versione")
        voci.append(voce)
    ora = dt.datetime.now(dt.timezone.utc)
    json.dump({"versione_sito": VERSIONE, "aggiornato_utc": ora.isoformat(timespec="minutes"), "voci": voci},
              open(os.path.join(SITO, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copy("index.html", os.path.join(SITO, "index.html"))
    open(os.path.join(SITO, ".nojekyll"), "w").close()
    print(f"build_site v{VERSIONE}: {len(voci)} giorni in {SITO}/")


if __name__ == "__main__":
    main()
