import { useEffect, useState } from 'react';
import TopBar from './components/TopBar.jsx';
import { usePoll } from './lib/api.js';
import { completeCognitoLogin, getSession, onSessionChange } from './lib/auth.js';
import { SHELL } from './lib/queries.js';
import { NAV_BY_ROLE } from './lib/format.js';
import Flags from './pages/Flags.jsx';
import Invoices from './pages/Invoices.jsx';
import Login from './pages/Login.jsx';
import PaymentMethods from './pages/PaymentMethods.jsx';
import Platform from './pages/Platform.jsx';
import Reserve from './pages/Reserve.jsx';
import Stations from './pages/Stations.jsx';
import Transactions from './pages/Transactions.jsx';
import Users from './pages/Users.jsx';

const ROUTES = {
  '#/estaciones': Stations,
  '#/reservar': Reserve,
  '#/transacciones': Transactions,
  '#/pagos': PaymentMethods,
  '#/facturas': Invoices,
  '#/flags': Flags,
  '#/plataforma': Platform,
  '#/usuarios': Users,
};

function useRoute() {
  const read = () => window.location.hash.split('?')[0] || '#/estaciones';
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onHash = () => setRoute(read());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  return route;
}

function Shell({ session }) {
  const route = useRoute();
  const { data } = usePoll(SHELL, {}, { interval: 30000 });
  // Cada rol solo navega a sus secciones; el BFF además rechaza lo que su token no permite.
  const nav = NAV_BY_ROLE[session.role] ?? NAV_BY_ROLE.auditor;
  const allowed = nav.some(([href]) => href === route);
  const current = allowed ? route : nav[0][0];
  useEffect(() => {
    if (!allowed) window.location.replace(current);
  }, [allowed, current]);
  const Page = ROUTES[current] ?? Stations;
  return (
    <>
      <TopBar env={data?.environment} viewer={data?.viewer} session={session} nav={nav} route={current} />
      <Page key={current} />
    </>
  );
}

export default function App() {
  const [session, setSession] = useState(getSession);
  useEffect(() => onSessionChange(setSession), []);
  useEffect(() => {
    completeCognitoLogin().catch(() => {});
  }, []);
  return session ? <Shell session={session} /> : <Login />;
}
