import { useState } from 'react';
import { BatteryLoader, CompensationAlert, SuccessToast } from '../components/Notices.jsx';
import { gql, usePoll } from '../lib/api.js';
import { formatCop, formatDateTime } from '../lib/format.js';
import { PAYMENT_METHODS, REGISTER_PAYMENT_METHOD } from '../lib/queries.js';

export default function PaymentMethods() {
  const { data, error, loading, refresh } = usePoll(PAYMENT_METHODS, {}, { interval: 4000 });
  const [form, setForm] = useState({ companyName: '', accountNumber: '' });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const methods = data?.paymentMethods ?? [];

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      const { registerPaymentMethod: pm } = await gql(REGISTER_PAYMENT_METHOD, { input: form });
      setNotice({ ok: true, text: `${pm.companyName} (cuenta terminada en ${pm.accountLast4}) quedó lista para facturar tus cargas.` });
      setForm({ companyName: '', accountNumber: '' });
      setTimeout(refresh, 800); // la lista viene del modelo de lectura (consistencia eventual)
    } catch (err) {
      setNotice({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto grid max-w-6xl gap-8 px-5 py-10 sm:px-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <section>
        <h1 className="text-6xl">Medios de pago</h1>
        <p className="mt-2 max-w-xl text-slate-600">
          Cada carga se factura a la empresa del medio de pago que elijas al reservar. Mientras la carga se activa,
          el monto estimado queda retenido en el cupo; si la carga falla, el cupo se libera solo.
        </p>
        {error && <div className="mt-6"><CompensationAlert title="No se pudieron leer tus medios de pago">{error.message}</CompensationAlert></div>}
        {loading && <div className="mt-8"><BatteryLoader label="Cargando medios de pago" /></div>}
        {!loading && methods.length === 0 && (
          <p className="surface-quiet mt-8 p-6">Aún no tienes medios de pago. Registra la empresa a la que se facturarán tus cargas para poder reservar.</p>
        )}
        <ul className="mt-8 grid gap-3">
          {methods.map((m) => {
            const used = m.creditLimitCop ? Math.round(((m.creditLimitCop - m.availableCop) / m.creditLimitCop) * 100) : 0;
            return (
              <li key={m.id} className="surface p-5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-xl font-extrabold">{m.companyName}</p>
                  <p className="font-mono text-sm text-slate-500">Cuenta •••• {m.accountLast4}</p>
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-paramo-deep" role="meter" aria-valuemin={0} aria-valuemax={100}
                  aria-valuenow={used} aria-label={`Cupo usado ${used} %`}>
                  <div className="h-full bg-pine" style={{ width: `${used}%` }} />
                </div>
                <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
                  <div><dt className="text-slate-500">Cupo disponible</dt><dd className="font-bold tabular-nums">{formatCop(m.availableCop)}</dd></div>
                  <div><dt className="text-slate-500">Retenido</dt><dd className="font-bold tabular-nums">{formatCop(m.heldCop)}</dd></div>
                  <div><dt className="text-slate-500">Facturado</dt><dd className="font-bold tabular-nums">{formatCop(m.capturedCop)}</dd></div>
                </dl>
                <p className="mt-2 text-xs text-slate-500">Registrado el {formatDateTime(m.createdAt)}</p>
              </li>
            );
          })}
        </ul>
      </section>

      <form className="av-checkout self-start" onSubmit={submit} aria-label="Registrar medio de pago">
        <div className="av-checkout__card av-checkout__cart">
          <p className="av-checkout__title">Nuevo medio de pago</p>
          <div className="av-checkout__steps">
            <div>
              <label className="av-checkout__section-label" htmlFor="company">Empresa de flota a facturar</label>
              <input id="company" className="av-field" required minLength={2} maxLength={80} placeholder="Transportes La Sabana S.A.S."
                value={form.companyName} onChange={(e) => setForm((f) => ({ ...f, companyName: e.target.value }))} />
            </div>
            <div>
              <label className="av-checkout__section-label" htmlFor="account">Número de cuenta</label>
              <input id="account" className="av-field" required inputMode="numeric" pattern="[0-9 \-]{6,24}" placeholder="0012 3456 78"
                value={form.accountNumber} onChange={(e) => setForm((f) => ({ ...f, accountNumber: e.target.value }))} />
              <p className="mt-1 text-xs text-slate-500">Solo se guardan los últimos 4 dígitos.</p>
            </div>
            {notice && (notice.ok
              ? <SuccessToast title="Medio de pago registrado" onClose={() => setNotice(null)}>{notice.text}</SuccessToast>
              : <CompensationAlert title="No se registró" onClose={() => setNotice(null)}>{notice.text}</CompensationAlert>)}
          </div>
        </div>
        <div className="av-checkout__card av-checkout__footer">
          <p className="text-sm text-slate-600">Una factura por cada carga completada.</p>
          <button type="submit" className="btn-charge" disabled={busy}>{busy ? 'Registrando…' : 'Registrar'}</button>
        </div>
      </form>
    </main>
  );
}
