#!/usr/bin/env python3
"""calcola_fetch.py v1.0 - tabelle di acqua libera controvento (fetch) e fondale medio per i punti dell'onda in laguna.

Per ogni punto, 16 direzioni da cui soffia il vento e 6 livelli di marea (m su ZMPS): risale controvento in linea retta
finche' fondale + livello resta sopra 10 cm. Fondale: DTM CNR-ISMAR 2013 dove c'e', altrimenti Sarretta et al. 2002
(batimetria_laguna.bin). Uscita: JSON {"punti": {nome: {"lat","lon","F_km","d_m"}}} da incollare in FETCH di fd_previsione.
Uso: python3 calcola_fetch.py batimetria_laguna.bin punti.json > fetch.json   (punti.json: {"nome": [lat, lon], ...})
"""
import sys, json, math, struct, zlib
import numpy as np

LIVELLI = [-0.3, 0.0, 0.3, 0.6, 0.9, 1.2]
DIREZIONI = [i * 22.5 for i in range(16)]
PASSO_M = 25.0
MIN_ACQUA = 0.10


def carica(percorso):
    b = open(percorso, "rb").read()
    n = struct.unpack("<I", b[6:10])[0]
    h = json.loads(b[10:10 + n]); o = 10 + n; G = []
    for g in h["griglie"]:
        a = np.frombuffer(zlib.decompress(b[o:o + g["nbytes"]]), "<i2").reshape(g["nr"], g["nc"]); o += g["nbytes"]
        G.append((g, a))
    return G


def quota(G, lat, lon):
    """Quota del fondo in m (negativa = sotto lo zero) dalla prima griglia che copre il punto, None se nessuna."""
    for g, a in G:
        r = int(round((g["lat0"] - lat) / g["dlat"])); c = int(round((lon - g["lon0"]) / g["dlon"]))
        if 0 <= r < g["nr"] and 0 <= c < g["nc"] and a[r, c] != -32768:
            return a[r, c] / 100.0
    return None


def tabella(G, lat, lon, segno):
    F, D = [], []
    kx = 111320 * math.cos(math.radians(lat)); ky = 110540
    for liv in LIVELLI:
        fr, dr = [], []
        for d in DIREZIONI:
            dx, dy = math.sin(math.radians(d)), math.cos(math.radians(d))
            dist, prof = 0.0, []
            while dist < 40000:
                q = quota(G, lat + dy * dist / ky, lon + dx * dist / kx)
                if q is None:
                    break
                acqua = segno * q + liv
                if acqua < MIN_ACQUA:
                    break
                prof.append(acqua); dist += PASSO_M
            fr.append(round(dist / 1000 / 0.05) * 0.05)
            dr.append(round(sum(prof) / len(prof), 2) if prof else 0.0)
        F.append([round(x, 2) for x in fr]); D.append(dr)
    return F, D


if __name__ == "__main__":
    G = carica(sys.argv[1]); P = json.load(open(sys.argv[2]))
    segno = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    out = {}
    for nome, (lat, lon) in P.items():
        F, D = tabella(G, lat, lon, segno)
        out[nome] = {"lat": lat, "lon": lon, "F_km": F, "d_m": D}
    json.dump({"livelli_m": LIVELLI, "direzioni": DIREZIONI, "punti": out}, sys.stdout, ensure_ascii=False)
