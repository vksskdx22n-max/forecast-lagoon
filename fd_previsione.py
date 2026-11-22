#!/usr/bin/env python3
"""fd_previsione.py v1.39 — previsione meteo-marina per un'uscita (Fishing Diary).

All'avvio verifica da solo le previsioni concluse (fd_verifica.py, stessa cartella) e usa l'archivio
verifica_archivio.json per la correzione di ICON-D2 (--no-verifica per saltare la verifica).

Uso (dalla cartella PYTON REPORT):
    python3 fd_previsione.py                      -> domani, 07:00-11:30
    python3 fd_previsione.py 2026-10-30           -> giorno indicato
    python3 fd_previsione.py 2026-10-30 06:30 11:00
    python3 fd_previsione.py 2026-10-30 --json risposta_open_meteo.json   (senza rete: usa una risposta salvata)

File letti nella cartella:
    diario_pesca_MASTER.json       storico uscite (riflessioni, ultima temperatura dell'acqua)
    marea_astronomica_2026.json    marea astronomica CPSM Punta Salute (passo 5 min, ora solare)
    correnti_laguna.json           coefficienti delle correnti (estratti da Laguna Viva 7.26)

Uscita in ./Previsioni/:
    previsione_GG-MM-AAAA_HHMM-HHMM_v1.29.html   pagina completa (dati in JSON, resa in JavaScript; due colonne su schermo largo)
    previsione_GG-MM-AAAA_HHMM-HHMM_v1.29.pdf    pagina unica per WhatsApp          (serve Google Chrome)
    previsione_GG-MM-AAAA_HHMM-HHMM_v1.29_1.png  prima meta' (verdetto, vento, marea, correnti)
    previsione_GG-MM-AAAA_HHMM-HHMM_v1.29_2.png  seconda meta' (meteo, riflessioni)

Vento: Open-Meteo, modelli ICON-D2 (riferimento), ICON-2i (conferma), ECMWF (tendenza).
Stima = ICON-D2 + CORR_D2 nodi quando ICON-D2 prevede almeno CORR_SOGLIA nodi
        (fd_vento_confronto v1.1, 29/10/2026: ICON-D2 sottostima di 2,2 nodi con vento reale 6-12 nodi, 34 misure).
Pioggia (v1.24): ora per ora, probabilita' (Open-Meteo, da modelli ensemble) per ICON-D2 ed ECMWF e mm di ICON-D2, ICON-2i, ECMWF.
Verdetto (v1.27): colore piu' frequente nelle ore; a parita' il primo in ordine di ora (prima: il peggiore).
        Con colori diversi nella finestra il verdetto riporta l'andamento, es. "verde 07 → giallo dalle 08 → rosso dalle 10".
Ripiego (v1.26): nelle ore che ICON-D2 non copre ancora (oltre ~2 giorni) stima = ICON-2i senza correzione
        (ECMWF se manca anche ICON-2i), raffiche e direzione dello stesso modello; scritto nel verdetto e nelle note.
        Pressione, cielo, temperatura, pioggia, alba: ICON-D2, poi ICON-2i, poi ECMWF.
Marea (v1.30): la previsione CPSM (con vento e pressione) si usa sempre quando copre la finestra. Se il bollettino e' piu'
        vecchio di CPSM_ORE_MAX (48 h) resta usato ma e' segnalato come "tendenza, meno affidabile" e l'astronomica e' mostrata
        come riferimento (16/11: bollettino di sabato, +45 cm previsti, livello misurato +11..+36 cm sull'astronomica).
        Se nella finestra il livello varia meno di SOGLIA_FERMA (2 cm/h) il verdetto dice "marea quasi ferma" (morto d'acqua).
Semaforo (v1.35, scelta di Ennio 20/11/2026): vento medio dell'ora arrotondato all'intero (come lo mostra la pagina), verde fino a 6 nodi,
  giallo da 7 a 12, rosso da 13. (v1.28-1.34: valore non arrotondato, verde < 6, giallo 6-12, rosso da 12.) Le raffiche non cambiano
          il colore; avviso nel verdetto se una zona supera RAFF_AVVISO (20 nodi).
Correnti: stima del modello di Laguna Viva, v = c*f - s*f'/omega (stesse formule del doppio clic di LV 7.25+),
          valore riportato = massimo lungo il canale, in m/s.
"""
import sys, os, re, json, math, time, glob, datetime as dt, subprocess, shutil, statistics, tempfile
try:
    import fd_verifica as FV          # verifica automatica e correzione misurata (fd_verifica.py nella stessa cartella)
except ImportError:
    FV = None

VERSIONE = "1.42"
CORR_D2 = 2.2
CORR_SOGLIA = 4.0
CORR_FONTE = "fd_vento_confronto v1.1 · 83 misure CPSM Malamocco Porto (31/05–29/10/2026) · sottostima media ICON-D2 con vento reale 6–12 nodi (34 misure)"
SOGLIA_VERDE, SOGLIA_ROSSO = 6.0, 12.0   # fasce di vento per correzioni e riflessioni (non piu' per il semaforo dalla v1.35)
VERDE_FINO_A, ROSSO_DA = 6, 13       # v1.35: semaforo sul vento medio arrotondato all'intero: verde <= 6, giallo 7-12, rosso >= 13
# v1.38: rischio nebbia ora per ora (stima dai modelli, da verificare con la visibilita' misurata a Venezia Tessera)
NEBBIA_PROB = {"umidita": 97, "vento": 5}        # probabile: umidita' >= 97 % e vento <= 5 kn (oppure codice meteo nebbia)
NEBBIA_POSS = {"umidita": 94, "vento": 8, "nubi_basse": 90}   # possibile: umidita' >= 94 % e vento <= 8 kn, oppure nubi basse >= 90 %
NEBBIA_CODICI = (45, 48)                          # codici meteo WMO "nebbia" / "nebbia con brina"
NEBBIA_LIVELLI = ["nessuna", "possibile", "probabile"]
# v1.39: probabilita' di nebbia = quota degli scenari ICON-D2-EPS (20 membri, punti della zona) che soddisfano la regola "probabile"
NEBBIA_ENS_MODELLO = "icon_d2_eps"
NEBBIA_SOGLIA_ALTA = 50      # %: ore indicate in prima pagina (se nessuna ora arriva qui, si usano quelle sopra NEBBIA_SOGLIA_MIN)
NEBBIA_SOGLIA_MIN = 20       # %: sotto questa probabilita' massima la nebbia non viene segnalata in prima pagina
RAFF_AVVISO = 20.0                    # v1.28: raffiche solo come avviso, non cambiano il colore
CPSM_ORE_MAX = 48                    # v1.30: oltre, il bollettino CPSM e' "tendenza": si usa ma e' segnalato
SOGLIA_FERMA = 2.0                   # v1.29: cm/h, sotto questa variazione massima nella finestra la marea e' "quasi ferma"
SOGLIA_QUADRATURA = 5.0             # cm/h: sotto questa velocita' del livello nella finestra il verso delle correnti stimate non e' attendibile
INIZIO_AUTUNNO = "09-20"            # MM-GG: inizio della stagione usata nelle riflessioni
SOGLIA_RIFL = 7.0                   # nodi: separa uscite calme / ventose nelle riflessioni
ZONE_TITOLO = "Malamocco · Valgrande · San Leonardo · Fisolo · Alberoni"
# Zone di pesca (dal 31/10/2026): Malamocco · Valgrande = stazione CPSM Malamocco Porto (interna, ~3 km dalla bocca lungo il
# Canale dei Petroli, ~1 km dal Canale Valgrande); San Leonardo · Melison; Fisolo (Valgrande nord); Bocca · Alberoni = tra le dighe (non verificata).
# Semaforo, verdetto e striscia = MEDIA delle zone di pesca (scelta di Ennio, 31/10/2026), non la zona peggiore.
PUNTI = [("Malamocco · Valgrande", 45.3397, 12.2919), ("San Leonardo · Melison", 45.3380, 12.2430), ("Fisolo", 45.3670, 12.3050),
         ("Bocca · Alberoni", 45.3330, 12.3250), ("Tronchetto", 45.4400, 12.3040),
         ("Mare aperto", 45.3142, 12.5082),
         ("Entroterra · Mestre", 45.4900, 12.2400)]  # temperatura aria: in laguna i modelli a 2 km la scaldano con l'acqua          # Mare aperto = Piattaforma CNR: vento per il riquadro onda, fuori dal semaforo
ZONE_VENTO = ["Malamocco · Valgrande", "San Leonardo · Melison", "Fisolo", "Bocca · Alberoni"]
# zone riparate dalla bora (v1.22: settore Bora 22,5-67,5°, prima 0-90°): stima = ICON-D2 senza correzione (osservazione di Ennio 31/10/2026, non misurata)
RIPARO_BORA = ["San Leonardo · Melison"]
# v1.32: tre zone = bacini delle bocche di porto. "centro" (predefinita) = bacino di Malamocco, i punti qui sopra.
# "nord" (bacino di Lido) e "sud" (bacino di Chioggia): punti scelti il 19/11/2026 (fondale da batimetria_laguna.bin, acqua libera),
# nessuna correzione del vento (non c'e' ancora una stazione di misura validata nella zona): stima = ICON-D2 cosi' com'e'.
CANALI_CENTRO = None                  # impostato sotto, dopo CANALI
AREE = {"nord": {"titolo": "Laguna Nord · bacino di Lido",
                 "zone": [("Burano sud", 45.4780, 12.4150), ("Sant'Erasmo nord", 45.4550, 12.4250), ("Murano nord", 45.4800, 12.3550),
                          ("Giudecca sud", 45.4180, 12.3180)],
                 "riparo": [], "bocca": "Bocca di Lido",
                 "canali": [("Bocca di Porto di Lido", "Bocca di Lido"), ("Canale di Treporti", "C. Treporti"), ("Canale di S. Erasmo", "C. Sant'Erasmo"),
                            ("Canale Burano", "C. Burano"), ("Canale San Felice", "C. San Felice")]},
        "sud": {"titolo": "Laguna Sud · bacino di Chioggia",
                "zone": [("Pellestrina interno", 45.2650, 12.2900), ("Chioggia · Canale Lombardo", 45.2320, 12.2720),
                         ("Sud ovest · verso Fogolana", 45.2730, 12.2390), ("Millecampi", 45.2850, 12.1750)],
                "riparo": [], "bocca": "Bocca di Chioggia",
                "canali": [("Canale di Chioggia", "Bocca di Chioggia"), ("Canale Lombardo esteno", "C. Lombardo"), ("Canale di Pellestrina", "C. Pellestrina"),
                           ("Canale Ca Roman", "C. Ca' Roman"), ("Canale di Sottomarina", "C. Sottomarina")]}}
AREA = "centro"
BOCCA = "Bocca di Malamocco"
TESTO_NO_CORR = "nessuna correzione: nella zona non c'è ancora una stazione di misura validata (stima = ICON-D2 così com'è)"


def imposta_area(nome):
    """Sostituisce zone, punti, titolo e canali con quelli dell'area scelta (predefinita: centro)."""
    global PUNTI, ZONE_VENTO, ZONE_TITOLO, RIPARO_BORA, AREA, CANALI, BOCCA
    if nome == "centro":
        return
    A = AREE[nome]
    extra = [p for p in PUNTI if p[0] in ("Tronchetto", "Mare aperto", "Entroterra · Mestre")]
    PUNTI = A["zone"] + extra
    ZONE_VENTO = [z[0] for z in A["zone"]]
    ZONE_TITOLO, RIPARO_BORA, AREA = A["titolo"], A["riparo"], nome
    CANALI, BOCCA = A["canali"], A["bocca"]


def zona_corr(z):
    """Zona da cui prendere correzione e raffiche: le zone fuori archivio usano Malamocco · Valgrande."""
    return z if FV and (z in FV.ZONE or z in getattr(FV, "CORR_DA", {})) else "Malamocco · Valgrande"
def nota_verifica_nord_sud():
    """v1.34: nota del vento per nord e sud (prima diceva per errore "usano la correzione di Malamocco Porto")."""
    st = [FV.ZONE[z][1] for z, a in getattr(FV, "ZONE_MEDIA", {}).items() if a == AREA and z in FV.ZONE] if FV else []
    return (". Verifica di controllo, nessuna correzione: media dei punti di calcolo confrontata con " + ", ".join(st)) if st else \
           ". Nessuna stazione di verifica e nessuna correzione"


TESTO_RIPARO = "zona riparata dalla bora: stima = ICON-D2 senza correzione; vento reale probabilmente più basso (osservazione di Ennio, non misurata)"


