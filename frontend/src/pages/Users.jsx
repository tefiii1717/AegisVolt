import { useState } from 'react';
import { BatteryLoader, CompensationAlert, SuccessToast } from '../components/Notices.jsx';
import { gql, usePoll } from '../lib/api.js';
import { ROLE_LABEL, formatDateTime } from '../lib/format.js';
import { CREATE_USER, USERS } from '../lib/queries.js';

const ROLE_CHIP = { 'fleet-operator': 'status-AVAILABLE', 'platform-admin': 'status-RESERVED', auditor: 'status-CHARGING' };
const EMPTY = { name: '', email: '', password: '' };

export default function Users() {
  const { data, error, loading, refresh } = usePoll(USERS, {}, { interval: 10000 });
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const users = data?.users ?? [];
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      const { createUser } = await gql(CREATE_USER, { input: form });
      setNotice({ ok: true, text: `${createUser.email} ya puede ingresar como ${ROLE_LABEL[createUser.role].toLowerCase()}. Entrégale la contraseña por un canal seguro.` });
      setForm(EMPTY);
      await refresh();
    } catch (err) {
      setNotice({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto grid max-w-6xl gap-8 px-5 py-10 sm:px-8 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <section>
        <h1 className="text-6xl">Usuarios</h1>
        <p className="mt-2 max-w-xl text-slate-600">
          El dominio del correo define el rol. Los operadores se registran solos; las cuentas de administrador y auditor se crean aquí.
        </p>
        {error && <div className="mt-6"><CompensationAlert title="No se pudo leer la lista">{error.message}</CompensationAlert></div>}
        {loading && <div className="mt-8"><BatteryLoader label="Cargando usuarios" /></div>}
        <div className="surface mt-8 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-paramo-deep text-slate-500">
              <tr><th className="p-4">Usuario</th><th className="p-4">Rol</th><th className="p-4">Creado</th></tr>
            </thead>
            <tbody className="divide-y divide-paramo-deep">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="p-4"><span className="block font-bold">{u.name}</span><span className="text-slate-600">{u.email}</span></td>
                  <td className="p-4"><span className={`chip ${ROLE_CHIP[u.role] ?? ''}`}>{ROLE_LABEL[u.role] ?? u.role}</span></td>
                  <td className="p-4 text-slate-600">{formatDateTime(u.createdAt)}<span className="block text-xs">por {u.createdBy}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <form className="av-checkout self-start" onSubmit={submit} aria-label="Crear usuario">
        <div className="av-checkout__card av-checkout__cart">
          <p className="av-checkout__title">Nueva cuenta</p>
          <div className="av-checkout__steps">
            <div>
              <label className="av-checkout__section-label" htmlFor="u-name">Nombre</label>
              <input id="u-name" className="av-field" required minLength={2} maxLength={80} value={form.name}
                onChange={(e) => set({ name: e.target.value })} />
            </div>
            <div>
              <label className="av-checkout__section-label" htmlFor="u-email">Correo</label>
              <input id="u-email" className="av-field" type="email" required placeholder="nombre@auditor.aegisvolt.co"
                value={form.email} onChange={(e) => set({ email: e.target.value })} />
              <p className="mt-1 text-xs text-slate-500">@operador, @admin o @auditor.</p>
            </div>
            <div>
              <label className="av-checkout__section-label" htmlFor="u-pass">Contraseña inicial</label>
              <input id="u-pass" className="av-field" type="password" autoComplete="new-password" required minLength={8}
                value={form.password} onChange={(e) => set({ password: e.target.value })} />
            </div>
            {notice && (notice.ok
              ? <SuccessToast title="Cuenta creada" onClose={() => setNotice(null)}>{notice.text}</SuccessToast>
              : <CompensationAlert title="No se creó la cuenta" onClose={() => setNotice(null)}>{notice.text}</CompensationAlert>)}
          </div>
        </div>
        <div className="av-checkout__card av-checkout__footer">
          <span />
          <button type="submit" className="btn-charge" disabled={busy}>{busy ? 'Creando…' : 'Crear cuenta'}</button>
        </div>
      </form>
    </main>
  );
}
