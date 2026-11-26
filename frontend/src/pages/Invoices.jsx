import { useState } from 'react';
import { BatteryLoader, CompensationAlert } from '../components/Notices.jsx';
import { usePoll } from '../lib/api.js';
import { getSession } from '../lib/auth.js';
import { formatCop, formatDateTime } from '../lib/format.js';
import { INVOICES } from '../lib/queries.js';

function InvoiceDetail({ inv }) {
  return (
    <div className="grid gap-4 border-t border-paramo-deep p-5 text-sm md:grid-cols-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-slate-500">Facturado a</dt><dd className="font-bold">{inv.companyName}</dd>
        <dt className="text-slate-500">Cuenta</dt><dd>•••• {inv.accountLast4 ?? '—'}</dd>
        {inv.requestedByEmail && <><dt className="text-slate-500">Operador</dt><dd>{inv.requestedByEmail}</dd></>}
        <dt className="text-slate-500">Estación</dt><dd>{inv.stationId} / {inv.connectorId}</dd>
        <dt className="text-slate-500">Vehículo</dt><dd>{inv.vehicleId ?? '—'}</dd>
        <dt className="text-slate-500">Transacción</dt><dd className="break-all text-slate-500">{inv.sagaId}</dd>
      </dl>
      <dl className="av-checkout__details self-start">
        <dt>Energía</dt><dd>{inv.energyKwh} kWh</dd>
        <dt>Tarifa</dt><dd>{formatCop(inv.tariffCopPerKwh)} / kWh</dd>
        {inv.multiplier && inv.multiplier !== 1 && <><dt>Recargo{inv.surgeApplied ? ' hora pico' : ''}</dt><dd>× {inv.multiplier}</dd></>}
        <dt>Subtotal</dt><dd>{formatCop(inv.subtotalCop)}</dd>
        <dt>IVA 19 %</dt><dd>{formatCop(inv.ivaCop)}</dd>
        <dt className="font-extrabold">Total</dt><dd className="text-lg">{formatCop(inv.totalCop)}</dd>
      </dl>
    </div>
  );
}

export default function Invoices() {
  const auditor = getSession()?.scopes.includes('aegisvolt/audit.read');
  const { data, error, loading } = usePoll(INVOICES, { limit: 100 }, { interval: 5000 });
  const [open, setOpen] = useState(null);
  const [company, setCompany] = useState('');
  const all = data?.invoices ?? [];
  const companies = [...new Set(all.map((i) => i.companyName))].sort();
  const invoices = company ? all.filter((i) => i.companyName === company) : all;
  const total = invoices.reduce((sum, i) => sum + i.totalCop, 0);

  return (
    <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
      <h1 className="text-6xl">Facturas</h1>
      <p className="mt-2 max-w-xl text-slate-600">
        {auditor
          ? 'Todas las facturas emitidas en este ambiente. Solo lectura.'
          : 'Las facturas de tus cargas. Se emite una por cada carga completada; las cargas revertidas no se facturan.'}
      </p>
      {error && <div className="mt-6"><CompensationAlert title="No se pudieron leer las facturas">{error.message}</CompensationAlert></div>}
      {loading && <div className="mt-10"><BatteryLoader label="Cargando facturas" /></div>}
      {!loading && all.length === 0 && (
        <p className="surface-quiet mt-8 p-6">
          Aún no hay facturas.{!auditor && <> <a className="font-bold text-charge-deep underline" href="#/reservar">Reserva una carga</a> para generar la primera.</>}
        </p>
      )}
      {all.length > 0 && (
        <div className="mt-8 flex flex-wrap items-end justify-between gap-4">
          <div className="w-64">
            <label className="av-checkout__section-label" htmlFor="company-filter">Empresa</label>
            <select id="company-filter" className="av-field" value={company} onChange={(e) => setCompany(e.target.value)}>
              <option value="">Todas</option>
              {companies.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <p className="text-right"><span className="block text-sm text-slate-500">{invoices.length} facturas</span>
            <span className="font-display text-3xl font-black tabular-nums">{formatCop(total)}</span></p>
        </div>
      )}
      <ul className="mt-4 grid gap-3">
        {invoices.map((inv) => {
          const expanded = open === inv.id;
          return (
            <li key={inv.id} className="surface overflow-hidden">
              <button type="button" className="flex w-full flex-wrap items-center gap-x-6 gap-y-2 p-5 text-left"
                aria-expanded={expanded} onClick={() => setOpen(expanded ? null : inv.id)}>
                <span className="font-mono text-sm font-bold">{inv.id}</span>
                <span className="font-bold">{inv.companyName}</span>
                {auditor && inv.requestedByEmail && <span className="text-slate-600">{inv.requestedByEmail}</span>}
                <span className="text-slate-600">{inv.energyKwh} kWh</span>
                <span className="font-semibold tabular-nums">{formatCop(inv.totalCop)}</span>
                <span className="ml-auto text-sm text-slate-500">{formatDateTime(inv.issuedAt)}</span>
              </button>
              {expanded && <InvoiceDetail inv={inv} />}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
