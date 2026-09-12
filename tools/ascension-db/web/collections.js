const labels = {
  Auctionator: 'Auction price observations', AIO_Client: 'Addon code variants',
  'coa-client-event': 'CoA client events', advancement: 'Character advancement',
  apiSurface: 'Client API references', battleground: 'Battlegrounds', byRealm: 'Realm reference data',
  dungeon: 'Dungeons', enum: 'Client enumerations', 'gossip-observation': 'Observed NPC dialogue',
  observed: 'Harvest observations', roleRequirement: 'Role requirements', skillCard: 'Skill cards',
  vendor: 'Vendor inventories', 'loot-pin': 'Loot locations', 'world-creature': 'Creature sightings',
  'world-object': 'World object sightings', 'display-icon': 'Item display icons',
  dbc: 'Client data tables', map: 'Maps', area: 'Areas', 'planner-item': 'Planner items',
  source: 'Source records', page: 'Page texts', 'item-name': 'Item names', gossip: 'NPC dialogue',
};
export function collectionLabel(key, entry) {
  return labels[key] || entry.label || key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[-_]/g, ' ');
}
export function collectionDirectory(kinds, escapeHtml) {
  return Object.entries(kinds).sort((a, b) => collectionLabel(a[0], a[1]).localeCompare(collectionLabel(b[0], b[1])))
    .map(([key, entry]) => `<a href="#search?kind=${escapeHtml(encodeURIComponent(key))}"><span>${escapeHtml(collectionLabel(key, entry))}</span><small>${escapeHtml(Number(entry.records).toLocaleString())} source records</small></a>`).join('');
}
