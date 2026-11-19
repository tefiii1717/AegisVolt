/**
 * Capa Anti-Corrupción SÍNCRONA para el catálogo del sistema heredado (Strangler Fig).
 * Traduce el modelo del legado (abreviaturas, kW como texto, estados en español, coordenadas en
 * un string) al lenguaje ubicuo del nuevo core, sin que ese modelo "contamine" el esquema GraphQL.
 */

const STATUS = { libre: 'AVAILABLE', cargando: 'CHARGING', reservado: 'RESERVED', 'fuera de servicio': 'OUT_OF_SERVICE' };
const TYPES = { 'CCS-2': 'CCS2', T2: 'TYPE2', CHADEMO: 'CHADEMO' };
// Caja geográfica de Bogotá–Sabana usada para proyectar lat/lng al mapa ilustrado (0–100 %).
const BBOX = { minLat: 4.6, maxLat: 4.9, minLng: -74.17, maxLng: -74.0 };

const titleCase = (s) => s.toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());

export function projectToMap(lat, lng) {
  const x = ((lng - BBOX.minLng) / (BBOX.maxLng - BBOX.minLng)) * 100;
  const y = (1 - (lat - BBOX.minLat) / (BBOX.maxLat - BBOX.minLat)) * 100;
  const clamp = (v) => Math.max(4, Math.min(96, Math.round(v)));
  return { mapX: clamp(x), mapY: clamp(y) };
}

export function translateLegacyStation(est) {
  const [lat, lng] = String(est.coord ?? '').split(',').map(Number);
  const connectors = (est.conectores ?? []).map((c) => {
    const powerKw = Number.parseFloat(c.kw);
    return {
      id: `C${c.num}`,
      type: TYPES[c.tipo] ?? c.tipo,
      powerKw,
      tier: powerKw >= 150 ? 'ULTRA' : 'FAST',
      status: STATUS[String(c.estado ?? '').toLowerCase()] ?? 'UNKNOWN',
    };
  });
  return {
    id: est.cod,
    name: titleCase(est.nom),
    city: titleCase(est.mpio.replace(' D.C.', '')),
    address: est.dir,
    lat,
    lng,
    ...projectToMap(lat, lng),
    emoji: '🏚️',
    source: 'LEGACY_ACL',
    connectors,
    availableConnectors: connectors.filter((c) => c.status === 'AVAILABLE').length,
  };
}

export function translateCoreStation(s) {
  const connectors = (s.connectors ?? []).map((c) => ({
    id: c.connector_id,
    type: c.connector_type,
    powerKw: c.power_kw,
    tier: c.tier,
    status: c.status ?? 'UNKNOWN',
    sagaId: c.saga_id && c.saga_id !== '-' ? c.saga_id : null,
    lastPowerKw: c.last_power_kw ?? null,
    energyKwh: c.energy_kwh ?? null,
    socPercent: c.soc_percent >= 0 ? c.soc_percent : null,
    telemetryAt: c.telemetry_at ?? null,
  }));
  return {
    id: s.station_id,
    name: s.name,
    city: s.city,
    address: s.address,
    lat: s.lat,
    lng: s.lng,
    mapX: s.map_x,
    mapY: s.map_y,
    emoji: s.emoji,
    source: 'CORE',
    connectors,
    availableConnectors: connectors.filter((c) => c.status === 'AVAILABLE').length,
  };
}
