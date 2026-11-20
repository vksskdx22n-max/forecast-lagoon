#!/usr/bin/env python3
"""fd_previsione_giorni.py v1.10 — previsione meteo-marina di piu' giorni in una sola pagina (Fishing Diary).

Usa le funzioni di fd_previsione.py (>= 1.26, stessa cartella): stesso download, stessa correzione, stesso ripiego
su ICON-2i/ECMWF quando ICON-D2 non copre ancora il giorno. La verifica automatica (fd_verifica) parte una sola volta.

Uso (dalla cartella PYTON REPORT):
    python3 fd_previsione_giorni.py                                   -> da domani, 3 giorni, 07:00-11:30
    python3 fd_previsione_giorni.py 2026-11-13 2026-11-15             -> dal giorno al giorno
    python3 fd_previsione_giorni.py 2026-11-13 2026-11-15 06:30 11:00
    opzioni: --non-aprire  --no-verifica  --area centro

Uscita in ./Previsioni/:
    previsione_<n>giorni_GG-MM_GG-MM_HHMM-HHMM_v1.7.html / .json / .pdf / .png   pagina unica (dati in JSON, resa in JavaScript)
    previsione_GG-MM-AAAA_HHMM-HHMM_v<fd_previsione>.html / .json                 previsione completa di ogni giorno
                                                                                    (serve anche a fd_verifica)
"""
import sys, os, json, shutil, subprocess, datetime as dt
import fd_previsione as FP

VERSIONE = "1.10"
GIORNI_DEFAULT = 3


def sequenza_finestra(D, giorno, da, a):
    """Fasi di marea che toccano la finestra: [{fase, da, a}] con orari HH:MM. v1.1: stessa fonte del report
    del giorno (previsione CPSM se copre la finestra, altrimenti astronomica)."""
    out, seq = [], D["marea"]["sequenza"]
    if D["marea"].get("fonte_fase") == "previsione CPSM":
        E = D["marea"]["cpsm"]
        seq = [{"da": e0["ora"], "a": e1["ora"], "fase": "crescente" if e0["tipo"] == "min" else "calante",
                "cm_da": e0["cm"], "cm_a": e1["cm"]} for e0, e1 in zip(E, E[1:])]
    else:                                   # v1.2: livelli dagli estremali astronomici del giorno (se presenti)
        cm = {e["ora"]: e["cm"] for e in D["marea"]["estremali"]}
        seq = [{**q, "cm_da": cm.get(q["da"]), "cm_a": cm.get(q["a"])} for q in seq]
    for q in seq:
        t0 = dt.datetime.strptime(f"{q['da']}/{giorno.year}", "%d/%m %H:%M/%Y")
        t1 = dt.datetime.strptime(f"{q['a']}/{giorno.year}", "%d/%m %H:%M/%Y")
        w0, w1 = dt.datetime.combine(giorno, da), dt.datetime.combine(giorno, a)
        if t1 > w0 and t0 < w1:
            out.append({"fase": q["fase"], "da": t0.strftime("%H:%M") if t0.date() == giorno else q["da"],
                        "a": t1.strftime("%H:%M") if t1.date() == giorno else q["a"], "cm_da": q.get("cm_da"), "cm_a": q.get("cm_a"),
                        "delta": None if None in (q.get("cm_da"), q.get("cm_a")) else round(q["cm_a"] - q["cm_da"])})
    return out


