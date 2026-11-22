/**
 * Fishing Diary — CPSM proxy v2.11 (v2.11: celle vuote <td><br></td> lette come dato mancante invece di spostare la riga (Saline bloccate dalla riga delle 01:55 del 22/11); v2.10: livello di marea misurato adesso in /adesso, per zona; v2.9: registro per l'indicatore locale di nebbia, ogni 30 minuti nel KV, letto da /nebbia/registro; v2.8: prova delle allerte con ritardo, per provarle a telefono bloccato; v2.7: andamento della visibilita' nelle ultime 3 ore; v2.6: visibilita' adesso a Venezia Tessera in /adesso; v2.5: allerte vento di Forecast Lagoon, Telegram + Web Push)
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
  'Chioggia_Porto': true,    // v2.1: verifica Laguna Sud
  'Diga_Sud_Lido': true,     // v2.5: allerte vento
  'Diga_Sud_Chioggia': true  // v2.5: allerte vento
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
    const c = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(x => x[1].replace(/<[^>]*>/g, '').trim());   // v2.11: celle vuote <td><br></td>
    if (c.length <= iV) continue;
    const p = c[0].split(/[\s\-:]/).map(Number);
    if (p.length < 5 || p.some(isNaN)) continue;
    const t = Date.UTC(p[0], p[1] - 1, p[2], p[3], p[4]) - 3600000;          // ora solare CPSM -> UTC
    const v = parseFloat(c[iV].replace(',', '.')), mx = iM >= 0 && c[iM] != null ? parseFloat(c[iM].replace(',', '.')) : NaN, d = parseFloat((c[iD] || '').replace(',', '.'));
    if (isNaN(v)) continue;
    if (zero && v === 0 && mx === 0) continue;                                   // sensore fermo (come fd_verifica)
    out.push({ t, kn: v * MS_KN, raff: isNaN(mx) ? null : mx * MS_KN, dir: isNaN(d) ? null : d });
  }
  return out.sort((a, b) => a.t - b.t);
}
// v2.10: livello di marea (cm sullo zero di Punta Salute, come la previsione) dalle stesse pagine del vento; indipendente
// dal sensore del vento (alle Saline il vento a volte resta a zero ma il livello c'e'). Colonna "Liv.Idrom." o "L.Idrometrico".
const LIVELLO_VECCHIO_MIN = 30;
function leggiLivello(html) {
  const th = [...html.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/g)].map(m => m[1].replace(/<[^>]+>/g, ' '));
  const iL = th.findIndex(h => /Liv\.\s*Idrom|L\.\s*Idrometrico/i.test(h));
  if (iL < 0) return null;
  let u = null;
  for (const m of html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)) {
    const c = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(x => x[1].replace(/<[^>]*>/g, '').trim());   // v2.11: celle vuote <td><br></td>
    if (c.length <= iL) continue;
    const p = c[0].split(/[\s\-:]/).map(Number);
    if (p.length < 5 || p.some(isNaN)) continue;
    const v = parseFloat(c[iL].replace(',', '.'));
    if (isNaN(v) || v <= -9) continue;                                           // -999 = dato non ricevuto
    const t = Date.UTC(p[0], p[1] - 1, p[2], p[3], p[4]) - 3600000;          // ora solare CPSM -> UTC
    if (!u || t > u.t) u = { t, cm: Math.round(v * 100) };
  }
  return u;
}
function livelloAdesso(u, ora) {
  if (!u) return { disponibile: false };
  const eta = Math.round((ora - u.t) / 60000);
  const loc = new Date(u.t).toLocaleString('sv-SE', { timeZone: 'Europe/Rome' });   // "AAAA-MM-GG HH:MM:SS" ora italiana
  return { disponibile: eta <= LIVELLO_VECCHIO_MIN, cm: u.cm, data: loc.slice(0, 10), ora: loc.slice(11, 16),
           min: Number(loc.slice(11, 13)) * 60 + Number(loc.slice(14, 16)), eta_min: eta };
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
// v2.6: visibilita' MISURATA adesso all'aeroporto di Venezia Tessera (METAR LIPZ, AviationWeather.gov / NOAA).
// Il Worker da' solo la misura (metri, ora, fenomeni); soglie e testi sono nel sito (index.json, campo "visibilita").
// Metri presi dal bollettino (gruppo a 4 cifre: 0200 = 200 m, 9999 = 10 km o piu', CAVOK = oltre 10 km);
// se il gruppo manca, dalle miglia dell'API (arrotondate). Cache 5 minuti sul edge: i METAR escono ogni 30 minuti.
const METAR_URL = 'https://aviationweather.gov/api/data/metar?ids=LIPZ&format=json&hours=4';   // v2.7: 4 ore per avere il punto di partenza delle 3 ore
const VIS_ORE_TREND = 3, VIS_STABILE_M = 100;   // v2.7: andamento su 3 ore (come il vento); 'stabile' se cambia meno di 100 m
function metriMetar(raw, miglia) {
  const t = String(raw || '').split(/\s+/);
  let i = 0;
  while (i < t.length && /^(METAR|SPECI|LIPZ|COR|AUTO|\d{6}Z)$/.test(t[i])) i++;
  if (i < t.length && /^(\d{3}|VRB)\d{2,3}(G\d{2,3})?(KT|MPS)$/.test(t[i])) i++;
  if (i < t.length && /^\d{3}V\d{3}$/.test(t[i])) i++;
  if (t[i] === 'CAVOK') return { m: 10000, oltre: true };
  const m = /^(\d{4})(NDV)?$/.exec(t[i] || '');
  if (m) { const v = Number(m[1]); return { m: v >= 9999 ? 10000 : v, oltre: v >= 9999 }; }
  const mi = parseFloat(String(miglia == null ? '' : miglia).replace('+', ''));
  if (!isNaN(mi)) return { m: Math.round(mi * 1609.344 / 50) * 50, oltre: /\+/.test(String(miglia)) };
  return null;
}
async function visibilitaAdesso(ora) {
  const base = { stazione: 'Venezia Tessera (aeroporto)', fonte: 'METAR LIPZ, AviationWeather.gov (NOAA)' };
  try {
    const r = await fetch(METAR_URL, { headers: { 'User-Agent': 'ForecastLagoon/1.0 (github.com/vksskdx22n-max/forecast-lagoon)', 'Accept': 'application/json' },
                                       cf: { cacheTtl: 300, cacheEverything: true } });
    if (!r.ok) return { ...base, disponibile: false, motivo: 'METAR non disponibile (HTTP ' + r.status + ')' };
    const L = (await r.json() || []).filter(x => x && x.obsTime).sort((a, b) => b.obsTime - a.obsTime);
    if (!L.length) return { ...base, disponibile: false, motivo: 'nessun METAR recente' };
    const u = L[0], t = u.obsTime * 1000, eta = Math.round((ora - t) / 60000), v = metriMetar(u.rawOb, u.visib);
    if (!v) return { ...base, disponibile: false, motivo: 'visibilita\' non indicata nel METAR', raw: u.rawOb };
    // v2.7: serie delle ultime 3 ore (dal piu' vecchio) e andamento rispetto alla prima misura della finestra
    const inizio = t - VIS_ORE_TREND * 3600000 - 5 * 60000;
    const serie = L.filter(x => x.obsTime * 1000 >= inizio).reverse()
                   .map(x => { const w = metriMetar(x.rawOb, x.visib); return w ? { ora: hhmm(x.obsTime * 1000), m: w.m } : null; }).filter(Boolean);
    let trend = null;
    if (serie.length >= 2) {
      const da = serie[0].m, diff = v.m - da;
      const verso = Math.abs(diff) < VIS_STABILE_M ? 'stabile' : diff > 0 ? 'in miglioramento' : 'in peggioramento';
      const fm = x => x >= 10000 ? 'oltre 10 km' : x >= 1000 ? (Math.round(x / 100) / 10).toLocaleString('it-IT') + ' km' : x + ' m';
      trend = { verso, da_m: da, testo: verso === 'stabile' ? `stabile nelle ultime ${VIS_ORE_TREND} ore` : `${verso} nelle ultime ${VIS_ORE_TREND} ore (da ${fm(da)})` };
    }
    return { ...base, disponibile: true, m: v.m, oltre: v.oltre, wx: u.wxString || null, ora: hhmm(t), eta_min: eta, raw: u.rawOb, serie, trend };
  } catch (e) { return { ...base, disponibile: false, motivo: 'errore: ' + String(e).slice(0, 80) }; }
}
async function adesso() {
  let R = null;
  try { const j = await (await fetch(ADESSO_REGOLE_URL, { cf: { cacheTtl: 3600, cacheEverything: true } })).json();
        if (j && j.regole && j.regole.verde_fino_a != null && j.regole.rosso_da != null) R = { verde_fino_a: j.regole.verde_fino_a, rosso_da: j.regole.rosso_da }; } catch (e) {}
  const ora = Date.now(), zone = {};
  const vis = visibilitaAdesso(ora);   // v2.6: in parallelo con le stazioni CPSM
  await Promise.all(Object.entries(ADESSO_ZONE).map(async ([id, z]) => {
    let L = [], liv = null;
    try { const h = await scaricaPagina(z.pagina); if (h) { L = leggiVento(h, z.zero); liv = leggiLivello(h); } } catch (e) {}
    zone[id] = { stazione: z.stazione, ...adessoZona(L, R, ora), livello: livelloAdesso(liv, ora) };   // v2.10
  }));
  return { versione: '2.11', generato_utc: new Date(ora).toISOString(), regole: R, zone, visibilita: await vis };
}


// ===================== v2.5: ALLERTE VENTO (Forecast Lagoon) =====================
// Ogni 5 minuti (cron CRON_ALLERTE, filtrato sull'ora italiana) legge le stazioni CPSM e, se in una stazione
// la raffica raggiunge la soglia e il vento medio e' salito rapidamente, avvisa su Telegram e con Web Push.
// Le soglie NON sono qui: arrivano da index.json (campo "allerta", scritto da build_site.py).
// Binding: KV "ALLERTE" (iscrizioni, stato, chiave VAPID generata al primo uso); secret TELEGRAM_TOKEN; variabile TELEGRAM_CHAT_ID.
const CRON_ALLERTE = '*/5 3-19 * * *';
const ALLERTA_STAZIONI = {
  Laguna_Nord:       { nome: 'Laguna Nord (Saline)', zero: true },
  Diga_Sud_Lido:     { nome: 'Diga Lido',            zero: true },
  Malamocco_Porto:   { nome: 'Malamocco Porto',      zero: false },
  Piattaforma:       { nome: 'Piattaforma CNR',      zero: true },
  Diga_Sud_Chioggia: { nome: 'Diga Sud Chioggia',    zero: true },
  Chioggia_Porto:    { nome: 'Chioggia Porto',       zero: true }
};
const SITO_URL = 'https://vksskdx22n-max.github.io/forecast-lagoon/';

