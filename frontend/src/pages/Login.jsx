import { useState } from 'react';
import RoadHero from '../components/RoadHero.jsx';
import { CompensationAlert } from '../components/Notices.jsx';
import { Logo } from '../components/TopBar.jsx';
import { cfg } from '../lib/api.js';
import { loginCognito, loginPassword, registerOperator } from '../lib/auth.js';

const DOMAINS = [
  ['@operador', 'Operador de flota', 'Reserva cargas, registra los medios de pago de su empresa y ve sus facturas.'],
  ['@admin', 'Administrador', 'Gestiona usuarios, feature flags y la salud de la plataforma.'],
  ['@auditor', 'Auditor', 'Revisa todas las transacciones y facturas, sin poder modificarlas.'],
];

function Field({ id, label, hint, ...props }) {
  return (
    <div>
      <label className="av-checkout__section-label" htmlFor={id}>{label}</label>
      <input id={id} className="av-field" required {...props} />
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export default function Login() {
  const [mode, setMode] = useState('login'); // login | register
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (mode === 'register' && form.password !== form.confirm) {
      setError('Las contraseñas no coinciden.');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'register') await registerOperator(form);
      else await loginPassword(form.email, form.password);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const switchMode = (next) => {
    setMode(next);
    setError(null);
  };

  return (
    <>
      <div className="bg-andes px-5 py-3 sm:px-8"><Logo /></div>
      <RoadHero title="Carga para flotas eléctricas, sin filas en la vía">
        <p className="max-w-md text-lg font-semibold text-andes-soft">Reserva bahías de carga rápida en la Sabana y factura cada sesión a tu empresa.</p>
      </RoadHero>
      <main className="mx-auto -mt-12 grid max-w-5xl gap-6 px-5 pb-16 sm:px-8 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <section className="surface relative z-10 p-6 sm:p-8">
          {cfg.authMode === 'cognito' ? (
            <>
              <h2 className="text-4xl">Ingresar</h2>
              <button type="button" className="btn-charge mt-5" disabled={busy}
                onClick={() => { setBusy(true); loginCognito().catch((err) => { setError(err.message); setBusy(false); }); }}>
                Ingresar con mi cuenta corporativa
              </button>
            </>
          ) : (
            <form onSubmit={submit} className="grid gap-4" aria-label={mode === 'login' ? 'Ingresar' : 'Crear cuenta de operador'}>
              <div className="flex gap-1 rounded-2xl bg-paramo p-1" role="tablist">
                {[['login', 'Ingresar'], ['register', 'Crear cuenta de operador']].map(([id, label]) => (
                  <button key={id} type="button" role="tab" aria-selected={mode === id} onClick={() => switchMode(id)}
                    className={`flex-1 rounded-xl px-3 py-2 text-sm font-extrabold ${mode === id ? 'bg-white shadow' : 'text-slate-600'}`}>
                    {label}
                  </button>
                ))}
              </div>
              {mode === 'register' && (
                <Field id="name" label="Nombre completo" autoComplete="name" value={form.name}
                  onChange={(e) => set({ name: e.target.value })} />
              )}
              <Field id="email" label="Correo" type="email" autoComplete="username" value={form.email}
                placeholder={mode === 'register' ? 'nombre@operador.aegisvolt.co' : 'nombre@operador.aegisvolt.co'}
                hint={mode === 'register' ? 'Debe ser un correo @operador.' : null}
                onChange={(e) => set({ email: e.target.value })} />
              <Field id="password" label="Contraseña" type="password" minLength={8}
                autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={form.password}
                hint={mode === 'register' ? 'Mínimo 8 caracteres, con letras y números.' : null}
                onChange={(e) => set({ password: e.target.value })} />
              {mode === 'register' && (
                <Field id="confirm" label="Repite la contraseña" type="password" autoComplete="new-password" value={form.confirm}
                  onChange={(e) => set({ confirm: e.target.value })} />
              )}
              {error && <CompensationAlert title={mode === 'login' ? 'No se pudo ingresar' : 'No se creó la cuenta'}>{error}</CompensationAlert>}
              <button type="submit" className="btn-charge" disabled={busy}>
                {busy ? 'Un momento…' : mode === 'login' ? 'Ingresar' : 'Crear cuenta e ingresar'}
              </button>
              <p className="text-xs text-slate-500">Ambiente {cfg.environment}. Las contraseñas se guardan cifradas con scrypt.</p>
            </form>
          )}
          {cfg.authMode === 'cognito' && error && <div className="mt-4"><CompensationAlert title="No se pudo ingresar">{error}</CompensationAlert></div>}
        </section>

        <aside className="relative z-10 grid content-start gap-3 lg:mt-16">
          <h2 className="text-2xl">Tu correo define tu rol</h2>
          {DOMAINS.map(([domain, name, desc]) => (
            <div key={domain} className="surface-quiet p-4">
              <p className="font-extrabold">{name} <span className="font-mono text-sm text-charge-deep">{domain}</span></p>
              <p className="text-sm text-slate-600">{desc}</p>
            </div>
          ))}
          <p className="text-sm text-slate-600">Solo los operadores crean su cuenta aquí. Las cuentas de administrador y auditor las crea un administrador.</p>
        </aside>
      </main>
    </>
  );
}
