// Mapa ilustrado de la Sabana — estructura y pines adaptados de Uiverse.io por SnyDeTreves (white-newt-65), MIT.

function dominantStatus(station) {
  const statuses = station.connectors.map((c) => c.status);
  if (station.availableConnectors > 0) return 'AVAILABLE';
  if (statuses.includes('CHARGING')) return 'CHARGING';
  if (statuses.includes('RESERVED')) return 'RESERVED';
  return 'OUT_OF_SERVICE';
}

export default function StationMap({ stations, selectedId, onSelect }) {
  return (
    <div className="av-map" role="group" aria-label="Mapa de electrolineras en Bogotá y la Sabana">
      <svg viewBox="0 0 500 500" className="av-map__bg" aria-hidden="true">
        <rect width="500" height="500" rx="28" fill="#eef5e6" />
        {/* cerros orientales */}
        <path d="M500 0 L500 500 L430 500 C410 420 450 360 420 300 C395 245 440 190 410 120 C392 75 420 30 440 0Z" fill="#cfe2c4" />
        {/* río Bogotá */}
        <path d="M60 0 C90 80 40 140 70 210 C100 280 60 330 40 400 C30 440 20 470 10 500" fill="none" stroke="#90daee" strokeWidth="14" strokeLinecap="round" />
        {/* vías principales */}
        <path d="M0 230 C120 220 260 215 500 200" stroke="#fff" strokeWidth="9" fill="none" />
        <path d="M290 0 C300 120 300 300 320 500" stroke="#fff" strokeWidth="9" fill="none" />
        <path d="M0 330 C140 320 220 310 330 300" stroke="#fff" strokeWidth="6" fill="none" />
        <text x="300" y="250" fontSize="16" fill="#7e9a86" fontFamily="Nunito Sans Variable">Bogotá</text>
        <text x="270" y="60" fontSize="14" fill="#7e9a86" fontFamily="Nunito Sans Variable">Chía</text>
        <text x="60" y="160" fontSize="14" fill="#7e9a86" fontFamily="Nunito Sans Variable">Cota</text>
      </svg>
      {stations.map((s) => {
        const status = dominantStatus(s);
        return (
          <button
            key={s.id}
            type="button"
            className={`av-map__pin status-${status}`}
            style={{ '--x': s.mapX, '--y': s.mapY }}
            aria-pressed={selectedId === s.id}
            aria-label={`${s.name}: ${s.availableConnectors} de ${s.connectors.length} conectores libres`}
            onClick={() => onSelect?.(s.id)}
          >
            <span className="av-map__label">
              <span className="av-map__sign" data-icon={s.emoji ?? '⚡'}>
                {s.name}: {s.availableConnectors}/{s.connectors.length} libres
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