// Parametri da index.json; se il sito non risponde si usano gli ultimi validi salvati in KV.
async function regoleAllerta(env) {
  try {
    const j = await (await fetch(ADESSO_REGOLE_URL + '?allerta=' + Math.floor(Date.now() / 900000), { cf: { cacheTtl: 900, cacheEverything: true } })).json();   // copia in cache separata, al massimo 15 minuti
    const A = j && j.allerta;
    if (A && A.raffica_kn != null && A.salita_kn != null && A.finestra_min != null) {
      const vecchie = env.ALLERTE ? await env.ALLERTE.get('regole') : null;
      if (env.ALLERTE && vecchie !== JSON.stringify(A)) await env.ALLERTE.put('regole', JSON.stringify(A));
      return A;
    }
  } catch (e) {}
  try { const k = env.ALLERTE && await env.ALLERTE.get('regole'); if (k) return JSON.parse(k); } catch (e) {}
  return null;
}
function oraRoma(ms) { return Number(new Date(ms).toLocaleString('en-GB', { timeZone: 'Europe/Rome', hour: '2-digit', hour12: false })) % 24; }
function hhmm(ms) { return new Date(ms).toLocaleTimeString('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit' }); }

// Per velocita' (CPU del piano Free) si leggono solo l'intestazione e la coda della pagina (ultime righe).
function codaPagina(html) {
  const i = html.indexOf('</thead>'), testa = i > 0 ? html.slice(0, i + 8) : html.slice(0, 4000);
  return testa + html.slice(Math.max(0, html.length - 9000));
}