# Onda da vento in laguna (v1.19): acqua libera controvento (fetch, km) e fondale medio (m) per 16 direzioni da cui
# soffia il vento e 6 livelli di marea (m su ZMPS), calcolati il 03/11/2026 da batimetria_laguna.bin (DTM CNR-ISMAR 2013 +
# Sarretta et al. 2002) risalendo controvento in linea retta finche' il fondale + livello supera 10 cm.
# Formula: Young & Verhagen (1996) per acque basse a fetch limitato. Onda massima ~ 1,8 x significativa. STIMA, non misura.
FETCH = json.loads(r'''{"livelli_m":[-0.3,0.0,0.3,0.6,0.9,1.2],"direzioni":[0.0,22.5,45.0,67.5,90.0,112.5,135.0,157.5,180.0,202.5,225.0,247.5,270.0,292.5,315.0,337.5],"punti":{"Malamocco · Valgrande":{"lat":45.3397,"lon":12.2919,"F_km":[[9.45,8.2,4.95,1.6,1.45,2.1,2.9,3.35,9.3,8.35,8.95,5.2,0.75,4.3,4.2,6.55],[9.45,8.65,4.95,1.6,1.45,2.1,2.9,3.65,9.3,8.45,9.15,6.1,5.0,4.3,4.2,6.55],[9.45,8.65,4.95,1.6,1.45,2.25,2.9,3.65,9.3,13.85,9.25,6.1,5.0,4.3,4.2,6.55],[9.45,8.65,4.95,1.6,1.45,2.3,2.9,3.65,9.3,14.0,9.25,6.1,5.05,4.3,4.2,6.55],[9.45,8.65,4.95,1.6,1.45,2.3,2.9,3.65,9.3,14.05,9.35,11.55,5.05,5.05,4.2,6.55],[9.45,8.65,4.95,1.6,1.45,2.3,2.9,3.65,9.3,14.75,13.6,11.55,5.05,5.05,4.2,6.55]],"d_m":[[1.86,1.73,2.76,8.46,10.48,4.53,0.94,0.54,0.81,1.09,1.87,1.28,2.8,7.69,3.29,2.37],[2.16,1.95,3.06,8.76,10.78,4.83,1.24,0.91,1.11,1.38,2.18,1.45,1.91,7.99,3.59,2.67],[2.46,2.25,3.36,9.06,11.08,4.81,1.54,1.21,1.41,1.28,2.5,1.75,2.21,8.29,3.89,2.97],[2.76,2.55,3.66,9.36,11.38,5.0,1.84,1.51,1.71,1.58,2.8,2.05,2.49,8.59,4.19,3.27],[3.06,2.85,3.96,9.66,11.68,5.3,2.14,1.81,2.01,1.87,3.07,1.88,2.79,7.98,4.49,3.57],[3.36,3.15,4.26,9.96,11.98,5.6,2.44,2.11,2.31,2.11,2.83,2.18,3.09,8.28,4.79,3.87]]},"San Leonardo · Melison":{"lat":45.338,"lon":12.243,"F_km":[[2.3,2.15,12.65,7.75,2.85,6.1,7.0,9.95,9.55,1.1,1.8,2.0,2.05,1.4,1.75,2.05],[2.55,2.15,12.65,7.75,6.55,6.1,7.0,9.95,10.2,4.25,1.8,2.0,2.1,1.5,1.85,2.15],[2.55,2.15,12.65,7.75,6.55,6.1,7.0,9.95,14.5,4.25,1.8,2.0,2.1,1.7,1.95,3.0],[2.55,2.15,12.65,7.75,6.55,6.1,7.0,9.95,14.5,4.25,1.8,2.0,2.1,1.7,1.95,3.0],[2.55,2.15,12.65,7.75,6.55,6.1,7.0,9.95,14.5,7.95,6.1,2.0,2.15,1.7,2.0,3.0],[2.55,2.15,12.65,7.75,6.55,6.1,7.0,9.95,14.5,9.75,10.1,7.3,2.75,1.7,8.15,3.0]],"d_m":[[2.13,2.93,1.42,2.3,1.43,1.14,1.09,1.14,1.12,1.19,0.88,1.09,1.22,1.34,1.4,1.23],[2.21,3.23,1.72,2.6,6.51,1.44,1.39,1.44,1.36,1.28,1.18,1.39,1.49,1.55,1.62,1.47],[2.51,3.53,2.02,2.9,6.81,1.74,1.69,1.74,1.43,1.58,1.48,1.69,1.79,1.79,1.83,2.0],[2.81,3.83,2.32,3.2,7.11,2.04,1.99,2.04,1.73,1.88,1.78,1.99,2.09,2.09,2.13,2.3],[3.11,4.13,2.62,3.5,7.41,2.34,2.29,2.34,2.03,2.18,1.44,2.29,2.34,2.39,2.38,2.6],[3.41,4.43,2.92,3.8,7.71,2.64,2.59,2.64,2.33,2.27,1.71,1.85,2.27,2.69,1.76,2.9]]},"Fisolo":{"lat":45.367,"lon":12.305,"F_km":[[5.0,5.05,5.9,2.9,2.4,2.25,2.25,2.3,6.5,11.65,9.55,1.2,4.0,4.5,4.95,8.1],[5.0,6.85,6.15,2.9,2.4,2.25,2.25,2.3,8.5,13.55,9.75,1.2,4.0,4.65,4.95,8.1],[5.0,6.85,6.15,2.9,2.4,2.25,2.25,2.3,8.5,15.25,9.75,1.2,4.0,4.65,4.95,8.1],[5.0,6.85,6.15,2.9,2.4,2.25,2.25,2.3,8.5,15.3,9.75,1.2,4.0,5.3,4.95,8.1],[5.0,6.85,6.15,2.9,2.4,2.25,2.25,2.3,8.5,17.2,10.7,1.2,4.0,5.5,4.95,8.1],[5.0,6.85,6.15,2.9,2.4,2.25,2.25,2.3,8.5,17.3,16.75,1.2,4.0,5.5,4.95,8.1]],"d_m":[[0.83,0.77,0.82,1.21,1.33,1.35,1.62,1.49,2.42,1.94,1.78,1.19,1.58,1.3,1.28,0.98],[1.13,1.02,1.09,1.51,1.63,1.65,1.92,1.79,2.31,2.0,2.05,1.49,1.88,1.56,1.58,1.28],[1.43,1.32,1.39,1.81,1.93,1.95,2.22,2.09,2.61,2.1,2.35,1.79,2.18,1.86,1.88,1.58],[1.73,1.62,1.69,2.11,2.23,2.25,2.52,2.39,2.91,2.39,2.65,2.09,2.48,1.95,2.18,1.88],[2.03,1.92,1.99,2.41,2.53,2.55,2.82,2.69,3.21,2.5,2.73,2.39,2.78,2.19,2.48,2.18],[2.33,2.22,2.29,2.71,2.83,2.85,3.12,2.99,3.51,2.79,2.53,2.69,3.08,2.49,2.78,2.48]]},"C. Petroli · Fusina":{"lat":45.405,"lon":12.268,"F_km":[[4.8,5.7,4.9,4.3,3.9,6.45,7.05,10.15,18.7,1.85,0.85,0.25,0.2,1.6,1.1,1.8],[4.8,8.15,4.9,5.3,3.9,6.45,7.05,10.15,22.15,1.85,0.85,0.25,0.2,1.95,1.1,1.8],[4.8,9.15,4.9,5.3,3.9,6.45,7.05,10.15,22.15,1.85,0.85,0.25,0.2,1.95,1.1,1.8],[4.8,9.15,4.9,5.3,3.9,6.45,7.05,10.15,22.15,1.85,0.85,0.25,0.2,1.95,1.1,1.8],[4.8,9.15,4.9,5.3,3.9,6.45,7.05,10.15,22.15,1.85,0.85,0.25,0.2,2.2,1.1,1.8],[4.8,9.15,4.9,5.3,3.9,6.45,7.05,10.15,22.15,1.85,0.85,0.25,0.2,3.0,1.1,1.8]],"d_m":[[0.63,0.85,2.57,0.82,0.87,0.94,1.17,3.01,1.29,3.69,2.99,0.71,0.79,1.72,2.67,2.72],[0.93,0.96,2.87,1.11,1.17,1.24,1.47,3.31,1.88,3.99,3.29,1.01,1.09,1.7,2.97,3.02],[1.23,1.18,3.17,1.41,1.47,1.54,1.77,3.61,2.18,4.29,3.59,1.31,1.39,2.0,3.27,3.32],[1.53,1.48,3.47,1.71,1.77,1.84,2.07,3.91,2.48,4.59,3.89,1.61,1.69,2.3,3.57,3.62],[1.83,1.78,3.77,2.01,2.07,2.14,2.37,4.21,2.78,4.89,4.19,1.91,1.99,2.42,3.87,3.92],[2.13,2.08,4.07,2.31,2.37,2.44,2.67,4.51,3.08,5.19,4.49,2.21,2.29,2.11,4.17,4.22]]},"Giudecca sud":{"lat":45.418,"lon":12.318,"F_km":[[0.85,0.2,0.2,1.5,1.6,3.45,1.3,4.7,1.3,5.2,6.9,4.9,4.75,4.5,7.6,0.9],[0.85,0.75,0.9,1.5,3.05,3.45,3.85,4.7,1.3,5.2,6.9,4.9,4.75,4.5,7.6,0.9],[0.85,0.75,0.9,1.5,3.05,3.45,3.85,4.7,1.3,5.2,6.9,4.9,4.75,4.5,7.6,0.9],[0.85,0.75,0.9,1.5,3.05,3.45,3.85,4.7,1.3,5.2,6.9,4.9,4.75,4.5,7.6,0.9],[0.85,0.75,0.9,1.5,3.05,3.45,3.85,4.7,1.3,5.2,6.9,4.9,4.75,4.5,7.6,0.9],[0.85,0.75,0.9,1.5,3.05,3.45,3.85,4.7,1.3,5.2,6.9,4.9,4.75,4.5,7.6,0.9]],"d_m":[[0.6,0.14,0.16,0.58,0.35,0.72,0.7,0.64,0.22,0.7,1.19,1.15,1.14,0.78,2.87,0.69],[0.9,0.94,1.0,0.88,1.01,1.02,0.97,0.94,0.52,1.0,1.49,1.45,1.44,1.08,3.17,0.99],[1.2,1.24,1.3,1.18,1.31,1.32,1.27,1.24,0.82,1.3,1.79,1.75,1.74,1.38,3.47,1.29],[1.5,1.54,1.6,1.48,1.61,1.62,1.57,1.54,1.12,1.6,2.09,2.05,2.04,1.68,3.77,1.59],[1.8,1.84,1.9,1.78,1.91,1.92,1.87,1.84,1.42,1.9,2.39,2.35,2.34,1.98,4.07,1.89],[2.1,2.14,2.2,2.08,2.21,2.22,2.17,2.14,1.72,2.2,2.69,2.65,2.64,2.28,4.37,2.19]]},"Sacca Sessola":{"lat":45.404,"lon":12.313,"F_km":[[2.4,2.4,3.45,0.4,0.45,3.3,3.4,4.55,6.5,18.0,6.65,4.6,4.15,4.6,5.3,6.55],[2.4,2.4,3.45,0.4,0.5,3.3,3.4,4.55,6.5,18.25,6.65,4.6,4.15,4.6,5.3,6.95],[2.4,2.4,3.45,0.4,0.5,3.3,3.4,4.55,6.5,18.25,6.65,4.6,4.15,4.6,5.3,7.0],[2.4,2.4,3.45,0.4,0.5,3.3,3.4,4.55,6.5,18.25,6.65,4.6,4.15,4.6,5.3,7.0],[2.4,2.4,3.45,0.4,0.5,3.3,3.4,4.55,6.5,18.7,6.65,4.6,4.15,4.6,5.3,7.0],[2.4,2.4,3.45,0.4,0.5,3.3,3.4,4.55,6.5,19.6,6.65,4.6,4.15,4.6,5.3,7.3]],"d_m":[[0.5,0.41,0.48,0.26,0.22,0.62,0.61,1.14,1.06,1.31,1.31,1.12,1.25,1.11,0.67,0.89],[0.8,0.71,0.78,0.56,0.51,0.92,0.91,1.44,1.36,1.59,1.61,1.42,1.55,1.41,0.97,1.14],[1.1,1.01,1.08,0.86,0.81,1.22,1.21,1.74,1.66,1.89,1.91,1.72,1.85,1.71,1.27,1.43],[1.4,1.31,1.38,1.16,1.11,1.52,1.51,2.04,1.96,2.19,2.21,2.02,2.15,2.01,1.57,1.73],[1.7,1.61,1.68,1.46,1.41,1.82,1.81,2.34,2.26,2.45,2.51,2.32,2.45,2.31,1.87,2.03],[2.0,1.91,1.98,1.76,1.71,2.12,2.11,2.64,2.56,2.65,2.81,2.62,2.75,2.61,2.17,2.28]]},"San Clemente · La Grazia":{"lat":45.412,"lon":12.332,"F_km":[[1.25,1.45,2.7,2.1,0.25,0.2,0.25,0.2,0.2,0.25,0.95,6.15,5.7,7.0,1.2,1.45],[1.25,1.45,2.7,2.1,0.25,0.2,0.25,3.05,3.2,17.25,0.95,6.15,5.7,7.0,2.1,1.45],[1.25,1.45,2.7,2.1,0.25,0.2,0.25,3.05,3.2,20.75,0.95,6.15,5.7,7.0,2.1,1.45],[1.25,1.45,2.7,2.1,0.25,0.2,0.25,3.05,3.2,20.75,0.95,6.15,5.7,7.0,2.1,1.45],[1.25,1.45,2.7,2.1,0.25,0.2,0.25,3.05,3.2,22.55,0.95,6.15,5.7,7.0,2.1,1.45],[1.25,1.45,2.7,2.1,0.25,0.2,0.25,3.05,3.2,22.55,0.95,6.15,5.7,7.0,2.1,1.45]],"d_m":[[0.4,0.4,1.36,1.34,4.44,4.13,2.59,2.84,2.79,2.83,1.07,1.1,0.92,1.93,0.33,0.46],[0.7,0.7,1.66,1.64,4.74,4.43,2.89,1.14,1.32,1.81,1.37,1.4,1.22,2.23,0.85,0.76],[1.0,1.0,1.96,1.94,5.04,4.73,3.19,1.44,1.62,1.87,1.67,1.7,1.52,2.53,1.15,1.06],[1.3,1.3,2.26,2.24,5.34,5.03,3.49,1.74,1.92,2.17,1.97,2.0,1.82,2.83,1.45,1.36],[1.6,1.6,2.56,2.54,5.64,5.33,3.79,2.04,2.22,2.35,2.27,2.3,2.12,3.13,1.75,1.66],[1.9,1.9,2.86,2.84,5.94,5.63,4.09,2.34,2.52,2.65,2.57,2.6,2.42,3.43,2.05,1.96]]},"Burano sud":{"lat":45.478,"lon":12.415,"F_km":[[0.7,0.65,0.75,0.8,0.8,1.0,0.45,0.3,0.5,0.15,0.7,1.05,8.65,5.75,0.9,0.75],[0.7,0.65,1.25,1.75,1.1,2.6,0.65,0.45,0.5,0.75,0.8,4.9,8.7,5.85,0.9,1.0],[0.7,0.65,1.3,1.75,1.15,2.6,0.65,0.45,0.5,0.75,0.8,4.9,8.7,5.85,0.9,1.0],[0.7,0.65,1.35,4.95,1.15,2.6,0.65,0.45,0.5,2.4,0.8,4.9,8.7,5.85,0.9,1.0],[0.7,0.65,1.35,9.9,1.4,2.6,0.9,0.45,0.5,2.4,3.2,4.9,9.55,5.85,0.9,1.0],[0.7,0.65,1.35,9.9,1.4,2.6,0.9,0.45,0.5,2.4,3.2,4.9,9.7,5.85,0.9,1.0]],"d_m":[[0.44,0.97,0.44,0.46,0.92,0.68,0.46,0.67,1.64,0.16,0.29,0.35,0.61,0.58,0.51,0.31],[0.74,1.27,0.58,1.14,0.95,2.13,0.6,0.71,1.94,0.63,0.55,0.89,0.91,0.87,0.81,0.68],[1.04,1.57,0.85,1.44,1.23,2.43,0.9,1.01,2.24,0.93,0.85,1.19,1.21,1.17,1.11,0.98],[1.34,1.87,1.14,1.5,1.53,2.73,1.2,1.31,2.54,1.2,1.15,1.49,1.51,1.47,1.41,1.28],[1.64,2.17,1.44,1.69,1.67,3.03,1.5,1.61,2.84,1.5,1.21,1.79,1.75,1.77,1.71,1.58],[1.94,2.47,1.74,1.99,1.97,3.33,1.8,1.91,3.14,1.8,1.51,2.09,2.04,2.07,2.01,1.88]]},"Sant'Erasmo nord":{"lat":45.455,"lon":12.425,"F_km":[[0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],[0.9,0.5,2.3,1.2,0.8,0.65,0.6,0.65,0.85,4.65,1.2,1.55,0.7,0.55,0.45,0.6],[0.9,0.5,2.3,1.2,0.8,0.65,0.6,0.65,0.85,4.65,1.25,1.55,0.7,0.55,0.45,0.6],[0.9,6.2,2.3,1.2,0.8,0.65,0.6,0.65,0.85,4.65,1.5,1.8,0.7,0.55,0.45,0.6],[0.9,6.2,2.3,1.2,0.8,0.65,0.6,0.65,0.85,4.65,3.5,1.8,0.7,0.55,0.45,0.6],[0.9,6.2,2.3,1.2,0.8,0.65,0.6,0.65,0.85,4.65,3.5,1.8,0.7,0.55,0.45,0.6]],"d_m":[[0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],[0.21,0.23,3.64,4.74,4.98,5.1,5.25,5.35,6.12,5.4,0.44,0.32,0.44,0.38,0.27,0.24],[0.51,0.53,3.94,5.04,5.28,5.4,5.55,5.65,6.42,5.7,0.71,0.61,0.74,0.68,0.57,0.54],[0.81,1.26,4.24,5.34,5.58,5.7,5.85,5.95,6.72,6.0,0.89,0.87,1.04,0.98,0.87,0.84],[1.11,1.56,4.54,5.64,5.88,6.0,6.15,6.25,7.02,6.3,2.72,1.17,1.34,1.28,1.17,1.14],[1.41,1.86,4.84,5.94,6.18,6.3,6.45,6.55,7.32,6.6,3.02,1.47,1.64,1.58,1.47,1.44]]},"Murano nord":{"lat":45.48,"lon":12.355,"F_km":[[2.8,3.15,3.5,3.4,5.45,3.0,2.9,3.9,1.65,4.0,10.5,6.6,3.2,0.2,0.2,1.3],[2.8,3.3,3.5,3.5,6.0,3.25,3.0,4.4,1.65,4.0,10.5,6.6,3.8,0.2,0.2,1.5],[2.8,3.3,3.5,4.3,6.0,3.3,3.1,4.4,1.65,4.0,10.5,6.6,3.8,0.2,0.2,1.55],[2.8,3.5,4.3,4.75,9.8,3.3,3.5,4.4,1.65,4.0,10.5,6.6,3.8,0.2,0.2,1.55],[2.8,4.8,4.3,4.75,9.8,5.0,3.5,4.4,1.65,4.0,10.5,6.6,4.1,0.2,0.2,1.8],[2.8,4.8,4.3,4.75,9.8,5.0,3.5,4.4,1.65,4.0,10.5,6.6,4.1,0.2,0.2,1.8]],"d_m":[[0.61,0.55,0.59,0.67,0.66,0.73,0.91,0.78,0.73,1.08,1.2,0.66,0.56,0.86,0.84,0.64],[0.91,0.83,0.89,0.96,0.91,0.97,1.18,1.11,1.03,1.38,1.5,0.96,0.78,1.16,1.14,0.87],[1.21,1.13,1.19,1.11,1.21,1.26,1.45,1.41,1.33,1.68,1.8,1.26,1.08,1.46,1.44,1.13],[1.51,1.35,1.51,1.43,1.52,1.55,1.59,1.71,1.63,1.98,2.1,1.56,1.38,1.76,1.74,1.43],[1.81,1.35,1.81,1.73,1.82,1.7,1.89,2.01,1.93,2.28,2.4,1.86,1.6,2.06,2.04,1.66],[2.11,1.65,2.11,2.03,2.12,2.0,2.19,2.31,2.23,2.58,2.7,2.16,1.9,2.36,2.34,1.96]]},"Pellestrina interno":{"lat":45.265,"lon":12.29,"F_km":[[22.95,2.95,1.2,0.8,0.8,0.85,1.0,1.95,2.7,3.6,6.45,2.05,6.1,5.1,7.5,11.0],[23.2,2.95,1.2,0.8,0.8,0.85,1.0,1.95,2.7,7.4,6.8,2.05,6.1,5.45,7.5,11.0],[23.2,2.95,1.2,0.8,0.8,0.85,1.0,1.95,2.7,7.4,6.8,2.05,6.1,5.45,9.55,11.0],[23.2,2.95,1.2,0.8,0.8,0.85,1.0,1.95,2.7,7.4,6.9,2.05,10.15,5.45,10.6,11.35],[23.2,2.95,1.2,0.8,0.8,0.85,1.0,1.95,2.7,7.4,7.4,2.05,11.0,5.6,10.6,11.35],[23.5,2.95,1.2,0.8,0.8,0.85,1.0,1.95,2.7,7.4,7.6,2.05,11.0,8.6,10.6,11.35]],"d_m":[[1.5,1.32,1.42,1.16,1.36,1.47,1.83,3.27,2.41,1.28,1.55,1.55,1.23,1.31,1.44,1.59],[1.79,1.62,1.72,1.46,1.66,1.77,2.13,3.57,2.71,1.84,1.77,1.85,1.53,1.52,1.74,1.89],[2.09,1.92,2.02,1.76,1.96,2.07,2.43,3.87,3.01,2.14,2.07,2.15,1.82,1.82,1.89,2.19],[2.39,2.22,2.32,2.06,2.26,2.37,2.73,4.17,3.31,2.44,2.35,2.45,1.73,2.12,2.13,2.44],[2.69,2.52,2.62,2.36,2.56,2.67,3.03,4.47,3.61,2.74,2.52,2.75,1.91,2.36,2.43,2.74],[2.96,2.82,2.92,2.66,2.86,2.97,3.33,4.77,3.91,3.04,2.76,3.05,2.21,2.68,2.73,3.04]]},"Chioggia · Canale Lombardo":{"lat":45.232,"lon":12.272,"F_km":[[0.3,6.15,3.1,1.5,5.4,1.9,2.1,0.9,0.8,3.5,3.6,3.25,4.85,2.5,5.95,4.45],[23.9,6.15,3.1,1.5,5.4,1.9,2.1,0.9,2.1,3.5,3.6,3.45,4.85,2.65,6.0,10.0],[23.9,6.15,3.1,1.5,5.4,1.9,2.1,0.9,2.1,3.5,3.6,3.45,4.85,2.75,6.0,10.3],[23.9,6.15,3.1,1.5,5.4,1.9,2.1,0.9,2.1,3.5,3.6,3.45,5.1,2.75,12.3,15.55],[23.9,6.15,3.1,1.5,5.4,1.9,2.1,0.9,2.1,3.5,3.6,3.45,5.4,4.9,12.75,15.55],[23.9,6.15,3.1,1.5,5.4,1.9,2.1,0.9,2.1,3.5,3.6,3.45,5.4,4.9,12.75,15.55]],"d_m":[[0.27,1.28,1.99,2.21,10.78,3.77,3.55,6.01,3.83,2.4,1.79,1.83,0.9,1.58,1.01,1.08],[1.66,1.58,2.29,2.51,11.08,4.07,3.85,6.31,3.85,2.7,2.09,2.03,1.2,1.79,1.31,1.65],[1.96,1.88,2.59,2.81,11.38,4.37,4.15,6.61,4.15,3.0,2.39,2.33,1.5,2.04,1.61,1.91],[2.26,2.18,2.89,3.11,11.68,4.67,4.45,6.91,4.45,3.3,2.69,2.63,1.74,2.33,1.65,1.92],[2.56,2.48,3.19,3.41,11.98,4.97,4.75,7.21,4.75,3.6,2.99,2.93,1.94,1.8,1.91,2.22],[2.86,2.78,3.49,3.71,12.28,5.27,5.05,7.51,5.05,3.9,3.29,3.23,2.24,2.1,2.21,2.52]]},"Sud ovest · verso Fogolana":{"lat":45.273,"lon":12.239,"F_km":[[9.1,18.0,8.75,5.65,4.85,0.8,5.5,7.2,0.75,2.5,2.4,2.25,1.1,1.0,1.5,3.5],[9.1,18.0,8.8,5.65,4.85,5.1,5.5,7.2,0.75,2.75,2.4,2.25,1.1,1.05,1.5,3.5],[9.2,18.0,9.0,5.65,4.85,5.1,5.5,7.2,0.75,2.8,2.4,2.3,1.1,1.05,1.5,3.5],[9.9,18.0,9.0,5.65,4.85,5.1,5.5,7.2,0.75,2.8,2.9,5.8,1.1,1.25,1.6,9.95],[9.9,18.0,9.0,5.65,4.85,5.1,5.5,7.2,0.75,5.95,2.9,5.8,6.8,7.1,1.7,9.95],[9.9,18.0,9.0,5.65,4.85,5.1,5.5,7.2,0.75,5.95,2.9,5.8,7.0,7.1,7.3,9.95]],"d_m":[[1.74,1.64,1.17,1.18,1.2,1.24,2.81,1.31,1.36,0.95,0.82,0.69,0.88,0.96,1.0,1.67],[2.04,1.94,1.47,1.48,1.5,1.74,3.11,1.61,1.66,1.16,1.11,0.99,1.18,1.24,1.28,1.95],[2.31,2.24,1.74,1.78,1.8,2.04,3.41,1.91,1.96,1.45,1.41,1.27,1.48,1.54,1.58,2.25],[2.49,2.54,2.04,2.08,2.1,2.34,3.71,2.21,2.26,1.74,1.52,1.17,1.78,1.57,1.79,1.76],[2.79,2.84,2.34,2.38,2.4,2.64,4.01,2.51,2.56,1.53,1.82,1.47,1.46,1.89,1.98,2.06],[3.09,3.14,2.64,2.68,2.7,2.94,4.31,2.81,2.86,1.83,2.12,1.77,1.73,2.19,2.46,2.36]]},"Millecampi":{"lat":45.285,"lon":12.175,"F_km":[[2.6,1.9,1.4,1.4,2.05,2.3,3.1,3.2,3.45,1.95,1.25,1.15,1.25,1.75,2.1,2.9],[2.6,5.35,1.4,1.4,2.3,2.3,3.1,3.2,3.55,2.35,2.3,1.15,1.25,1.9,2.1,2.9],[2.65,5.35,1.4,1.7,2.35,2.3,3.1,3.2,3.55,2.35,2.3,1.15,1.25,1.9,2.1,2.95],[3.7,6.4,26.1,12.4,3.8,5.4,5.55,3.2,3.6,3.65,2.3,1.15,1.25,1.9,2.1,2.95],[3.7,6.4,26.1,12.4,10.0,5.4,10.8,3.2,3.6,3.65,2.4,1.75,1.6,1.9,2.1,3.55],[3.7,6.4,26.1,12.4,10.0,5.4,10.8,3.2,3.6,3.65,2.6,2.0,1.6,1.9,2.1,3.55]],"d_m":[[0.59,0.78,0.59,0.58,0.69,1.04,0.69,0.67,0.5,0.49,0.55,0.61,0.62,0.62,0.59,0.53],[0.89,1.09,0.89,0.88,1.22,1.34,0.99,0.97,0.79,0.72,0.57,0.91,0.92,0.89,0.89,0.83],[1.18,1.39,1.19,1.33,1.5,1.64,1.29,1.27,1.09,1.02,0.87,1.21,1.22,1.19,1.19,1.12],[1.36,1.64,3.24,2.28,1.86,1.44,1.16,1.57,1.38,1.11,1.17,1.51,1.53,1.49,1.49,1.42],[1.66,1.94,3.54,2.58,2.29,1.74,1.73,1.87,1.68,1.41,1.41,1.55,1.64,1.79,1.79,1.54],[1.96,2.23,3.84,2.88,2.59,2.04,2.03,2.17,1.98,1.71,1.62,1.7,1.94,2.09,2.09,1.84]]}}}''')
H_MAX_SU_HS = 1.8


