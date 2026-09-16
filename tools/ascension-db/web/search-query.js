/* Shared exact search/column-filter semantics for browsing and SQL export. */
(() => {
const words = s => AscensionSearchAliases.tokens(s);
function candidates(d) {
  const m=d.manifest,q=String(d.q || '').trim(),identifier=String(d.recordId || '').trim(),numeric=/^-?\d+$/.test(q);
  const queryVariants=numeric?[[]]:AscensionSearchAliases.variants(q).map(ts=>ts.filter(t=>t.length>=2||/^\d+$/.test(t)));
  const nameVariants=AscensionSearchAliases.variants(d.name || '').map(ts=>ts.filter(t=>t.length>=2||/^\d+$/.test(t)));
  const alternatives=queryVariants.flatMap(query=>nameVariants.map(name=>[...query,...name]));
  let keys;
  if(identifier)keys=['id:'+identifier.slice(0,2)];
  else if(numeric)keys=['id:'+q.slice(0,2)];
  else if(alternatives.some(ts=>ts.some(t=>t.length>=2)))keys=alternatives.filter(ts=>ts.some(t=>t.length>=2)).map(ts=>ts.filter(t=>t.length>=2).map(t=>'name:'+t.slice(0,2)).sort((a,b)=>(m.search[a]?.count||0)-(m.search[b]?.count||0))[0]);
  else keys=d.kind?['browse:'+d.kind]:Object.keys(m.search).filter(k=>k.startsWith('browse:'));
  keys=[...new Set(keys)];
  const parts=[...new Set(keys.flatMap(k=>m.search[k]?.parts||[]))],partsByKey=new Map(keys.map(k=>[k,new Set(m.search[k]?.parts||[])]));
  const match=(r,part)=>{
    if((d.kind&&r[2]!==d.kind)||(d.source&&r[4]!==d.source)||(d.mode&&!r[3].split(/[,|]/).map(s=>s.trim()).includes(d.mode))||(identifier&&r[5]!==identifier)||(numeric&&r[5]!==q))return false;
    const tokens=words(r[1]);
    if(!alternatives.some(ts=>ts.every(t=>tokens.some(w=>/^\d+$/.test(t)?w===t:w.startsWith(t)))))return false;
    // An alias may read several name buckets. Assign each row one owning bucket,
    // preserving distinct record keys without an unbounded deduplication cache.
    if(keys.length>1){const owner=keys.find(k=>k.startsWith('browse:')?k==='browse:'+r[2]:k.startsWith('id:')?r[5].startsWith(k.slice(3)):tokens.some(t=>t.startsWith(k.slice(5))));if(!partsByKey.get(owner)?.has(part))return false;}
    return true;
  };
  return {parts,match};
}
function validate(d) {
  // Zone UI is being developed separately. Refuse it until its predicate is wired
  // here, so no SQL export can silently omit a newly introduced zone constraint.
  if (d.zone || d.filters?.zone) throw Error('Zone filtering is not yet available in this converter version.');
  const q = String(d.q || '').trim();
  if (q && !/^-?\d+$/.test(q) && !AscensionSearchAliases.variants(q).some(ts => ts.some(t => t.length >= 2))) throw Error('Enter at least two letters, or an exact numeric ID.');
  if (String(d.name || '').trim() && !AscensionSearchAliases.variants(d.name).some(ts => ts.some(t => t.length >= 2))) throw Error('Enter at least two letters in the Name column filter.');
}
globalThis.AscensionSearchQuery = Object.freeze({candidates, validate});
})();