// Regola pura (usata anche nelle prove): L = misure ordinate {t, kn, raff, dir}; restituisce l'evento o null.
function valutaStazione(L, A, ora) {
  const fin = A.finestra_min * 60000, fresca = (A.misura_max_min || 20) * 60000;
  for (let i = L.length - 1; i >= 0; i--) {
    const x = L[i];
    if (ora - x.t > fresca) break;
    if (x.raff == null || x.raff < A.raffica_kn) continue;
    const p = L.filter(y => y.t <= x.t - fin + 60000 && y.t >= x.t - fin - 5 * 60000).pop();
    if (p && x.kn - p.kn >= A.salita_kn) return { t: x.t, kn: x.kn, raff: x.raff, dir: x.dir, kn_prima: p.kn };
  }
  return null;
}
// Pre-allarme sperimentale (solo registrato): rotazione >= rotazione_gradi in finestra con vento medio >= rotazione_min_kn
function valutaRotazione(L, A, ora) {
  if (A.rotazione_gradi == null || !L.length) return null;
  const x = L[L.length - 1]; if (ora - x.t > 20 * 60000 || x.dir == null || x.kn < (A.rotazione_min_kn || 0)) return null;
  const p = L.filter(y => y.t <= x.t - A.finestra_min * 60000 + 60000).pop(); if (!p || p.dir == null) return null;
  const d = Math.abs(((x.dir - p.dir + 540) % 360) - 180);
  return d >= A.rotazione_gradi ? { t: x.t, da: p.dir, a: x.dir, gradi: Math.round(d), kn: Math.round(x.kn) } : null;
}

