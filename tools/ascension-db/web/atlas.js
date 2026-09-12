const LAYERS = {
  'route-bosses': ['Instance reference bosses', '★', '#e8cf71'],
  'route-npcs': ['Instance reference NPCs', '◇', '#80bad7'],
  creatures: ['Creature sightings', '●', '#80bad7'],
  objects: ['World objects', '◆', '#e2bc69'],
  vendors: ['Vendors', '$', '#dfc582'],
  'quest-givers': ['Quest givers', '!', '#e8cf71'],
  'npc-claims': ['Other NPC location claims', '△', '#bd9fdd'],
  worldforged: ['Worldforged loot', '✦', '#e9a26d'],
  mystic: ['Mystic scroll loot', '✧', '#80d1b2'],
  'other-loot': ['Other loot', '■', '#aebdca'],
};
export function atlasVisibleRole(point, enabled) { return enabled.has(point.layer) ? point.layer : (point.roles || [point.layer]).find(role=>enabled.has(role)) || point.layer; }
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
  let selected='', page=0, listSort=params.get('order') || 'name', enabled = new Set(params.has('layers') ? params.get('layers').split(',').filter(k=>LAYERS[k]) : Object.keys(LAYERS));
  let zoom = clamp(finite(params.get('zoom'),1),1,12), cx=clamp(finite(params.get('cx'),500),0,1000), cy=clamp(finite(params.get('cy'),333.5),0,667);
  let observations=[], filtered=[], visible=[], disposed=false, mapArt=true, source=params.get('source') || '', mode=params.get('mode') || '', origin=params.get('origin') || '', query=params.get('q') || '';
  const $=s=>root.querySelector(s);
  const visibleRole=p=>atlasVisibleRole(p,enabled);
  const option=(v,t,chosen)=>`<option value="${esc(v)}" ${v===chosen?'selected':''}>${esc(t)}</option>`;
  const zoneTitle=z=> `${z.name}${z.space==='floor'?' · '+z.era:z.space==='world'?' · raw coordinates':z.space==='reported'?' · reported coordinates':z.space==='inventory'?' · map inventory':''}${z.wma ? ' · map space '+z.wma : z.map ? ' · map '+z.map : ''}`;
  root.innerHTML=`<section class="atlas">${manifest.sample?'<div class="banner">Development preview · limited record sample. The complete catalog is still building.</div>':''}<div class="result-head"><div><p class="eyebrow">THE PRESERVED WORLD</p><h2>World Atlas</h2></div><p class="muted">${(atlas.preserved_observations ?? atlas.observations).toLocaleString()} preserved observations${atlas.reference_locations ? ` · ${atlas.reference_locations.toLocaleString()} instance references` : ""}</p></div><p class="atlas-intro">Explore original and custom worlds. Every displayed marker leads to its preserved evidence.</p><div class="atlas-picker"><label>Find a world or zone<input id="atlas-zone-query" type="search" placeholder="Search names, aliases or map IDs…"></label><label>World<select id="atlas-world"><option value="">All worlds</option>${(atlas.maps || []).map(m=>option(m.id,`${m.name}${m.alternate_names?.length ? " / "+m.alternate_names.join(" / ") : ""} · map ${m.id}`,zone.map)).join('')}</select></label><label class="atlas-zone-select">Zone / map space<span class="atlas-zone-open"><select id="atlas-zone"></select><button type="button" id="atlas-open-zone">Open</button></span></label></div><div id="atlas-zone-summary" class="atlas-zone-summary"></div><nav id="atlas-floor-nav" class="atlas-floor-nav" aria-label="Instance floors and evidence views"></nav><div class="atlas-workbench"><div class="atlas-main"><div class="atlas-map-heading"><div><h3 id="atlas-zone-title"></h3><span id="atlas-space-label" class="muted"></span></div><div class="atlas-zoom"><button id="atlas-out" aria-label="Zoom out">−</button><output id="atlas-zoom-level">1×</output><button id="atlas-in" aria-label="Zoom in">+</button><button id="atlas-reset">Reset view</button></div></div><div class="atlas-canvas"><svg id="atlas-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 667" role="group" tabindex="0" aria-label="Interactive zone map. Arrow keys pan, plus and minus zoom, Home resets the view."><defs><pattern id="atlas-grid" width="100" height="66.7" patternUnits="userSpaceOnUse"><path d="M100 0H0V66.7" fill="none" stroke="#c4ad74" stroke-opacity=".18" stroke-width="1"/></pattern></defs><rect width="1000" height="667" fill="#16252d"/><image id="atlas-image" x="0" y="0" width="1000" height="667" preserveAspectRatio="none"/><rect width="1000" height="667" fill="url(#atlas-grid)"/><g id="atlas-grid-labels"></g><g id="atlas-points"></g></svg><div class="atlas-compass" aria-hidden="true">N<br>↑</div><div id="atlas-empty-overlay" class="atlas-empty-overlay" hidden></div></div><div class="atlas-map-foot"><span>Drag to pan · scroll or + / − to zoom · select a marker</span><span id="atlas-art-credit"></span></div><div class="atlas-layer-bar" role="group" aria-label="Map layers">${Object.entries(LAYERS).map(([k,[name,glyph]])=>`<label class="layer-${k}"><input type="checkbox" data-layer="${k}" ${enabled.has(k)?'checked':''}><span aria-hidden="true">${glyph}</span>${name}</label>`).join('')}</div><div class="atlas-evidence-note"><strong>What these markers mean</strong><p>Instance reference markers use the route planner’s own floor images and positions; they are not confirmed Ascension spawns. Objects and creatures show example sightings. Exiles NPC markers preserve website claims, including explicitly recorded vendor and quest roles. Loot markers show the player’s position when the loot window opened, not a confirmed drop source. Missing markers do not mean an empty zone.</p></div><details class="atlas-coverage"><summary>Map sources & coverage</summary><div id="atlas-coverage-detail"></div><p>${Object.entries(atlas.unmapped).map(([k,v])=>`${esc(k)}: ${v.toLocaleString()}`).join('<br>')}</p><p>Artwork is a reference backdrop. Custom geography may differ; coordinate-only views remain available. Captured map bounds and historical records are preserved separately.</p></details></div><aside class="atlas-sidebar" aria-label="${zone.space==='floor'?'Reference markers on this floor':'Observations in this zone'}"><form id="atlas-filter"><label>${zone.space==='floor'?'Find a reference marker':'Find an observation'}<input id="atlas-query" type="search" placeholder="Name or exact ID" value="${esc(query)}"></label><div class="atlas-filter-pair"><label>Source<select id="atlas-source">${option('','All sources',source)}${atlas.sources.map(x=>option(x,x,source)).join('')}</select></label><label>Game mode<select id="atlas-mode">${option('','All modes',mode)}${atlas.modes.map(x=>option(x,x,mode)).join('')}</select></label></div><div class="atlas-filter-pair"><label>Origin<select id="atlas-origin">${option('','All / unspecified',origin)}${option('ascension','Ascension additions',origin)}${option('stock','Stock entries',origin)}</select></label><label>Sort list<select id="atlas-order">${option('name','Name A–Z',listSort)}${option('id','ID ascending',listSort)}${option('source','Source A–Z',listSort)}</select></label></div><div class="atlas-filter-actions"><button type="submit">Apply filters</button><button id="atlas-clear" type="button" class="quiet-button">Clear</button></div></form><div id="atlas-selected" class="atlas-selected" aria-live="polite"><p>Select a marker below to inspect its source evidence.</p></div><div class="atlas-list-heading"><strong id="atlas-count" role="status"></strong><button id="atlas-export" class="quiet-button" type="button">Export</button></div><div id="atlas-list" class="atlas-list"></div><div class="atlas-list-pager"><button id="atlas-prev" class="quiet-button">Previous</button><span id="atlas-page"></span><button id="atlas-next" class="quiet-button">Next</button></div></aside></div></section>`;
  const svg=$('#atlas-svg'), NS='http://www.w3.org/2000/svg';
  function url() {
    const p=new URLSearchParams({zone:zone.key});
    for(const [k,v] of Object.entries({q:query,source,mode,origin,order:listSort,record:params.get('record')}))if(v)p.set(k,v);
    if(enabled.size!==Object.keys(LAYERS).length)p.set('layers',[...enabled].join(','));
    if(zoom!==1){p.set('zoom',zoom.toFixed(3));p.set('cx',cx.toFixed(2));p.set('cy',cy.toFixed(2));}
    history.replaceState(null,'','#atlas?'+p);
  }
  function populateZones() {
    const q=normalize($('#atlas-zone-query').value),world=$('#atlas-world').value;
    const relatedWorlds=new Set((atlas.maps||[]).filter(m=>q&&normalize([m.name,...(m.alternate_names||[]),...(m.areas||[]).map(a=>a.name)].join(' ')).includes(q)).map(m=>m.id));
    const items=zones.filter(z=>(!world||z.map===world)&&(!q||normalize(`${z.name} ${z.aliases.join(' ')} ${z.map} ${z.wma} ${z.map_name || ''}`).includes(q)||relatedWorlds.has(z.map)));
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
    img.setAttribute('href',zone.image);img.addEventListener('error',gridFallback,{once:true});$('#atlas-space-label').textContent=pairedFloorImage?'Paired floor map · '+zone.era:'Zone percentages · reference artwork';$('#atlas-art-credit').innerHTML=`<a href="${esc(zone.image)}" target="_blank" rel="noopener noreferrer">${pairedFloorImage?'Map: Exiles M+ / Keystone.guru':'Reference map hosted by Wowhead'}</a>`;
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
    const groups=new Map(),cell=30*unit;
    for(const p of visible){const key=Math.floor(p.plot_x*10/cell)+','+Math.floor(p.plot_y*6.67/cell);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p);}
    const fragment=document.createDocumentFragment();
    for(const ps of groups.values()) {
      const p=ps[0],x=ps.reduce((s,p)=>s+p.plot_x*10,0)/ps.length,y=ps.reduce((s,p)=>s+p.plot_y*6.67,0)/ps.length,g=document.createElementNS(NS,'g');g.setAttribute('transform',`translate(${x} ${y})`);g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('class','atlas-marker');
      const multiple=ps.length>1,description=multiple?`${ps.length} locations. Zoom to inspect.`:`${p.name}, ${LAYERS[visibleRole(p)]?.[0] || visibleRole(p)}, ${p.x.toFixed(1)}, ${p.y.toFixed(1)}`;
      g.setAttribute('aria-label',description);
      const title=document.createElementNS(NS,'title');title.textContent=description;g.append(title);
      const hit=document.createElementNS(NS,'circle');hit.setAttribute('r',15*unit);hit.setAttribute('fill','transparent');g.append(hit);
      const circle=document.createElementNS(NS,'circle');circle.setAttribute('r',(multiple?14:7)*unit);circle.setAttribute('fill',multiple?'#233d4c':LAYERS[visibleRole(p)]?.[2]||'#e2bc69');circle.setAttribute('stroke',ps.some(v=>v.key===selected)?'#fff':'#e5c684');circle.setAttribute('stroke-width',(ps.some(v=>v.key===selected)?3:1.5)*unit);g.append(circle);
      if(multiple){const t=document.createElementNS(NS,'text');t.setAttribute('text-anchor','middle');t.setAttribute('dy',4*unit);t.setAttribute('font-size',11*unit);t.setAttribute('fill','#f5dfae');t.textContent=ps.length;g.append(t);}
      else if(visibleRole(p)!=='creatures'){const t=document.createElementNS(NS,'text');t.setAttribute('text-anchor','middle');t.setAttribute('dy',4*unit);t.setAttribute('font-size',11*unit);t.setAttribute('fill','#14202a');t.textContent=LAYERS[visibleRole(p)]?.[1]||'◆';g.append(t);}
      const activate=()=>{if(multiple&&zoom<12){cx=x;cy=y;zoom=Math.min(12,zoom*2);draw();renderList();url();}else{select(p);if(multiple){$('#atlas-selected').insertAdjacentHTML('beforeend',`<p>${ps.length} locations overlap here.</p><div class="atlas-overlaps">${ps.map(v=>`<button type="button" data-overlap="${esc(v.key)}" class="quiet-button">${esc(v.name)}</button>`).join('')}</div>`);$('#atlas-selected').querySelectorAll('[data-overlap]').forEach(b=>b.onclick=()=>select(ps.find(v=>v.key===b.dataset.overlap)));}}};
      g.addEventListener('click',activate);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activate();}});fragment.append(g);
    }
    $('#atlas-points').replaceChildren(fragment);$('#atlas-count').textContent=`${filtered.length.toLocaleString()} matching · ${visible.length.toLocaleString()} in view`;
    const empty=$('#atlas-empty-overlay');empty.hidden=visible.length>0||(zone.space==='floor'&&!zone.count&&mapArt);
    empty.textContent=!zone.count?'No mapped observations preserved for this space. Browse its map sources and named areas below.':!filtered.length?'No locations match these filters. Clear filters or enable more layers.':'No locations in this view. Reset the map to see this zone.';
  }
  function select(p,center=false) {
    selected=p.key;
    if(center){cx=p.plot_x*10;cy=p.plot_y*6.67;zoom=Math.max(3,zoom);}
    $('#atlas-selected').innerHTML=`<p class="eyebrow">${esc(LAYERS[visibleRole(p)]?.[0]||visibleRole(p))}</p><h3>${esc(p.name)}</h3><p class="atlas-coordinate">${p.x.toFixed(2)}, ${p.y.toFixed(2)} <small>${zone.space==='floor'?'image pixels':['reported','world'].includes(zone.space)?'reported units':'zone %'}</small></p><p>${esc(p.meta.meaning)}</p><dl><dt>Source</dt><dd>${esc(p.source)}</dd><dt>Game mode</dt><dd>${esc(p.mode)}</dd>${Object.entries(p.meta).filter(([k,v])=>k!=='meaning'&&v!==''&&v!==undefined).map(([k,v])=>`<dt>${esc(k.replaceAll('_',' '))}</dt><dd>${esc(v)}</dd>`).join('')}</dl><p><a href="#record=${encodeURIComponent(p.record)}">Open preserved record →</a></p>${p.entity==='landmark'?'':`<p><a href="#search?q=${encodeURIComponent(p.id)}&kind=${encodeURIComponent(p.entity)}">Find ${esc(p.entity)} #${esc(p.id)} across sources →</a></p>`}`;
    draw();url();
  }
  function renderList() {
    page=clamp(page,0,Math.max(0,Math.ceil(filtered.length/30)-1));const rows=filtered.slice(page*30,page*30+30);
    $('#atlas-list').innerHTML=rows.map(p=>`<button class="atlas-list-item layer-${visibleRole(p)}" data-point="${esc(p.key)}"><span class="atlas-list-glyph" aria-hidden="true">${esc(LAYERS[visibleRole(p)]?.[1]||'●')}</span><span><strong>${esc(p.name)}</strong><small>${esc(LAYERS[visibleRole(p)]?.[0]||visibleRole(p))} · ${esc(p.source)}</small></span><span class="atlas-list-coordinate">${p.x.toFixed(1)}<br>${p.y.toFixed(1)}</span></button>`).join('')||'<p class="muted atlas-no-results">No matching locations in this map space.</p>';
    $('#atlas-list').querySelectorAll('[data-point]').forEach(b=>b.onclick=()=>select(observations.find(p=>p.key===b.dataset.point),true));
    $('#atlas-page').textContent=`${page+1} / ${Math.max(1,Math.ceil(filtered.length/30))}`;$('#atlas-prev').disabled=page===0;$('#atlas-next').disabled=(page+1)*30>=filtered.length;
  }
  function filter() {
    const q=normalize(query.trim());filtered=observations.filter(p=>(p.roles || [p.layer]).some(k=>enabled.has(k))&&(!source||p.source===source)&&(!mode||p.mode.split(',').map(x=>x.trim()).includes(mode))&&(!origin||p.meta.origin===origin)&&(!q||(/^-?\d+$/.test(q)?p.id===q:normalize(p.name).includes(q))));
    filtered.sort((a,b)=>listSort==='id'?a.id.localeCompare(b.id,'en',{numeric:true})||a.key.localeCompare(b.key):listSort==='source'?a.source.localeCompare(b.source)||a.name.localeCompare(b.name)||a.key.localeCompare(b.key):a.name.localeCompare(b.name)||a.key.localeCompare(b.key));
    if(selected&&!filtered.some(p=>p.key===selected)){selected='';$('#atlas-selected').innerHTML='<p>Select a marker to inspect its source evidence.</p>';}
    page=0;draw();renderList();url();
  }
  $('#atlas-filter').onsubmit=e=>{e.preventDefault();query=$('#atlas-query').value;source=$('#atlas-source').value;mode=$('#atlas-mode').value;origin=$('#atlas-origin').value;listSort=$('#atlas-order').value;params.delete('record');filter();};
  for(const id of ['atlas-source','atlas-mode','atlas-origin','atlas-order'])$('#'+id).onchange=()=>$('#atlas-filter').requestSubmit();
  $('#atlas-clear').onclick=()=>{query=source=mode=origin='';params.delete('record');$('#atlas-query').value='';for(const id of ['atlas-source','atlas-mode','atlas-origin'])$('#'+id).value='';enabled=new Set(Object.keys(LAYERS));root.querySelectorAll('[data-layer]').forEach(x=>x.checked=true);filter();};
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
  const record=params.get('record');if(record){const found=observations.find(p=>p.record===record);if(found){enabled.add(found.layer);root.querySelector(`[data-layer="${found.layer}"]`).checked=true;source=mode=origin=query='';$('#atlas-query').value='';for(const id of ['atlas-source','atlas-mode','atlas-origin'])$('#'+id).value='';filter();select(found,true);}else filter();}else filter();
  notice('');
}
const CONTINENT_NAMES={'0':'Eastern Kingdoms','1':'Kalimdor','530':'Outland','571':'Northrend'};
