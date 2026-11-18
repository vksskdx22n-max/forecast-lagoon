# Forecast Lagoon

Previsione meteo-marina e verifica per la pesca in laguna di Venezia, generate ogni giorno da GitHub Actions
e pubblicate su GitHub Pages: https://vksskdx22n-max.github.io/forecast-lagoon/

Ogni giorno (circa 16:17 ora solare) il workflow `Previsione e verifica`:
1. verifica le previsioni dei giorni conclusi (misure CPSM) e aggiorna `verifica_archivio.json`;
2. genera la previsione del giorno dopo, finestra 05:00-12:00;
3. pubblica le pagine sul sito.

Lancio manuale: scheda **Actions** > **Previsione e verifica** > **Run workflow** (giorno e finestra facoltativi).

## File dati necessari (nella cartella principale)

- `marea_astronomica_AAAA.json` (anno corrente; da gennaio serve anche quello dell'anno nuovo)
- `correnti_laguna.json`
- `verifica_archivio.json` (lo aggiorna il workflow; il primo caricamento viene dal Mac)

Il diario di pesca (`diario_pesca_MASTER.json`) non va mai caricato: l'esecuzione automatica funziona senza e salta
solo i riquadri dello storico.

## Versioni

fd_previsione 1.31 · fd_previsione_giorni 1.9 · fd_verifica 1.21 · build_site 1.0
