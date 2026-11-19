import { useState } from 'react';
import RoadHero from '../components/RoadHero.jsx';
import { CompensationAlert } from '../components/Notices.jsx';
import { Logo } from '../components/TopBar.jsx';
import { cfg } from '../lib/api.js';
import { loginCognito, loginDev } from '../lib/auth.js';

const ROLES = [
  ['fleet-operator', 'Operador de flota', 'Consulta estaciones, reserva bahías y sigue sus cargas.'],
  ['platform-admin', 'Administrador de plataforma', 'Todo lo anterior y además cambia feature flags del ambiente.'],
  ['auditor', 'Auditor', 'Solo lectura de estaciones y transacciones.'],
];

export default function Login() {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const enter = async (role) => {
    setBusy(true);
    setError(null);
    try {
      if (cfg.authMode === 'cognito') await loginCognito();
      else await loginDev(role);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <>
      <div className="bg-andes px-5 py-3 sm:px-8"><Logo /></div>
      <RoadHero title="Carga para flotas eléctricas, sin filas en la vía">
        <p className="max-w-md text-lg font-semibold text-andes-soft">Reserva bahías de carga rápida en la Sabana y sigue cada sesión en vivo.</p>
      </RoadHero>
      <main className="mx-auto -mt-12 max-w-4xl px-5 pb-16 sm:px-8">
        <section className="surface relative z-10 p-6 sm:p-8">
          <h2 className="text-4xl">Ingresar</h2>
          {cfg.authMode === 'cognito' ? (
            <button type="button" className="btn-charge mt-5" disabled={busy} onClick={() => enter()}>Ingresar con mi cuenta corporativa</button>
          ) : (
            <>
              <p className="mt-1 text-slate-600">Ambiente {cfg.environment}: elige un rol de prueba. Cada rol recibe un token con scopes distintos.</p>
              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                {ROLES.map(([id, name, desc]) => (
                  <button key={id} type="button" disabled={busy} onClick={() => enter(id)}
                    className="surface-quiet p-4 text-left transition hover:bg-white">
                    <span className="block text-lg font-extrabold">{name}</span>
                    <span className="block text-sm text-slate-600">{desc}</span>
                  </button>
                ))}
              </div>
            </>
          )}
          {error && <div className="mt-4"><CompensationAlert title="No se pudo ingresar">{error}</CompensationAlert></div>}
        </section>
      </main>
    </>
  );
}
