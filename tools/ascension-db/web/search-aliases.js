/* Shared by the atlas module and classic search worker. Display names stay unchanged.
 * Common player terminology: https://www.wowhead.com/classic/guide/classic-wow-glossary-terminology
 * Ambiguous DM usage: https://us.forums.blizzard.com/en/wow/t/blizzard-which-is-official-dm-or-vc/298950
 * Aliases aid retrieval; they do not equate map IDs, eras or source records.
 */
(() => {
 const aliases = Object.freeze(Object.assign(Object.create(null), {
  bwl:['blackwing lair','blackwinglair'], mc:['molten core','moltencore'], ony:['onyxia'], onyx:['onyxia'],
  naxx:['naxxramas'], aq:['ahn qiraj','ahnqiraj'], aq20:['ruins of ahn qiraj','ruins of ahnqiraj'], aq40:['temple of ahn qiraj','temple of ahnqiraj'],
  zg:["zul'gurub",'zulgurub'], za:["zul'aman",'zulaman'],
  rfc:['ragefire chasm'], wc:['wailing caverns'], dm:['deadmines','dire maul'], vc:['deadmines'],
  sfk:['shadowfang keep'], bfd:['blackfathom deeps'], stocks:['stockade'], stockades:['stockade'],
  gnomer:['gnomeregan'], rfk:['razorfen kraul'], rfd:['razorfen downs'],
  sm:['scarlet monastery'], smgy:['scarlet monastery graveyard'], smlib:['scarlet monastery library'],
  smarm:['scarlet monastery armory'], smarms:['scarlet monastery armory'], smcath:['scarlet monastery cathedral'],
  ulda:['uldaman'], zf:["zul'farrak",'zulfarrak'], mara:['maraudon'], st:['sunken temple','temple of atal hakkar'],
  brd:['blackrock depths'], brs:['blackrock spire'], lbrs:['lower blackrock spire'], ubrs:['upper blackrock spire'],
  strat:['stratholme'], scholo:['scholomance'], dme:['dire maul east'], dmn:['dire maul north'], dmw:['dire maul west'],
  ramps:['hellfire ramparts'], ramp:['hellfire ramparts'], bf:['blood furnace'], sp:['slave pens'], ub:['underbog'],
  mt:['mana tombs'], ac:['auchenai crypts'], sh:['sethekk halls','shattered halls'], shh:['shattered halls'],
  sl:['shadow labyrinth'], slabs:['shadow labyrinth'], sv:['steamvault'], bot:['botanica'], mech:['mechanar'], arc:['arcatraz'],
  ohb:['old hillsbrad'], bm:['black morass'], kara:['karazhan'], gruul:['gruul'], mag:['magtheridon'],
  ssc:['serpentshrine cavern'], tk:['tempest keep','the eye'], mh:['mount hyjal','hyjal summit'], bt:['black temple'], swp:['sunwell plateau'],
  uk:['utgarde keep'], up:['utgarde pinnacle'], nexus:['nexus'], occ:['oculus'], an:['azjol nerub'], ak:['ahn kahet','ahnkahet'],
  dt:['drak tharon'], gundrak:['gundrak'], hos:['halls of stone'], hol:['halls of lightning'], cos:['culling of stratholme'],
  vh:['violet hold'], toc:['trial of the champion','trial of the crusader'], togc:['trial of the grand crusader','trial of the crusader'],
  fos:['forge of souls'], pos:['pit of saron'], hor:['halls of reflection'], os:['obsidian sanctum'], eoe:['eye of eternity'],
  voa:['vault of archavon'], uld:['ulduar'], icc:['icecrown citadel'], rs:['ruby sanctum'],
  sw:['stormwind'], if:['ironforge'], org:['orgrimmar'], tb:['thunder bluff'], uc:['undercity'], darn:['darnassus'], dal:['dalaran'],
  ek:['eastern kingdoms'], stv:['stranglethorn'], wpl:['western plaguelands'], epl:['eastern plaguelands'],
  hfp:['hellfire peninsula'], smv:['shadowmoon valley'], hf:['howling fjord'], borean:['borean tundra'],
  coa:['conquest of azeroth']
 }));
 const tokens = text => String(text ?? '').replace(/\b(?:[a-z]\.){2,}[a-z]?\.?/gi,word=>word.replaceAll('.','')).normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().replaceAll('ß','ss').match(/[\p{L}\p{N}]+/gu) || [];
 const compact = text => tokens(text).join('');
 function variants(text) {
  const literal=tokens(text);if(!literal.length)return [[]];
  const whole=aliases[compact(text)];
  if(whole)return [literal.every(t=>t.length===1)?[literal.join('')]:literal,...whole.map(tokens)];
  // Replace one recognized, complete token/phrase. Never expand word prefixes or recurse.
  for(let width=Math.min(3,literal.length);width>=1;width--)for(let start=0;start<=literal.length-width;start++) {
   const key=literal.slice(start,start+width).join(''),targets=aliases[key];
   if(targets)return [literal.slice(start,start+width).every(t=>t.length===1)?[...literal.slice(0,start),key,...literal.slice(start+width)]:literal,...targets.map(target=>[...literal.slice(0,start),...tokens(target),...literal.slice(start+width)])];
  }
  return [literal];
 }
 function matches(text, choices) {
  const target=tokens(text);
  return choices.some(terms=>terms.every(term=>target.some(word=>/^\d+$/.test(term)?word===term:word.startsWith(term))));
 }
 globalThis.AscensionSearchAliases=Object.freeze({variants,matches,tokens});
})();