function testoAllerta(ev) {
  const righe = ev.map(e => `${e.nome}: raffica ${intero(e.raff)} kn${e.dir != null ? ' da ' + cardinale(e.dir) : ''} alle ${hhmm(e.t)}, vento medio da ${intero(e.kn_prima)} a ${intero(e.kn)} kn`);
  return { titolo: '⚠️ Allerta vento in laguna', corpo: righe.join('\n') + '\nValuta il rientro. Misure CPSM, avviso automatico di Forecast Lagoon.' };
}

async function inviaTelegram(env, testo) {
  if (!env.TELEGRAM_TOKEN || !env.TELEGRAM_CHAT_ID) return 'telegram non configurato';
  const r = await fetch('https://api.telegram.org/bot' + env.TELEGRAM_TOKEN + '/sendMessage', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: testo, disable_web_page_preview: true }) });
  return 'telegram HTTP ' + r.status;
}

// ---------- Web Push (RFC 8291 aes128gcm + VAPID RFC 8292), solo WebCrypto ----------
const b64u = {
  enc: buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''),
  dec: s => { s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; return Uint8Array.from(atob(s), c => c.charCodeAt(0)); }
};
const unisci = (...a) => { const n = a.reduce((s, x) => s + x.length, 0), o = new Uint8Array(n); let k = 0; for (const x of a) { o.set(x, k); k += x.length; } return o; };
const testoB = s => new TextEncoder().encode(s);
async function vapid(env) {
  if (env.VAPID_JWK) return JSON.parse(env.VAPID_JWK);
  if (!env.ALLERTE) return null;
  const k = await env.ALLERTE.get('vapid'); if (k) return JSON.parse(k);
  const c = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const j = await crypto.subtle.exportKey('jwk', c.privateKey), v = { x: j.x, y: j.y, d: j.d };
  await env.ALLERTE.put('vapid', JSON.stringify(v)); return v;
}
function chiavePubblicaVapid(j) { return unisci(new Uint8Array([4]), b64u.dec(j.x), b64u.dec(j.y)); }
async function hmac(chiave, dati) { const k = await crypto.subtle.importKey('raw', chiave, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']); return new Uint8Array(await crypto.subtle.sign('HMAC', k, dati)); }
async function cifraPayload(sub, payload) {
  const uaPub = b64u.dec(sub.keys.p256dh), auth = b64u.dec(sub.keys.auth);
  const eph = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const asPub = new Uint8Array(await crypto.subtle.exportKey('raw', eph.publicKey));
  const uaKey = await crypto.subtle.importKey('raw', uaPub, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, eph.privateKey, 256));
  const prkKey = await hmac(auth, ecdh);
  const ikm = (await hmac(prkKey, unisci(testoB('WebPush: info\0'), uaPub, asPub, new Uint8Array([1])))).slice(0, 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const prk = await hmac(salt, ikm);
  const cek = (await hmac(prk, unisci(testoB('Content-Encoding: aes128gcm\0'), new Uint8Array([1])))).slice(0, 16);
  const nonce = (await hmac(prk, unisci(testoB('Content-Encoding: nonce\0'), new Uint8Array([1])))).slice(0, 12);
  const k = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const cif = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, k, unisci(testoB(payload), new Uint8Array([2]))));
  const rs = new Uint8Array([0, 0, 16, 0]);
  return unisci(salt, rs, new Uint8Array([asPub.length]), asPub, cif);
}
async function jwtVapid(j, aud) {
  const k = await crypto.subtle.importKey('jwk', { kty: 'EC', crv: 'P-256', x: j.x, y: j.y, d: j.d, ext: true }, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const h = b64u.enc(testoB(JSON.stringify({ typ: 'JWT', alg: 'ES256' })));
  const p = b64u.enc(testoB(JSON.stringify({ aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: 'mailto:ennioweb@users.noreply.github.com' })));
  const firma = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, k, testoB(h + '.' + p));
  return h + '.' + p + '.' + b64u.enc(firma);
}
async function inviaPush(env, sub, dati) {
  const V = await vapid(env); if (!V) return 503;
  const corpo = await cifraPayload(sub, JSON.stringify(dati));
  const aud = new URL(sub.endpoint).origin;
  const r = await fetch(sub.endpoint, { method: 'POST', headers: {
    'Authorization': 'vapid t=' + (await jwtVapid(V, aud)) + ', k=' + b64u.enc(chiavePubblicaVapid(V)),
    'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', 'TTL': '900', 'Urgency': 'high' }, body: corpo });
  return r.status;
}
async function idIscrizione(endpoint) { return 'sub:' + b64u.enc((await crypto.subtle.digest('SHA-256', testoB(endpoint)))).slice(0, 22); }
async function elencoIscritti(env) { try { return JSON.parse((await env.ALLERTE.get('iscritti')) || '[]'); } catch (e) { return []; } }
async function aggiornaIscritti(env, fn) { const L = await elencoIscritti(env), N = fn(L); if (JSON.stringify(N) !== JSON.stringify(L)) await env.ALLERTE.put('iscritti', JSON.stringify(N)); return N; }
async function inviaATutti(env, dati) {
  if (!env.ALLERTE) return 'web push non configurato';
  const ids = await elencoIscritti(env), scadute = []; let ok = 0, err = 0;
  for (const id of ids.slice(0, 35)) {                    // piano Free: max 50 richieste esterne per esecuzione
    try { const v = await env.ALLERTE.get(id); if (!v) { scadute.push(id); continue; }
          const s = await inviaPush(env, JSON.parse(v), dati);
          if (s === 404 || s === 410) { await env.ALLERTE.delete(id); scadute.push(id); } else if (s < 300) ok++; else err++; } catch (e) { err++; }
  }
  if (scadute.length) await aggiornaIscritti(env, L => L.filter(x => !scadute.includes(x)));
  return `push: ${ok} inviate, ${scadute.length} iscrizioni scadute tolte, ${err} errori (iscritti ${ids.length})`;
}

