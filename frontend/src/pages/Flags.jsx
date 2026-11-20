import { useState } from 'react';
import { CompensationAlert, SuccessToast } from '../components/Notices.jsx';
import { gql, usePoll } from '../lib/api.js';
import { getSession } from '../lib/auth.js';
import { formatDateTime } from '../lib/format.js';
import { FLAGS, SET_FLAG } from '../lib/queries.js';

const PLAIN_NAME = {
  ENABLE_DYNAMIC_SURGE_PRICING: 'Tarifa dinámica en hora pico',
  ENABLE_FAST_CHARGE_BOOST: 'Reserva de ultra-carga',
  SIMULATE_CHARGER_HARDWARE_FAULT: 'Simular falla física del cargador',
  STRANGLER_ROUTE_STATIONS_TO_CORE: 'Catálogo desde el nuevo core',
};

export default function Flags() {
  const { data, error, refresh } = usePoll(FLAGS, {}, { interval: 3000 });
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);
  const isAdmin = getSession()?.scopes.includes('aegisvolt/flags.admin');
  const env = data?.environment.name;

  async function toggle(flag) {
    setBusy(flag.name);
    setNotice(null);
    try {
      const { setFeatureFlag } = await gql(SET_FLAG, { name: flag.name, enabled: !flag.enabled });
      setNotice({ ok: true, text: `${PLAIN_NAME[flag.name] ?? flag.name} ${setFeatureFlag.enabled ? 'activada' : 'desactivada'} en ${env}. Los servicios la aplican en menos de 3 segundos, sin redesplegar.` });
      await refresh();
    } catch (e) {
      setNotice({ ok: false, text: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
      <h1 className="text-6xl">Feature flags de {env ?? '…'}</h1>
      <p className="mt-2 max-w-xl text-slate-600">
        Cada ambiente tiene su propia tabla de flags. Lo que cambies aquí solo afecta a {env ?? 'este ambiente'}.
        {!isAdmin && ' Tu rol puede ver las flags pero no cambiarlas.'}
      </p>
      <div className="mt-6 grid gap-3">
        {notice && (notice.ok
          ? <SuccessToast title="Flag actualizada" onClose={() => setNotice(null)}>{notice.text}</SuccessToast>
          : <CompensationAlert title="No se cambió la flag" onClose={() => setNotice(null)}>{notice.text}</CompensationAlert>)}
        {error && <CompensationAlert title="No se pudieron leer las flags">{error.message}</CompensationAlert>}
      </div>
      <ul className="surface mt-6 divide-y divide-paramo-deep">
        {data?.featureFlags.map((f) => {
          const locked = env === 'prod' && f.prodLocked && !f.enabled;
          return (
            <li key={f.name} className="flex items-center gap-5 p-5">
              <div className="flex-1">
                <p className="text-lg font-extrabold">{PLAIN_NAME[f.name] ?? f.name}</p>
                <p className="text-sm text-slate-600">{f.description}</p>
                <p className="mt-1 font-mono text-xs text-slate-500">{f.name}</p>
                {f.updatedBy && <p className="text-xs text-slate-500">Cambiada por {f.updatedBy} el {formatDateTime(f.updatedAt)}</p>}
                {locked && <p className="text-xs font-bold text-coral">Bloqueada en producción.</p>}
              </div>
              <button type="button" role="switch" aria-checked={f.enabled} aria-label={PLAIN_NAME[f.name] ?? f.name}
                className="switch" disabled={!isAdmin || busy === f.name || locked} onClick={() => toggle(f)} />
            </li>
          );
        })}
      </ul>
    </main>
  );
}
