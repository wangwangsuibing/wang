// Map initialization with AMap tiles (GCJ-02). All app data is WGS-84;
// convert with Coords.wgs2gcj before adding to map, gcj2wgs after map clicks.
const MapMod = (() => {
  const map = L.map('map', { center: Coords.wgs2gcj(31.2304, 121.4737), zoom: 13 });

  const amapNormal = L.tileLayer(
    'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    { subdomains: '1234', maxZoom: 18, attribution: '&copy; 高德地图' });
  const amapSat = L.tileLayer(
    'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
    { subdomains: '1234', maxZoom: 18, attribution: '&copy; 高德地图' });
  amapNormal.addTo(map);
  L.control.layers({ '标准图': amapNormal, '卫星图': amapSat }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  // layer groups
  const layers = {
    points: L.layerGroup().addTo(map),
    paths: L.layerGroup().addTo(map),
    vehicles: L.layerGroup().addTo(map),
    geofences: L.layerGroup().addTo(map),
    replay: L.layerGroup().addTo(map),
    draw: L.layerGroup().addTo(map),
    gaps: L.layerGroup().addTo(map),
  };
  let heatLayer = null;

  function setHeat(data) {
    // data: [[lat,lng,intensity], ...] in WGS-84
    if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    if (!data) return;
    const gcj = data.map(d => [...Coords.wgs2gcj(d[0], d[1]), d[2]]);
    heatLayer = L.heatLayer(gcj, { radius: 22, blur: 18, maxZoom: 17 });
    heatLayer.addTo(map);
  }

  function ll(lat, lng) { return Coords.wgs2gcj(lat, lng); }

  return { map, layers, setHeat, ll };
})();