async function controllaAllerte(env, forzaOra) {
  const ora = forzaOra || Date.now(), h = oraRoma(ora);
  const A = await regoleAllerta(env);
  if (!A) { console.log('allerte: regole non disponibili'); return { stato: 'regole non disponibili' }; }
  if (!forzaOra && (h < A.ora_inizio || h >= A.ora_fine)) return { stato: 'fuori orario' };
  const S = env.ALLERTE ? JSON.parse((await env.ALLERTE.get('stato')) || '{}') : {};
  S.ultime = S.ultime || {};
  const eventi = [], rotazioni = [], letture = {};
  await Promise.all(Object.entries(ALLERTA_STAZIONI).map(async ([pag, st]) => {
    let L = []; try { const html = await scaricaPagina(pag); if (html) L = leggiVento(codaPagina(html), st.zero); } catch (e) {}
    letture[pag] = L.length ? hhmm(L[L.length - 1].t) : 'nessuna misura';
    const e = valutaStazione(L, A, ora);
    if (e && !(S.ultime[pag] && e.t - S.ultime[pag] < A.pausa_min * 60000)) eventi.push({ pag, nome: st.nome, ...e });
    const r = valutaRotazione(L, A, ora); if (r) rotazioni.push({ pag, ...r });
  }));
  let esito = { stato: 'controllato', letture, eventi: eventi.length };
  if (rotazioni.length && env.ALLERTE) {
    const R = JSON.parse((await env.ALLERTE.get('rotazioni')) || '[]');
    const n0 = R.length; for (const r of rotazioni) if (!R.some(x => x.pag === r.pag && x.t === r.t)) R.push(r);
    if (R.length > n0) await env.ALLERTE.put('rotazioni', JSON.stringify(R.slice(-300)));
  }
  // dopo un invio, per pausa_globale_min le altre stazioni colpite dallo stesso fronte non generano nuovi messaggi
  if (eventi.length && A.pausa_globale_min && S.ultimo_invio && ora - S.ultimo_invio < A.pausa_globale_min * 60000) {
    for (const e of eventi) S.ultime[e.pag] = e.t;
    if (env.ALLERTE) await env.ALLERTE.put('stato', JSON.stringify(S));
    return { ...esito, soppresse: eventi.map(e => e.nome) };
  }
  if (eventi.length) {
    eventi.sort((a, b) => a.t - b.t); S.ultimo_invio = ora;
    const T = testoAllerta(eventi);
    const tg = await inviaTelegram(env, T.titolo + '\n' + T.corpo);
    const wp = await inviaATutti(env, { titolo: T.titolo, corpo: T.corpo, url: SITO_URL, tag: 'allerta-vento' });
    for (const e of eventi) S.ultime[e.pag] = e.t;
    S.storico = (S.storico || []).concat(eventi.map(e => ({ t: e.t, stazione: e.nome, raffica_kn: intero(e.raff), kn: intero(e.kn), kn_prima: intero(e.kn_prima), dir: e.dir == null ? null : cardinale(e.dir) }))).slice(-100);
    if (env.ALLERTE) await env.ALLERTE.put('stato', JSON.stringify(S));
    esito = { ...esito, telegram: tg, push: wp };
    console.log('ALLERTA ' + T.corpo.replace(/\n/g, ' | ') + ' · ' + tg + ' · ' + wp);
  }
  return esito;
}

