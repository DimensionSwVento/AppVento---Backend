// Geometría del viewport: imita lo que el mapa del frontend le pide al backend.
// Espejo de AppVento/Vento/src/app/Utils/h3/mapGeo.ts (zoomToH3Res) y
// h3Resolution.ts (INITIAL_VIEW_STATE).

export const INITIAL_VIEW = { lng: -73.5, lat: 4.5, zoom: 6 };

// Pantalla típica de escritorio sobre la que se calcula el bbox visible.
const VIEW_W = 1440;
const VIEW_H = 900;

// Zona continental de Colombia (donde un usuario realmente explora).
const LNG = [-77.5, -67.5];
const LAT = [-4.0, 12.0];

export function zoomToH3Res(zoom) {
  if (zoom >= 12) return 8;
  if (zoom >= 10) return 7;
  if (zoom >= 8) return 6;
  if (zoom >= 7) return 5;
  if (zoom >= 6) return 4;
  return 3;
}

/** bbox visible para un centro y zoom (Web Mercator, aproximado al viewport). */
export function viewportBBox(lng, lat, zoom) {
  const degPerPx = 360 / (256 * Math.pow(2, zoom));
  const halfLng = (VIEW_W / 2) * degPerPx;
  const halfLat = (VIEW_H / 2) * degPerPx * Math.cos((lat * Math.PI) / 180);
  const r = (n) => Math.round(n * 1e4) / 1e4;
  return { west: r(lng - halfLng), south: r(lat - halfLat), east: r(lng + halfLng), north: r(lat + halfLat) };
}

const rand = (a, b) => a + Math.random() * (b - a);

/** Punto aleatorio sobre Colombia. */
export function randomCenter() {
  return { lng: rand(LNG[0], LNG[1]), lat: rand(LAT[0], LAT[1]) };
}

/** Paneo: desplaza el centro ~medio viewport en una dirección aleatoria. */
export function pan(view) {
  const b = viewportBBox(view.lng, view.lat, view.zoom);
  const dx = (b.east - b.west) * rand(-0.5, 0.5);
  const dy = (b.north - b.south) * rand(-0.5, 0.5);
  return {
    lng: Math.min(LNG[1], Math.max(LNG[0], view.lng + dx)),
    lat: Math.min(LAT[1], Math.max(LAT[0], view.lat + dy)),
    zoom: view.zoom,
  };
}
