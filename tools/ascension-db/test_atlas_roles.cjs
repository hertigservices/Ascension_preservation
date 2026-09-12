const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
require('./web/search-aliases.js');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'web/atlas.js'),'utf8').replace("import './search-aliases.js';",'')).toString('base64'));
test('dual-role markers use the enabled role for labels and glyphs',async()=>{const {atlasVisibleRole}=await modulePromise;const p={layer:'vendors',roles:['vendors','quest-givers']};assert.equal(atlasVisibleRole(p,new Set(['quest-givers'])),'quest-givers');assert.equal(atlasVisibleRole(p,new Set(['vendors'])),'vendors');assert.equal(atlasVisibleRole(p,new Set(['vendors','quest-givers'])),'vendors');});

test('tracking icons distinguish confirmed resources from props and NPC names',async()=>{
 const {atlasMarkerStyle,atlasDisplayRoles}=await modulePromise;
 for(const name of ['Peacebloom','Silverleaf','Earthroot'])assert.equal(atlasMarkerStyle({layer:'objects',name}).icon,'leaf');
 assert.equal(atlasMarkerStyle({layer:'objects',name:'Copper Vein'}).icon,'pick');
 for(const name of ['Bag of Herbs','Herbalist',"Herbalist's Cane",'Crystalvein Mine - Keep Out!','Copper Vein RPG Prop'])assert.equal(atlasMarkerStyle({layer:'objects',name}).icon,'box');
 assert.equal(atlasMarkerStyle({layer:'npc-claims',name:'Peacebloom'}).icon,'person');
 assert.equal(atlasMarkerStyle({layer:'creatures',name:'Wolf'}).icon,'dot');
 assert.deepEqual(atlasDisplayRoles({layer:'objects',name:'Silverleaf'}),['herbs']);
});
test('relevant controls include secondary roles and resource types without changing source records',async()=>{
 const {atlasRelevantRoles,atlasVisibleRole}=await modulePromise;
 const p={layer:'vendors',roles:['vendors','quest-givers']},herb={layer:'objects',name:'Silverleaf'};
 assert.deepEqual([...atlasRelevantRoles([p,herb])],['vendors','quest-givers','herbs']);
 assert.equal(atlasVisibleRole(p,new Set(['quest-givers'])),'quest-givers');assert.equal(herb.layer,'objects');
});
test('nearby points cluster across bucket boundaries; distant points separate as zoom increases',async()=>{
 const {clusterAtlasPoints}=await modulePromise;
 const a={key:'a',plot_x:1.5,plot_y:1},b={key:'b',plot_x:1.7,plot_y:1},c={key:'c',plot_x:5,plot_y:1};
 assert.deepEqual(clusterAtlasPoints([c,b,a],1).map(ps=>ps.map(p=>p.key)),[['a','b'],['c']]);
 assert.equal(clusterAtlasPoints([a,b],.1).length,2);
 assert.deepEqual(clusterAtlasPoints([a,b,c],1),clusterAtlasPoints([c,b,a],1));
});
test('exact overlaps retain every source identity at maximum zoom',async()=>{
 const {clusterAtlasPoints}=await modulePromise;
 const ps=['Josetta','Pestle','Wefhellt'].map((name,i)=>({key:String(i),name,plot_x:43,plot_y:66}));
 const groups=clusterAtlasPoints(ps,1/12);assert.equal(groups.length,1);assert.equal(groups[0].length,3);assert.deepEqual(groups[0],ps);
});

test('atlas shorthand is case and punctuation tolerant, with precise wing and numeric matching',()=>{
 const {variants,matches}=globalThis.AscensionSearchAliases;
 for(const q of ['BWL','b.w.l.','b w l'])assert(matches('Blackwing Lair — Floor 1',variants(q)));
 assert(matches('The Deadmines',variants('DM')));assert(matches('Dire Maul North',variants('dm')));
 assert(matches('Scarlet Monastery Library',variants('SM lib')));assert(!matches('Scarlet Monastery Armory',variants('SM lib')));
 assert(matches('Blackwing Lair Floor 4',variants('bwl floor 4')));assert(!matches('Blackwing Lair Floor 1',variants('bwl floor 4')));
 assert(!matches('Blackwing Lair',variants('bwlxyz')));assert(matches('469',variants('469')));assert(!matches('4690',variants('469')));
 assert(matches('Conquest of Azeroth',variants('CoA')));
});