// ===================== v2.9: REGISTRO PER L'INDICATORE LOCALE DI NEBBIA =====================
// Ogni 30 minuti (giri del cron allerte ai minuti 00 e 30, cioe' dalle 04 alle 20 ora solare) salva nel KV una riga con:
// umidita' e temperatura dell'aria delle stazioni CPSM che le misurano (Palazzo Cavalli, San Giorgio, Piattaforma),
// vento misurato delle tre zone (stesse stazioni di /adesso) e visibilita' di Tessera (METAR). Serve a capire, confrontando
// con Tessera, se la distanza dal punto di rugiada e il vento misurati in laguna anticipano la nebbia. NON e' mostrato nel sito.
// Il giro della previsione (GitHub) copia il registro in nebbia_archivio.json (campo "locale").
const NEBBIA_REG_GIORNI = 45;
const OPENDATA = 'https://dati.venezia.it/sites/default/files/dataset/opendata/';
async function opendata(nome) {
  const r = await fetch(OPENDATA + nome, { headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'it-IT,it;q=0.9', 'Referer': 'https://dati.venezia.it/' }, cf: { cacheTtl: 60, cacheEverything: true } });
  return r.ok ? await r.json() : [];
}
function puntoRugiada(t, rh) { if (t == null || rh == null || rh <= 0) return null; const a = 17.62, b = 243.12, g = Math.log(rh / 100) + a * t / (b + t); return Math.round(b * g / (a - g) * 10) / 10; }
async function rigaNebbia(ora) {
  const num = v => { const x = parseFloat(String(v == null ? '' : v).replace(',', '.')); return isNaN(x) ? null : x; };
  const [U, T, vis] = await Promise.all([opendata('umidita.json').catch(() => []), opendata('temparia.json').catch(() => []), visibilitaAdesso(ora)]);
  const aria = {};
  for (const x of (U || [])) if (x && x.nome_abbr && x.valore != null) (aria[x.nome_abbr] = aria[x.nome_abbr] || {}).rh = num(x.valore), aria[x.nome_abbr].ora = x.data;
  for (const x of (T || [])) if (x && x.nome_abbr && x.valore != null) (aria[x.nome_abbr] = aria[x.nome_abbr] || {}).t = num(x.valore), aria[x.nome_abbr].ora_t = x.data;
  for (const k in aria) { const a = aria[k], td = puntoRugiada(a.t, a.rh); if (td != null) { a.td = td; a.spread = Math.round((a.t - td) * 10) / 10; } }
  const vento = {};
  await Promise.all(Object.entries(ADESSO_ZONE).map(async ([id, z]) => {
    try { const h = await scaricaPagina(z.pagina); const L = h ? leggiVento(codaPagina(h), z.zero) : [];
          const u = L[L.length - 1]; vento[id] = u && ora - u.t <= 30 * 60000 ? Math.round(u.kn * 10) / 10 : null; } catch (e) { vento[id] = null; }
  }));
  return { t: new Date(ora).toISOString().slice(0, 16), aria, vento,
           vis: vis && vis.disponibile ? { m: vis.m, oltre: !!vis.oltre, wx: vis.wx || null, ora: vis.ora, eta_min: vis.eta_min } : null };
}
async function registraNebbia(env, ora) {
  if (!env.ALLERTE) return 'KV non configurato';
  const R = await rigaNebbia(ora);
  const L = JSON.parse((await env.ALLERTE.get('nebbia_registro')) || '[]').filter(x => x.t !== R.t);
  const lim = new Date(ora - NEBBIA_REG_GIORNI * 86400000).toISOString().slice(0, 16);
  L.push(R); await env.ALLERTE.put('nebbia_registro', JSON.stringify(L.filter(x => x.t >= lim)));
  return 'registro nebbia: ' + L.length + ' righe';
}

