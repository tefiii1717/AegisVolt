import { useState } from 'react';
import RoadHero from '../components/RoadHero.jsx';
import StationMap from '../components/StationMap.jsx';
import { BatteryLoader, CompensationAlert } from '../components/Notices.jsx';
import { usePoll } from '../lib/api.js';
import { getSession } from '../lib/auth.js';
import { CONNECTOR_STATUS } from '../lib/format.js';
import { STATIONS } from '../lib/queries.js';

function ConnectorRow({ station, c }) {
  return (
    <li className={`status-${c.status} flex flex-wrap items-center justify-between gap-3 py-3`}>
      <div className="flex items-center gap-3">
        <span className="font-display text-2xl font-black">{c.id}</span>
        <div>
          <p className="font-bold">{c.type} de {c.powerKw} kW {c.tier === 'ULTRA' && <span className="text-charge-deep">ultra-carga</span>}</p>
          {c.status === 'CHARGING' && c.lastPowerKw != null && (
            <p className="text-sm text-slate-600">
              Entregando {c.lastPowerKw.toFixed(1)} kW{c.energyKwh != null && `, ${c.energyKwh.toFixed(1)} kWh acumulados`}
              {c.socPercent != null && `, batería al ${Math.round(c.socPercent)} %`}
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="chip">{CONNECTOR_STATUS[c.status]}</span>
        {c.status === 'AVAILABLE' && station.source === 'CORE' && getSession()?.scopes.includes('aegisvolt/reservations.write') && (
          <a className="btn-charge !px-3 !py-2 text-sm" href={`#/reservar?station=${station.id}&connector=${c.id}`}>Reservar</a>
        )}
      </div>
    </li>
  );
}

export default function Stations() {
  const { data, error, loading } = usePoll(STATIONS);
  const [selected, setSelected] = useState(null);
  const stations = data?.stations ?? [];
  const free = stations.reduce((n, s) => n + s.availableConnectors, 0);
  const total = stations.reduce((n, s) => n + s.connectors.length, 0);
  const fromLegacy = stations[0]?.source === 'LEGACY_ACL';
  const current = stations.find((s) => s.id === selected) ?? stations[0];

  return (
    <>
      <RoadHero title="Bahías de carga en la Sabana">
        <p className="max-w-md text-lg font-semibold text-andes-soft">
          {loading ? 'Consultando electrolineras…' : `${free} de ${total} conectores libres ahora mismo en ${stations.length} electrolineras.`}
        </p>
      </RoadHero>

      <main className="mx-auto -mt-12 grid max-w-6xl gap-6 px-5 pb-16 sm:px-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        {error && <div className="lg:col-span-2"><CompensationAlert title="No se pudo leer el estado de las estaciones">{error.message}</CompensationAlert></div>}
        {fromLegacy && (
          <p className="surface-quiet px-4 py-3 text-sm lg:col-span-2">
            Este catálogo viene del sistema heredado a través de la capa anticorrupción (Strangler Fig sin activar en este ambiente).
            La reserva se habilita cuando el catálogo se sirve desde el nuevo core.
          </p>
        )}
        <section className="surface relative z-10 p-4" aria-labelledby="mapa">
          <h2 id="mapa" className="sr-only">Mapa</h2>
          {loading ? <div className="grid aspect-square place-items-center"><BatteryLoader label="Cargando mapa" /></div>
            : <StationMap stations={stations} selectedId={current?.id} onSelect={setSelected} />}
          <div className="mt-3 flex flex-wrap gap-2 text-sm">
            {['AVAILABLE', 'RESERVED', 'CHARGING'].map((s) => <span key={s} className={`chip status-${s}`}>{CONNECTOR_STATUS[s]}</span>)}
          </div>
        </section>

        <section className="surface relative z-10 p-6" aria-live="polite">
          {current && (
            <>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-4xl">{current.name}</h2>
                  <p className="text-slate-600">{current.address}, {current.city}</p>
                </div>
                <span className="text-4xl" aria-hidden="true">{current.emoji}</span>
              </div>
              <ul className="mt-4 divide-y divide-paramo-deep">
                {current.connectors.map((c) => <ConnectorRow key={c.id} station={current} c={c} />)}
              </ul>
            </>
          )}
          <label className="mt-6 block text-sm font-bold" htmlFor="station-picker">Otra estación</label>
          <select id="station-picker" className="av-field mt-1" value={current?.id ?? ''} onChange={(e) => setSelected(e.target.value)}>
            {stations.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.availableConnectors} libres)</option>)}
          </select>
        </section>
      </main>
    </>
  );
}