def onda_laguna(punto, kn, dir_g, liv_m):
    """(Hs, Hmax) in metri stimati nel punto, o None."""
    P = FETCH["punti"].get(punto)
    if P is None or kn is None or dir_g is None or liv_m is None:
        return None
    q = (dir_g % 360) / 22.5; i0 = int(q) % 16; i1 = (i0 + 1) % 16; u = q - int(q)     # tra le due direzioni vicine
    L = FETCH["livelli_m"]; x = min(max(liv_m, L[0]), L[-1])
    j = max(k for k in range(len(L) - 1) if L[k] <= x) if x < L[-1] else len(L) - 2
    t = (x - L[j]) / (L[j + 1] - L[j])
    def bil(M):
        a0 = M[j][i0] + (M[j + 1][i0] - M[j][i0]) * t; a1 = M[j][i1] + (M[j + 1][i1] - M[j][i1]) * t
        return a0 + (a1 - a0) * u
    F = bil(P["F_km"]) * 1000
    d = bil(P["d_m"])
    U = kn * 0.514444; g = 9.81
    if F <= 0 or d <= 0 or U <= 0:
        return (0.0, 0.0)
    A = 0.493 * (g * d / U ** 2) ** 0.75; B = 3.13e-3 * (g * F / U ** 2) ** 0.57
    hs = 4 * math.sqrt(3.64e-3 * (math.tanh(A) * math.tanh(B / math.tanh(A))) ** 1.74) * U ** 2 / g
    return round(hs, 2), round(hs * H_MAX_SU_HS, 2)


def media_ok(v):
    """Media dei valori non None, o None."""
    v = [x for x in v if x is not None]
    return statistics.mean(v) if v else None


def max_ok(v, default=None):
    return max((x for x in v if x is not None), default=default)


def riparata(z, dir_g):
    return z in RIPARO_BORA and bora(dir_g)
MODELLI = [("icon_d2", "D2"), ("italia_meteo_arpae_icon_2i", "2i"), ("ecmwf_ifs025", "EC")]
CANALI = [("Porto di Malamocco", "Bocca di Malamocco"), ("Canale Malamocco Marghera", "C. Malamocco Marghera"),
          ("Canale Rocchetta", "C. Rocchetta"), ("Canale Fisolo", "C. Fisolo"),
          ("Canale Darsena San Leonardo", "C. Darsena San Leonardo")]
ID_ESCLUSI = {1779811089369}
OMEGA = 2 * math.pi / (12.4206 * 60)
GIORNI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
CROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Previsione marea CPSM (estremali con effetto di vento e pressione), ora italiana; via proxy, riserva: open data del Comune
CPSM_PREV_PROXY = ("https://fd-cpsm-proxy.ennioweb.workers.dev/previsione", ["-H", "Origin: https://vksskdx22n-max.github.io"])
CPSM_PREV_OPENDATA = ("https://dati.venezia.it/sites/default/files/dataset/opendata/previsione.json",
                      ["-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"])


def tz_italia(giorno):
    """+2 h dall'ultima domenica di marzo all'ultima domenica di ottobre, altrimenti +1 h."""
    def ultima_dom(m):
        d = dt.date(giorno.year, m, 31)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    return dt.timezone(dt.timedelta(hours=2 if ultima_dom(3) <= giorno < ultima_dom(10) else 1))


def num(x, d=1):
    return None if x is None else f"{x:.{d}f}".replace(".", ",")


def card(g):
    return ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'][int((g % 360) / 22.5 + .5) % 16]


# v1.22: settori e nomi dei venti da "Il vento nell'Adriatico settentrionale 1983-2004" (Centro Maree, Comune di Venezia)
NOMI_VENTO = ["Tramontana", "Bora", "Levante", "Scirocco", "Ostro", "Libeccio", "Ponente", "Maestrale"]


def gradi(g):
    return int(g % 360 + 0.5) % 360