async function rispostaAllerta(request, env, sotto, ctx) {
  const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { ...corsHeaders(request), 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  const org = request.headers.get('Origin');
  if (org && !ORIGIN_WHITELIST.includes(org)) return json({ error: 'origin_not_allowed' }, 403);
  if (sotto === 'chiave') { const V = await vapid(env); return V ? json({ chiave: b64u.enc(chiavePubblicaVapid(V)) }) : json({ error: 'non configurato' }, 503); }
  if (sotto === 'telegram-chat') {          // aiuto alla configurazione: chat che hanno scritto al bot (solo id e nome)
    if (!env.TELEGRAM_TOKEN) return json({ error: 'TELEGRAM_TOKEN mancante' }, 503);
    const j = await (await fetch('https://api.telegram.org/bot' + env.TELEGRAM_TOKEN + '/getUpdates')).json();
    const chat = {}; for (const u of (j.result || [])) { const c = (u.message || u.my_chat_member || {}).chat; if (c) chat[c.id] = c.first_name || c.title || ''; }
    return json({ chat, configurato: !!env.TELEGRAM_CHAT_ID });
  }
  if (sotto === 'stato') {
    const S = env.ALLERTE ? JSON.parse((await env.ALLERTE.get('stato')) || '{}') : {};
    const n = env.ALLERTE ? (await elencoIscritti(env)).length : null;
    return json({ versione: '2.11', regole: await regoleAllerta(env), iscritti_push: n, telegram: !!(env.TELEGRAM_TOKEN && env.TELEGRAM_CHAT_ID),
                  ultime_allerte: (S.storico || []).slice(-10).map(e => ({ ...e, ora: new Date(e.t).toLocaleString('it-IT', { timeZone: 'Europe/Rome' }) })) });
  }
  if (sotto === 'controlla') return json(await controllaAllerte(env, Date.now()));   // controllo immediato (senza filtro orario)
  if (request.method !== 'POST') return json({ error: 'usare POST' }, 405);
  if (sotto === 'prova-telegram') {
    if (!env.ALLERTE) return json({ error: 'archivio non configurato' }, 503);
    const u = Number((await env.ALLERTE.get('prova_tg')) || 0);
    if (Date.now() - u < 10 * 60000) return json({ telegram: 'attendere 10 minuti tra due prove' }, 429);
    await env.ALLERTE.put('prova_tg', String(Date.now()));
    return json({ telegram: await inviaTelegram(env, '✅ Prova allerte vento di Forecast Lagoon: il bot funziona.') });
  }
  let b = {}; try { b = await request.json(); } catch (e) { return json({ error: 'json non valido' }, 400); }
  const sub = b.subscription || b;
  if (!sub || !sub.endpoint || !/^https:\/\//.test(sub.endpoint) || !sub.keys || !sub.keys.p256dh || !sub.keys.auth) return json({ error: 'iscrizione non valida' }, 400);
  if (!env.ALLERTE) return json({ error: 'archivio non configurato' }, 503);
  const id = await idIscrizione(sub.endpoint);
  if (sotto === 'iscrivi') { await env.ALLERTE.put(id, JSON.stringify({ endpoint: sub.endpoint, keys: sub.keys, dal: new Date().toISOString() })); await aggiornaIscritti(env, L => L.includes(id) ? L : L.concat(id)); return json({ ok: true }); }
  if (sotto === 'disiscrivi') { await env.ALLERTE.delete(id); await aggiornaIscritti(env, L => L.filter(x => x !== id)); return json({ ok: true }); }
  if (sotto === 'prova') {
    const msg = { titolo: '✅ Prova allerte vento', corpo: 'Le allerte vento di Forecast Lagoon arrivano su questo telefono.', url: SITO_URL, tag: 'prova' };
    // v2.8: ritardo facoltativo (max 25 s, entro il limite di waitUntil) per chiudere l'app e bloccare il telefono prima che arrivi
    const rit = Math.max(0, Math.min(25, Number(b.ritardo_s) || 0));
    if (rit && ctx && ctx.waitUntil) {
      ctx.waitUntil(new Promise(r => setTimeout(r, rit * 1000)).then(() => inviaPush(env, sub, { ...msg, corpo: 'Prova ritardata: se la vedi con l\'app chiusa e il telefono bloccato, le allerte arrivano anche in barca.' }))
        .then(st => console.log('prova ritardata: HTTP ' + st)).catch(e => console.log('prova ritardata: errore ' + e)));
      return json({ programmata: true, ritardo_s: rit });
    }
    const s = await inviaPush(env, sub, msg);
    return json({ push_http: s });
  }
  return json({ error: 'sconosciuto' }, 404);
}
// =================================================================================

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/+|\/+$/g, ''); // strip slash

    // v2.5: /allerta/<chiave|stato|controlla|iscrivi|disiscrivi|prova|prova-telegram>
    if (path.startsWith('allerta/')) {
      if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { ...corsHeaders(request), 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' } });
      try { return await rispostaAllerta(request, env, path.slice('allerta/'.length), ctx); }
      catch (e) { return new Response(JSON.stringify({ error: 'allerta_failed', message: String(e) }), { status: 500, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } }); }
    }

    // Preflight CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    // v2.9: /nebbia/registro -> registro per l'indicatore locale di nebbia (lettura); /nebbia/prova -> una riga calcolata adesso, senza salvarla
    if (path === 'nebbia/registro' || path === 'nebbia/prova') {
      try {
        const corpo = path === 'nebbia/prova' ? await rigaNebbia(Date.now()) : JSON.parse((env.ALLERTE && await env.ALLERTE.get('nebbia_registro')) || '[]');
        return new Response(JSON.stringify(corpo), { status: 200, headers: { ...corsHeaders(request), 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
      } catch (e) { return new Response(JSON.stringify({ error: 'nebbia_failed', message: String(e) }), { status: 500, headers: { ...corsHeaders(request), 'Content-Type': 'application/json' } }); }
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
  , async scheduled(event, env, ctx) {
    if (event.cron === CRON_ALLERTE) {
      ctx.waitUntil(controllaAllerte(env).then(e => { if (e.eventi) console.log(JSON.stringify(e)); }));   // v2.5
      const m = new Date(event.scheduledTime || Date.now()).getUTCMinutes();
      if (m % 30 < 5) ctx.waitUntil(registraNebbia(env, event.scheduledTime || Date.now()).then(x => console.log(x)).catch(e => console.log('registro nebbia: errore ' + e)));   // v2.9
      return;
    }
    ctx.waitUntil(avviaGiro(env, event.cron === CRON_MATTINO ? 'mattino' : ''));
  }
};
