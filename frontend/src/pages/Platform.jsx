import { BatteryLoader, CompensationAlert } from '../components/Notices.jsx';
import { usePoll } from '../lib/api.js';
import { HEALTH } from '../lib/queries.js';

const BREAKER = {
  CLOSED: ['Cerrado: tráfico normal', 'status-AVAILABLE'],
  HALF_OPEN: ['Semi-abierto: probando recuperación', 'status-RESERVED'],
  OPEN: ['Abierto: rechazando llamadas', 'status-OUT_OF_SERVICE'],
};

export default function Platform() {
  const { data, error, loading } = usePoll(HEALTH, {}, { interval: 4000 });
  return (
    <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
      <h1 className="text-6xl">Plataforma</h1>
      <p className="mt-2 max-w-xl text-slate-600">Salud de cada microservicio y estado de sus circuit breakers.</p>
      {error && <div className="mt-6"><CompensationAlert title="No se pudo consultar la salud">{error.message}</CompensationAlert></div>}
      {loading && <div className="mt-8"><BatteryLoader label="Revisando servicios" /></div>}
      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {data?.platformHealth.map((s) => (
          <section key={s.service} className="surface p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-2xl">{s.service}</h2>
              <span className={`chip ${s.status === 'UP' ? 'status-AVAILABLE' : 'status-OUT_OF_SERVICE'}`}>{s.status === 'UP' ? 'En línea' : 'Caído'}</span>
            </div>
            {s.version && <p className="text-sm text-slate-500">Versión {s.version}</p>}
            {s.circuitBreakers.length > 0 && (
              <ul className="mt-3 grid gap-2">
                {s.circuitBreakers.map((b) => (
                  <li key={b.name} className="text-sm">
                    <p className="font-bold">{b.name}</p>
                    <span className={`chip ${BREAKER[b.state]?.[1]}`}>{BREAKER[b.state]?.[0] ?? b.state}</span>
                    <span className="ml-2 text-slate-500">{b.failures} fallos, {b.rejected} rechazadas</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </main>
  );
}
