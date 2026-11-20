import { useEffect, useState } from 'react';
import TopBar from './components/TopBar.jsx';
import { usePoll } from './lib/api.js';
import { completeCognitoLogin, getSession, onSessionChange } from './lib/auth.js';
import { SHELL } from './lib/queries.js';
import Flags from './pages/Flags.jsx';
import Login from './pages/Login.jsx';
import Platform from './pages/Platform.jsx';
import Reserve from './pages/Reserve.jsx';
import Stations from './pages/Stations.jsx';
import Transactions from './pages/Transactions.jsx';

const ROUTES = {
  '#/estaciones': Stations,
  '#/reservar': Reserve,
  '#/transacciones': Transactions,
  '#/flags': Flags,
  '#/plataforma': Platform,
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

function Shell() {
  const route = useRoute();
  const { data } = usePoll(SHELL, {}, { interval: 30000 });
  const Page = ROUTES[route] ?? Stations;
  return (
    <>
      <TopBar env={data?.environment} viewer={data?.viewer} route={route} />
      <Page key={route} />
    </>
  );
}

export default function App() {
  const [session, setSession] = useState(getSession);
  useEffect(() => onSessionChange(setSession), []);
  useEffect(() => {
    completeCognitoLogin().catch(() => {});
  }, []);
  return session ? <Shell /> : <Login />;
}
