/**
 * Fishing Diary — CPSM proxy v2.4
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
const CRON_MATTINO = '25 7 * * *'; /* v2.3: 08:25 = giro del mattino */ async function avviaGiro(env, giro) { const r = await fetch('https://api.github.com/repos/vksskdx22n-max/forecast-lagoon/actions/workflows/previsione.yml/dispatches', { method: 'POST', headers: { 'Authorization': 'Bearer ' + env.GITHUB_TOKEN, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'fd-cpsm-proxy' }, body: JSON.stringify({ ref: 'main', inputs: { forza: 'no', giro: giro || '' } }) }); console.log('avvio giro: HTTP ' + r.status + (r.ok ? '' : ' ' + (await r.text()))); }

// v2.4: /adesso -> vento misurato adesso nelle tre zone di Forecast Lagoon, gia' calcolato (la pagina lo mostra e basta).
// Fonte: pagine temporeale delle 24 ore (le stesse delle verifiche di fd_verifica). Orari CPSM sempre in ora solare (UTC+1 fisso).
// Semaforo con le soglie di index.json (regole.verde_fino_a, regole.rosso_da), vento arrotondato all'intero come in fd_previsione.
const ADESSO_ZONE = {
  nord:   { pagina: 'Laguna_Nord',     stazione: 'Laguna Nord (Saline)', zero: true },
  centro: { pagina: 'Malamocco_Porto', stazione: 'Malamocco Porto',      zero: false },
  sud:    { pagina: 'Chioggia_Porto',  stazione: 'Chioggia Porto',       zero: true }
};
const ADESSO_REGOLE_URL = 'https://vksskdx22n-max.github.io/forecast-lagoon/index.json';
const ADESSO_VECCHIA_MIN = 30, ADESSO_ORE_TREND = 3, ADESSO_PASSO_MIN = 15, ADESSO_STABILE_KN = 1;
const MS_KN = 1.943844;
const PUNTI = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSO','SO','OSO','O','ONO','NO','NNO'];
const intero = kn => Math.floor(Math.round(kn * 10) / 10 + 0.5);
function cardinale(g) { return PUNTI[Math.round((((g % 360) + 360) % 360) / 22.5) % 16]; }
function mediaDir(gs) {
  let x = 0, y = 0; for (const g of gs) { x += Math.cos(g * Math.PI / 180); y += Math.sin(g * Math.PI / 180); }
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}
async function scaricaPagina(nome) {
  const r = await fetch(MISURE_BASE + nome + '.html', {
    headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
               'Accept': 'text/html,application/xhtml+xml,*/*', 'Accept-Language': 'it-IT,it;q=0.9', 'Referer': 'https://www.comune.venezia.it/' },
    cf: { cacheTtl: 60, cacheEverything: true } });
  return r.ok ? await r.text() : null;
}
function leggiVento(html, zero) {
  const th = [...html.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map(m => m[1].replace(/<[^>]+>/g, ' '));
  const iD = th.findIndex(h => /D\.Vento/i.test(h)), iV = th.findIndex(h => /V\.Vento med/i.test(h)), iM = th.findIndex(h => /V\.Vento max/i.test(h));
  if (iD < 0 || iV < 0) return [];
  const out = [];
  for (const m of html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)) {
    const c = [...m[1].matchAll(/<td[^>]*>([^<]*)<\/td>/g)].map(x => x[1].trim());
    if (c.length <= iV) continue;
    const p = c[0].split(/[\s\-:]/).map(Number);
    if (p.length < 5 || p.some(isNaN)) continue;
    const t = Date.UTC(p[0], p[1] - 1, p[2], p[3], p[4]) - 3600000;          // ora solare CPSM -> UTC
    const v = parseFloat(c[iV].replace(',', '.')), mx = iM >= 0 ? parseFloat(c[iM].replace(',', '.')) : NaN, d = parseFloat(c[iD].replace(',', '.'));
    if (isNaN(v)) continue;
    if (zero && v === 0 && mx === 0) continue;                                   // sensore fermo (come fd_verifica)
    out.push({ t, kn: v * MS_KN, raff: isNaN(mx) ? null : mx * MS_KN, dir: isNaN(d) ? null : d });
  }
  return out.sort((a, b) => a.t - b.t);
}
function semaforo(n, R) { if (!R) return null; return n >= R.rosso_da ? 'rosso' : n > R.verde_fino_a ? 'giallo' : 'verde'; }
function adessoZona(L, R, ora) {
  if (!L.length) return { disponibile: false, motivo: 'misura non disponibile' };
  const u = L[L.length - 1], eta = Math.round((ora - u.t) / 60000);
  const fmt = ms => new Date(ms).toLocaleTimeString('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit' });
  const inizio = u.t - ADESSO_ORE_TREND * 3600000, fin = L.filter(x => x.t >= inizio);
  const serie = [];
  for (let k = 0; k < ADESSO_ORE_TREND * 60 / ADESSO_PASSO_MIN; k++) {
    const a = inizio + k * ADESSO_PASSO_MIN * 60000, b = a + ADESSO_PASSO_MIN * 60000, xs = fin.filter(x => x.t >= a && x.t < b);
    serie.push(xs.length ? Math.round(xs.reduce((s, x) => s + x.kn, 0) / xs.length * 10) / 10 : null);
  }
  const prima = fin.filter(x => x.t < inizio + 30 * 60000), n = intero(u.kn);
  let trend = null;
  if (prima.length) {
    const da = prima.reduce((s, x) => s + x.kn, 0) / prima.length, nd = intero(da), diff = u.kn - da;
    const verso = Math.abs(diff) < ADESSO_STABILE_KN ? 'stabile' : diff > 0 ? 'in aumento' : 'in calo';
    const v = serie.filter(x => x != null);
    trend = { verso, da_kn: nd, testo: verso === 'stabile' ? `stabile nelle ultime ${ADESSO_ORE_TREND} ore` : `${verso} nelle ultime ${ADESSO_ORE_TREND} ore (da ${nd} kn)`,
              min_kn: v.length ? intero(Math.min(...v)) : null, max_kn: v.length ? intero(Math.max(...v)) : null };
  }
  const dirs = fin.filter(x => x.t > u.t - 10 * 60000 && x.dir != null).map(x => x.dir);
  const dir = u.dir != null ? u.dir : (dirs.length ? mediaDir(dirs) : null);
  return { disponibile: eta <= ADESSO_VECCHIA_MIN, motivo: eta <= ADESSO_VECCHIA_MIN ? null : `ultima misura di ${eta} minuti fa`,
           kn: n, raffica_kn: u.raff == null ? null : intero(u.raff), dir_g: dir == null ? null : Math.round(dir), dir: dir == null ? null : cardinale(dir),
           sem: semaforo(n, R), ora: fmt(u.t), eta_min: eta, trend, serie };
}
async function adesso() {
  let R = null;
  try { const j = await (await fetch(ADESSO_REGOLE_URL, { cf: { cacheTtl: 3600, cacheEverything: true } })).json();
        if (j && j.regole && j.regole.verde_fino_a != null && j.regole.rosso_da != null) R = { verde_fino_a: j.regole.verde_fino_a, rosso_da: j.regole.rosso_da }; } catch (e) {}
  const ora = Date.now(), zone = {};
  await Promise.all(Object.entries(ADESSO_ZONE).map(async ([id, z]) => {
    let L = [];
    try { const h = await scaricaPagina(z.pagina); if (h) L = leggiVento(h, z.zero); } catch (e) {}
    zone[id] = { stazione: z.stazione, ...adessoZona(L, R, ora) };
  }));
  return { versione: '2.4', generato_utc: new Date(ora).toISOString(), regole: R, zone };
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/+|\/+$/g, ''); // strip slash

    // Preflight CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    // v2.4: /adesso -> vento misurato adesso nelle tre zone (JSON gia' calcolato)
    if (path === 'adesso') {
      const org = request.headers.get('Origin');
      if (org && !ORIGIN_WHITELIST.includes(org)) {
        return new Response(JSON.stringify({ error: 'origin_not_allowed', origin: org }), { status: 403, headers: { 'Content-Type': 'application/json' } });
      }
      try {
        return new Response(JSON.stringify(await adesso()), { status: 200, headers: { ...corsHeaders(request),
          'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'public, max-age=60' } });
      } catch (e) {
        return new Response(JSON.stringify({ error: 'adesso_failed', message: String(e) }),
          { status: 500, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } });
      }
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
  , async scheduled(event, env, ctx) { ctx.waitUntil(avviaGiro(env, event.cron === CRON_MATTINO ? 'mattino' : '')); }
};
