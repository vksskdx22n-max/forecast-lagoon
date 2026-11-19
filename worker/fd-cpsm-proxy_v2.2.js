/**
 * Fishing Diary — CPSM proxy v2.2
 * Cloudflare Worker che fa da relay tra l'app Fishing Diary e gli endpoint
 * pubblici CPSM del Comune di Venezia (dati.venezia.it).
 *
 * Motivo: dati.venezia.it ha una whitelist di domini di provenienza e blocca
 * con 403 "Host not in allowlist" le richieste fetch() da github.io e dalla
 * WebView Capacitor. Questo worker rilancia la richiesta da un'origine
 * accettata e restituisce il JSON con header CORS aperto verso l'app.
 *
 * Uso dall'app:
 *   fetch('https://fd-cpsm-proxy.ennioweb.workers.dev/livello')
 *   fetch('https://fd-cpsm-proxy.ennioweb.workers.dev/vento')
 *   ...
 *
 * Endpoint disponibili: tutti quelli definiti in ENDPOINTS qui sotto.
 */

// Mappa endpoint pubblici -> path su dati.venezia.it
// Aggiungere qui se in futuro servono altri dataset CPSM.
const ENDPOINTS = {
  'livello':      'livello.json',
  'vento':        'vento.json',
  'pressione':    'pressione.json',
  'tempacqua':    'tempacqua.json',
  'temparia':     'temparia.json',
  'umidita':      'umidita.json',
  'onde_laguna':  'onde_laguna.json',
  'onde_mare':    'onde_mare.json',
  'radiazione':   'radiazione.json',
  'previsione':   'previsione.json',
  'as2026min':    'as2026min.json',
  'as2026est':    'as2026est.json',
  'fluna2026':    'fluna2026.json'
};

// v2.0: pagine "temporeale" delle stazioni CPSM (ultime 24 ore, HTML) per la verifica automatica su GitHub Actions.
// Il sito www.comune.venezia.it blocca i server di GitHub: qui la richiesta parte da Cloudflare.
const MISURE = {
  'Malamocco_Porto': true,
  'Palazzo_Cavalli': true,
  'Piattaforma': true,
  'Laguna_Nord': true,       // v2.1: verifica Laguna Nord (Saline)
  'Chioggia_Porto': true     // v2.1: verifica Laguna Sud
};
const MISURE_BASE = 'https://www.comune.venezia.it/sites/default/files/publicCPSM2/stazioni/temporeale/';

// Whitelist origini autorizzate a usare il proxy.
// Aggiungere qui se l'app viene deployata su nuovi domini.
const ORIGIN_WHITELIST = [
  'https://vksskdx22n-max.github.io',
  'capacitor://localhost',
  'http://localhost',
  'ionic://localhost'
];

// Header CORS dinamici in base all'Origin del chiamante.
function corsHeaders(request) {
  const origin = request.headers.get('Origin') || '';
  const allowOrigin = ORIGIN_WHITELIST.includes(origin) ? origin : 'null';
  return {
    'Access-Control-Allow-Origin': allowOrigin,
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400'
  };
}

// v2.2: avvio del workflow Previsione e verifica su GitHub (forza=no: se la previsione di domani esiste il giro si ferma subito)
async function avviaGiro(env) { const r = await fetch('https://api.github.com/repos/vksskdx22n-max/forecast-lagoon/actions/workflows/previsione.yml/dispatches', { method: 'POST', headers: { 'Authorization': 'Bearer ' + env.GITHUB_TOKEN, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'fd-cpsm-proxy' }, body: JSON.stringify({ ref: 'main', inputs: { forza: 'no' } }) }); console.log('avvio giro: HTTP ' + r.status + (r.ok ? '' : ' ' + (await r.text()))); }

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/+|\/+$/g, ''); // strip slash

    // Preflight CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    // v2.0: /misure/<Pagina> -> pagina temporeale HTML della stazione
    if (path.startsWith('misure/')) {
      const nome = path.slice('misure/'.length);
      if (!(nome in MISURE)) {
        return new Response(JSON.stringify({ error: 'unknown_station', available: Object.keys(MISURE) }),
          { status: 404, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } });
      }
      const org = request.headers.get('Origin');
      if (org && !ORIGIN_WHITELIST.includes(org)) {
        return new Response(JSON.stringify({ error: 'origin_not_allowed', origin: org }),
          { status: 403, headers: { 'Content-Type': 'application/json' } });
      }
      try {
        const r = await fetch(MISURE_BASE + nome + '.html', {
          headers: {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
            'Accept': 'text/html,application/xhtml+xml,*/*',
            'Accept-Language': 'it-IT,it;q=0.9',
            'Referer': 'https://www.comune.venezia.it/'
          },
          cf: { cacheTtl: 60, cacheEverything: true }
        });
        const body = await r.text();
        return new Response(body, { status: r.status, headers: { ...corsHeaders(request),
          'Content-Type': 'text/html; charset=utf-8', 'X-Upstream-Bytes': String(body.length) } });
      } catch (e) {
        return new Response(JSON.stringify({ error: 'fetch_failed', message: String(e) }),
          { status: 500, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } });
      }
    }

    // Root: pagina informativa
    if (path === '' || path === 'index.html') {
      const list = Object.keys(ENDPOINTS).map(k => `  - /${k}`).join('\n');
      return new Response(
        `Fishing Diary — CPSM proxy attivo.\n\nEndpoint disponibili:\n${list}\n`,
        { status: 200, headers: { 'Content-Type': 'text/plain; charset=utf-8' } }
      );
    }

    // Endpoint sconosciuto
    if (!(path in ENDPOINTS)) {
      return new Response(
        JSON.stringify({ error: 'unknown_endpoint', path: path, available: Object.keys(ENDPOINTS) }),
        { status: 404, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } }
      );
    }

    // Filtro origine: blocca richieste da domini non in whitelist
    const origin = request.headers.get('Origin');
    if (origin && !ORIGIN_WHITELIST.includes(origin)) {
      return new Response(
        JSON.stringify({ error: 'origin_not_allowed', origin: origin }),
        { status: 403, headers: { 'Content-Type': 'application/json' } }
      );
    }

    // Fetch upstream con headers browser-like (necessari per superare il check di dati.venezia.it)
    const upstream = 'https://dati.venezia.it/sites/default/files/dataset/opendata/' + ENDPOINTS[path];
    try {
      const r = await fetch(upstream, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
          'Accept': 'application/json, text/plain, */*',
          'Accept-Language': 'it-IT,it;q=0.9',
          'Referer': 'https://dati.venezia.it/'
        },
        cf: { cacheTtl: 60, cacheEverything: true } // cache 60s sul edge Cloudflare
      });

      if (!r.ok) {
        return new Response(
          JSON.stringify({ error: 'upstream_error', status: r.status, upstream: upstream }),
          { status: 502, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } }
        );
      }

      const body = await r.text();
      return new Response(body, {
        status: 200,
        headers: {
          ...corsHeaders(request),
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'public, max-age=60'
        }
      });
    } catch (e) {
      return new Response(
        JSON.stringify({ error: 'fetch_failed', message: String(e) }),
        { status: 500, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } }
      );
    }
  }
  , async scheduled(event, env, ctx) { ctx.waitUntil(avviaGiro(env)); }
};
