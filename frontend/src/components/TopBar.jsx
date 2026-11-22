import { clearSession } from '../lib/auth.js';
import { ROLE_LABEL } from '../lib/format.js';

const ENV_STYLE = {
  local: 'bg-slate-200 text-andes',
  dev: 'bg-flag-yellow text-andes',
  test: 'bg-[#6aa9ff] text-andes',
  prod: 'bg-flag-red text-white',
};

export function Logo() {
  return (
    <a href="#/estaciones" className="flex items-center gap-2" aria-label="AegisVolt, inicio">
      <svg width="34" height="34" viewBox="0 0 64 64" aria-hidden="true">
        <path d="M6 42 L38 42 L38 35 L54 46 L38 57 L38 50 L6 50Z" fill="#CE1126" />
        <path d="M6 30 L34 30 L34 24 L48 34 L34 44 L34 38 L6 38Z" fill="#003893" />
        <path d="M6 18 L30 18 L30 12 L42 22 L30 32 L30 26 L6 26Z" fill="#FCD116" />
      </svg>
      <span className="font-display text-3xl font-black tracking-wide text-white">aegis<span className="text-charge">volt</span></span>
    </a>
  );
}

export default function TopBar({ env, viewer, session, nav, route }) {
  return (
    <header className="bg-andes text-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3 sm:px-8">
        <Logo />
        <nav aria-label="Secciones" className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
          {nav.map(([href, label]) => (
            <a
              key={href}
              href={href}
              aria-current={route === href ? 'page' : undefined}
              className={`whitespace-nowrap rounded-xl px-3 py-2 text-sm font-bold ${route === href ? 'bg-white/15' : 'text-white/75 hover:text-white'}`}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {env && (
            <span className={`rounded-lg px-2.5 py-1 text-sm font-extrabold ${ENV_STYLE[env.name] ?? ENV_STYLE.local}`} title={`Versión ${env.version}`}>
              Ambiente {env.name}
            </span>
          )}
          <span className="hidden text-right text-sm leading-tight text-white/80 md:block">
            <span className="block font-bold text-white">{viewer?.name ?? session?.name}</span>
            {ROLE_LABEL[viewer?.role ?? session?.role] ?? viewer?.subject}
          </span>
          <button type="button" className="rounded-xl px-3 py-2 text-sm font-bold text-white/80 hover:bg-white/10" onClick={clearSession}>
            Salir
          </button>
        </div>
      </div>
    </header>
  );
}