def sintesi(D, giorno, da, a, file_html):
    """Valori del confronto tra i giorni, tutti presi dal risultato di FP.calcola."""
    S = D["striscia"]["ore"]
    st = [o["stima"] for o in S if o["stima"] is not None]
    rf = [o["raff"] for o in S if o["raff"] is not None]
    z0 = D["zone_vento"][0]
    r0, r1 = D["vento"][z0][0], D["vento"][z0][-1]
    PO = D["meteo"].get("pioggia_ore") or []
    p_d2 = [r["prob_D2"] for r in PO if r.get("prob_D2") is not None]
    p_ec = [r["prob_EC"] for r in PO if r.get("prob_EC") is not None]
    mm = [r[f"mm_{m}"] for r in PO for m in ("D2", "2i", "EC") if r.get(f"mm_{m}") is not None]
    onda = None
    if D.get("mare"):
        R = D["mare"]["righe"]
        hc = [r.get("h_corr_max", r.get("h_corr", r["h"])) for r in R]
        hm = [r.get("h_corr_min", r.get("h_corr", r["h"])) for r in R]
        onda = {"min": round(min(hm), 2), "max": round(max(hc), 2), "dir": R[len(R) // 2]["dir"],
                "corretta": any(r.get("h_corr") is not None for r in R)}
    Mr = D["marea"]                                   # v1.2: curva del giorno (corretta CPSM se c'e') ed estremi da segnare
    corr = bool(Mr.get("curva_cpsm"))
    curva = Mr["curva_cpsm"] if corr else Mr["curva"]
    x0, x1 = curva[0][0], curva[-1][0]
    if corr:                                          # punti sulla stessa curva disegnata
        est = [{"tipo": e["tipo"], "min": e["min_curva"], "cm": e["cm"], "ora": e["ora"][-5:]} for e in Mr["cpsm"] if x0 <= e["min_curva"] <= x1]
    else:
        est = []
        for e in Mr["estremali"]:
            hh, mm_ = map(int, e["ora"][-5:].split(":"))
            if x0 <= hh * 60 + mm_ <= x1:
                est.append({"tipo": e["tipo"], "min": hh * 60 + mm_, "cm": e["cm"], "ora": e["ora"][-5:]})
    vel = [abs(v["cm_h"]) for v in Mr["velocita"]]
    def liv(m):                                       # v1.4: livello della curva al minuto m (interpolato)
        for (a0, v0), (a1, v1) in zip(curva, curva[1:]):
            if a0 <= m <= a1:
                return round(v0 + (v1 - v0) * (m - a0) / ((a1 - a0) or 1))
        return None
    f0, f1 = Mr["fascia"]
    liv_fin = [{"min": m, "ora": f"{m // 60:02d}:{m % 60:02d}", "cm": liv(m)} for m in (f0, f1)]
    # estremo fuori dal grafico solo se la fase che tocca la finestra finisce (o comincia) fuori dal grafico
    Ec = Mr["cpsm"] if corr else []
    dopo = [e for e in Ec if e["min_curva"] > x1][:1] if not any(f1 <= e["min_curva"] <= x1 for e in Ec) else []
    prima = [e for e in Ec if e["min_curva"] < x0][-1:] if not any(x0 <= e["min_curva"] <= f0 for e in Ec) else []
    oltre = [{"tipo": e["tipo"], "ora": e["ora"][-5:], "cm": e["cm"], "dopo": e in dopo} for e in dopo + prima]
    rip_mod = sorted({r["ripiego"] for z in D["zone_vento"] for r in D["vento"][z] if r.get("ripiego")})
    return {"titolo": D["titolo"], "giorno": giorno.isoformat(), "colore": D["verdetto"]["colore"], "andamento": D["verdetto"].get("andamento") or [],
            "vento": [min(st), max(st)] if st else None, "raff": max(rf) if rf else None,
            "dir_ini": f"{r0['dir']} · {r0['nome']}", "dir_fin": f"{r1['dir']} · {r1['nome']}",
            "marea": sequenza_finestra(D, giorno, da, a), "fonte_marea": D["marea"]["fonte_fase"],
            "curva": curva, "fonte_curva": "previsione CPSM" if corr else "astronomica", "estremi": est, "fascia": Mr["fascia"],
            "vel_max": max(vel) if vel else None, "liv_finestra": liv_fin, "ferma": bool(Mr.get("ferma")),
            "cpsm_vecchia": bool(Mr.get("cpsm_vecchia")), "cpsm_emessa": Mr.get("cpsm_emessa"), "cpsm_eta_h": Mr.get("cpsm_eta_h"),
            "curva_astro": Mr["curva"] if (corr and Mr.get("cpsm_vecchia")) else None,
            "oltre": oltre,
            "pioggia_d2": max(p_d2) if p_d2 else None, "pioggia_ec": max(p_ec) if p_ec else None, "pioggia_mm": max(mm) if mm else None,
            "onda": onda, "aria": D["meteo"]["aria"], "pressione": D["meteo"]["pressione"], "alba": D["meteo"]["alba"],
            "ore_ripiego": D.get("ore_ripiego") or [], "ripiego_mod": rip_mod,
            "opinione": [r["testo"] for r in D["riflessioni"] if r["tipo"] == "opinione"],
            "striscia": D["striscia"], "pioggia_ore": PO, "file": file_html}


HTML = r"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Previsione più giorni</title>
<style>
:root{--bg:#f1f5f9;--card:#fff;--bd:#d8e0ea;--tx:#1e293b;--mu:#64748b;--ac:#3b82c4;--soft:#f1f5f9;
--verde:#16a34a;--giallo:#ca8a04;--rosso:#dc2626;--vs:#dcfce7;--gs:#fef3c7;--rs:#fee2e2}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.45 -apple-system,"Inter","Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
.hero{border-radius:14px;padding:22px;color:#fff;background:linear-gradient(135deg,#1e3a6b,#2c5aa0 60%,#4a8fd8)}
.hero .k{font-size:12px;letter-spacing:.14em;opacity:.85;text-transform:uppercase}
.hero h1{font:400 28px/1.15 Georgia,serif;margin:10px 0 8px}.hero .z{opacity:.9;font-size:15px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;margin-top:16px}
.card h2{margin:0 0 12px;font-size:13px;letter-spacing:.1em;color:var(--ac);text-transform:uppercase}
.nota{color:var(--mu);font-size:12.5px;margin-top:10px}
table.cf{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums;table-layout:fixed}
table.cf th{font-size:13px;padding:6px 4px;text-align:center;color:var(--tx)}table.cf th:first-child{width:92px}
table.cf td{padding:8px 4px;border-top:1px solid #e5eaf0;text-align:center;vertical-align:top}
table.cf td:first-child{text-align:left;color:var(--mu);font-size:12px}
.sm{font-size:11.5px;color:var(--mu)}
.dot{display:inline-flex;width:58px;height:58px;border-radius:50%;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:11px;text-transform:uppercase}
.dot.verde{background:var(--verde)}.dot.giallo{background:var(--giallo)}.dot.rosso{background:var(--rosso)}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-weight:700}.pill.verde{background:var(--vs);color:var(--verde)}.pill.giallo{background:var(--gs);color:var(--giallo)}.pill.rosso{background:var(--rs);color:var(--rosso)}
.rip{color:#a16207;font-weight:700}
.giorni{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:0 16px;align-items:start}
.gt{display:flex;align-items:center;gap:10px;margin-bottom:10px}.gt b{font-size:17px}
.op{font-size:13.5px;margin-top:10px;padding:8px 10px;background:#f5f3ff;border-radius:8px;color:#4c1d95}
a{color:var(--ac)}
.fonti{color:var(--mu);font-size:12px;text-align:center;margin:18px 8px 8px}
body.stampa .wrap{max-width:560px}body.stampa a.link{display:none}
</style></head><body><div class="wrap" id="app"></div>
<script id="dati" type="application/json">__DATI__</script>
<script>
const D=JSON.parse(document.getElementById('dati').textContent);
const h=(t,a={},...c)=>{const e=document.createElement(t);for(const[k,v]of Object.entries(a))k==='cls'?e.className=v:k==='html'?e.innerHTML=v:e.setAttribute(k,v);c.flat().forEach(x=>x!=null&&e.append(x));return e;};
const n=(x,d=1)=>x==null?'–':Number(x).toFixed(d).replace('.',',');
if(location.hash.includes('stampa'))document.body.classList.add('stampa');
const app=document.getElementById('app');const G=D.giorni;
document.title='Previsione '+G.map(g=>g.titolo.split(' ')[0]).join(' · ');
app.append(h('div',{cls:'hero'},h('div',{cls:'k'},'Previsione meteo-marina · '+G.length+' giorni · v'+D.versione),h('h1',{},G[0].titolo+' – '+G[G.length-1].titolo),h('div',{cls:'z'},'Finestra '+D.finestra+' · '+D.zone)));
/* confronto */
const corto=g=>{const p=g.titolo.split(' ');return p[0].slice(0,3)+' '+p[1].slice(0,5);};
const riga=(lab,f)=>h('tr',{},h('td',{},lab),...G.map(g=>h('td',{},f(g))));
const t=h('table',{cls:'cf'},h('tr',{},h('th',{}),...G.map(g=>h('th',{},corto(g)))));
t.append(riga('Semaforo',g=>h('div',{},h('span',{cls:'dot '+g.colore},g.colore),g.andamento.length>1?h('div',{style:'margin-top:6px;display:flex;flex-direction:column;gap:3px;align-items:center'},
  ...g.andamento.map((x,i)=>h('span',{cls:'pill '+x.colore,style:'font-size:11.5px'},(i?'dalle '+x.da:x.da+(x.a!==x.da?'–'+x.a:''))+' '+x.colore))):null)));
t.append(riga('Vento medio zone',g=>h('div',{},h('b',{},g.vento?n(g.vento[0])+'–'+n(g.vento[1]):'–'),' kn',g.ore_ripiego.length?h('span',{cls:'rip'},' †'):null)));
t.append(riga('Raffiche',g=>h('div',{},'fino a ',h('b',{},n(g.raff)))));
t.append(riga('Direzione',g=>h('div',{cls:'sm',style:'color:var(--tx)'},g.dir_ini===g.dir_fin?g.dir_ini:g.dir_ini+' → '+g.dir_fin)));
t.append(riga('Marea',g=>h('div',{},...g.marea.map(m=>h('div',{style:'font-size:12.5px;margin-bottom:4px'},h('b',{style:'color:'+(m.fase==='crescente'?'#0f766e':'#6d28d9')},(m.fase==='crescente'?'↑ ':'↓ ')+m.fase),
  h('div',{},m.da+' → '+m.a),m.cm_da!=null&&m.cm_a!=null?h('div',{},m.cm_da+' → '+m.cm_a+' cm ',h('b',{},'('+(m.delta>0?'+':'')+m.delta+' cm)')):null)),
  (()=>{const L=g.liv_finestra;if(!L||L.some(x=>x.cm==null))return null;const d=L[1].cm-L[0].cm;
    return h('div',{style:'font-size:12.5px;margin-top:4px;padding-top:4px;border-top:1px dashed #d8e0ea'},'in finestra ',h('b',{},L[0].ora+' '+L[0].cm+' → '+L[1].ora+' '+L[1].cm+' cm'),' ('+(d>0?'+':'')+d+')');})())));
t.append(riga('Velocità livello',g=>h('div',{},'max ',h('b',{},n(g.vel_max)),' cm/h',g.ferma?h('div',{style:'color:#b45309;font-weight:700;font-size:12.5px'},'quasi ferma (morto d’acqua)'):null,
  g.cpsm_vecchia?h('div',{cls:'sm'},'bollettino CPSM del '+g.cpsm_emessa+' ('+g.cpsm_eta_h+' h prima della fine finestra: oltre 48 h, tendenza meno affidabile)'):null)));
/* v1.2: curva di marea, stessa scala per tutti i giorni */
const ALL=G.flatMap(g=>g.curva.map(p=>p[1]).concat(g.estremi.map(e=>e.cm)).concat((g.curva_astro||[]).map(p=>p[1])));const YMIN=Math.floor((Math.min(...ALL)-8)/20)*20,YMAX=Math.ceil((Math.max(...ALL)+8)/20)*20;   /* margine per le etichette degli estremi */
function curvaMarea(g){const W=220,H=130,L=24,R=4,T=14,B=16,c=g.curva,x0=c[0][0],x1=c[c.length-1][0];
  const X=v=>L+(v-x0)/((x1-x0)||1)*(W-L-R),Y=v=>T+(YMAX-v)/((YMAX-YMIN)||1)*(H-T-B);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="font:9px sans-serif;fill:#64748b">`;
  s+=`<rect x="${X(g.fascia[0])}" y="${T}" width="${X(g.fascia[1])-X(g.fascia[0])}" height="${H-T-B}" fill="#dbe7f6" opacity=".7"/>`;
  for(let y=YMIN;y<=YMAX;y+=20)s+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="#e2e8f0"/><text x="1" y="${Y(y)+3}">${y}</text>`;
  for(let m=Math.ceil(x0/120)*120;m<=x1;m+=120)s+=`<text x="${X(m)-5}" y="${H-3}">${String(m/60).padStart(2,'0')}</text>`;
  if(g.curva_astro)s+=`<polyline fill="none" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="3 3" points="${g.curva_astro.map(p=>X(p[0])+','+Y(p[1])).join(' ')}"/>`;
  s+=`<polyline fill="none" stroke="#1e3a6b" stroke-width="2" points="${c.map(p=>X(p[0])+','+Y(p[1])).join(' ')}"/>`;
  (g.liv_finestra||[]).forEach((l,i)=>{if(l.cm==null)return;const x=X(l.min),y=Y(l.cm);
    s+=`<circle cx="${x}" cy="${y}" r="3" fill="#1e3a6b"/>`;if(g.estremi.some(e=>Math.abs(e.min-l.min)<=40))return;   /* etichetta solo se lontana da un estremo */
    s+=`<text x="${i?x-46:x+3}" y="${y+12}" style="fill:#1e3a6b;font-weight:700">${l.ora} ${l.cm}</text>`;});
  (g.oltre||[]).forEach(o=>{s+=`<text x="${o.dopo?W-R-62:L+2}" y="${T+8}" style="fill:#dc2626;font-weight:700">${o.dopo?'':'← '}${o.tipo} ${o.ora} ${o.cm}${o.dopo?' →':''}</text>`;});
  g.estremi.forEach(e=>{const x=X(e.min),y=Y(e.cm);s+=`<circle cx="${x}" cy="${y}" r="3" fill="#dc2626"/><text x="${Math.min(W-44,Math.max(L,x-20))}" y="${e.tipo==='max'?y-5:y+11}" style="fill:#dc2626;font-weight:700">${e.ora} ${e.cm}</text>`;});
  return h('div',{html:s+'</svg>'});}
t.append(riga('Curva marea',g=>h('div',{},curvaMarea(g),h('div',{cls:'sm'},g.fonte_curva+(g.curva_astro?' (tratteggio: astronomica)':'')))));
t.append(riga('Pioggia',g=>h('div',{},h('b',{},g.pioggia_d2!=null?g.pioggia_d2+'%':(g.pioggia_ec!=null?g.pioggia_ec+'% EC':'–')),h('div',{cls:'sm'},'max '+n(g.pioggia_mm)+' mm/h'))));
t.append(riga('Onda fuori bocca',g=>g.onda?h('div',{},h('b',{},n(g.onda.min,1)+'–'+n(g.onda.max,1)+' m'),h('div',{cls:'sm'},g.onda.dir+(g.onda.corretta?' · corretta':''))):'–'));
t.append(riga('Aria',g=>h('div',{},n(+String(g.aria[0]).replace(',','.'))+'–'+n(+String(g.aria[1]).replace(',','.'))+' °C')));
t.append(riga('Pressione',g=>h('div',{cls:'sm',style:'color:var(--tx)'},g.pressione[0]+' → '+g.pressione[1]+' hPa',h('div',{cls:'sm'},g.pressione[2]))));
t.append(riga('Alba',g=>g.alba||'–'));
const rip=G.filter(g=>g.ore_ripiego.length);
app.append(h('div',{cls:'card'},h('h2',{},'📊 Confronto dei giorni'),t,
  h('div',{cls:'nota'},'Semaforo e vento = media delle zone di pesca ('+D.zone_vento.join(', ')+'), nodi, stima corretta; colore: '+D.testo_semaforo+'; le raffiche non cambiano il colore); cerchio = colore più frequente nelle ore (a parità quello che viene prima), sotto l’andamento ora per ora. Pioggia: probabilità massima ICON-D2 di più di 0,1 mm nella finestra (EC = ECMWF se ICON-D2 manca); mm/h = massimo tra i tre modelli. Onda: modello Open-Meteo Marine fuori dalla bocca, corretta con le verifiche alla Piattaforma CNR quando disponibile. Marea: fasi che toccano la finestra con livelli agli estremi in cm su Punta Salute ('+[...new Set(G.map(g=>g.fonte_marea))].join(' / ')+'); velocità = massima variazione oraria del livello nella finestra; curva: stessa scala per tutti i giorni, fascia azzurra = ore di pesca, punti rossi = estremi (con ora e cm; se fuori dal grafico, scritti sul bordo con la freccia), punti blu = livello a inizio e fine finestra.'
    +(rip.length?' † '+rip.map(g=>corto(g)+': ICON-D2 non copre le ore '+g.ore_ripiego.map(o=>o.slice(0,2)).join(', ')+', stima = '+g.ripiego_mod.join('/')+' senza correzione (meno affidabile)').join('; ')+'.':''))));
/* giorni */
const colP=v=>v==null?['#f1f5f9','#64748b']:v<20?['#e6f1fb','#0c447c']:v<40?['#b5d4f4','#0c447c']:v<60?['#85b7eb','#042c53']:v<80?['#378add','#fff']:['#185fa5','#fff'];
function striscia(g){const S=g.striscia;if(!S||!S.ore.length)return null;
  const mx=Math.max(0.3,...S.ore.map(o=>o.corr||0));
  const q=h('div',{style:`display:grid;grid-template-columns:52px repeat(${S.ore.length},1fr);gap:6px 4px;align-items:center;font-size:13px`});
  const lab=t=>h('div',{style:'color:var(--mu);font-size:11px'},t);
  q.append(lab(''),...S.ore.map(o=>h('div',{style:'text-align:center;font-weight:700'},o.ora)));
  q.append(lab('Vento'),...S.ore.map(o=>h('div',{style:'text-align:center'},h('span',{cls:'pill '+o.sem},n(o.stima,0)),g.ore_ripiego.includes(o.ora+':00')?h('span',{cls:'rip'},'†'):null)));
  q.append(lab('Raffiche'),...S.ore.map(o=>h('div',{style:'text-align:center;font-size:12px;color:var(--mu)'},n(o.raff))));
  q.append(lab('Marea'),...S.ore.map(o=>h('div',{style:'text-align:center;line-height:1.1'},h('div',{style:'font-size:18px;font-weight:700;color:'+(o.marea==='↑'?'#0f766e':o.marea==='↓'?'#6d28d9':'#64748b')},o.marea),h('div',{style:'font-size:10px;color:var(--mu)'},(o.cm_h>0?'+':'')+n(o.cm_h)))));
  if(S.canale)q.append(lab('Corrente'),...S.ore.map(o=>{const w=o.corr==null?0:Math.max(4,Math.round(o.corr/mx*100));const c=o.verso==='entra'?'#2dd4bf':'#a78bfa';
    return h('div',{style:'text-align:center'},h('div',{style:`height:8px;border-radius:4px;background:${c};width:${w}%;margin:0 auto`}),h('div',{style:'font-size:10px;color:var(--mu);margin-top:2px'},n(o.corr,2)));}));
  const PO=g.pioggia_ore||[];const kP=PO.some(r=>r.prob_D2!=null)?'prob_D2':'prob_EC';
  if(PO.length===S.ore.length&&PO.some(r=>r[kP]!=null))q.append(lab('Pioggia'+(kP==='prob_EC'?' EC':'')),...PO.map(r=>{const c=colP(r[kP]);
    return h('div',{style:'text-align:center'},h('span',{style:`display:inline-block;padding:2px 6px;border-radius:10px;font-weight:700;font-size:12px;background:${c[0]};color:${c[1]}`},r[kP]==null?'–':r[kP]+'%'));}));
  return q;}
const cont=h('div',{cls:'giorni'});
G.forEach(g=>{const c=h('div',{cls:'card'},h('div',{cls:'gt'},h('span',{cls:'pill '+g.colore},g.colore.toUpperCase()),h('b',{},g.titolo)),striscia(g),
  ...g.opinione.map(x=>h('div',{cls:'op'},x)),
  h('div',{cls:'nota'},'Marea ↑ crescente, ↓ calante, ≈ ferma (cm/h); corrente '+(g.striscia.canale||'')+' m/s, azzurro entra, viola esce (modello). ',h('a',{cls:'link',href:g.file},'Previsione completa del giorno →')));
  cont.append(c);});
app.append(cont);
app.append(h('div',{cls:'fonti'},D.fonti+' · generata '+D.generato));
if(document.body.classList.contains('stampa')){const st=document.createElement('style');st.textContent=`@page{size:${document.documentElement.scrollWidth}px ${document.documentElement.scrollHeight+4}px;margin:0}`;document.head.append(st);}
</script></body></html>"""


def main():
    valori = {sys.argv[i + 1] for i, x in enumerate(sys.argv[:-1]) if x in ("--area",)}
    arg = [x for x in sys.argv[1:] if not x.startswith("--") and x not in valori]
    if "--area" in sys.argv:
        nome = sys.argv[sys.argv.index("--area") + 1]
        if nome not in FP.AREE:
            sys.exit(f"Area sconosciuta: {nome}. Aree: sud (predefinita), {', '.join(FP.AREE)}")
        FP.imposta_area(nome)
    date = [x for x in arg if len(x) == 10 and x[4] == "-"]
    ore = [x for x in arg if x not in date]
    g0 = dt.date.fromisoformat(date[0]) if date else dt.date.today() + dt.timedelta(days=1)
    g1 = dt.date.fromisoformat(date[1]) if len(date) > 1 else g0 + dt.timedelta(days=GIORNI_DEFAULT - 1)
    if g1 < g0:
        sys.exit("La seconda data deve essere uguale o successiva alla prima.")
    da = dt.time.fromisoformat(ore[0]) if ore else dt.time(7, 0)
    a = dt.time.fromisoformat(ore[1]) if len(ore) > 1 else dt.time(11, 30)
    giorni = [g0 + dt.timedelta(days=i) for i in range((g1 - g0).days + 1)]
    for f in ["correnti_laguna.json"] + sorted({f"marea_astronomica_{g.year}.json" for g in giorni}):
        if not os.path.exists(f):
            sys.exit(f"Manca {f} nella cartella {os.getcwd()}")
    print(f"fd_previsione_giorni v{VERSIONE} (fd_previsione v{FP.VERSIONE}) — {g0.strftime('%d/%m')}–{g1.strftime('%d/%m/%Y')} "
          f"{da.strftime('%H:%M')}–{a.strftime('%H:%M')}")
    if FP.FV is None:
        print("fd_verifica.py non trovato: niente verifica automatica, correzione fissa +2,2 nodi.")
    elif "--no-verifica" not in sys.argv:
        for r in FP.FV.verifica_pendenti("."):
            print(r)
    CP = FP.scarica_cpsm()
    print(f"Previsione marea CPSM: {len(CP[0])} estremali (emessa {CP[1]})" if CP[0] else "Previsione marea CPSM non disponibile: uso la marea astronomica")
    ARIA = FP.misure_aria()
    M = FP.carica_diario()
    C = json.load(open("correnti_laguna.json", encoding="utf-8"))
    os.makedirs("Previsioni", exist_ok=True)
    out = []
    for g in giorni:
        OM = FP.scarica(g, da, a)
        MAR = FP.scarica_mare(g)
        D = FP.calcola(g, da, a, OM, M, FP.Marea(f"marea_astronomica_{g.year}.json"), C, CP, MAR, ARIA)
        base = os.path.join("Previsioni", f"previsione_{g.strftime('%d-%m-%Y')}_{da.strftime('%H%M')}-{a.strftime('%H%M')}"
                                          f"{'' if FP.AREA == 'sud' else '_' + FP.AREA}_v{FP.VERSIONE}")
        json.dump(D, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(base + ".html", "w", encoding="utf-8").write(FP.HTML.replace("__DATI__", json.dumps(D, ensure_ascii=False).replace("</", "<\\/")))
        S = sintesi(D, g, da, a, os.path.basename(base + ".html"))
        out.append(S)
        print(f"  {D['titolo']}: {D['verdetto']['colore'].upper()} · vento {FP.num(S['vento'][0])}–{FP.num(S['vento'][1])} nodi, raffiche fino a {FP.num(S['raff'])}"
              + (f" · ripiego {'/'.join(S['ripiego_mod'])} alle {', '.join(o[:2] for o in S['ore_ripiego'])}" if S["ore_ripiego"] else ""))
    T = {"versione": VERSIONE, "testo_semaforo": f"verde fino a {FP.VERDE_FINO_A} nodi, giallo da {FP.VERDE_FINO_A + 1} a {FP.ROSSO_DA - 1}, rosso da {FP.ROSSO_DA} (vento medio arrotondato all'intero",   # v1.10
         "finestra": f"{da.strftime('%H:%M')}–{a.strftime('%H:%M')}", "zone": FP.ZONE_TITOLO, "zone_vento": FP.ZONE_VENTO,
         "giorni": out,
         "fonti": f"Fonti: Open-Meteo (ICON-D2 DWD, ICON-2i ItaliaMeteo-ARPAE, ECMWF IFS) · marea CPSM · correnti: modello Laguna Viva · "
                  f"fd_previsione v{FP.VERSIONE} · fd_previsione_giorni v{VERSIONE}",
         "generato": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
    base = os.path.join("Previsioni", f"previsione_{len(giorni)}giorni_{g0.strftime('%d-%m')}_{g1.strftime('%d-%m')}_{da.strftime('%H%M')}-{a.strftime('%H%M')}"
                                      f"{'' if FP.AREA == 'sud' else '_' + FP.AREA}_v{VERSIONE}")
    json.dump(T, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(base + ".html", "w", encoding="utf-8").write(HTML.replace("__DATI__", json.dumps(T, ensure_ascii=False).replace("</", "<\\/")))
    chrome = FP.trova_chrome()
    if not chrome:
        print("Google Chrome non trovato: creata solo la pagina HTML.")
    elif FP.stampa(chrome, base + ".html", "stampa", base + ".pdf"):
        FP.pdf_png(base + ".pdf", base + ".png")
    print("Creati:", ", ".join(os.path.basename(base + x) for x in [".html", ".pdf", ".png"] if os.path.exists(base + x)))
    print("Cartella:", os.path.abspath("Previsioni"))
    if "--non-aprire" not in sys.argv and shutil.which("open"):
        subprocess.run(["open", base + ".html"])


if __name__ == "__main__":
    main()
