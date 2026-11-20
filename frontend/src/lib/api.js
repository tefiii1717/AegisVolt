import { useCallback, useEffect, useRef, useState } from 'react';
import { clearSession, getToken } from './auth.js';

export const cfg = window.__AEGIS_CONFIG__ ?? {};

export class GraphQLRequestError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

export async function gql(query, variables = {}) {
  const res = await fetch(cfg.graphqlUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${getToken() ?? ''}` },
    body: JSON.stringify({ query, variables }),
  });
  if (res.status === 401) {
    clearSession();
    throw new GraphQLRequestError('Tu sesión expiró. Vuelve a ingresar.', 'UNAUTHENTICATED');
  }
  const body = await res.json();
  if (body.errors?.length) {
    const err = body.errors[0];
    if (err.extensions?.code === 'UNAUTHENTICATED') clearSession();
    throw new GraphQLRequestError(err.message, err.extensions?.code);
  }
  return body.data;
}

/**
 * Polling de queries. Detrás de AWS API Gateway (HTTP API) no hay WebSocket ni SSE,
 * así que el "tiempo real" se logra consultando el modelo de lectura cada pocos segundos.
 */
export function usePoll(query, variables, { interval = cfg.pollMs ?? 2500, enabled = true } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const vars = JSON.stringify(variables ?? {});
  const timer = useRef();

  const refresh = useCallback(async () => {
    try {
      setData(await gql(query, JSON.parse(vars)));
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [query, vars]);

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      if (document.visibilityState === 'visible') await refresh();
      timer.current = setTimeout(tick, interval);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer.current);
    };
  }, [refresh, interval, enabled]);

  return { data, error, loading, refresh };
}