def nome_vento(g):
    return NOMI_VENTO[int((gradi(g) + 22.5) // 45) % 8]


def gradi_card(g):
    return f"{gradi(g)}° {card(gradi(g))}"


def dir_txt(g):
    return "–" if g is None else f"{gradi_card(g)} · {nome_vento(g)}"


def intervallo_corr(v):
    """v1.23: '+0,0…+1,0' invece dell'elenco di tutti i valori orari."""
    x = sorted(v, key=lambda t: float(t.replace(",", ".")))
    return " / ".join(x) if len(x) <= 2 else f"da {x[0]} a {x[-1]}"


def bora(g):
    return g is not None and 23 <= gradi(g) <= 67


def arrotonda(kn):
    """v1.35: nodi interi come li mostra la pagina (valore a un decimale, poi mezzo in su: 6,5 -> 7)."""
    return int(math.floor(round(kn, 1) + 0.5))


def semaforo(kn, raff):
    """v1.35: colore dal vento medio arrotondato all'intero; raff resta nella firma per compatibilita' ma non conta."""
    n = arrotonda(kn)
    if n >= ROSSO_DA:
        return 'rosso'
    return 'giallo' if n > VERDE_FINO_A else 'verde'


ORD = {'verde': 0, 'giallo': 1, 'rosso': 2}


def correzione(d2, dir_g, zona="Malamocco · Valgrande"):
    """Correzione da sommare a ICON-D2: misurata dalle verifiche (fd_verifica) se ci sono abbastanza ore, altrimenti fissa.
    v1.32: solo per l'area centro; nord e sud senza correzione."""
    if AREA != "centro":
        return 0.0, TESTO_NO_CORR, 0
    if FV:
        return FV.correzione(d2, dir_g, ".", zona_corr(zona))
    return (CORR_D2 if d2 >= CORR_SOGLIA else 0.0), CORR_FONTE, 0


# ------------------------------------------------------------------ Open-Meteo
def scarica(giorno, da, a):
    g = giorno.isoformat()
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={','.join(str(p[1]) for p in PUNTI)}&longitude={','.join(str(p[2]) for p in PUNTI)}"
           "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl,temperature_2m,precipitation,precipitation_probability,cloud_cover"
           ",relative_humidity_2m,cloud_cover_low,weather_code"   # v1.38: nebbia
           f"&models={','.join(m for m, _ in MODELLI)}&wind_speed_unit=kn&timezone=Europe%2FRome"
           f"&start_hour={g}T{max(0, da.hour - 2):02d}:00&end_hour={g}T{min(23, a.hour + 2):02d}:00&daily=sunrise,sunset&start_date={g}&end_date={g}")
    for tentativo in range(3):                      # v1.33: fino a 3 tentativi (Open-Meteo a volte non risponde)
        r = subprocess.run(["curl", "-s", "-m", "45", url], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().startswith("["):
            return json.loads(r.stdout)
        print(f"Open-Meteo non risponde (tentativo {tentativo + 1} di 3): {(r.stdout or r.stderr)[:200]}")
        time.sleep(20 * (tentativo + 1))
    sys.exit(f"Errore Open-Meteo dopo 3 tentativi: {r.stdout[:300] or r.stderr[:300]}")


def scarica_ensemble(giorno, da, a, path=None):
    """v1.39: ICON-D2-EPS sui punti della zona (lista di risposte Open-Meteo, una per punto), o None."""
    if path:
        return json.load(open(path, encoding="utf-8"))
    g = giorno.isoformat()
    pts = [p for p in PUNTI if p[0] in ZONE_VENTO]
    url = ("https://ensemble-api.open-meteo.com/v1/ensemble?"
           f"latitude={','.join(str(p[1]) for p in pts)}&longitude={','.join(str(p[2]) for p in pts)}"
           "&hourly=relative_humidity_2m,wind_speed_10m,cloud_cover_low,weather_code"
           f"&models={NEBBIA_ENS_MODELLO}&wind_speed_unit=kn&timezone=Europe%2FRome&start_date={g}&end_date={g}")
    for tentativo in range(2):
        r = subprocess.run(["curl", "-s", "-m", "45", url], capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
            d = d if isinstance(d, list) else [d]
            if all("hourly" in x for x in d):
                return d
        except ValueError:
            pass
        print(f"Open-Meteo ensemble non risponde (tentativo {tentativo + 1} di 2): {(r.stdout or r.stderr)[:200]}")
        time.sleep(10)
    return None


def probabilita_nebbia(ENS, ore_txt):
    """v1.39: {ora 'HH:00': (percentuale, scenari)} con la regola "probabile" applicata a ogni membro e punto."""
    out = {}
    for t in ore_txt:
        si = tot = 0
        for P in ENS or []:
            H = P["hourly"]; i = {x: j for j, x in enumerate(H.get("time", []))}.get(t)
            if i is None:
                continue
            for key in [k_ for k_ in H if k_ == "relative_humidity_2m" or k_.startswith("relative_humidity_2m_member")]:
                suf = key[len("relative_humidity_2m"):]
                rh = H[key][i]; ws = (H.get("wind_speed_10m" + suf) or [None] * (i + 1))[i]
                wc = (H.get("weather_code" + suf) or [None] * (i + 1))[i]
                if rh is None and wc is None:
                    continue
                tot += 1
                if wc in NEBBIA_CODICI or (rh is not None and ws is not None and rh >= NEBBIA_PROB["umidita"] and ws <= NEBBIA_PROB["vento"]):
                    si += 1
        out[t] = (round(100 * si / tot) if tot else None, tot)
    return out


# ------------------------------------------------------------------ onda in mare aperto (Open-Meteo Marine)
MARE_PUNTO = (45.325, 12.335)          # davanti alla bocca di Malamocco: il modello lo porta sulla cella a ~45,29 N 12,375 E
SOGLIA_ONDA = 1.0                      # m: oltre questa altezza da NE o SE il report segnala mare formato fuori dalla bocca


def scarica_mare(giorno, path=None):
    """Risposta hourly dell'API marina per MARE_PUNTO, o None."""
    try:
        if path:
            return json.load(open(path, encoding="utf-8"))
        g = giorno.isoformat()
        url = (f"https://marine-api.open-meteo.com/v1/marine?latitude={MARE_PUNTO[0]}&longitude={MARE_PUNTO[1]}"
               f"&hourly=wave_height,wave_direction,wave_period&timezone=Europe%2FRome&start_date={g}&end_date={g}")
        r = subprocess.run(["curl", "-s", "-m", "20", url], capture_output=True, text=True)
        d = json.loads(r.stdout)
        return d if "hourly" in d else None
    except (ValueError, OSError):
        return None


# ------------------------------------------------------------------ marea prevista CPSM
def scarica_cpsm(path=None):
    """Lista di (tipo, ms, cm) degli estremali previsti CPSM e ora di emissione; ([], None) se non disponibile."""
    dati = None
    if path:
        dati = json.load(open(path, encoding="utf-8"))
    else:
        for url, extra in (CPSM_PREV_PROXY, CPSM_PREV_OPENDATA):
            r = subprocess.run(["curl", "-s", "-m", "20"] + extra + [url], capture_output=True, text=True)
            try:
                dati = json.loads(r.stdout)
                if isinstance(dati, list) and dati:
                    break
            except ValueError:
                dati = None
    if not isinstance(dati, list):
        return [], None
    out, emessa = [], None
    for x in dati:
        try:
            t = dt.datetime.fromisoformat(x["DATA_ESTREMALE"])
            t = t.replace(tzinfo=tz_italia(t.date()))
            out.append((x["TIPO_ESTREMALE"].strip().lower(), t.timestamp() * 1000, float(x["VALORE"])))
            if not emessa and x.get("DATA_PREVISIONE"):
                emessa = dt.datetime.fromisoformat(x["DATA_PREVISIONE"]).strftime("%d/%m %H:%M")
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda e: e[1]), emessa


# ------------------------------------------------------------------ marea astronomica
class Marea:
    def __init__(self, path):
        A = json.load(open(path, encoding="utf-8"))
        self.t0 = dt.datetime.fromisoformat(A["t0_utc"].replace("Z", "+00:00")).timestamp() * 1000
        self.p = A["passo_min"] * 60000
        self.cm = A["cm"]

    def cm_at(self, ms):
        k = (ms - self.t0) / self.p
        i = int(math.floor(k))
        if i < 0 or i + 1 >= len(self.cm):
            return None
        return self.cm[i] + (self.cm[i + 1] - self.cm[i]) * (k - i)

    def tf(self, ms):
        # f = dh/dt su +-30 min (m/h); f' su +-60 min (m/h per minuto): passo piu' largo per non amplificare
        # l'arrotondamento al cm della marea astronomica (con le maree di quadratura dominerebbe il termine s)
        a, b, c = self.cm_at(ms - 1800000), self.cm_at(ms), self.cm_at(ms + 1800000)
        a2, c2 = self.cm_at(ms - 3600000), self.cm_at(ms + 3600000)
        if None in (a, b, c, a2, c2):
            return None
        return (c - a) / 100, (c2 - 2 * b + a2) / 100 / 60

    def estremali(self, ms0, ms1):
        """Massimi e minimi tra ms0 e ms1 (primo istante del plateau)."""
        i0 = max(1, int((ms0 - self.t0) // self.p)); i1 = min(len(self.cm) - 2, int((ms1 - self.t0) // self.p))
        out, i = [], i0
        while i <= i1:
            j = i
            while j + 1 < len(self.cm) and self.cm[j + 1] == self.cm[i]:
                j += 1
            pre, post = self.cm[i - 1], self.cm[j + 1] if j + 1 < len(self.cm) else None
            if post is not None:
                if self.cm[i] > pre and self.cm[i] > post:
                    out.append(('max', self.t0 + i * self.p, self.cm[i]))
                elif self.cm[i] < pre and self.cm[i] < post:
                    out.append(('min', self.t0 + i * self.p, self.cm[i]))
            i = j + 1
        return out


class MareaCorretta:
    """Astronomica 'stirata' sugli estremali CPSM: tra due estremali abbinati il tempo scorre in proporzione
    e il livello si sposta dello scarto interpolato. Fuori dagli estremali abbinati: None."""
    def __init__(self, MA, coppie):
        self.MA, self.c = MA, coppie            # coppie: [(ms_cpsm, ms_astro, scarto_cm)] in ordine di tempo

    def cm_at(self, ms):
        for (c0, a0, d0), (c1, a1, d1) in zip(self.c, self.c[1:]):
            if c0 <= ms <= c1:
                fr = (ms - c0) / (c1 - c0)
                v = self.MA.cm_at(a0 + fr * (a1 - a0))
                return None if v is None else v + d0 + fr * (d1 - d0)
        return None

    def tf(self, ms):
        # f = dh/dt su +-30 min (m/h); f' su +-60 min (m/h per minuto): passo piu' largo per non amplificare
        # l'arrotondamento al cm della marea astronomica (con le maree di quadratura dominerebbe il termine s)
        a, b, c = self.cm_at(ms - 1800000), self.cm_at(ms), self.cm_at(ms + 1800000)
        a2, c2 = self.cm_at(ms - 3600000), self.cm_at(ms + 3600000)
        if None in (a, b, c, a2, c2):
            return None
        return (c - a) / 100, (c2 - 2 * b + a2) / 100 / 60


# ------------------------------------------------------------------ luna
def luna(giorno, tz):
    ms = dt.datetime(giorno.year, giorno.month, giorno.day, 9, tzinfo=tz).timestamp()
    rif = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc).timestamp()
    P = 29.530588853
    eta = ((ms - rif) / 86400) % P
    ill = round((1 - math.cos(2 * math.pi * eta / P)) / 2 * 100)
    if eta < 1.0 or eta > P - 1.0:
        fase = "nuova"
    elif abs(eta - P / 2) < 1.0:
        fase = "piena"
    else:
        fase = "crescente" if eta < P / 2 else "calante"
    return ill, fase


# ------------------------------------------------------------------ storico
def carica_diario(percorso="diario_pesca_MASTER.json"):
    """v1.31: senza diario (esecuzione automatica su GitHub) restano vuoti solo i riquadri dello storico."""
    if not os.path.exists(percorso):
        print("Diario non presente: senza riflessioni dello storico e senza tendenza dell'acqua dal diario.")
        return {}
    return json.load(open(percorso, encoding="utf-8"))


def storico(M, anno):
    U = {u["id"]: u for u in M.get("uscite", [])}
    inizio = f"{anno}-{INIZIO_AUTUNNO}"
    uscite, catt, acqua = [], [], []
    for t in M.get("tracciati", []):
        u = U.get(t.get("uscita_id"))
        if not u or u["id"] in ID_ESCLUSI or u.get("data", "") < inizio:
            continue
        m0 = t.get("meteo_inizio_uscita") or {}
        mm = [m0] + [x.get("meteo_at_capture") or {} for x in t.get("spot_catture", [])]
        ws = [v * 1.94384 for v in ((m.get("vento") or {}).get("intensita_ms") for m in mm) if v is not None]
        ta = [(m.get("temp_acqua") or {}).get("valore_c") for m in mm]
        ta = [x for x in ta if x is not None]
        if ta:
            acqua.append((u["data"], ta[0]))
        if ws:
            uscite.append({"data": u["data"], "catture": len(u.get("catture", [])), "vento_medio": statistics.mean(ws)})
        for x in t.get("spot_catture", []):
            f = (x.get("meteo_at_capture") or {}).get("fase_marea")
            d = dt.datetime.fromtimestamp(x["t"] / 1000, dt.timezone.utc)
            catt.append((d.astimezone(tz_italia(d.date())).strftime("%H:%M"), f))
    acqua.sort()
    return inizio, uscite, catt, acqua


# ------------------------------------------------------------------ calcolo
# v1.36: temperatura dell'acqua MISURATA dal CPSM (tempacqua.json). Oggi (20/11/2026) trasmette solo Punta Salute (Canal Grande),
# in gradi interi; Piattaforma CNR e Meda Abate sono nell'elenco ma senza valore. Il modello Open-Meteo Marine non si usa:
# sulle celle della laguna il 20/11 dava 2-3,5 °C in piu' della misura (celle di mare). Orario come nel file CPSM.
CPSM_ACQUA_PROXY = ("https://fd-cpsm-proxy.ennioweb.workers.dev/tempacqua", ["-H", "Origin: https://vksskdx22n-max.github.io"])
CPSM_ACQUA_OPENDATA = ("https://dati.venezia.it/sites/default/files/dataset/opendata/tempacqua.json", [])
ACQUA_ARCHIVIO = "acqua_archivio.json"   # una misura per giro, per la tendenza
ACQUA_PREFERITE = ["PSalute", "Piattaforma", "M_Abate"]
ACQUA_TEND_GIORNI = 5                    # tendenza: confronto con la misura di circa 5 giorni prima (almeno 3)
ACQUA_STABILE = 0.5                      # °C: variazione sotto la quale la tendenza e' "stabile"
ACQUA_MAX_ORE = 24                       # se il CPSM non risponde: ultima misura in archivio, se non piu' vecchia di cosi'


def acqua_misurata(path=None, archivio=ACQUA_ARCHIVIO):
    """{valore, data, ora, stazione, tendenza} dall'ultima misura CPSM della temperatura dell'acqua; None se non disponibile."""
    dati = None
    if path:
        dati = json.load(open(path, encoding="utf-8"))
    else:
        for url, extra in (CPSM_ACQUA_PROXY, CPSM_ACQUA_OPENDATA):
            r = subprocess.run(["curl", "-s", "-m", "20"] + extra + [url], capture_output=True, text=True)
            try:
                dati = json.loads(r.stdout)
                if isinstance(dati, list) and dati:
                    break
            except ValueError:
                dati = None
    misura = None
    if isinstance(dati, list):
        validi = []
        for x in dati:
            m_v = re.search(r"-?\d+(?:[.,]\d+)?", str(x.get("valore") or ""))
            try:
                t = dt.datetime.strptime(str(x.get("data") or ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if m_v:
                validi.append((ACQUA_PREFERITE.index(x.get("nome_abbr")) if x.get("nome_abbr") in ACQUA_PREFERITE else 99,
                               t, float(m_v.group(0).replace(",", ".")), x.get("stazione") or x.get("nome_abbr") or "CPSM"))
        if validi:
            _, t, v, st = min(validi, key=lambda y: (y[0], -y[1].timestamp()))
            misura = {"t": t.strftime("%Y-%m-%d %H:%M"), "valore": v, "stazione": st}
    try:
        A = json.load(open(archivio, encoding="utf-8")) if os.path.exists(archivio) else {"misure": []}
    except ValueError:
        A = {"misure": []}
    if misura and misura["t"] not in {x["t"] for x in A["misure"]}:
        A["misure"] = sorted(A["misure"] + [misura], key=lambda x: x["t"])
        A["versione"] = VERSIONE
        A["nota"] = "Temperatura dell'acqua misurata dal CPSM (tempacqua.json), una riga per giro di fd_previsione"
        json.dump(A, open(archivio, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not misura and A["misure"]:
        u = A["misure"][-1]
        if dt.datetime.now() - dt.datetime.strptime(u["t"], "%Y-%m-%d %H:%M") <= dt.timedelta(hours=ACQUA_MAX_ORE):
            misura = u
    if not misura:
        return None
    t = dt.datetime.strptime(misura["t"], "%Y-%m-%d %H:%M")
    tend = None
    prima = [x for x in A["misure"] if x["stazione"] == misura["stazione"]
             and dt.timedelta(days=3) <= t - dt.datetime.strptime(x["t"], "%Y-%m-%d %H:%M") <= dt.timedelta(days=ACQUA_TEND_GIORNI + 2)]
    if prima:
        p = min(prima, key=lambda x: abs((t - dt.datetime.strptime(x["t"], "%Y-%m-%d %H:%M")).total_seconds() - ACQUA_TEND_GIORNI * 86400))
        d = round(misura["valore"] - p["valore"], 1)
        tend = {"giorni": (t.date() - dt.datetime.strptime(p["t"], "%Y-%m-%d %H:%M").date()).days, "diff": d,
                "verso": "stabile" if abs(d) < ACQUA_STABILE else ("in aumento" if d > 0 else "in calo")}
    return {"valore": misura["valore"], "data": t.strftime("%d/%m"), "ora": t.strftime("%H:%M"),
            "stazione": misura["stazione"], "fonte": "misura CPSM (Comune di Venezia)", "tendenza": tend}


# v1.37: onda in laguna MISURATA (CPSM onde_laguna.json, solo l'ultimo valore: "Hs m, Hmax m") salvata a ogni giro, per verificare
# la stima dell'onda in laguna prima di rimetterla sul sito. Fusina e' l'unica stazione in laguna aperta (le altre sono canali di
# Venezia, dominati dal moto ondoso delle barche) e coincide con il punto di calcolo "C. Petroli · Fusina" dell'area centro.
CPSM_ONDE_PROXY = ("https://fd-cpsm-proxy.ennioweb.workers.dev/onde_laguna", ["-H", "Origin: https://vksskdx22n-max.github.io"])
ONDE_ARCHIVIO = "onde_archivio.json"
ONDE_CONFRONTO = {"Fusina": "C. Petroli · Fusina"}     # stazione CPSM (nome_abbr) -> punto di calcolo


def onde_misurate_archivia(path=None, archivio=ONDE_ARCHIVIO, cartella="."):
    """Salva l'ultima misura di onda delle stazioni CPSM e, se c'e', il confronto con la stima della previsione del centro
    per la stessa ora. Non cambia la previsione. Ritorna una riga di testo per il log."""
    dati = None
    if path:
        dati = json.load(open(path, encoding="utf-8"))
    else:
        r = subprocess.run(["curl", "-s", "-m", "20"] + CPSM_ONDE_PROXY[1] + [CPSM_ONDE_PROXY[0]], capture_output=True, text=True)
        try:
            dati = json.loads(r.stdout)
        except ValueError:
            dati = None
    if not isinstance(dati, list):
        return "Onda in laguna misurata: non disponibile"
    try:
        A = json.load(open(archivio, encoding="utf-8")) if os.path.exists(archivio) else {}
    except ValueError:
        A = {}
    A.setdefault("misure", []); A.setdefault("confronti", [])
    gia = {(x["t"], x["stazione"]) for x in A["misure"]}
    nuove, conf = 0, 0
    for x in dati:
        v = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", str(x.get("valore") or ""))]
        try:
            t = dt.datetime.strptime(str(x.get("data") or ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        st = x.get("nome_abbr") or x.get("stazione")
        if len(v) < 2 or not st or (t.strftime("%Y-%m-%d %H:%M"), st) in gia:
            continue
        A["misure"].append({"t": t.strftime("%Y-%m-%d %H:%M"), "stazione": st, "hs": v[0], "hmax": v[1]})
        nuove += 1
        punto = ONDE_CONFRONTO.get(st)
        if not punto:
            continue
        ora = (t + dt.timedelta(minutes=30)).replace(minute=0)          # ora piu' vicina
        scelta = None
        for f in glob.glob(os.path.join(cartella, "Previsioni", f"previsione_{t.strftime('%d-%m-%Y')}_*.json")):
            try:
                P = json.load(open(f, encoding="utf-8"))
                gen = dt.datetime.strptime(P["generato"], "%d/%m/%Y %H:%M")
                da = dt.datetime.combine(t.date(), dt.time.fromisoformat(P["finestra"].replace("-", "–").split("–")[0]))
            except (ValueError, KeyError):
                continue
            if (P.get("area") or "centro") != "centro" or gen > da:
                continue
            riga = next((o for o in (P.get("onda_laguna") or {}).get("ore", []) if o.get("ora") == ora.strftime("%H:%M")), None)
            if riga and riga["v"].get(punto) and (scelta is None or gen > scelta[0]):
                scelta = (gen, os.path.basename(f), riga["v"][punto])
        if scelta:
            A["confronti"].append({"t": t.strftime("%Y-%m-%d %H:%M"), "stazione": st, "punto": punto, "ora_stima": ora.strftime("%H:%M"),
                                   "mis": [v[0], v[1]], "stima": scelta[2], "previsione": scelta[1]})
            conf += 1
    if nuove:
        A["versione"] = VERSIONE
        A["nota"] = ("Onda in laguna misurata dal CPSM (onde_laguna.json: altezza significativa e massima, m), una misura per stazione e giro; "
                     "confronti = stima della previsione del centro (emessa prima della finestra) per la stessa ora nel punto di calcolo")
        json.dump(A, open(archivio, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return f"Onda in laguna misurata: {nuove} misure nuove, {conf} confronti con la stima (archivio {len(A['misure'])} misure, {len(A['confronti'])} confronti)"


# v1.38: visibilita' MISURATA all'aeroporto di Venezia Tessera (METAR LIPZ, AviationWeather.gov / NOAA), salvata a ogni giro
# per verificare il rischio nebbia stimato. Non cambia la previsione.
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=LIPZ&format=json&hours=24"
NEBBIA_ARCHIVIO = "nebbia_archivio.json"
NEBBIA_GIORNI = 60


NEBBIA_LOCALE_URL = ("https://fd-cpsm-proxy.ennioweb.workers.dev/nebbia/registro", ["-H", "Origin: https://vksskdx22n-max.github.io"])


def nebbia_locale_archivia(path=None, archivio=NEBBIA_ARCHIVIO):
    """v1.40: copia in nebbia_archivio.json (campo "locale") il registro del Worker v2.9: ogni 30 minuti umidita' e temperatura
    dell'aria CPSM (Palazzo Cavalli, San Giorgio, Piattaforma, con punto di rugiada), vento misurato delle tre zone e visibilita'
    di Tessera. t in UTC. Serve allo studio dell'indicatore locale di nebbia; non cambia la previsione. Ritorna una riga di log."""
    if path:
        dati = json.load(open(path, encoding="utf-8"))
    else:
        r = subprocess.run(["curl", "-s", "-m", "20"] + NEBBIA_LOCALE_URL[1] + [NEBBIA_LOCALE_URL[0]], capture_output=True, text=True)
        try:
            dati = json.loads(r.stdout)
        except ValueError:
            return f"Registro nebbia locale: non disponibile ({(r.stdout or r.stderr)[:120]!r})"
    if not isinstance(dati, list):
        return f"Registro nebbia locale: risposta inattesa ({str(dati)[:120]!r})"
    try:
        A = json.load(open(archivio, encoding="utf-8")) if os.path.exists(archivio) else {}
    except ValueError:
        A = {}
    L = {x["t"]: x for x in A.get("locale", []) if isinstance(x, dict) and x.get("t")}
    n0 = len(L)
    for x in dati:
        if isinstance(x, dict) and x.get("t"):
            L[x["t"]] = x
    limite = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=NEBBIA_GIORNI)).strftime("%Y-%m-%dT%H:%M")
    A["locale"] = [L[k] for k in sorted(L) if k >= limite]
    A["nota_locale"] = ("Registro del Worker (v2.9) ogni 30 minuti, t in UTC: aria = umidita' (rh, %), temperatura (t, °C), punto di rugiada (td) "
                        "e distanza dal punto di rugiada (spread) delle stazioni CPSM; vento = vento medio misurato (kn) di Saline, Malamocco Porto, "
                        "Chioggia Porto; vis = visibilita' di Tessera (m). Per lo studio dell'indicatore locale di nebbia.")
    json.dump(A, open(archivio, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return f"Registro nebbia locale: {len(A['locale']) - n0} righe nuove, totale {len(A['locale'])}"


# v1.41: stazioni ARPAV intorno alla laguna (dati in diretta, non validati, CC BY 4.0 "ARPAV" www.arpa.veneto.it).
# Stesso file JSON della mappa "Variabili meteorologiche": una richiesta per variabile e per ora, tutte le stazioni del Veneto.
ARPAV_URL = "https://api.arpa.veneto.it/REST/v1/meteo_meteogrammi?rete=MGRAMMI&coordcd={c}&orario={o}"
ARPAV_VAR = {"vis_km": 20059, "rh": 19, "t": 18}          # visibilita' (km), umidita' (%), temperatura aria 2 m (°C)
ARPAV_STAZ = {160: "Cavallino-Treporti", 252: "Venezia Istituto Cavanis", 230: "Campagna Lupia - Valle Averto", 168: "Chioggia - Sant'Anna"}
ARPAV_ORE = 24                                             # orario 0 .. -23 (storico disponibile)
ARPAV_RILEGGI = 2                                          # le ultime ore si rileggono sempre (dati che arrivano in ritardo)


def arpav_leggi(c, o, path=None):
    """Una variabile (coordcd) a un'ora (orario <= 0). Ritorna (ora_solare 'YYYY-MM-DDTHH:MM' o None, {codice: valore})."""
    if path:
        j = json.load(open(path, encoding="utf-8")).get(f"{c}|{o}", {})
    else:
        r = subprocess.run(["curl", "-s", "-m", "20", "-A", "ForecastLagoon/1.0 (github.com/vksskdx22n-max/forecast-lagoon)",
                            ARPAV_URL.format(c=c, o=o)], capture_output=True, text=True)
        try:
            j = json.loads(r.stdout)
        except ValueError:
            return None, {}
    ora, val = None, {}
    for s in (j.get("data") or []) if isinstance(j, dict) else []:
        try:
            cod = int(s["codice_stazione"])
        except (KeyError, TypeError, ValueError):
            continue
        if cod not in ARPAV_STAZ:
            continue
        ora = ora or str(s.get("dataora", ""))[:16] or None
        try:
            val[cod] = float(str(s.get("valore")).replace(",", "."))
        except (TypeError, ValueError):
            pass
    return ora, val


def nebbia_arpav_archivia(path=None, archivio=NEBBIA_ARCHIVIO):
    """v1.41: archivia in nebbia_archivio.json (campo "arpav") le ultime 24 ore orarie di visibilita' (Cavallino-Treporti),
    umidita' e temperatura delle stazioni ARPAV intorno alla laguna. Solo archivio per la taratura; non cambia la previsione.
    Ritorna una riga di log."""
    try:
        A = json.load(open(archivio, encoding="utf-8")) if os.path.exists(archivio) else {}
    except ValueError:
        A = {}
    R = {x["ora_solare"]: x for x in A.get("arpav", []) if isinstance(x, dict) and x.get("ora_solare")}
    ora0, _ = arpav_leggi(ARPAV_VAR["vis_km"], 0, path)
    if not ora0:
        return "ARPAV (visibilità Cavallino): non disponibile"
    t0 = dt.datetime.strptime(ora0, "%Y-%m-%dT%H:%M")
    nuove = 0
    for k in range(ARPAV_ORE):
        attesa = (t0 - dt.timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M")
        if k >= ARPAV_RILEGGI and attesa in R:
            continue
        riga = {"ora_solare": attesa,
                "t": (t0 - dt.timedelta(hours=k + 1)).strftime("%Y-%m-%dT%H:%M"),    # UTC = ora solare - 1 h (inferenza: "ore solari")
                "staz": {}}
        ok = False
        for nome, c in ARPAV_VAR.items():
            ora, val = arpav_leggi(c, -k, path)
            if ora != attesa:          # fuori dallo storico l'API restituisce l'ultima ora: scartare
                continue
            ok = True
            for cod, v in val.items():
                riga["staz"].setdefault(str(cod), {})[nome] = v
            if not path:
                time.sleep(0.2)
        if ok and riga["staz"]:
            nuove += attesa not in R
            R[attesa] = riga
    limite = (dt.datetime.now() - dt.timedelta(days=NEBBIA_GIORNI)).strftime("%Y-%m-%dT%H:%M")
    A["arpav"] = [R[k] for k in sorted(R) if k >= limite]
    A["nota_arpav"] = ("Stazioni ARPAV (dati in diretta, non validati; CC BY 4.0, fonte ARPAV www.arpa.veneto.it), un dato all'ora. "
                       "ora_solare = ora del dato ARPAV (ora solare italiana); t = UTC (ora solare - 1 h, inferenza). staz per codice: "
                       + ", ".join(f"{k} {v}" for k, v in ARPAV_STAZ.items())
                       + ". vis_km = visibilita' (km, solo Cavallino-Treporti), rh = umidita' (%), t = temperatura aria 2 m (°C).")
    json.dump(A, open(archivio, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    u = A["arpav"][-1] if A["arpav"] else {}
    vis = (u.get("staz", {}).get("160") or {}).get("vis_km")
    return (f"ARPAV (visibilità Cavallino): {nuove} ore nuove, archivio {len(A['arpav'])} ore; "
            f"ultima {u.get('ora_solare')} solare: {'-' if vis is None else str(vis).replace('.', ',') + ' km'}")


def nebbia_misurata_archivia(path=None, archivio=NEBBIA_ARCHIVIO):
    """Ritorna una riga di testo per il log."""
    if path:
        dati = json.load(open(path, encoding="utf-8"))
    else:
        r = subprocess.run(["curl", "-s", "-m", "20", "-A", "ForecastLagoon/1.0 (github.com/vksskdx22n-max/forecast-lagoon)", METAR_URL],
                           capture_output=True, text=True)
        try:
            dati = json.loads(r.stdout)
        except ValueError:
            return f"Visibilità Tessera (METAR LIPZ): non disponibile ({(r.stdout or r.stderr)[:120]!r})"
    if not isinstance(dati, list) or not dati:
        return "Visibilità Tessera (METAR LIPZ): nessun dato"
    try:
        A = json.load(open(archivio, encoding="utf-8")) if os.path.exists(archivio) else {}
    except ValueError:
        A = {}
    A.setdefault("misure", [])
    gia = {x["t"] for x in A["misure"]}; nuove = 0
    for x in dati:
        try:
            t = dt.datetime.fromtimestamp(int(x["obsTime"]), dt.timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        tk = t.strftime("%Y-%m-%d %H:%M")
        if tk in gia:
            continue
        vis = x.get("visib")
        A["misure"].append({"t": tk, "visib": vis, "wx": x.get("wxString"), "raw": x.get("rawOb")})
        nuove += 1
    limite = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=NEBBIA_GIORNI)).strftime("%Y-%m-%d %H:%M")
    A["misure"] = sorted([m for m in A["misure"] if m["t"] >= limite], key=lambda m: m["t"])
    A["versione"] = VERSIONE
    A["nota"] = ("METAR di Venezia Tessera (LIPZ) da AviationWeather.gov (NOAA): t in UTC, visib in miglia terrestri come restituito dall'API "
                 "('6+' = oltre 6 miglia, circa 10 km; 1 miglio = 1609 m), wx = fenomeni (FG nebbia, BR foschia). Serve a verificare il rischio nebbia stimato.")
    json.dump(A, open(archivio, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    u = A["misure"][-1] if A["misure"] else {}
    return f"Visibilità Tessera (METAR LIPZ): {nuove} misure nuove, archivio {len(A['misure'])}; ultima {u.get('t')} UTC: {u.get('raw')}"


def misure_aria(html_dir=None):
    """Ultima temperatura dell'aria misurata (CPSM): [{stazione, ora, valore}]."""
    if FV is None:
        return []
    try:
        M = FV.scarica_misure(html_dir)
    except Exception:
        return []
    out = []
    for st in ("Piattaforma CNR", "Palazzo Cavalli"):
        r = [x for x in M.get(st, []) if x.get("aria") is not None]
        if r:
            t = r[-1]["t"].astimezone(tz_italia(r[-1]["t"].date()))
            out.append({"stazione": st, "ora": t.strftime("%d/%m %H:%M"), "valore": num(r[-1]["aria"])})
    return out


def calcola(giorno, da, a, OM, M, MA, C, CP=([], None), MAR=None, ARIA=None, ENS=None):
    tz = tz_italia(giorno)
    ore = [h for h in range(24) if da <= dt.time(h, 0) <= a]
    idx = {p[0]: i for i, p in enumerate(PUNTI)}
    tempi = OM[0]["hourly"]["time"]

    def serie(punto, var, mod):
        v = OM[idx[punto]]["hourly"].get(f"{var}_{mod}")       # v1.26: variabile assente -> serie vuota
        return dict(zip(tempi, v)) if v else {}

    NOME_MOD = {"icon_d2": "ICON-D2", "italia_meteo_arpae_icon_2i": "ICON-2i", "ecmwf_ifs025": "ECMWF"}
    usati_meteo = set()

    def serie_rip(punto, var, conta=True):
        """v1.26: ora per ora ICON-D2, se manca ICON-2i, se manca ECMWF; conta i modelli usati nelle ore della finestra."""
        S_ = [serie(punto, var, m) for m, _ in MODELLI]
        out = {}
        for t in tempi:
            for (m, _), Sm in zip(MODELLI, S_):
                if Sm.get(t) is not None:
                    out[t] = Sm[t]
                    if conta and t in {k(h) for h in ore}:
                        usati_meteo.add(m)
                    break
            else:
                out[t] = None
        return out

    def k(h):
        return f"{giorno.isoformat()}T{h:02d}:00"

    # vento per zona
    vento, sem_ore, incerte, fonti_corr, n_alte = {}, {h: 'verde' for h in ore}, [], {}, []
    ore_rip = set()                                         # v1.26: ore senza ICON-D2 (ripiego)
    fonti_raff = set()
    ZONE_CALCOLO = ZONE_VENTO + ([z for z in ["Malamocco · Valgrande"] if z not in ZONE_VENTO] if AREA == "centro" else [])   # v1.21: per la verifica
    for z in ZONE_CALCOLO:
        S = {m: serie(z, "wind_speed_10m", m) for m, _ in MODELLI}
        Dm = {m: serie(z, "wind_direction_10m", m) for m, _ in MODELLI}
        Rm = {m: serie(z, "wind_gusts_10m", m) for m, _ in MODELLI}
        D, R = dict(Dm["icon_d2"]), dict(Rm["icon_d2"])
        righe = []
        for h in ore:
            d2, i2, ec = S["icon_d2"].get(k(h)), S["italia_meteo_arpae_icon_2i"].get(k(h)), S["ecmwf_ifs025"].get(k(h))
            if d2 is None:                                  # v1.26: ripiego su ICON-2i (o ECMWF) senza correzione
                mod = "italia_meteo_arpae_icon_2i" if i2 is not None else "ecmwf_ifs025"
                base = S[mod].get(k(h))
                D[k(h)] = Dm[mod].get(k(h)) if Dm[mod].get(k(h)) is not None else Dm["ecmwf_ifs025"].get(k(h))
                if base is None or D[k(h)] is None:
                    sys.exit(f"Nessun modello copre il vento di {z} alle {h:02d}:00: riprova più tardi.")
                r_m = Rm[mod].get(k(h))
                stima, corr = round(base, 1), 0.0
                fonti_corr.setdefault(f"ICON-D2 non disponibile, stima = {NOME_MOD[mod]} senza correzione misurata (meno affidabile)", set()).add("+0,0")
                raff = round(max(stima, r_m), 1) if r_m is not None else None
                sem = semaforo(stima, raff)
                if ORD[sem] > ORD[sem_ore[h]]:
                    sem_ore[h] = sem
                if z in ZONE_VENTO:
                    ore_rip.add(h)
                righe.append({"ora": f"{h:02d}:00", "dir": gradi_card(D[k(h)]), "nome": nome_vento(D[k(h)]), "dir_g": D[k(h)], "stima": stima, "corr": corr, "sem": sem,
                              "d2": None, "i2": i2, "raff": raff, "raff_d2": None, "ec": ec, "riparo": False, "ripiego": NOME_MOD[mod]})
                continue
            corr, corr_fonte, corr_n = correzione(d2, D[k(h)], z)
            rip = riparata(z, D[k(h)])
            if rip:
                corr, corr_fonte = 0.0, f"{z} con bora: {TESTO_RIPARO}"
            stima = d2 + corr
            # v1.23: fonti uguali raggruppate (il valore orario del raccordo non fa piu' una fonte diversa per ogni ora)
            fonte_k = re.sub(r"(fascia [^(;]+?) \([+-]?\d+,\d\)", r"\1", corr_fonte) if corr_fonte.count("graduale tra") else corr_fonte
            fonti_corr.setdefault(fonte_k, set()).add(f"{'+' if corr >= 0 else ''}{num(corr)}")
            if d2 >= SOGLIA_ROSSO:
                n_alte.append(corr_n)
            # v1.20: raffica corretta (misurata a Malamocco Porto); prima di 10 ore verificate: stessa correzione del vento
            r_d2 = R.get(k(h))
            if rip:
                cr, fr = 0.0, None
            else:
                cr, fr, _ = (FV.correzione_raffiche(D[k(h)], ".", zona_corr(z)) if FV and hasattr(FV, "correzione_raffiche") else (None, None, 0)) if AREA == "centro" else (0.0, "raffiche ICON-D2 senza correzione", 0)
                if cr is None:
                    cr, fr = corr, (f"stessa correzione del vento; {fr}" if fr else "stessa correzione del vento")
            if fr:
                fonti_raff.add(fr)
            raff = round(max(stima, (r_d2 or 0) + cr), 1) if r_d2 is not None else None
            sem = semaforo(stima, raff)
            if ORD[sem] > ORD[sem_ore[h]]:
                sem_ore[h] = sem
            st2 = None if i2 is None else i2 + (0.0 if rip else correzione(i2, D[k(h)], z)[0])
            if st2 is not None and (abs(d2 - i2) > 3 or semaforo(st2, raff) != sem):
                if z in ZONE_VENTO:
                    incerte.append(f"{z} {h:02d}:00")
            righe.append({"ora": f"{h:02d}:00", "dir": gradi_card(D[k(h)]), "nome": nome_vento(D[k(h)]), "dir_g": D[k(h)], "stima": round(stima, 1), "corr": corr, "sem": sem,
                          "d2": d2, "i2": i2, "raff": raff, "raff_d2": r_d2, "ec": ec, "riparo": rip, "ripiego": None})
        vento[z] = righe

    # semaforo di ogni ora = media delle zone di pesca (vento stimato e raffiche)
    media_ore = {}
    for i, h in enumerate(ore):
        st = statistics.mean(vento[z][i]["stima"] for z in ZONE_VENTO)
        rf = media_ok(vento[z][i]["raff"] for z in ZONE_VENTO)
        media_ore[h] = (round(st, 1), None if rf is None else round(rf, 1))
        sem_ore[h] = semaforo(st, rf)
    conteggio = {}
    for h in ore:
        conteggio[sem_ore[h]] = conteggio.get(sem_ore[h], 0) + 1
    # v1.27: a parita' vince il colore che compare prima (non piu' il peggiore); andamento ora per ora a tratti consecutivi
    verdetto = max(conteggio, key=lambda c: (conteggio[c], -next(i for i, h in enumerate(ore) if sem_ore[h] == c)))
    andamento = []
    for h in ore:
        if andamento and andamento[-1]["colore"] == sem_ore[h]:
            andamento[-1]["a"] = f"{h:02d}"
        else:
            andamento.append({"colore": sem_ore[h], "da": f"{h:02d}", "a": f"{h:02d}"})
    andamento_txt = " → ".join((t["colore"] + (f" {t['da']}" if t["da"] == t["a"] else f" {t['da']}–{t['a']}")) if i == 0
                               else f"{t['colore']} dalle {t['da']}" for i, t in enumerate(andamento)) if len(andamento) > 1 else ""
    peggio = [f"{h:02d}" for h in ore if ORD[sem_ore[h]] > ORD[verdetto]]
    stime = [media_ore[h][0] for h in ore]
    raffiche = [media_ore[h][1] for h in ore]
    dirs = [card(r["dir_g"]) for r in vento[ZONE_VENTO[0]]]
    g_ini, g_fin = vento[ZONE_VENTO[0]][0]["dir_g"], vento[ZONE_VENTO[0]][-1]["dir_g"]
    dir_verd = dir_txt(g_ini) if dirs[0] == dirs[-1] else f"{dir_txt(g_ini)} in rotazione a {dir_txt(g_fin)}"

    # marea
    ms_g0 = dt.datetime(giorno.year, giorno.month, giorno.day, tzinfo=tz).timestamp() * 1000
    est = MA.estremali(ms_g0 - 6 * 3600000, ms_g0 + 30 * 3600000)
    def fmt_ms(ms, f="%H:%M"):
        return dt.datetime.fromtimestamp(ms / 1000, tz).strftime(f)
    est_g = [e for e in est if ms_g0 <= e[1] < ms_g0 + 86400000]
    seq = []
    for e0, e1 in zip(est, est[1:]):
        if e1[1] < ms_g0 or e0[1] >= ms_g0 + 86400000:
            continue
        seq.append({"da": fmt_ms(e0[1], "%d/%m %H:%M"), "a": fmt_ms(e1[1], "%d/%m %H:%M"),
                    "fase": "crescente" if e0[0] == "min" else "calante"})
    ms_da = dt.datetime.combine(giorno, da, tz).timestamp() * 1000
    ms_a = dt.datetime.combine(giorno, a, tz).timestamp() * 1000
    # previsione CPSM: abbinata all'estremale astronomico dello stesso tipo entro 3 h
    cpsm, emessa = [], CP[1]
    for tipo, ms, cm in CP[0]:
        if not (ms_g0 - 6 * 3600000 <= ms <= ms_g0 + 30 * 3600000):
            continue
        cand = [e for e in est if e[0] == tipo and abs(e[1] - ms) <= 3 * 3600000]
        e = min(cand, key=lambda e: abs(e[1] - ms)) if cand else None
        cpsm.append({"tipo": tipo, "ms": ms, "ora": fmt_ms(ms, "%d/%m %H:%M"), "cm": round(cm),
                     "astro_ora": fmt_ms(e[1], "%H:%M") if e else None, "astro_cm": e[2] if e else None,
                     "diff_cm": round(cm - e[2]) if e else None, "diff_min": round((ms - e[1]) / 60000) if e else None,
                     "min_curva": round((ms - ms_g0) / 60000)})
    # v1.29: eta' del bollettino rispetto alla fine della finestra (la data di emissione e' "gg/mm HH:MM", senza anno)
    eta_cpsm = None
    if emessa:
        t_em = dt.datetime.strptime(f"{emessa}/{giorno.year}", "%d/%m %H:%M/%Y").replace(tzinfo=tz)
        if t_em.timestamp() * 1000 > ms_a:
            t_em = t_em.replace(year=giorno.year - 1)
        eta_cpsm = (ms_a - t_em.timestamp() * 1000) / 3600000
    cpsm_vecchia = eta_cpsm is not None and eta_cpsm > CPSM_ORE_MAX
    # per la fase della finestra si usa la previsione CPSM se ha estremali prima e dopo la finestra
    est_fase, fonte_fase = est, "astronomica"
    cp = [(c["tipo"], c["ms"], c["cm"]) for c in cpsm]
    if any(e[1] <= ms_da for e in cp) and any(e[1] >= ms_a for e in cp):
        est_fase, fonte_fase = cp, "previsione CPSM"
    fase_txt = []
    for e0, e1 in zip(est_fase, est_fase[1:]):
        if e1[1] > ms_da and e0[1] < ms_a:
            fase_txt.append(f"Marea {'crescente' if e0[0] == 'min' else 'calante'} dalle {fmt_ms(e0[1])} "
                            f"alla {'massima' if e1[0] == 'max' else 'minima'} delle {fmt_ms(e1[1])}")
    # curva corretta CPSM: estremali CPSM abbinati in modo univoco e nello stesso ordine dell'astronomica
    coppie, usati = [], set()
    for c in cpsm:
        if c["astro_ora"] is None:
            continue
        e = min((e for e in est if e[0] == c["tipo"] and abs(e[1] - c["ms"]) <= 3 * 3600000), key=lambda e: abs(e[1] - c["ms"]))
        if e[1] in usati or (coppie and e[1] <= coppie[-1][1]):
            continue
        usati.add(e[1]); coppie.append((c["ms"], e[1], c["diff_cm"]))
    MC = MareaCorretta(MA, coppie)
    usa_cpsm = len(coppie) >= 2 and all(MC.tf(ms_g0 + h * 3600000) is not None for h in ore)
    MM, fonte_liv = (MC, "previsione CPSM") if usa_cpsm else (MA, "astronomica")
    curva, curva_c = [], []
    for m in range(max(0, da.hour - 2) * 60, min(24, a.hour + 2) * 60 + 1, 10):
        ms = ms_g0 + m * 60000
        curva.append([m, round(MA.cm_at(ms), 1)])
        v = MC.cm_at(ms) if usa_cpsm else None
        if v is not None:
            curva_c.append([m, round(v, 1)])
    vel = []
    for h in ore:
        f = MM.tf(ms_g0 + h * 3600000)
        vel.append({"ora": f"{h:02d}:00", "cm_h": round(f[0] * 100, 1)})
    vel_max = max(abs(v["cm_h"]) for v in vel)
    ferma = vel_max < SOGLIA_FERMA                    # v1.29: morto d'acqua

    # correnti
    correnti = []
    for nome, etich in CANALI:
        pts = [p for o in C if o["n"] == nome for p in o["p"] if p[2] is not None]
        if not pts:
            continue
        val = []
        for h in ore:
            f, fp = MM.tf(ms_g0 + h * 3600000)
            best = max(pts, key=lambda p: abs(p[2] * f - p[3] * fp / OMEGA))
            v = best[2] * f - best[3] * fp / OMEGA
            val.append({"ms": round(abs(v) / 100, 2), "verso": "entra" if v * best[2] > 0 else "esce"})
        correnti.append({"canale": etich, "valori": val})
    picco_ore = [max(range(len(ore)), key=lambda i: c["valori"][i]["ms"]) for c in correnti]
    versi = {v["verso"] for c in correnti for v in c["valori"]}

    # meteo (ICON-D2, Malamocco)
    z0 = ZONE_VENTO[0]
    # v1.26: ICON-D2, se manca ICON-2i, se manca ECMWF (riquadro meteo); aria_ore per la verifica resta solo ICON-D2
    P = serie_rip(z0, "pressure_msl"); T = serie_rip(z0, "temperature_2m")
    N = serie_rip(z0, "cloud_cover"); Rn = serie_rip(z0, "precipitation")
    Tec = serie(z0, "temperature_2m", "ecmwf_ifs025"); Nec = serie(z0, "cloud_cover", "ecmwf_ifs025")
    T_d2_acqua = serie(z0, "temperature_2m", "icon_d2")
    p0, p1 = P.get(k(ore[0])), P.get(k(ore[-1]))
    tend = "–" if None in (p0, p1) else "in salita" if p1 - p0 >= 0.5 else "in calo" if p0 - p1 >= 0.5 else "stabile"
    nuv = media_ok(N.get(k(h)) for h in ore); nuv_ec = media_ok(Nec.get(k(h)) for h in ore)
    t_acqua = [T.get(k(h)) for h in ore]                  # aria sopra la laguna (cella su acqua)
    T_d2_mestre = {}
    if idx["Entroterra · Mestre"] < len(OM):               # aria principale: entroterra, dove i modelli concordano
        T = serie_rip("Entroterra · Mestre", "temperature_2m"); Tec = serie("Entroterra · Mestre", "temperature_2m", "ecmwf_ifs025")
        T_d2_mestre = serie("Entroterra · Mestre", "temperature_2m", "icon_d2")
        aria_punto = "Mestre"
    else:
        aria_punto = None
    t_ore = [T.get(k(h)) for h in ore]
    Tmare = serie("Mare aperto", "temperature_2m", "icon_d2") if idx["Mare aperto"] < len(OM) else {}
    aria_ore = [{"ora": f"{h:02d}:00", "mestre": (T_d2_mestre.get(k(h)) if aria_punto else None), "acqua": T_d2_acqua.get(k(h)),
                 "mare": Tmare.get(k(h))} for i, h in enumerate(ore)]
    # v1.24: pioggia ora per ora (Open-Meteo: mm e probabilita' >0,1 mm riferiti all'ora PRECEDENTE)
    serie_opz = serie
    PR = {m: serie_opz(z0, "precipitation_probability", m) for m, _ in MODELLI}
    MMp = {m: serie_opz(z0, "precipitation", m) for m, _ in MODELLI}
    pioggia_ore = [{"ora": f"{h:02d}:00", **{f"prob_{s}": PR[m].get(k(h)) for m, s in MODELLI},
                    **{f"mm_{s}": MMp[m].get(k(h)) for m, s in MODELLI}} for h in ore]
    prob_max = max((r["prob_D2"] for r in pioggia_ore if r["prob_D2"] is not None), default=None)
    # v1.38: rischio nebbia ora per ora nel punto principale della zona (ICON-D2, se manca ICON-2i, se manca ECMWF)
    RH = serie_rip(z0, "relative_humidity_2m", False); NB = serie_rip(z0, "cloud_cover_low", False)
    WS = serie_rip(z0, "wind_speed_10m", False)
    WC = {m: serie(z0, "weather_code", m) for m, _ in MODELLI}
    nebbia_ore = []
    for h in ore:
        t_ = k(h); rh, nb, ws = RH.get(t_), NB.get(t_), WS.get(t_)
        cod = [NOME_MOD[m] for m, _ in MODELLI if WC[m].get(t_) in NEBBIA_CODICI]
        liv = 0
        if cod or (rh is not None and ws is not None and rh >= NEBBIA_PROB["umidita"] and ws <= NEBBIA_PROB["vento"]):
            liv = 2
        elif (rh is not None and ws is not None and rh >= NEBBIA_POSS["umidita"] and ws <= NEBBIA_POSS["vento"]) or (nb is not None and nb >= NEBBIA_POSS["nubi_basse"]):
            liv = 1
        nebbia_ore.append({"ora": f"{h:02d}:00", "livello": liv, "umidita": None if rh is None else round(rh),
                           "nubi_basse": None if nb is None else round(nb), "vento": None if ws is None else round(ws, 1), "codice_nebbia": cod})
    PE = probabilita_nebbia(ENS, [k(h) for h in ore]) if ENS else {}
    for h, r in zip(ore, nebbia_ore):
        r["prob"], r["scenari"] = PE.get(k(h), (None, 0))
    nb_max = max((r["livello"] for r in nebbia_ore), default=0)
    ore_n = [r["ora"] for r in nebbia_ore if r["livello"] == nb_max] if nb_max else []
    nebbia = {"livello": nb_max, "testo": NEBBIA_LIVELLI[nb_max],
              "dalle": ore_n[0] if ore_n else None, "alle": (f"{int(ore_n[-1][:2]) + 1:02d}:00" if ore_n else None),
              "regola": f"probabile: umidità ≥ {NEBBIA_PROB['umidita']} % e vento ≤ {NEBBIA_PROB['vento']} kn, o codice meteo nebbia; "
                        f"possibile: umidità ≥ {NEBBIA_POSS['umidita']} % e vento ≤ {NEBBIA_POSS['vento']} kn, o nubi basse ≥ {NEBBIA_POSS['nubi_basse']} %",
              "nota": "stima dai modelli, non verificata"}
    probs = [r["prob"] for r in nebbia_ore if r.get("prob") is not None]
    if probs:                                   # v1.39: probabilita' dall'ensemble (sostituisce i livelli nella sintesi)
        pmax = max(probs)
        sopra = [r["ora"] for r in nebbia_ore if (r.get("prob") or 0) >= (NEBBIA_SOGLIA_ALTA if pmax >= NEBBIA_SOGLIA_ALTA else NEBBIA_SOGLIA_MIN)]
        nebbia.update({"prob_max": pmax, "segnala": pmax >= NEBBIA_SOGLIA_MIN,
                       "dalle": sopra[0] if sopra else None, "alle": (f"{int(sopra[-1][:2]) + 1:02d}:00" if sopra else None),
                       "soglia_alta": NEBBIA_SOGLIA_ALTA, "soglia_min": NEBBIA_SOGLIA_MIN,
                       "fonte_prob": f"ICON-D2-EPS, {max(r['scenari'] for r in nebbia_ore)} scenari (20 membri × punti della zona)",
                       "nota": "probabilità dai modelli, non ancora verificata con le misure"})
    note_meteo = []
    t0e, t0d = Tec.get(k(ore[0])), (T_d2_mestre if aria_punto else T_d2_acqua).get(k(ore[0]))
    if None not in (t0e, t0d) and abs(t0e - t0d) >= 3:
        note_meteo.append(f"ECMWF dà {num(t0e)} °C alle {ore[0]:02d}:00 contro {num(t0d)} °C di ICON-D2: incertezza sulla temperatura del primo mattino.")
    nuv_d2 = media_ok(serie(z0, "cloud_cover", "icon_d2").get(k(h)) for h in ore)
    if None not in (nuv_ec, nuv_d2) and abs(nuv_ec - nuv_d2) >= 40:
        note_meteo.append(f"Nuvolosità: ECMWF {round(nuv_ec)}% contro {round(nuv_d2)}% di ICON-D2.")
    fonte_meteo = " / ".join(NOME_MOD[m] for m, _ in MODELLI if m in usati_meteo) or "ICON-D2"
    if usati_meteo - {"icon_d2"}:
        note_meteo.append(f"ICON-D2 non copre tutte le ore: pressione, cielo, temperatura e pioggia da {fonte_meteo} (nell'ordine, ora per ora).")
    def giornaliero(var):
        for m, _ in MODELLI:
            v = (OM[idx[z0]].get("daily", {}).get(f"{var}_{m}") or [None])[0]
            if v:
                return v
        return None
    alba, tram = giornaliero("sunrise"), giornaliero("sunset")
    ill, lfase = luna(giorno, tz)

    # riflessioni
    inizio, usc, catt, acqua = storico(M, giorno.year)
    # tendenza dell'acqua in laguna (misure CPSM alle uscite): ultima misura contro quella piu' vicina a 7 giorni prima
    tend_acqua = None
    if len(acqua) >= 2:
        d_ult = dt.date.fromisoformat(acqua[-1][0])
        prec = [x for x in acqua[:-1] if (d_ult - dt.date.fromisoformat(x[0])).days >= 3]
        if prec:
            x = min(prec, key=lambda x: abs((d_ult - dt.date.fromisoformat(x[0])).days - 7))
            gg = (d_ult - dt.date.fromisoformat(x[0])).days
            dv = acqua[-1][1] - x[1]
            tend_acqua = f"{'+' if dv > 0 else ''}{num(dv)} °C in {gg} giorni"
    # onda in mare aperto
    mare = None
    if MAR and "hourly" in MAR:
        H = MAR["hourly"]; idx_t = {t: i for i, t in enumerate(H.get("time", []))}
        righe_m, fonti_onda = [], set()
        for h in ore:
            i = idx_t.get(f"{giorno.isoformat()}T{h:02d}:00")
            if i is None or H["wave_height"][i] is None:
                continue
            rm = {"ora": f"{h:02d}:00", "h": H["wave_height"][i], "dir": gradi_card(H["wave_direction"][i]),
                  "dir_g": H["wave_direction"][i], "per": H["wave_period"][i], "vento": None, "raff_v": None, "dir_v": None}
            if idx["Mare aperto"] < len(OM):        # vento ICON-D2 senza correzione sul punto della piattaforma
                try:
                    mv = next((m for m in ("icon_d2", "italia_meteo_arpae_icon_2i") if serie("Mare aperto", "wind_speed_10m", m).get(k(h)) is not None), "icon_d2")
                    rm["vento"] = serie("Mare aperto", "wind_speed_10m", mv).get(k(h))
                    rm["raff_v"] = serie("Mare aperto", "wind_gusts_10m", mv).get(k(h))
                    dg = serie("Mare aperto", "wind_direction_10m", mv).get(k(h))
                    rm["mod_v"] = NOME_MOD[mv]                # v1.26: ICON-2i se ICON-D2 non copre l'ora
                    rm["dir_v"], rm["nome_v"] = gradi_card(dg), nome_vento(dg)
                    if FV and dg is not None:                 # onda corretta con le verifiche alla Piattaforma
                        co, fo, _ = FV.correzione_onda(dg, ".")   # v1.20: rapporto misurata/modello (scala con l'onda)
                        fonti_onda.add(fo)
                        if co is not None:
                            rm["h_corr"] = round(rm["h"] * co, 2); rm["rapp_h"] = co
                            fx = FV.correzione_onda_fascia(dg, ".") if hasattr(FV, "correzione_onda_fascia") else None
                            if fx:   # fascia tra il giorno con rapporto minimo e quello con rapporto massimo
                                rm["h_corr_min"] = round(rm["h"] * fx[0], 2); rm["h_corr_max"] = round(rm["h"] * fx[1], 2)
                except (KeyError, TypeError):
                    pass
            righe_m.append(rm)
        if righe_m:
            mare = {"righe": righe_m, "cella": f"{num(MAR.get('latitude'), 3)} N {num(MAR.get('longitude'), 3)} E",
                    "fonte_corr": "; ".join(sorted(fonti_onda))}
    rifl = []
    calme = [u for u in usc if u["vento_medio"] < SOGLIA_RIFL]
    vent = [u for u in usc if u["vento_medio"] >= SOGLIA_RIFL]
    dal = dt.date.fromisoformat(inizio).strftime("%d/%m")
    if calme and vent:
        mc = statistics.mean(u["catture"] for u in calme); mv = statistics.mean(u["catture"] for u in vent)
        rifl.append({"tipo": "dato", "testo": f"Dal {dal}, vento medio misurato durante l'uscita: sotto i {num(SOGLIA_RIFL, 0)} nodi "
                     f"{len(calme)} uscite, media {num(mc)} catture; da {num(SOGLIA_RIFL, 0)} nodi in su {len(vent)} uscite, media {num(mv)} catture."})
    cr = sorted(o for o, f in catt if f == "crescente")
    if catt:
        rifl.append({"tipo": "dato", "testo": f"Dal {dal}: {len(cr)} catture su {len(catt)} in marea crescente"
                     + (f", tra le {cr[0]} e le {cr[-1]}." if cr else ".")})
    if len(acqua) >= 2:
        rifl.append({"tipo": "dato", "testo": f"Acqua a Punta Salute: {num(acqua[0][1])} °C il {dt.date.fromisoformat(acqua[0][0]).strftime('%d/%m')}, "
                     f"{num(acqua[-1][1])} °C il {dt.date.fromisoformat(acqua[-1][0]).strftime('%d/%m')}."})
    smin, smax = min(stime), max(stime)
    if calme and vent:
        fascia = vent if smin >= SOGLIA_RIFL else calme if smax < SOGLIA_RIFL else None
        if fascia is vent:
            rifl.append({"tipo": "inferenza", "testo": f"Con la stima di {num(smin)}–{num(smax)} nodi la giornata cade nella fascia ventosa, "
                         f"dove le uscite dal {dal} hanno reso meno (media {num(statistics.mean(u['catture'] for u in vent))} catture)."})
        elif fascia is calme:
            rifl.append({"tipo": "inferenza", "testo": f"Con la stima di {num(smin)}–{num(smax)} nodi la giornata cade nella fascia calma, "
                         f"quella delle uscite migliori dal {dal}."})
        else:
            rifl.append({"tipo": "inferenza", "testo": f"La stima ({num(smin)}–{num(smax)} nodi) attraversa la soglia dei {num(SOGLIA_RIFL, 0)} nodi: "
                         "conteranno le ore più calme."})
    if n_alte and min(n_alte) < (FV.MIN_ORE_CORR if FV else 10):
        n_v, e_d2, e_st = FV.errori_fascia(FV.fascia(SOGLIA_ROSSO), ".") if FV else (0, None, None)
        rifl.append({"tipo": "inferenza", "testo": f"Con ICON-D2 da {num(SOGLIA_ROSSO, 0)} nodi in su la correzione non è ancora misurata"
                     + (f": nelle {n_v} ore verificate ICON-D2 ha sbagliato di {'+' if e_d2 > 0 else ''}{num(e_d2)} nodi e la stima di "
                        f"{'+' if e_st > 0 else ''}{num(e_st)}, quindi il vento reale potrebbe stare tra ICON-D2 e la stima." if n_v else
                        ": il vento reale potrebbe essere diverso dalla stima in entrambi i sensi.")})
    if mare:
        alte = [r for r in mare["righe"] if r.get("h_corr", r["h"]) >= SOGLIA_ONDA]
        if alte:
            g_m = statistics.mean(r["dir_g"] for r in alte) % 360
            tipo = f"da {gradi_card(g_m)}" if g_m < 180 else None   # direzione di provenienza dell'onda
            if tipo:
                rifl.append({"tipo": "inferenza", "testo": f"Mare formato {tipo} fuori dalla bocca: fino a {num(max(r.get('h_corr', r['h']) for r in alte))} m "
                             f"tra le {alte[0]['ora']} e le {alte[-1]['ora']}. {BOCCA} e dighe probabilmente agitate."})
    if ore_rip:                                             # v1.26
        rifl.append({"tipo": "inferenza", "testo": f"ICON-D2 non copre ancora le ore {', '.join(f'{x:02d}:00' for x in sorted(ore_rip))}: stima = ICON-2i senza la correzione "
                     "misurata alle verifiche (che vale per ICON-D2). Rigenerare la previsione quando ICON-D2 copre il giorno."})
    if incerte:
        rifl.append({"tipo": "inferenza", "testo": "ICON-D2 e ICON-2i non concordano (oltre 3 nodi o colore diverso) in: " + ", ".join(incerte) + "."})
    ordine = sorted(ore, key=lambda h: (ORD[sem_ore[h]], -max(c["valori"][ore.index(h)]["ms"] for c in correnti)))
    migliori = sorted(h for h in ore if sem_ore[h] == sem_ore[ordine[0]])
    if sem_ore[ordine[0]] == "rosso":
        rifl.append({"tipo": "opinione", "testo": "Semaforo rosso per tutta la finestra: uscita sconsigliata. Se si esce, zone riparate dal vento "
                     f"di {dir_txt(g_ini)} e rientro anticipato."})
    else:
        zona_calma = min(ZONE_VENTO, key=lambda z: statistics.mean(r["stima"] for r in vento[z]))
        tratti = []                                   # v1.21: solo ore consecutive (prima univa 08 e 11 in "08–12")
        for h in migliori:
            if tratti and h == tratti[-1][1] + 1:
                tratti[-1][1] = h
            else:
                tratti.append([h, h])
        txt_ore = " e ".join(f"{x:02d}:00" if x == y else f"{x:02d}:00–{y:02d}:00" for x, y in tratti)   # v1.23: ore della tabella
        rifl.append({"tipo": "opinione", "testo": f"Ore migliori per vento: {txt_ore} (semaforo {sem_ore[ordine[0]]}). "
                     f"Corrente più forte verso le {ore[statistics.mode(picco_ore)]:02d}:00. Zona meno ventosa: {zona_calma}."})

    # onda stimata in laguna (v1.19): vento stimato e direzione ICON-D2 della zona, livello di marea previsto
    VENTO_DI = {"C. Petroli · Fusina": "Malamocco · Valgrande"}      # punto senza zona del vento: usa quella piu' vicina
    punti_o = [p for p in FETCH["punti"] if VENTO_DI.get(p, p) in ZONE_VENTO]
    onda_lag = {"punti": punti_o, "ore": [], "nota": ""}
    for i, h in enumerate(ore):
        lv = MM.cm_at(ms_g0 + h * 3600000)
        riga = {"ora": f"{h:02d}:00", "v": {}}
        for p in punti_o:
            r = vento[VENTO_DI.get(p, p)][i]
            riga["v"][p] = onda_laguna(p, r["stima"], r["dir_g"], None if lv is None else lv / 100)
        onda_lag["ore"].append(riga)
    onda_lag["nota"] = ("Metri: onda significativa (in grassetto) – onda massima. STIMA calcolata, non misura: vento stimato della zona "
                        "(C. Petroli · Fusina usa quello di Malamocco · Valgrande), direzione ICON-D2, livello di marea previsto, acqua libera "
                        "controvento e fondali da batimetria_laguna.bin; formula Young & Verhagen (1996) per acque basse, massima = 1,8 × significativa. "
                        "Non considera corrente contro vento, scie delle navi e barene che spezzano l'onda. Bocca · Alberoni esclusa: lì conta l'onda dal mare. "
                        "Da tarare con le osservazioni in barca.")
    if not any(any(v for v in r["v"].values()) for r in onda_lag["ore"]):
        onda_lag = None

    # striscia della mattina: per ogni ora semaforo (media delle zone), fase della marea, corrente in bocca
    bocca = next((c for c in correnti if c["canale"] == BOCCA), correnti[0] if correnti else None)
    s_ore = []
    for i, h in enumerate(ore):
        cmh = vel[i]["cm_h"]
        s_ore.append({"ora": f"{h:02d}", "sem": sem_ore[h], "stima": media_ore[h][0], "raff": media_ore[h][1],
                      "marea": "≈" if abs(cmh) < 1.5 else ("↑" if cmh > 0 else "↓"), "cm_h": cmh,
                      "corr": bocca["valori"][i]["ms"] if bocca else None, "verso": bocca["valori"][i]["verso"] if bocca else None})
    striscia = {"zone": ZONE_VENTO, "ore": s_ore, "riparo": [z for z in RIPARO_BORA if any(r["riparo"] for r in vento[z])], "alba": alba[-5:] if alba else None, "tramonto": tram[-5:] if tram else None,
                "fonte_marea": fonte_liv, "canale": bocca["canale"] if bocca else None}
    titolo = f"{GIORNI[giorno.weekday()]} {giorno.strftime('%d/%m/%Y')}"
    per_zona = "; ".join(f"{z} {num(min(r['stima'] for r in vento[z]))}–{num(max(r['stima'] for r in vento[z]))} nodi, "
                         f"raffiche fino a {num(max_ok(r['raff'] for r in vento[z]))}" for z in ZONE_VENTO)
    verd = [f"Vento da {dir_verd}. Media delle {len(ZONE_VENTO)} zone di pesca: {num(min(stime))}–{num(max(stime))} nodi, "
            f"raffiche medie fino a {num(max_ok(raffiche))}. Per zona: {per_zona}."]
    forti = [(z, r) for z in ZONE_VENTO for r in vento[z] if r["raff"] is not None and r["raff"] >= RAFF_AVVISO]   # v1.28
    if forti:
        z_f, r_f = max(forti, key=lambda x: x[1]["raff"])
        verd.append(f"Attenzione: raffiche fino a {num(r_f['raff'])} nodi ({z_f}, {r_f['ora']}); non cambiano il colore, che segue il vento medio.")
    if ore_rip:                                             # v1.26
        verd.append(f"ICON-D2 non copre le ore {', '.join(f'{x:02d}' for x in sorted(ore_rip))}: lì la stima è ICON-2i senza correzione misurata, meno affidabile.")
    if ferma:                                         # v1.29
        verd.append(f"Marea quasi ferma (morto d'acqua): nella finestra il livello varia al massimo di {num(vel_max)} cm/h"
                    + (" — fasi: " + "; ".join(t[0].lower() + t[1:] for t in fase_txt) if fase_txt else "") + ".")
    else:
        verd += [t + "." for t in fase_txt]
    verd += [f"(Orari marea: {fonte_fase}"
             + (f"; previsione CPSM emessa {emessa}, {round(eta_cpsm)} h prima della fine della finestra: oltre {CPSM_ORE_MAX} h, tendenza meno affidabile (astronomica come riferimento)" if cpsm_vecchia else "")
             + ".)"] if fase_txt else []
    return {
        "versione": VERSIONE, "titolo": titolo, "finestra": f"{da.strftime('%H:%M')}–{a.strftime('%H:%M')}", "zone": ZONE_TITOLO,
        "verdetto": {"colore": verdetto, "testo": ((f"Andamento: {andamento_txt}. ") if andamento_txt else "") + " ".join(verd),
                     "andamento": andamento, "andamento_txt": andamento_txt,
                     "nota": f"Semaforo = media delle zone {', '.join(ZONE_VENTO)} (non la zona peggiore)."
                             + (f" Ore con semaforo peggiore: {', '.join(peggio)}." if peggio else "")},
        "striscia": striscia,
        "vento": vento, "zone_vento": ZONE_VENTO, "testo_riparo": TESTO_RIPARO, "ore_ripiego": [f"{x:02d}:00" for x in sorted(ore_rip)],
        "vento_nota": (f"Nodi. D2 = ICON-D2, 2i = ICON-2i, Raff. = raffiche ICON-D2 corrette ({'; '.join(sorted(fonti_raff)) or 'senza correzione'}), EC = ECMWF. Stima = ICON-D2 + correzione — "
                       + "; ".join(f"{intervallo_corr(v)} nodi: {k}" for k, v in fonti_corr.items())
                       + (nota_verifica_nord_sud() if AREA != "centro" else
                          (". Misure di verifica: " + "; ".join(f"{z} = {FV.ZONE[z][1]}" for z in ZONE_VENTO if z in FV.ZONE) + "; "
                           if any(z in FV.ZONE for z in ZONE_VENTO) else ". ")
                          + ", ".join(z for z in ZONE_VENTO if z not in FV.ZONE) + ": nessuna stazione di verifica valida, "
                            "usano la correzione di Malamocco Porto" if FV else "")
                       + f". Colori sul vento medio arrotondato all'intero: verde fino a {VERDE_FINO_A} nodi, "
                       f"giallo da {VERDE_FINO_A + 1} a {ROSSO_DA - 1}, rosso da {ROSSO_DA} (le raffiche non cambiano il colore)."),
        "marea": {"estremali": [{"tipo": e[0], "ora": fmt_ms(e[1], "%d/%m %H:%M"), "cm": e[2]} for e in est_g],
                  "cpsm": [{k: v for k, v in c.items() if k != "ms"} for c in cpsm], "cpsm_emessa": emessa, "fonte_fase": fonte_fase,
                  "cpsm_vecchia": cpsm_vecchia, "cpsm_eta_h": None if eta_cpsm is None else round(eta_cpsm), "ferma": ferma, "vel_max": vel_max,
                  "sequenza": seq, "curva": curva, "curva_cpsm": curva_c, "fonte_livello": fonte_liv, "fascia": [da.hour * 60 + da.minute, a.hour * 60 + a.minute], "velocita": vel},
        "correnti": {"ore": [f"{h:02d}:00" for h in ore], "righe": correnti,
                     "nota": ("Velocità in m/s, massimo lungo il canale; "
                              + ("acqua che entra (marea crescente). " if versi == {"entra"} else "acqua che esce (marea calante). " if versi == {"esce"} else "▲ entra, ▼ esce. ")
                              + f"Marea usata: {fonte_liv}. Stima del modello di Laguna Viva, non misura: "
                              + ("il verso è attendibile, le velocità indicative." if max(abs(v["cm_h"]) for v in vel) >= SOGLIA_QUADRATURA else
                                 f"il livello varia al massimo di {num(max(abs(v['cm_h']) for v in vel))} cm/h (marea di quadratura): correnti deboli, verso e velocità poco attendibili.")),
                     "solo_verso": (next(iter(versi)) if len(versi) == 1 else None)},
        "meteo": {"pressione": [num(p0), num(p1), tend], "nuvolosita": None if nuv is None else round(nuv), "pioggia": num(max_ok(Rn.get(k(h)) for h in ore)), "fonte_meteo": fonte_meteo, "pioggia_prob": prob_max, "pioggia_ore": pioggia_ore, "nebbia": nebbia, "nebbia_ore": nebbia_ore,
                  "aria": [num(min((x for x in t_ore if x is not None), default=None)), num(max_ok(t_ore))], "aria_punto": aria_punto,
                  "aria_ore": aria_ore, "aria_misure": ARIA or [],
                  "aria_acqua": ([num(min((x for x in t_acqua if x is not None), default=None)), num(max_ok(t_acqua))] if aria_punto else None),
                  "acqua": ([num(acqua[-1][1]), dt.date.fromisoformat(acqua[-1][0]).strftime("%d/%m")] if acqua else None),
                  "acqua_tendenza": tend_acqua,
                  "alba": alba[-5:] if alba else None, "tramonto": tram[-5:] if tram else None, "luna": [ill, lfase], "note": note_meteo},
        "riflessioni": rifl,
        "mare": mare,
        "onda_laguna": onda_lag,
        "fonti": f"Fonti: Open-Meteo (ICON-D2 DWD, ICON-2i ItaliaMeteo-ARPAE, ECMWF IFS) · marea astronomica CPSM Punta Salute · "
                 f"correnti: modello Laguna Viva · storico: diario {giorno.year} · fd_previsione v{VERSIONE}",
        "generato": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "area": AREA,
    }


# ------------------------------------------------------------------ pagina
HTML = r"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Previsione meteo-marina</title>
<style>
:root{--bg:#f1f5f9;--card:#fff;--bd:#d8e0ea;--tx:#1e293b;--mu:#64748b;--ac:#3b82c4;--soft:#f1f5f9;
--verde:#16a34a;--giallo:#ca8a04;--rosso:#dc2626;--vs:#dcfce7;--gs:#fef3c7;--rs:#fee2e2}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.45 -apple-system,"Inter","Segoe UI",Roboto,sans-serif}
.wrap{max-width:560px;margin:0 auto;padding:16px}.wrap.larga{max-width:1180px;padding:24px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:0 20px;align-items:start}.wrap.larga .hero h1{font-size:34px}
.hero{border-radius:14px;padding:22px 22px 20px;color:#fff;background:linear-gradient(135deg,#1e3a6b,#2c5aa0 60%,#4a8fd8)}
.hero .k{font-size:12px;letter-spacing:.14em;opacity:.85;text-transform:uppercase}
.hero h1{font:400 30px/1.15 Georgia,serif;margin:10px 0 8px}.hero .z{opacity:.9;font-size:15px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px;margin-top:16px}
.card h2{margin:0 0 14px;font-size:13px;letter-spacing:.1em;color:var(--ac);text-transform:uppercase}
.verd{display:flex;gap:16px;align-items:center}.dot{flex:0 0 76px;height:76px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:13px;letter-spacing:.04em;text-transform:uppercase}
.dot.verde{background:var(--verde)}.dot.giallo{background:var(--giallo)}.dot.rosso{background:var(--rosso)}
.nota{color:var(--mu);font-size:13px;margin-top:10px}
.leg{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--mu);margin:2px 0 8px}.leg i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:4px}
.tabs{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;margin-bottom:8px}.tab{flex:1;padding:8px;border:1px solid var(--bd);border-radius:8px;text-align:center;color:var(--mu);font-weight:600;cursor:pointer;background:#fff}
.tab.on{background:#2c5aa0;color:#fff;border-color:#2c5aa0}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th{color:var(--mu);font-weight:500;font-size:12px;text-align:right;padding:6px 3px}th:first-child,td:first-child{text-align:left}
td{padding:8px 3px;border-top:1px solid #e5eaf0;text-align:right}
td.dir{font-size:12px;white-space:nowrap}.dnome{font-size:10.5px;color:var(--mu)}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-weight:700}.pill.verde{background:var(--vs);color:var(--verde)}.pill.giallo{background:var(--gs);color:var(--giallo)}.pill.rosso{background:var(--rs);color:var(--rosso)}
.zona-t{font-weight:600;margin:6px 0 2px;display:none}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{background:var(--soft);border:1px solid var(--bd);border-radius:8px;padding:4px 10px;font-size:14px}
.seq{font-size:13px;color:var(--mu);margin:10px 0 0;padding-left:18px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.tile{background:var(--soft);border-radius:10px;padding:12px}.tile b{font-size:21px;display:block}.tile span{color:var(--mu);font-size:13px}
.r{display:flex;gap:10px;margin:10px 0;align-items:flex-start}.tag{flex:0 0 auto;font-size:11px;font-weight:700;letter-spacing:.06em;padding:3px 8px;border-radius:6px;text-transform:uppercase}
.tag.dato{background:#dbeafe;color:#1e40af}.tag.inferenza{background:#fef3c7;color:#a16207}.tag.opinione{background:#ede9fe;color:#6d28d9}
.fonti{color:var(--mu);font-size:12px;text-align:center;margin:18px 8px 8px}
.p2 .parte1,.p1 .parte2{display:none}
@media print{body{background:var(--bg)}.tabs{display:none}.zona-t{display:block}.zbox{display:block!important}}
body.stampa .tabs{display:none}body.stampa .zona-t{display:block}body.stampa .zbox{display:block!important}
</style></head><body><div class="wrap" id="app"></div>
<script id="dati" type="application/json">__DATI__</script>
<script>
const D=JSON.parse(document.getElementById('dati').textContent);
const h=(t,a={},...c)=>{const e=document.createElement(t);for(const[k,v]of Object.entries(a))k==='cls'?e.className=v:k==='html'?e.innerHTML=v:e.setAttribute(k,v);c.flat().forEach(x=>x!=null&&e.append(x));return e;};
const n=(x,d=1)=>x==null?'–':Number(x).toFixed(d).replace('.',',');
const app=document.getElementById('app');const K=[];const add=e=>K.push(e);
const parte=location.hash.replace('#','');if(parte==='p1'||parte==='p2'){document.body.classList.add(parte,'stampa');}
if(location.hash.includes('stampa'))document.body.classList.add('stampa');
document.title='Previsione '+D.titolo;
add(h('div',{cls:'hero parte1'},h('div',{cls:'k'},'Previsione meteo-marina · v'+D.versione),h('h1',{},D.titolo+' · '+D.finestra),h('div',{cls:'z'},D.zone)));
const V=D.verdetto;
add(h('div',{cls:'card parte1'},h('h2',{},'Verdetto'),h('div',{cls:'verd'},h('div',{cls:'dot '+V.colore},V.colore),h('div',{},V.testo)),
  (V.andamento||[]).length>1?h('div',{style:'display:flex;gap:6px;flex-wrap:wrap;margin-top:10px'},...V.andamento.map((t,i)=>h('span',{cls:'pill '+t.colore},(i?'dalle '+t.da:t.da+(t.a!==t.da?'–'+t.a:''))+' · '+t.colore))):null,
  V.nota?h('div',{cls:'nota'},V.nota+((V.andamento||[]).length>1?' Colore del cerchio: il più frequente nelle ore; a parità quello che viene prima.':'')):null));
const colP=v=>v==null?['#f1f5f9','#64748b']:v<20?['#e6f1fb','#0c447c']:v<40?['#b5d4f4','#0c447c']:v<60?['#85b7eb','#042c53']:v<80?['#378add','#fff']:['#185fa5','#fff'];
(function(){const S=D.striscia;if(!S||!S.ore.length)return;
  const mx=Math.max(0.3,...S.ore.map(o=>o.corr||0));const cols=S.ore.length;
  const g=h('div',{style:`display:grid;grid-template-columns:52px repeat(${cols},1fr);gap:6px 4px;align-items:center;font-size:13px`});
  const lab=t=>h('div',{style:'color:var(--mu);font-size:11px'},t);
  g.append(lab(''),...S.ore.map(o=>h('div',{style:'text-align:center;font-weight:700'},o.ora)));
  g.append(lab('Vento'),...S.ore.map(o=>h('div',{style:'text-align:center'},h('span',{cls:'pill '+o.sem},n(o.stima,0)))));
  g.append(lab('Marea'),...S.ore.map(o=>h('div',{style:'text-align:center;line-height:1.1'},h('div',{style:'font-size:18px;font-weight:700;color:'+(o.marea==='↑'?'#0f766e':o.marea==='↓'?'#6d28d9':'#64748b')},o.marea),h('div',{style:'font-size:10px;color:var(--mu)'},(o.cm_h>0?'+':'')+n(o.cm_h)))));
  if(S.canale)g.append(lab('Corrente'),...S.ore.map(o=>{const w=o.corr==null?0:Math.max(4,Math.round(o.corr/mx*100));const c=o.verso==='entra'?'#2dd4bf':'#a78bfa';
    return h('div',{style:'text-align:center'},h('div',{style:`height:8px;border-radius:4px;background:${c};width:${w}%;margin:0 auto`}),h('div',{style:'font-size:10px;color:var(--mu);margin-top:2px'},n(o.corr,2)));}));
  const PO=(D.meteo&&D.meteo.pioggia_ore)||[];
  if(PO.length===S.ore.length&&PO.some(r=>r.prob_D2!=null))g.append(lab('Pioggia'),...PO.map(r=>{const c=colP(r.prob_D2);
    return h('div',{style:'text-align:center;line-height:1.1'},h('span',{style:`display:inline-block;padding:2px 7px;border-radius:10px;font-weight:700;background:${c[0]};color:${c[1]}`},r.prob_D2==null?'–':r.prob_D2+'%'),h('div',{style:'font-size:10px;color:var(--mu);margin-top:2px'},r.mm_D2==null?'':n(r.mm_D2)+' mm'));}));
  add(h('div',{cls:'card parte1'},h('h2',{},'🕐 La mattina in breve · media delle zone'),g,
    h('div',{cls:'nota'},'Vento: MEDIA delle zone di pesca ('+S.zone.join(', ')+'), nodi; colore = semaforo sul vento medio (le raffiche non contano). '+(S.riparo.length?S.riparo.join(', ')+': '+D.testo_riparo+'. ':'')+'Marea: ↑ crescente, ↓ calante, ≈ quasi ferma; cm/h ('+S.fonte_marea+'). '+(((D.meteo||{}).pioggia_ore||[]).some(r=>r.prob_D2!=null)?'Pioggia: probabilità ICON-D2 e mm nell’ora precedente. ':'')
      +(S.canale?'Corrente: '+S.canale+', m/s, azzurro entra, viola esce (stima del modello). ':'')+(S.alba?'Alba '+S.alba:'')+(S.tramonto?' · tramonto '+S.tramonto:'')+'.')));})();
const GV=[['stima','#dc2626',2.6,''],['d2','#f59e0b',1.6,''],['i2','#0d9488',1.6,''],['ec','#7c3aed',1.6,''],['raff','#64748b',1.2,'4 3']];
function graficoVento(rr){const W=520,H=200,L=26,R=10,T=10,B=22,N=rr.length;if(!N)return null;
 const ymax=Math.max(15,Math.ceil(Math.max(...rr.flatMap(r=>GV.map(g=>r[g[0]]||0)))/5)*5);
 const X=i=>L+(N>1?i/(N-1):.5)*(W-L-R),Y=v=>T+(ymax-v)/ymax*(H-T-B);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="font:10px sans-serif;fill:#64748b">`;
 [[0,6,'#dcfce7'],[6,12,'#fef3c7'],[12,ymax,'#fee2e2']].forEach(([a,b,c])=>s+=`<rect x="${L}" y="${Y(b)}" width="${W-L-R}" height="${Y(a)-Y(b)}" fill="${c}" opacity=".7"/>`);
 for(let y=0;y<=ymax;y+=5)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="#e2e8f0"/><text x="4" y="${Y(y)+3}">${y}</text>`;
 rr.forEach((r,i)=>s+=`<text x="${X(i)-6}" y="${H-6}">${r.ora.slice(0,2)}</text>`);
 GV.forEach(([k,c,w,da])=>{const p=rr.map((r,i)=>r[k]==null?null:X(i)+','+Y(r[k])).filter(Boolean);
  s+=`<polyline fill="none" stroke="${c}" stroke-width="${w}" ${da?'stroke-dasharray="'+da+'"':''} points="${p.join(' ')}"/>`;
  if(k==='stima')rr.forEach((r,i)=>s+=`<circle cx="${X(i)}" cy="${Y(r.stima)}" r="3.5" fill="${c}"/>`);});
 return h('div',{html:s+'</svg>'});}
const LEGV='<span><i style="background:#dc2626;height:4px"></i><b>stima</b></span><span><i style="background:#f59e0b"></i>ICON-D2</span><span><i style="background:#0d9488"></i>ICON-2i</span><span><i style="background:#7c3aed"></i>ECMWF</span><span><i style="background:repeating-linear-gradient(90deg,#64748b 0 4px,transparent 4px 7px)"></i>raffiche (corrette)</span><span>sfondo: verde sotto 6, giallo 6–12, rosso da 12 nodi</span>';
const cv=h('div',{cls:'card parte1'},h('h2',{},'💨 Vento'));const tabs=h('div',{cls:'tabs'});cv.append(tabs);
D.zone_vento.forEach((z,i)=>{const b=h('div',{cls:'tab'+(i?'':' on')},z);tabs.append(b);
  const t=h('table',{},h('tr',{},...['Ora','Dir.','Stima','D2','2i','Raff.','EC'].map(x=>h('th',{},x))));
  D.vento[z].forEach(r=>t.append(h('tr',{},h('td',{},r.ora),h('td',{cls:'dir'},r.dir,h('div',{cls:'dnome'},r.nome||'')),h('td',{},h('span',{cls:'pill '+r.sem},n(r.stima,0)),r.riparo?' *':'',r.ripiego?' †':''),h('td',{},n(r.d2)),h('td',{},n(r.i2)),h('td',{},n(r.raff)),h('td',{},n(r.ec)))));
  const box=h('div',{cls:'zbox',style:i?'display:none':''},h('div',{cls:'zona-t'},z),graficoVento(D.vento[z]),h('div',{cls:'leg',html:LEGV}),t);
  if(D.vento[z].some(r=>r.riparo))box.append(h('div',{cls:'nota'},'* Bora, '+D.testo_riparo+'.'));
  if(D.vento[z].some(r=>r.ripiego))box.append(h('div',{cls:'nota'},'† ICON-D2 non disponibile: stima = '+D.vento[z].find(r=>r.ripiego).ripiego+' senza correzione misurata, raffiche dello stesso modello (meno affidabile).'));cv.append(box);
  b.onclick=()=>{[...tabs.children].forEach(x=>x.classList.remove('on'));b.classList.add('on');cv.querySelectorAll('.zbox').forEach((x,j)=>x.style.display=j===i?'':'none');};});
cv.append(h('div',{cls:'nota'},D.vento_nota));
if(D.onda_laguna&&D.onda_laguna.ore.length){const OL=D.onda_laguna;
 const to=h('table',{style:'font-size:13px'},h('tr',{},h('th',{},'Ora'),...OL.punti.map(p=>h('th',{},p))));
 OL.ore.forEach(o=>to.append(h('tr',{},h('td',{},o.ora),...OL.punti.map(p=>{const v=o.v[p];return h('td',{style:'white-space:nowrap',html:v?'<b>'+n(v[0],2)+'</b>–'+n(v[1],2):'–'});}))));
 cv.append(h('h3',{style:'margin:18px 0 6px;font-size:14px'},'🌊 Onda stimata in laguna (calcolo)'),to,h('div',{cls:'nota'},OL.nota));}
add(cv);
const M=D.marea,cm=h('div',{cls:'card parte1'},h('h2',{},'🌊 Marea · Punta Salute'));
if(M.cpsm&&M.cpsm.length){
  cm.append(h('div',{cls:'nota',style:'margin:0 0 8px'},'Previsione CPSM (con vento e pressione)'+(M.cpsm_emessa?', emessa '+M.cpsm_emessa:'')+' · tra parentesi l’astronomica'
    +(M.cpsm_vecchia?' · ATTENZIONE: bollettino di '+M.cpsm_eta_h+' h prima della fine della finestra (oltre 48 h = tendenza, meno affidabile): fasi, curva e velocità seguono il bollettino; astronomica tratteggiata come riferimento':'')));
  cm.append(h('div',{cls:'chips'},...M.cpsm.map(e=>h('div',{cls:'chip',html:(e.tipo==='max'?'▲ max ':'▼ min ')+'<b>'+e.ora+'</b> · '+e.cm+' cm'
    +(e.astro_ora?` <span style="color:var(--mu)">(${e.astro_ora} · ${e.astro_cm} cm; ${e.diff_cm>0?'+':''}${e.diff_cm} cm, ${e.diff_min>0?'+':''}${e.diff_min} min)</span>`:'')}))));
} else {
  cm.append(h('div',{cls:'nota',style:'margin:0 0 8px'},'Previsione CPSM non disponibile: marea astronomica'));
  cm.append(h('div',{cls:'chips'},...M.estremali.map(e=>h('div',{cls:'chip',html:(e.tipo==='max'?'▲ max ':'▼ min ')+'<b>'+e.ora+'</b> · '+e.cm+' cm'}))));
}
const W=520,H=190,L=28,R=10,T=16,B=26,xs=M.curva.map(p=>p[0]),ys=M.curva.map(p=>p[1]).concat((M.curva_cpsm||[]).map(p=>p[1])).concat((M.cpsm||[]).filter(e=>e.min_curva>=Math.min(...xs)&&e.min_curva<=Math.max(...xs)).map(e=>e.cm));const x0=Math.min(...xs),x1=Math.max(...xs),ymin=Math.floor(Math.min(...ys)/20)*20,ymax=Math.ceil(Math.max(...ys)/20)*20;
const X=v=>L+(v-x0)/(x1-x0)*(W-L-R),Y=v=>T+(ymax-v)/(ymax-ymin||1)*(H-T-B);
let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="margin-top:12px;font:10px sans-serif;fill:#64748b"><text x="${L}" y="10">cm · ${(M.curva_cpsm||[]).length?'blu: corretta CPSM · grigia: astronomica':'curva astronomica'} · ● estremali CPSM · fascia: ore di pesca</text>`;
s+=`<rect x="${X(M.fascia[0])}" y="${T}" width="${X(M.fascia[1])-X(M.fascia[0])}" height="${H-T-B}" fill="#dbe7f6" opacity=".6"/>`;
for(let y=ymin;y<=ymax;y+=20)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="#e2e8f0"/><text x="2" y="${Y(y)+3}">${y}</text>`;
for(let m=x0;m<=x1;m+=60)s+=`<text x="${X(m)-6}" y="${H-8}">${String(m/60).padStart(2,'0')}</text>`;
if((M.curva_cpsm||[]).length){
  s+=`<polyline fill="none" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="3 3" points="${M.curva.map(p=>X(p[0])+','+Y(p[1])).join(' ')}"/>`;
  s+=`<polyline fill="none" stroke="#1e3a6b" stroke-width="2" points="${M.curva_cpsm.map(p=>X(p[0])+','+Y(p[1])).join(' ')}"/>`;
} else s+=`<polyline fill="none" stroke="#1e3a6b" stroke-width="2" points="${M.curva.map(p=>X(p[0])+','+Y(p[1])).join(' ')}"/>`;
(M.cpsm||[]).filter(e=>e.min_curva>=x0&&e.min_curva<=x1).forEach(e=>{s+=`<circle cx="${X(e.min_curva)}" cy="${Y(Math.max(ymin,Math.min(ymax,e.cm)))}" r="4.5" fill="#dc2626"/><text x="${X(e.min_curva)+6}" y="${Y(Math.max(ymin,Math.min(ymax,e.cm)))-6}" style="fill:#dc2626">CPSM ${e.cm}</text>`;});
s+='</svg>';
cm.append(h('div',{html:s}));
cm.append(h('div',{cls:'nota'},'Sequenza astronomica:'),h('ul',{cls:'seq',style:'margin-top:4px'},...M.sequenza.map(q=>h('li',{},`${q.da} → ${q.a}: ${q.fase}`))));
cm.append(h('div',{cls:'nota'},'Velocità del livello ('+(M.fonte_livello||'astronomica')+'): '+M.velocita.map(v=>v.ora+' '+(v.cm_h>0?'+':'')+n(v.cm_h)).join(' · ')+' cm/h'));
add(cm);
const Cr=D.correnti,cc=h('div',{cls:'card parte1'},h('h2',{},'🔄 Correnti nei canali (modello)'));
const tc=h('table',{},h('tr',{},h('th',{},'Canale'),...Cr.ore.map(o=>h('th',{},o))));
Cr.righe.forEach(r=>{const mx=Math.max(...r.valori.map(v=>v.ms));tc.append(h('tr',{},h('td',{},r.canale),...r.valori.map(v=>h('td',{html:(Cr.solo_verso?'':(v.verso==='entra'?'▲':'▼'))+(v.ms===mx?'<b>'+n(v.ms,2)+'</b>':n(v.ms,2))}))));});
cc.append(tc,h('div',{cls:'nota'},Cr.nota));add(cc);
const Me=D.meteo,cmt=h('div',{cls:'card parte2'},h('h2',{},'📈 Pressione, cielo, temperatura'));
const tiles=[[`${Me.pressione[0]} → ${Me.pressione[1]}`,`hPa, ${Me.pressione[2]}`],[(Me.nuvolosita==null?'–':Me.nuvolosita)+'%','nuvolosità media '+(Me.fonte_meteo||'ICON-D2')],[Me.pioggia+' mm'+(Me.pioggia_prob!=null?' · '+Me.pioggia_prob+'%':''),'pioggia max oraria'+(Me.pioggia_prob!=null?' · probabilità max ICON-D2':'')],[`${Me.aria[0]}–${Me.aria[1]} °C`,Me.aria_punto?'aria, '+(Me.fonte_meteo||'ICON-D2')+' entroterra ('+Me.aria_punto+')'+(Me.aria_acqua?' · sull’acqua (modello): '+Me.aria_acqua[0]+'–'+Me.aria_acqua[1]+' °C':''):'aria (ICON-D2)'],
  ...((Me.aria_misure||[]).length?[[Me.aria_misure.map(x=>x.valore+' °C').join(' · '),'aria misurata CPSM: '+Me.aria_misure.map(x=>x.stazione+' '+x.ora).join(' · ')]]:[])];
if(Me.acqua)tiles.push([Me.acqua[0]+' °C','acqua in laguna, misura CPSM del '+Me.acqua[1]+(Me.acqua_tendenza?' · '+Me.acqua_tendenza:'')]);if(Me.alba)tiles.push([Me.alba,'alba (Open-Meteo)']);if(Me.tramonto)tiles.push([Me.tramonto,'tramonto (Open-Meteo)']);tiles.push([Me.luna[0]+'%','luna '+Me.luna[1]+' (calcolo)']);
cmt.append(h('div',{cls:'grid'},...tiles.map(t=>h('div',{cls:'tile'},h('b',{},t[0]),h('span',{},t[1])))));
if((Me.pioggia_ore||[]).length){const PO=Me.pioggia_ore,N=PO.length,W=380,H=250,L=46,R=6,T=26,B=74,ec=PO.some(r=>r.prob_EC!=null);
  const bw=Math.min(56,(W-L-R)/N*0.55),X=i=>L+(i+0.5)*(W-L-R)/N,Y=v=>T+(100-v)/100*(H-T-B);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="font:11px sans-serif;fill:#64748b;margin-top:6px">`;
  [0,50,100].forEach(v=>s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${v?'#e2e8f0':'#c3c2b7'}"${v===50?' stroke-dasharray="4 3"':''}/><text x="2" y="${Y(v)+4}">${v}%</text>`);
  PO.forEach((r,i)=>{const v=r.prob_D2,c=colP(v),x=X(i);
    if(v!=null){s+=`<rect x="${x-bw/2}" y="${Y(v)}" width="${bw}" height="${Math.max(1,Y(0)-Y(v))}" rx="4" fill="${c[0]}" stroke="#85b7eb" stroke-width=".5"/><text x="${x}" y="${Y(v)-6}" text-anchor="middle" style="font-weight:700;fill:#1e293b;font-size:12px">${v}%</text>`;}
    if(ec&&r.prob_EC!=null)s+=`<line x1="${x-bw/2-4}" x2="${x+bw/2+4}" y1="${Y(r.prob_EC)}" y2="${Y(r.prob_EC)}" stroke="#7c3aed" stroke-width="2"/>`;
    s+=`<text x="${x}" y="${Y(0)+15}" text-anchor="middle" style="fill:#1e293b;font-weight:700">${r.ora}</text>`;
    [['D2',r.mm_D2],['2i',r.mm_2i],['EC',r.mm_EC]].forEach(([m,val],j)=>s+=`<text x="${x}" y="${Y(0)+32+j*13}" text-anchor="middle" style="font-size:11px">${val==null?'–':n(val)}</text>`);});
  s+=`<text x="2" y="${Y(0)+32}" style="font-size:11px">mm D2</text><text x="2" y="${Y(0)+45}" style="font-size:11px">mm 2i</text><text x="2" y="${Y(0)+58}" style="font-size:11px">mm EC</text></svg>`;
  cmt.append(h('h3',{style:'margin:14px 0 0;font-size:13px;color:var(--mu)'},'🌧 Pioggia ora per ora'),h('div',{html:s}),
    h('div',{cls:'nota'},'Barre: probabilità ICON-D2 di più di 0,1 mm'+(ec?'; linea viola: probabilità ECMWF':'')+'. Sotto: mm previsti da ICON-D2, ICON-2i ed ECMWF. Ogni ora si riferisce all’ora precedente (08:00 = dalle 07 alle 08). Probabilità calcolata da Open-Meteo con modelli ensemble; ICON-2i non la fornisce. Dati sul punto '+D.zone_vento[0]+'.'));}
Me.note.forEach(t=>cmt.append(h('div',{cls:'nota'},t)));
let MARE=null;
if(D.mare){const cv=D.mare.righe.some(r=>r.vento!=null);
  const oc=D.mare.righe.some(r=>r.h_corr!=null),ob=x=>x>=1?'<b>'+n(x,2)+' m</b>':n(x,2)+' m';
  const tm=h('table',{},h('tr',{},...['Ora','Onda'].concat(oc?['Corretta']:[],['Dir.','Periodo'],cv?['Vento','Raff.','Dir.']:[]).map(x=>h('th',{},x))));
  D.mare.righe.forEach(r=>tm.append(h('tr',{},h('td',{},r.ora),h('td',{html:ob(r.h)}),...(oc?[h('td',{html:r.h_corr_min!=null?n(r.h_corr_min,2)+'–'+n(r.h_corr_max,2)+' m':r.h_corr!=null?ob(r.h_corr):'–'})]:[]),h('td',{cls:'dir'},r.dir),h('td',{},n(r.per)+' s'),
    ...(cv?[h('td',{},n(r.vento)),h('td',{},n(r.raff_v)),h('td',{cls:'dir'},r.dir_v||'–',h('div',{cls:'dnome'},r.nome_v||''))]:[]))));
  MARE=h('div',{cls:'card parte2'},h('h2',{},'〰️ Onda in mare aperto'),tm,h('div',{cls:'nota'},'Onda: modello Open-Meteo Marine, cella a '+D.mare.cella+' (circa 5 km dal litorale). '+(cv?'Vento in nodi: '+(D.mare.righe.some(r=>r.mod_v==='ICON-2i')?'ICON-D2 (ICON-2i nelle ore non coperte)':'ICON-D2')+' senza correzione sul punto della Piattaforma CNR (8 miglia al largo). ':'')+(oc?'Corretta = modello × rapporto misurata/modello alla Piattaforma CNR ('+D.mare.fonte_corr+'); fascia tra il giorno con rapporto più piccolo e quello con rapporto più grande: l’errore cambia molto da un giorno all’altro. ':(D.mare.fonte_corr?'Correzione onda non ancora attiva ('+D.mare.fonte_corr+'). ':''))+'Vale fuori dalla bocca, non dentro la laguna.'));}
if(MARE)add(MARE);add(cmt);
const cr=h('div',{cls:'card parte2'},h('h2',{},'🧭 Riflessioni'));
D.riflessioni.forEach(r=>cr.append(h('div',{cls:'r'},h('span',{cls:'tag '+r.tipo},r.tipo),h('div',{},r.testo))));add(cr);
add(h('div',{cls:'fonti parte2'},D.fonti+' · generata '+D.generato));
/* impaginazione: una colonna (iPhone, PDF, PNG) o due colonne su schermo largo (Mac) */
const STRISCIA=(D.striscia&&D.striscia.ore.length)?K[2]:null,o0=STRISCIA?1:0;
const [HERO,VERD]=K,VENTO=K[2+o0],MAREA=K[3+o0],CORR=K[4+o0],FONTI=K[K.length-1],RIFL=K[K.length-2],METEO=K[K.length-3];
const stampaMode=document.body.classList.contains('stampa');
function impagina(){const larga=!stampaMode&&window.innerWidth>=1000;app.innerHTML='';app.classList.toggle('larga',larga);
  if(!larga){K.forEach(e=>app.append(e));return;}
  const c1=h('div',{}),c2=h('div',{});app.append(HERO,h('div',{cls:'cols'},c1,c2),FONTI);
  [VERD,STRISCIA,MAREA,VENTO,CORR,MARE,METEO,RIFL].filter(Boolean).forEach(e=>(c1.offsetHeight<=c2.offsetHeight?c1:c2).append(e));}   /* v1.24: riquadri sulla colonna piu' corta */
impagina();let modo=window.innerWidth>=1000;window.addEventListener('resize',()=>{const m=window.innerWidth>=1000;if(m!==modo){modo=m;impagina();}});
if(document.body.classList.contains('stampa')){const st=document.createElement('style');st.textContent=`@page{size:${document.documentElement.scrollWidth}px ${document.documentElement.scrollHeight+4}px;margin:0}`;document.head.append(st);}
</script></body></html>"""


def trova_chrome():
    for c in [os.environ.get("FD_CHROME"), CROME_MAC, shutil.which("google-chrome"), shutil.which("chromium")]:
        if c and os.path.exists(c):
            return c
    return None


def stampa(chrome, html, ancora, pdf):
    url = "file://" + os.path.abspath(html) + "#" + ancora
    with tempfile.TemporaryDirectory() as prof:
        try:
            subprocess.run([chrome, "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking",
                            f"--user-data-dir={prof}", "--window-size=560,900", "--hide-scrollbars", "--no-pdf-header-footer",
                            "--print-to-pdf-no-header", "--virtual-time-budget=4000", f"--print-to-pdf={os.path.abspath(pdf)}", url],
                           capture_output=True, timeout=90)
        except subprocess.TimeoutExpired:
            print(f"Chrome non ha risposto entro 90 s ({ancora}).")
    return os.path.exists(pdf)


def pdf_png(pdf, png):
    if shutil.which("sips"):
        r = subprocess.run(["sips", "-s", "format", "png", "-s", "dpiHeight", "144", "-s", "dpiWidth", "144", pdf, "--out", png], capture_output=True)
    elif shutil.which("pdftoppm"):
        r = subprocess.run(["pdftoppm", "-r", "144", "-png", "-singlefile", pdf, png[:-4]], capture_output=True)
    else:
        return False
    return os.path.exists(png)


def archivi_taratura():
    """v1.42: archivi per la taratura della nebbia (Tessera, registro del Worker, ARPAV), eseguiti PRIMA delle richieste
    a Open-Meteo. Ogni archivio e' indipendente: un errore viene scritto nel log e non ferma la previsione."""
    def opz(nome):
        return sys.argv[sys.argv.index(nome) + 1] if nome in sys.argv else None
    passi = [("Visibilità Tessera (METAR LIPZ)", lambda: nebbia_misurata_archivia(opz("--metar-json"))),     # v1.38
             ("Registro nebbia locale", lambda: nebbia_locale_archivia(opz("--nebbia-locale-json")))]      # v1.40
    if AREA == "centro":   # v1.41: una sola lettura ARPAV per giro (le tre zone usano lo stesso archivio)
        passi.append(("ARPAV (visibilità Cavallino)", lambda: nebbia_arpav_archivia(opz("--arpav-json"))))
    for nome, f in passi:
        try:
            print(f())
        except Exception as e:   # noqa: BLE001 - un archivio non deve mai fermare la previsione
            print(f"{nome}: errore {type(e).__name__}: {str(e)[:120]}")


def main():
    valori = {sys.argv[i + 1] for i, x in enumerate(sys.argv[:-1]) if x in ("--json", "--cpsm-json", "--mare-json", "--misure-dir", "--area")}
    arg = [x for x in sys.argv[1:] if not x.startswith("--") and x not in valori]
    jpath = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    if "--area" in sys.argv:
        nome = sys.argv[sys.argv.index("--area") + 1]
        if nome != "centro" and nome not in AREE:
            sys.exit(f"Area sconosciuta: {nome}. Aree: centro (predefinita), {', '.join(AREE)}")
        imposta_area(nome)
    giorno = dt.date.fromisoformat(arg[0]) if arg else dt.date.today() + dt.timedelta(days=1)
    da = dt.time.fromisoformat(arg[1]) if len(arg) > 1 else dt.time(7, 0)
    a = dt.time.fromisoformat(arg[2]) if len(arg) > 2 else dt.time(11, 30)
    for f in [f"marea_astronomica_{giorno.year}.json", "correnti_laguna.json"]:       # v1.31: il diario e' opzionale (versione pubblica su GitHub)
        if not os.path.exists(f):
            sys.exit(f"Manca {f} nella cartella {os.getcwd()}")
    print(f"fd_previsione v{VERSIONE} — {giorno.strftime('%d/%m/%Y')} {da.strftime('%H:%M')}–{a.strftime('%H:%M')}")
    if FV is None:
        print("fd_verifica.py non trovato: niente verifica automatica, correzione fissa +2,2 nodi.")
    elif "--no-verifica" not in sys.argv:
        for r in FV.verifica_pendenti("."):
            print(r)
    archivi_taratura()   # v1.42: prima di Open-Meteo, cosi' i dati per la taratura si salvano anche se la previsione fallisce
    OM = json.load(open(jpath, encoding="utf-8")) if jpath else scarica(giorno, da, a)
    mpath = sys.argv[sys.argv.index("--mare-json") + 1] if "--mare-json" in sys.argv else None
    MAR = scarica_mare(giorno, mpath)
    print("Onda in mare aperto: dati disponibili" if MAR else "Onda in mare aperto: API non disponibile, riquadro omesso")
    cpath = sys.argv[sys.argv.index("--cpsm-json") + 1] if "--cpsm-json" in sys.argv else None
    CP = scarica_cpsm(cpath)
    print(f"Previsione marea CPSM: {len(CP[0])} estremali (emessa {CP[1]})" if CP[0] else "Previsione marea CPSM non disponibile: uso la marea astronomica")
    ENS = scarica_ensemble(giorno, da, a, sys.argv[sys.argv.index("--ens-json") + 1] if "--ens-json" in sys.argv else None)   # v1.39
    print(f"Ensemble ICON-D2-EPS (nebbia): {len(ENS)} punti" if ENS else "Ensemble ICON-D2-EPS non disponibile: nebbia solo come livello")
    D = calcola(giorno, da, a, OM, carica_diario(),
                Marea(f"marea_astronomica_{giorno.year}.json"), json.load(open("correnti_laguna.json", encoding="utf-8")), CP, MAR,
                misure_aria(sys.argv[sys.argv.index("--misure-dir") + 1] if "--misure-dir" in sys.argv else None),
                ENS)
    os.makedirs("Previsioni", exist_ok=True)
    base = os.path.join("Previsioni", f"previsione_{giorno.strftime('%d-%m-%Y')}_{da.strftime('%H%M')}-{a.strftime('%H%M')}{'' if AREA == 'centro' else '_' + AREA}_v{VERSIONE}")
    print(onde_misurate_archivia(sys.argv[sys.argv.index("--onde-json") + 1] if "--onde-json" in sys.argv else None))   # v1.37: solo archivio
    D["acqua_misurata"] = acqua_misurata(sys.argv[sys.argv.index("--acqua-json") + 1] if "--acqua-json" in sys.argv else None)   # v1.36
    print(f"Acqua: {num(D['acqua_misurata']['valore'])} °C a {D['acqua_misurata']['stazione']} ({D['acqua_misurata']['data']} {D['acqua_misurata']['ora']})"
          if D["acqua_misurata"] else "Acqua: misura CPSM non disponibile, riquadro omesso")
    json.dump(D, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(base + ".html", "w", encoding="utf-8").write(HTML.replace("__DATI__", json.dumps(D, ensure_ascii=False).replace("</", "<\\/")))
    print(f"Verdetto: {D['verdetto']['colore'].upper()}" + (f" ({D['verdetto']['andamento_txt']})" if D['verdetto'].get('andamento_txt') else "")
          + f" — {D['verdetto']['testo']} {D['verdetto']['nota']}")
    for z in D["zone_vento"]:
        print(f"  {z:<12}", " ".join(f"{r['ora'][:2]}h {r['dir']:>3} {r['stima']:>4} ({r['sem'][0].upper()})" for r in D["vento"][z]))
    for q in D["marea"]["sequenza"]:
        print(f"  Marea {q['da']} -> {q['a']}: {q['fase']}")
    for r in D["riflessioni"]:
        print(f"  [{r['tipo']}] {r['testo']}")
    chrome = trova_chrome()
    if not chrome:
        print("Google Chrome non trovato: creata solo la pagina HTML.")
    else:
        ok = stampa(chrome, base + ".html", "stampa", base + ".pdf")
        for i in (1, 2):
            tmp = base + f"_p{i}.pdf"
            if stampa(chrome, base + ".html", f"p{i}", tmp):
                pdf_png(tmp, base + f"_{i}.png"); os.remove(tmp)
        print("Creati:", ", ".join(os.path.basename(base + x) for x in [".html", ".pdf", "_1.png", "_2.png"] if os.path.exists(base + x)) if ok else "Errore nella stampa PDF")
    print("Cartella:", os.path.abspath("Previsioni"))
    if "--non-aprire" not in sys.argv and shutil.which("open"):
        subprocess.run(["open", base + ".html"])


if __name__ == "__main__":
    main()
