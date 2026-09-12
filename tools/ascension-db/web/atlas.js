import './search-aliases.js';
const LAYERS = {
  'route-bosses': ['Bosses', 'skull', '#e8cf71'],
  'route-npcs': ['NPCs', 'person', '#80bad7'],
  creatures: ['Creatures', 'dot', '#ec6262'],
  objects: ['Objects', 'box', '#e2bc69'],
  herbs: ['Herbs', 'leaf', '#88d779'],
  mining: ['Ore', 'pick', '#c1d4df'],
  vendors: ['Vendors', 'coin', '#dfc582'],
  'quest-givers': ['Quests', 'quest', '#e8cf71'],
  'npc-claims': ['NPCs', 'person', '#bd9fdd'],
  worldforged: ['Worldforged', 'sword', '#e9a26d'],
  mystic: ['Scrolls', 'scroll', '#80d1b2'],
  'other-loot': ['Loot', 'bag', '#aebdca'],
};
export function atlasDisplayRoles(point) {
 const roles=point.roles || [point.layer];
 if(point.layer!=='objects')return roles;
 const style=atlasMarkerStyle(point,'objects'),resource=style.icon==='leaf'?'herbs':style.icon==='pick'?'mining':'';
 return resource?roles.map(role=>role==='objects'?resource:role):roles;
}
export function atlasVisibleRole(point, enabled) { const roles=atlasDisplayRoles(point),primary=point.layer==='objects'?roles[0]:point.layer;return enabled.has(primary)?primary:roles.find(role=>enabled.has(role)) || primary; }
// Artwork is code-owned; resource identities are exact names, never substring guesses.
const HERBS = new Set(['peacebloom','silverleaf','earthroot','firebloom','plaguebloom','wild steelbloom','mageroyal','briarthorn','bruiseweed','kingsblood','liferoot','fadeleaf','goldthorn','khadgar’s whisker',"khadgar's whisker",'wintersbite','purple lotus','sungrass','blindweed','ghost mushroom','gromsblood','golden sansam','dreamfoil','mountain silversage','icecap','black lotus']);
const ORES = new Set(['copper vein','tin vein','silver vein','gold vein','iron deposit','mithril deposit','truesilver deposit','small thorium vein','rich thorium vein','incendicite mineral vein','lesser bloodstone deposit','ooze covered mithril deposit','ooze covered rich thorium vein','ooze covered silver vein','ooze covered truesilver deposit']);
const ICONS = {
 dot:'<circle r="4"/>',
 person:'<circle cy="-3.5" r="2.3"/><path d="M-4.5 5V3a4.5 4.5 0 0 1 9 0v2Z"/>',
 quest:'<path d="M-2.3-6h4.6L1.6 2h-3.2Z"/><circle cy="5" r="2"/>',
 coin:'<circle cx="-2" cy="1" r="4.5"/><path d="M1-5a4.5 4.5 0 0 1 4 7M-2-1v4" fill="none"/>',
 leaf:'<path d="M-5 4C-7-3 0-6 6-6 6 1 2 7-5 4Z"/><path d="m-6 6 9-9" fill="none"/>',
 pick:'<path d="m-5 6 8-11 2 1L-3 7Z" fill="#c49b64"/><path d="M-6-2C-3-8 4-6 7 0L1-3Z"/>',
 box:'<path d="m-5-3 5-3 5 3v8H-5Z"/><path d="M-5-2H5M0-5v3M-1 0h2v2h-2Z" fill="none"/>',
 bag:'<path d="m-3-6 3 1 3-1-1 4c7 7 3 9-2 9s-9-2-2-9Z"/><path d="M-3-2h6" fill="none"/>',
 scroll:'<path d="M-4-5H4V4a2 2 0 0 1-2 2h-6Z"/><path d="M-4-5c-3 0-3 3 0 3M2 3h4c0 4-4 4-4 0M-2-2h4M-2 0h4" fill="none"/>',
 sword:'<path d="m4-7 2 2-6 8-3-3Z"/><path d="m-5 0 6 5M-2 3l-4 4" fill="none" stroke-width="2"/>',
 skull:'<path d="M-5 1C-10-9 10-9 5 1L3 2v4h-6V2Z"/><path d="M-3-2h1M2-2h1M-1 3v3M1 3v3" fill="none" stroke-width="2"/>'
};
export function atlasMarkerStyle(point, role=point.layer) {
 let icon=LAYERS[role]?.[1] || 'person', color=LAYERS[role]?.[2] || '#80bad7', label=LAYERS[role]?.[0] || 'NPCs';
 if(role==='objects') {
  const name=String(point.name||'').trim().toLowerCase();
  if(HERBS.has(name)){icon='leaf';color='#88d779';label='Herb';}
  else if(ORES.has(name)){icon='pick';color='#c1d4df';label='Mining node';}
 }
 return {icon,color,label};
}
function iconMarkup(point,role) {
 const {icon,color}=atlasMarkerStyle(point,role);
 return `<svg class="atlas-icon" viewBox="-8 -8 16 16" aria-hidden="true" fill="${color}" stroke="#14202a" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round">${ICONS[icon]}</svg>`;
}
export function atlasRelevantRoles(points) { return new Set(points.flatMap(atlasDisplayRoles)); }
export function clusterAtlasPoints(points, unit, spacing=16) {
 const cell=spacing*unit,buckets=new Map(),groups=[];
 for(const point of [...points].sort((a,b)=>a.plot_x-b.plot_x||a.plot_y-b.plot_y||a.key.localeCompare(b.key))) {
  const x=point.plot_x*10,y=point.plot_y*6.67,bx=Math.floor(x/cell),by=Math.floor(y/cell);
  let nearest=null,distance=cell*cell;
  for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++)for(const group of buckets.get(`${bx+dx},${by+dy}`)||[]) {
   const d=(group.x-x)**2+(group.y-y)**2;if(d<=distance){nearest=group;distance=d;}
  }
  if(nearest)nearest.points.push(point);
  else {const group={x,y,points:[point]},key=`${bx},${by}`;if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(group);groups.push(group);}
 }
 return groups.map(g=>g.points);
}
let epoch = 0, cleanup = () => {}, atlasPromise, linksPromise;
const cache = new Map();
const clamp = (n,a,b) => Math.max(a,Math.min(b,n));
const finite = (v,fallback) => Number.isFinite(Number(v)) && v !== null ? Number(v) : fallback;
const normalize = s => String(s).normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();
export function closeAtlas() { epoch++; cleanup(); cleanup = () => {}; }
export async function atlasRecordLinks(key, manifest, gz) {
  if (!manifest.atlas) return [];
  try { linksPromise ||= gz(manifest.atlas.links).catch(e => { linksPromise = null; throw e; }); atlasPromise ||= gz(manifest.atlas.manifest).catch(e=>{atlasPromise=null;throw e;});const [links,atlas]=await Promise.all([linksPromise,atlasPromise]);return (links[key]||[]).map(k=>({key:k,label:atlas.zones.find(z=>z.key===k)?.name||k})); }
  catch { return []; }
}
export async function showAtlas({root, manifest, gz, notice, esc, params}) {
  const job = epoch;
  if (!manifest.atlas) { root.innerHTML='<div class="banner">The atlas is being prepared for this catalog snapshot. Refresh after the next publication.</div>'; return; }
  notice('Opening the preserved world atlas…');
  atlasPromise ||= gz(manifest.atlas.manifest).catch(e => { atlasPromise = null; throw e; });
  const atlas = await atlasPromise;
  if (job !== epoch) return;
  if (atlas.revision !== manifest.revision) throw Error('The atlas snapshot changed. Refresh to load matching records.');
  const zones = atlas.zones;
  let zone = zones.find(z=>z.key===params.get('zone')) || zones.find(z=>z.name==='Elwynn Forest' && z.count) || zones.find(z=>z.count) || zones[0];
  let clusterMembers=[], clusterAnchor='', selected='', page=0, listSort=params.get('order') || 'name', enabled = new Set(params.has('layers') ? params.get('layers').split(',').filter(k=>LAYERS[k]) : Object.keys(LAYERS));
  if(params.has('layers')&&!params.has('tracking')&&enabled.has('objects')){enabled.add('herbs');enabled.add('mining');}
  let zoom = clamp(finite(params.get('zoom'),1),1,12), cx=clamp(finite(params.get('cx'),500),0,1000), cy=clamp(finite(params.get('cy'),333.5),0,667);
  let observations=[], filtered=[], visible=[], disposed=false, mapArt=true, source=params.get('source') || '', mode=params.get('mode') || '', origin=params.get('origin') || '', query=params.get('q') || '';
  const $=s=>root.querySelector(s);
  const visibleRole=p=>atlasVisibleRole(p,enabled);
  const option=(v,t,chosen)=>`<option value="${esc(v)}" ${v===chosen?'selected':''}>${esc(t)}</option>`;
  const zoneTitle=z=> `${z.name}${z.space==='floor'?' · '+z.era:z.space==='world'?' · raw coordinates':z.space==='reported'?' · reported coordinates':z.space==='inventory'?' · map inventory':''}${z.wma ? ' · map space '+z.wma : z.map ? ' · map '+z.map : ''}`;
  root.innerHTML=`<section class="atlas">
    ${manifest.sample?'<div class="banner">Limited preview</div>':''}
    <div class="atlas-title"><h2>World Atlas</h2></div>
    <div class="atlas-picker"><label>Find a zone<input id="atlas-zone-query" type="search" placeholder="Zone name, shortcut or map ID"></label><label>World<select id="atlas-world"><option value="">All worlds</option>${(atlas.maps || []).map(m=>option(m.id,`${m.name} · ${m.id}`,zone.map)).join('')}</select></label><label class="atlas-zone-select">Zone<span class="atlas-zone-open"><select id="atlas-zone"></select><button type="button" id="atlas-open-zone">Open</button></span></label></div>
    <nav id="atlas-floor-nav" class="atlas-floor-nav" aria-label="Instance floors and evidence views"></nav>
    <div class="atlas-workbench"><div class="atlas-main">
      <div class="atlas-map-heading"><div><h3 id="atlas-zone-title"></h3><span id="atlas-space-label" class="muted"></span></div><div class="atlas-zoom"><button id="atlas-out" aria-label="Zoom out">−</button><output id="atlas-zoom-level">1×</output><button id="atlas-in" aria-label="Zoom in">+</button><button id="atlas-reset">Reset</button></div></div>
      <div class="atlas-layer-bar" role="group" aria-label="Map layers">${Object.entries(LAYERS).map(([k,[name]])=>`<label class="layer-${k}"><input type="checkbox" data-layer="${k}" ${enabled.has(k)?'checked':''}>${iconMarkup({layer:k},k)}${name}</label>`).join('')}</div>
      <div class="atlas-canvas"><svg id="atlas-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 667" role="group" tabindex="0" aria-label="Interactive map. Arrow keys pan, plus and minus zoom, Home resets."><defs><pattern id="atlas-grid" width="100" height="66.7" patternUnits="userSpaceOnUse"><path d="M100 0H0V66.7" fill="none" stroke="#c4ad74" stroke-opacity=".18" stroke-width="1"/></pattern></defs><rect width="1000" height="667" fill="#16252d"/><image id="atlas-image" x="0" y="0" width="1000" height="667" preserveAspectRatio="none"/><rect width="1000" height="667" fill="url(#atlas-grid)"/><g id="atlas-grid-labels"></g><g id="atlas-points"></g></svg><div class="atlas-compass" aria-hidden="true">N<br>↑</div><div id="atlas-empty-overlay" class="atlas-empty-overlay" hidden></div></div>
      <div class="atlas-map-foot"><span>Drag to pan · scroll to zoom</span><span id="atlas-art-credit"></span></div>
      <details class="atlas-coverage"><summary>Map details</summary><div id="atlas-zone-summary" class="atlas-zone-summary"></div><p>${(atlas.preserved_observations ?? atlas.observations).toLocaleString()} preserved observations${atlas.reference_locations?` · ${atlas.reference_locations.toLocaleString()} instance references`:''}</p><div id="atlas-coverage-detail"></div><p>${Object.entries(atlas.unmapped).map(([k,v])=>`${esc(k)}: ${v.toLocaleString()}`).join('<br>')}</p></details>
    </div><aside class="atlas-sidebar" aria-label="Map locations">
      <form id="atlas-filter"><label class="atlas-search-label">Search this map<input id="atlas-query" type="search" placeholder="Name or ID" value="${esc(query)}"></label><div class="atlas-filter-actions"><button type="submit">Search</button><button id="atlas-clear" type="button" class="quiet-button">Clear</button></div><details id="atlas-advanced" ${source||mode||origin||listSort!=='name'?'open':''}><summary>Filters & sort<span id="atlas-filter-status"></span></summary><div class="atlas-filter-pair"><label>Source<select id="atlas-source">${option('','All sources',source)}${atlas.sources.map(x=>option(x,x,source)).join('')}</select></label><label>Game mode<select id="atlas-mode">${option('','All modes',mode)}${atlas.modes.map(x=>option(x,x,mode)).join('')}</select></label></div><div class="atlas-filter-pair"><label>Origin<select id="atlas-origin">${option('','All origins',origin)}${option('ascension','Ascension additions',origin)}${option('stock','Stock entries',origin)}</select></label><label>Sort<select id="atlas-order">${option('name','Name A–Z',listSort)}${option('id','ID ascending',listSort)}${option('source','Source A–Z',listSort)}</select></label></div></details></form>
      <section id="atlas-cluster" class="atlas-cluster" aria-label="Grouped locations" hidden></section>
      <div id="atlas-selected" class="atlas-selected" aria-live="polite" hidden></div>
      <div class="atlas-list-heading"><strong id="atlas-count" role="status"></strong><button id="atlas-export" class="quiet-button" type="button">Export</button></div><div id="atlas-list" class="atlas-list"></div><div class="atlas-list-pager"><button id="atlas-prev" class="quiet-button">Previous</button><span id="atlas-page"></span><button id="atlas-next" class="quiet-button">Next</button></div>
    </aside></div></section>`;
  const svg=$('#atlas-svg'), NS='http://www.w3.org/2000/svg';
  function url() {
    const p=new URLSearchParams({zone:zone.key});
    for(const [k,v] of Object.entries({q:query,source,mode,origin,order:listSort,record:params.get('record')}))if(v)p.set(k,v);
    if(enabled.size!==Object.keys(LAYERS).length){p.set('layers',[...enabled].join(','));p.set('tracking','1');}
    if(zoom!==1){p.set('zoom',zoom.toFixed(3));p.set('cx',cx.toFixed(2));p.set('cy',cy.toFixed(2));}
    history.replaceState(null,'','#atlas?'+p);
  }
  function populateZones() {
    const q=$('#atlas-zone-query').value.trim(),world=$('#atlas-world').value;
    const choices=AscensionSearchAliases.variants(q),matches=text=>AscensionSearchAliases.matches(text,choices);
    const direct=z=>[z.name,...z.aliases,String(z.map),String(z.wma)].some(matches);
    const relatedWorlds=new Set((atlas.maps||[]).filter(m=>q&&[m.name,...(m.alternate_names||[])].some(matches)).map(m=>m.id));
    if(q&&!zones.some(direct)&&!relatedWorlds.size)for(const m of atlas.maps||[])if((m.areas||[]).some(a=>matches(a.name)))relatedWorlds.add(m.id);
    const items=zones.filter(z=>(!world||z.map===world)&&(!q||direct(z)||relatedWorlds.has(z.map)));
    if(q)items.sort((a,b)=>(b.space==='floor')-(a.space==='floor')||a.name.localeCompare(b.name));
    $('#atlas-zone').innerHTML=items.map(z=>option(z.key,`${zoneTitle(z)} · ${z.count} ${z.space==='floor'?'reference markers':'observations'}`,zone.key)).join('') || '<option value="">No matching zones</option>';
  }
  $('#atlas-world').value=zone.map;
  populateZones();
  $('#atlas-zone-query').oninput=()=>{$('#atlas-world').value='';populateZones();};
  const openZone=()=>{if(!$('#atlas-zone').value)return;const p=new URLSearchParams({zone:$('#atlas-zone').value});location.hash='atlas?'+p;};
  $('#atlas-world').onchange=()=>{$('#atlas-zone-query').value='';populateZones();const preferred=zones.find(z=>z.map===$('#atlas-world').value&&z.space==='floor') || zones.find(z=>z.map===$('#atlas-world').value&&z.count);if(preferred)$('#atlas-zone').value=preferred.key;openZone();};
  $('#atlas-zone').onchange=openZone;
  $('#atlas-open-zone').onclick=openZone;
  $('#atlas-zone-query').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();openZone();}};
  const worldInfo=(atlas.maps||[]).find(m=>m.id===zone.map);
  const floorSpaces=zones.filter(z=>z.map===zone.map&&z.space==='floor');
  if(floorSpaces.length){
    const layouts=[...new Set(floorSpaces.map(z=>z.layout))];
    $('#atlas-floor-nav').innerHTML=layouts.map(layout=>`<div><strong>${esc(floorSpaces.find(z=>z.layout===layout).layout_name)}</strong><div>${floorSpaces.filter(z=>z.layout===layout).sort((a,b)=>a.floor-b.floor).map(z=>`<a class="quiet-button ${zone.key===z.key?'is-current':''}" ${zone.key===z.key?'aria-current="page"':''} href="#atlas?zone=${encodeURIComponent(z.key)}">Floor ${z.floor}</a>`).join('')}</div></div>`).join('')+`<details ${zone.space!=='floor'?'open':''}><summary>Original observations · floor not established</summary><div>${zones.filter(z=>z.map===zone.map&&z.space!=='floor'&&z.count).map(z=>`<a class="quiet-button ${zone.key===z.key?'is-current':''}" href="#atlas?zone=${encodeURIComponent(z.key)}">${esc(zoneTitle(z))} · ${z.count}</a>`).join('') || '<span class="muted">No original coordinate observations preserved.</span>'}</div></details>`;
  }else $('#atlas-floor-nav').hidden=true;
  $('#atlas-zone-title').textContent=zone.name;
  $('#atlas-zone-summary').innerHTML=`<span>${esc(zone.map_name || CONTINENT_NAMES[zone.map] || 'Preserved world')} ${zone.map ? '· map '+esc(zone.map):''}</span><span>${zone.space==='floor' ? esc(zone.era)+' · paired floor map' : zone.wma ? 'Captured map space '+esc(zone.wma) : 'Source-reported area'}</span><span>${zone.count.toLocaleString()} ${zone.space==='floor'?'reference markers':'observations'}</span>${zone.inventory?.above_stock_range==='1'?'<span class="atlas-custom">Extended map ID</span>':''}`;
  $('#atlas-coverage-detail').innerHTML=`<dl><dt>Names preserved in this map space</dt><dd>${zone.aliases.map(esc).join(', ')}</dd>${worldInfo?`<dt>Map names across sources</dt><dd>${esc(worldInfo.name)}${worldInfo.alternate_names?.length ? ' · '+worldInfo.alternate_names.map(esc).join(' · '):''}</dd>`:''}<dt>Coordinate space</dt><dd>${zone.space==='floor'?'Provider image pixels, scaled to this exact floor image':zone.space==='inventory'?'No coordinate observations preserved':zone.space==='world'?'Area-local schematic with extent derived from reported raw coordinates':['reported','world'].includes(zone.space)?'Reported instance coordinates; units and floor unverified':zone.bounds?'Zone percentages with captured bounds':'Source-reported zone percentages'}</dd>${zone.bounds?`<dt>Captured world bounds</dt><dd>Top ${esc(zone.bounds.top)} · bottom ${esc(zone.bounds.bottom)} · left ${esc(zone.bounds.left)} · right ${esc(zone.bounds.right)}</dd>`:''}${zone.inventory?`<dt>Preserved terrain inventory</dt><dd>${esc(zone.inventory.terrain_tiles)} terrain tiles · ${esc(zone.inventory.vmap_tiles)} visibility tiles. The inventory does not include terrain artwork.</dd>`:''}</dl>${(worldInfo?.records||[]).map(k=>`<p><a href="#record=${encodeURIComponent(k)}">Open map source record →</a></p>`).join('')}${worldInfo?.areas?.length?`<details><summary>${worldInfo.areas.length} named areas in this world</summary><ul>${worldInfo.areas.map(a=>`<li><a href="#record=${encodeURIComponent(a.record)}">${esc(a.name)}</a></li>`).join('')}</ul></details>`:''}`;
  if(zone.space==='floor') $('#atlas-coverage-detail').insertAdjacentHTML('afterbegin',`<p><strong>${esc(zone.era)}</strong> · Exiles M+ route planner, with maps and positions attributed to Keystone.guru. These are reference positions, not confirmed Ascension spawns. <a href="#record=${encodeURIComponent(zone.source_record)}">Open floor source record →</a></p>`);
  if(!zone.count){
    for(const selector of ['#atlas-filter','#atlas-selected','.atlas-list-heading','#atlas-list','.atlas-list-pager','.atlas-layer-bar'])$(selector).hidden=true;
    svg.setAttribute('tabindex',zone.space==='floor'?'0':'-1');svg.setAttribute('aria-label',zone.space==='floor'?'Instance floor reference map. Arrow keys pan, plus and minus zoom, Home resets the view.':'Map space with no preserved coordinate observations');
    $('.atlas-sidebar').insertAdjacentHTML('beforeend',`<section class="atlas-empty-card"><p class="eyebrow">PRESERVED MAP</p><h3>${esc(zone.map_name || zone.name)}</h3><p>${zone.space==='floor'?'This floor map is available, but its source provides no reference markers.':'No coordinate observations have been preserved for this map space yet. Its identity and available source records remain part of the atlas.'}</p>${(worldInfo?.records||[]).map(k=>`<p><a href="#record=${encodeURIComponent(k)}">Open map source record →</a></p>`).join('')}${worldInfo?.areas?.length?`<h4>Named areas</h4><ul>${worldInfo.areas.map(a=>`<li>${a.record?`<a href="#record=${encodeURIComponent(a.record)}">${esc(a.name)}</a>`:esc(a.name)}</li>`).join('')}</ul>`:''}</section>`);
  }
  const img=$('#atlas-image');
  function gridFallback() {if(disposed||job!==epoch)return;mapArt=false;img.removeAttribute('href');$('#atlas-space-label').textContent=zone.space==='floor'?'Floor coordinate grid · reference artwork unavailable':zone.space==='inventory'?'Map inventory · no locations preserved':zone.space==='world'?'Area-local schematic · reported raw coordinates':['reported','world'].includes(zone.space)?'Reported coordinates · unverified space':'Coordinate map · artwork unavailable';$('#atlas-art-credit').textContent='Captured bounds / reported coordinates';}
  const pairedFloorImage=zone.space==='floor'&&/^https:\/\/mplus\.exil\.es\/assets\/maps\/[a-z0-9_]+\.webp$/.test(zone.image);
  if(zone.image && (pairedFloorImage || /^https:\/\/wow\.zamimg\.com\/images\/wow\/(classic|wotlk)\/maps\/enus\/original\/\d+\.jpg$/.test(zone.image))) {
    img.setAttribute('href',zone.image);img.addEventListener('error',gridFallback,{once:true});$('#atlas-space-label').textContent=pairedFloorImage?'Paired floor map · '+zone.era:'Zone percentages · reference artwork';$('#atlas-art-credit').innerHTML=`<a href="${esc(zone.image)}" target="_blank" rel="noopener noreferrer">${pairedFloorImage?'Map: Exiles M+ / Keystone.guru':'Map: Wowhead'}</a>`;
  } else gridFallback();
  if(['reported','world','inventory','floor'].includes(zone.space)) $('.atlas-compass').hidden=true;
  const axisX=v=>zone.extent?(zone.extent.left+(zone.extent.right-zone.extent.left)*v/100).toFixed(0):v;
  const axisY=v=>zone.extent?(zone.extent.top+(zone.extent.bottom-zone.extent.top)*v/100).toFixed(0):v;
  $('#atlas-grid-labels').innerHTML=[10,20,30,40,50,60,70,80,90].map(v=>`<text x="${v*10+4}" y="18" fill="#d2c4a5" font-size="12">${axisX(v)}</text><text x="5" y="${v*6.67-4}" fill="#d2c4a5" font-size="12">${axisY(v)}</text>`).join('');
  if(zone.space==='floor')$('#atlas-grid-labels').innerHTML='';
  const canNavigate=zone.count>0||zone.space==='floor';
  function draw() {
    const w=1000/zoom,h=667/zoom;cx=clamp(cx,w/2,1000-w/2);cy=clamp(cy,h/2,667-h/2);
    svg.setAttribute('viewBox',`${cx-w/2} ${cy-h/2} ${w} ${h}`);$('#atlas-zoom-level').textContent=zoom.toFixed(1)+'×';$('#atlas-out').disabled=!canNavigate||zoom<=1;$('#atlas-in').disabled=!canNavigate||zoom>=12;$('#atlas-reset').disabled=!canNavigate;
    visible=filtered.filter(p=>p.plot_x*10>=cx-w/2&&p.plot_x*10<=cx+w/2&&p.plot_y*6.67>=cy-h/2&&p.plot_y*6.67<=cy+h/2);
    const unit=1000/Math.max(1,svg.getBoundingClientRect().width)/zoom;
    const groups=clusterAtlasPoints(visible,unit,matchMedia('(pointer:coarse)').matches?24:16);
    const fragment=document.createDocumentFragment();
    for(const ps of groups) {
      const p=ps[0],x=ps.reduce((s,p)=>s+p.plot_x*10,0)/ps.length,y=ps.reduce((s,p)=>s+p.plot_y*6.67,0)/ps.length,g=document.createElementNS(NS,'g');g.setAttribute('transform',`translate(${x} ${y})`);g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('class','atlas-marker');
      const multiple=ps.length>1,style=atlasMarkerStyle(p,visibleRole(p)),description=multiple?`${ps.length} locations. Show all.`:`${p.name}, ${style.label}, ${p.x.toFixed(1)}, ${p.y.toFixed(1)}`;
      g.setAttribute('aria-label',description);g.setAttribute('data-marker-key',ps.some(v=>v.key===clusterAnchor)?clusterAnchor:p.key);
      if(multiple||ps.some(v=>v.key===clusterAnchor)){g.setAttribute('aria-expanded',String(clusterMembers.length>0&&ps.some(v=>v.key===clusterAnchor)));g.setAttribute('aria-controls','atlas-cluster');}
      const title=document.createElementNS(NS,'title');title.textContent=description;g.append(title);
      const hit=document.createElementNS(NS,'circle');hit.setAttribute('r',12*unit);hit.setAttribute('fill','transparent');hit.setAttribute('class','atlas-hit');g.append(hit);
      if(multiple){
        const circle=document.createElementNS(NS,'circle');circle.setAttribute('r',10*unit);circle.setAttribute('fill','#172c3a');circle.setAttribute('stroke',ps.some(v=>v.key===selected)?'#fff':'#d9bd7e');circle.setAttribute('stroke-width',1.2*unit);g.append(circle);
        const t=document.createElementNS(NS,'text');t.setAttribute('text-anchor','middle');t.setAttribute('dy',3.5*unit);t.setAttribute('font-size',10*unit);t.setAttribute('fill','#f5dfae');t.textContent=ps.length;g.append(t);
      }else{
        const art=document.createElementNS(NS,'svg');art.setAttribute('x',-7*unit);art.setAttribute('y',-7*unit);art.setAttribute('width',14*unit);art.setAttribute('height',14*unit);art.setAttribute('viewBox','-8 -8 16 16');art.setAttribute('fill',style.color);art.setAttribute('stroke','#14202a');art.setAttribute('stroke-width','1.3');art.setAttribute('stroke-linecap','round');art.setAttribute('stroke-linejoin','round');art.innerHTML=ICONS[style.icon];art.setAttribute('aria-hidden','true');g.append(art);
        if(p.key===selected){const ring=document.createElementNS(NS,'circle');ring.setAttribute('r',9*unit);ring.setAttribute('fill','none');ring.setAttribute('stroke','#fff');ring.setAttribute('stroke-width',1.5*unit);g.append(ring);}
      }
      const activate=()=>{if(multiple)openCluster(ps);else{closeCluster();select(p);$('#atlas-selected h3').focus({preventScroll:true});}};
      g.addEventListener('click',activate);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activate();}});fragment.append(g);
    }
    $('#atlas-points').replaceChildren(fragment);$('#atlas-count').textContent=`${filtered.length.toLocaleString()} matching · ${visible.length.toLocaleString()} in view`;
    const empty=$('#atlas-empty-overlay');empty.hidden=visible.length>0||(zone.space==='floor'&&!zone.count&&mapArt);
    empty.textContent=!zone.count?'No mapped observations preserved for this space. Browse its map sources and named areas below.':!filtered.length?'No locations match these filters. Clear filters or enable more layers.':'No locations in this view. Reset the map to see this zone.';
  }
  function closeCluster(restoreFocus=false) {
    const anchor=clusterAnchor;clusterMembers=[];clusterAnchor='';$('#atlas-cluster').hidden=true;$('#atlas-cluster').replaceChildren();
    draw();
    if(restoreFocus)([...root.querySelectorAll('[data-marker-key]')].find(g=>g.dataset.markerKey===anchor)||svg).focus({preventScroll:true});
  }
  function openCluster(points) {
    clusterMembers=[...points].sort((a,b)=>a.name.localeCompare(b.name)||a.key.localeCompare(b.key));clusterAnchor=points[0].key;
    selected='';params.delete('record');$('#atlas-selected').hidden=true;
    renderCluster();draw();url();$('#atlas-cluster [data-overlap]')?.focus({preventScroll:true});
    if(matchMedia('(max-width:850px)').matches)$('#atlas-cluster').scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  function renderCluster() {
    const panel=$('#atlas-cluster');panel.hidden=!clusterMembers.length;if(!clusterMembers.length)return;
    panel.innerHTML=`<div class="atlas-cluster-heading"><strong>${clusterMembers.length} locations</strong><button type="button" id="atlas-close-cluster" class="quiet-button" aria-label="Close group">×</button></div><div class="atlas-overlaps">${clusterMembers.map(v=>`<button type="button" data-overlap="${esc(v.key)}" aria-pressed="${selected===v.key}">${iconMarkup(v,visibleRole(v))}<span><strong>${esc(v.name)}</strong>${clusterMembers.some(other=>other.key!==v.key&&other.name===v.name)?`<small>#${esc(v.id)} · ${esc(v.source)} · ${v.x.toFixed(2)}, ${v.y.toFixed(2)}</small>`:''}</span></button>`).join('')}</div>${zoom<12?'<button type="button" id="atlas-cluster-zoom" class="quiet-button">Zoom to group</button>':''}`;
    panel.querySelectorAll('[data-overlap]').forEach(b=>b.onclick=()=>select(clusterMembers.find(v=>v.key===b.dataset.overlap)));
    $('#atlas-close-cluster').onclick=()=>closeCluster(true);
    panel.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();closeCluster(true);}};
    if($('#atlas-cluster-zoom'))$('#atlas-cluster-zoom').onclick=()=>{cx=clusterMembers.reduce((sum,p)=>sum+p.plot_x*10,0)/clusterMembers.length;cy=clusterMembers.reduce((sum,p)=>sum+p.plot_y*6.67,0)/clusterMembers.length;zoom=Math.min(12,zoom*2);draw();renderCluster();url();$('#atlas-cluster [data-overlap]')?.focus({preventScroll:true});};
  }
  function select(p,center=false) {
    selected=p.key;params.set('record',p.record);
    if(center){cx=p.plot_x*10;cy=p.plot_y*6.67;zoom=Math.max(3,zoom);}
    const note=zone.space==='floor'?'Reference position':p.source==='LootCollector'?'Loot collected here':p.source==='Exiles DB'?'Reported location':'Recorded sighting';
    $('#atlas-selected').hidden=false;
    $('#atlas-selected').innerHTML=`<h3 tabindex="-1">${esc(p.name)}</h3><p class="atlas-coordinate">${p.x.toFixed(2)}, ${p.y.toFixed(2)} <small>${zone.space==='floor'?'pixels':['reported','world'].includes(zone.space)?'reported units':'%'}</small></p><p class="atlas-location-kind">${esc(note)} · ${esc(atlasMarkerStyle(p,visibleRole(p)).label)}</p><p class="atlas-record-action"><a href="#record=${encodeURIComponent(p.record)}">View record →</a></p><details><summary>Source details</summary><p>${esc(p.meta.meaning)}</p><dl><dt>Source</dt><dd>${esc(p.source)}</dd><dt>Game mode</dt><dd>${esc(p.mode)}</dd>${Object.entries(p.meta).filter(([k,v])=>k!=='meaning'&&v!==''&&v!==undefined).map(([k,v])=>`<dt>${esc(k.replaceAll('_',' '))}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${p.entity==='landmark'?'':`<p><a href="#search?q=${encodeURIComponent(p.id)}&kind=${encodeURIComponent(p.entity)}">Find ${esc(p.entity)} #${esc(p.id)} across sources →</a></p>`}</details>`;
    $('#atlas-cluster').querySelectorAll('[data-overlap]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.overlap===selected)));
    draw();url();
  }
  function renderList() {
    page=clamp(page,0,Math.max(0,Math.ceil(filtered.length/30)-1));const rows=filtered.slice(page*30,page*30+30);
    $('#atlas-list').innerHTML=rows.map(p=>`<button class="atlas-list-item layer-${visibleRole(p)}" data-point="${esc(p.key)}">${iconMarkup(p,visibleRole(p))}<span><strong>${esc(p.name)}</strong><small>${esc(atlasMarkerStyle(p,visibleRole(p)).label)}</small></span><span class="atlas-list-coordinate">${p.x.toFixed(1)}<br>${p.y.toFixed(1)}</span></button>`).join('')||'<p class="muted atlas-no-results">No matching locations in this map space.</p>';
    $('#atlas-list').querySelectorAll('[data-point]').forEach(b=>b.onclick=()=>{closeCluster();select(observations.find(p=>p.key===b.dataset.point),true);});
    $('#atlas-page').textContent=`${page+1} / ${Math.max(1,Math.ceil(filtered.length/30))}`;$('#atlas-prev').disabled=page===0;$('#atlas-next').disabled=(page+1)*30>=filtered.length;
  }
  function filter() {
    const q=normalize(query.trim()),choices=AscensionSearchAliases.variants(q);filtered=observations.filter(p=>atlasDisplayRoles(p).some(k=>enabled.has(k))&&(!source||p.source===source)&&(!mode||p.mode.split(',').map(x=>x.trim()).includes(mode))&&(!origin||p.meta.origin===origin)&&(!q||(/^-?\d+$/.test(q)?p.id===q:AscensionSearchAliases.matches(p.name,choices))));
    filtered.sort((a,b)=>listSort==='id'?a.id.localeCompare(b.id,'en',{numeric:true})||a.key.localeCompare(b.key):listSort==='source'?a.source.localeCompare(b.source)||a.name.localeCompare(b.name)||a.key.localeCompare(b.key):a.name.localeCompare(b.name)||a.key.localeCompare(b.key));
    if(selected&&!filtered.some(p=>p.key===selected)){selected='';$('#atlas-selected').hidden=true;}
    if(selected)select(filtered.find(p=>p.key===selected));
    if(clusterMembers.length)closeCluster();
    $('#atlas-filter-status').textContent=[source,mode,origin].filter(Boolean).length?` · ${[source,mode,origin].filter(Boolean).length} active`:listSort!=='name'?' · sorted':'';
    page=0;draw();renderList();url();
  }
  $('#atlas-filter').onsubmit=e=>{e.preventDefault();query=$('#atlas-query').value;source=$('#atlas-source').value;mode=$('#atlas-mode').value;origin=$('#atlas-origin').value;listSort=$('#atlas-order').value;params.delete('record');filter();};
  for(const id of ['atlas-source','atlas-mode','atlas-origin','atlas-order'])$('#'+id).onchange=()=>$('#atlas-filter').requestSubmit();
  $('#atlas-clear').onclick=()=>{query=source=mode=origin='';params.delete('record');$('#atlas-query').value='';listSort='name';$('#atlas-order').value='name';for(const id of ['atlas-source','atlas-mode','atlas-origin'])$('#'+id).value='';enabled=new Set(Object.keys(LAYERS));root.querySelectorAll('[data-layer]').forEach(x=>x.checked=true);filter();};
  root.querySelectorAll('[data-layer]').forEach(x=>x.onchange=()=>{x.checked?enabled.add(x.dataset.layer):enabled.delete(x.dataset.layer);params.delete('record');filter();});
  $('#atlas-prev').onclick=()=>{page--;renderList();};$('#atlas-next').onclick=()=>{page++;renderList();};
  function zoomBy(f){if(!canNavigate)return;zoom=clamp(zoom*f,1,12);draw();url();}
  $('#atlas-in').onclick=()=>zoomBy(1.5);$('#atlas-out').onclick=()=>zoomBy(1/1.5);$('#atlas-reset').onclick=()=>{zoom=1;cx=500;cy=333.5;draw();url();};
  svg.addEventListener('wheel',e=>{e.preventDefault();zoomBy(e.deltaY<0?1.15:1/1.15);},{passive:false});
  svg.addEventListener('keydown',e=>{if(e.target!==svg||!canNavigate)return;const delta=60/zoom;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Home'].includes(e.key))e.preventDefault();if(e.key==='ArrowLeft')cx-=delta;if(e.key==='ArrowRight')cx+=delta;if(e.key==='ArrowUp')cy-=delta;if(e.key==='ArrowDown')cy+=delta;if(e.key==='+'||e.key==='=')zoom=clamp(zoom*1.5,1,12);if(e.key==='-')zoom=clamp(zoom/1.5,1,12);if(e.key==='Home'){zoom=1;cx=500;cy=333.5;}draw();url();});
  let drag=null, moved=false;const pointers=new Map();let pinch=0;
  svg.addEventListener('pointerdown',e=>{if(e.target.closest('.atlas-marker')||!canNavigate)return;svg.setPointerCapture(e.pointerId);pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});drag={x:e.clientX,y:e.clientY,cx,cy};moved=false;if(pointers.size===2){const [a,b]=[...pointers.values()];pinch=Math.hypot(a.x-b.x,a.y-b.y);}});
  svg.addEventListener('pointermove',e=>{if(!pointers.has(e.pointerId))return;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});if(pointers.size===2){const[a,b]=[...pointers.values()],d=Math.hypot(a.x-b.x,a.y-b.y);if(pinch>0)zoom=clamp(zoom*d/pinch,1,12);pinch=d;draw();return;}if(!drag)return;const rect=svg.getBoundingClientRect();cx=drag.cx-(e.clientX-drag.x)*1000/rect.width/zoom;cy=drag.cy-(e.clientY-drag.y)*667/rect.height/zoom;moved=true;draw();});
  const endPointer=e=>{pointers.delete(e.pointerId);drag=null;pinch=0;url();};svg.addEventListener('pointerup',endPointer);svg.addEventListener('pointercancel',endPointer);
  $('#atlas-export').onclick=()=>{const blob=new Blob([JSON.stringify({snapshot:atlas.revision,zone,filters:{query,source,mode,origin,layers:[...enabled]},evidence_type:zone.space==='floor'?'route-reference':'preserved-observation',locations:filtered},null,2)],{type:'application/json'});const href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=`ascension-atlas-${zone.key}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);};
  const resize=new ResizeObserver(()=>{if(!disposed)draw();});resize.observe(svg);
  cleanup=()=>{disposed=true;resize.disconnect();};
  if(zone.file){if(!cache.has(zone.file))cache.set(zone.file,gz(zone.file).catch(e=>{cache.delete(zone.file);throw e;}));observations=await cache.get(zone.file);while(cache.size>8)cache.delete(cache.keys().next().value);}
  if(disposed||job!==epoch)return;
  const relevant=atlasRelevantRoles(observations);root.querySelectorAll('[data-layer]').forEach(input=>input.closest('label').hidden=!relevant.has(input.dataset.layer));
  const record=params.get('record');if(record){const found=observations.find(p=>p.record===record);if(found){const role=atlasDisplayRoles(found)[0];enabled.add(role);root.querySelector(`[data-layer="${role}"]`).checked=true;source=mode=origin=query='';$('#atlas-query').value='';for(const id of ['atlas-source','atlas-mode','atlas-origin'])$('#'+id).value='';filter();select(found,true);}else filter();}else filter();
  notice('');
}
const CONTINENT_NAMES={'0':'Eastern Kingdoms','1':'Kalimdor','530':'Outland','571':'Northrend'};
