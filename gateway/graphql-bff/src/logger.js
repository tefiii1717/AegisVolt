import { AsyncLocalStorage } from 'node:async_hooks';
import { createWriteStream } from 'node:fs';
import { config } from './config.js';

export const requestContext = new AsyncLocalStorage();
// Sidecar: copia de los logs en un volumen compartido que lee Fluent Bit (si LOG_FILE está definido).
const fileSink = process.env.LOG_FILE ? createWriteStream(process.env.LOG_FILE, { flags: 'a' }) : null;

const errorTimes = [];
export function errorsLastMinute() {
  const cutoff = Date.now() - 60_000;
  while (errorTimes.length && errorTimes[0] < cutoff) errorTimes.shift();
  return errorTimes.length;
}

function write(level, message, extra = {}) {
  if (level === 'ERROR') errorTimes.push(Date.now());
  const store = requestContext.getStore();
  const line = {
    timestamp: new Date().toISOString(),
    level,
    service: 'graphql-bff',
    environment: config.environment,
    version: config.version,
    correlation_id: extra.correlation_id ?? store?.correlationId ?? null,
    message,
    ...extra,
  };
  const serialized = `${JSON.stringify(line)}\n`;
  process.stdout.write(serialized);
  fileSink?.write(serialized);
}

export const log = {
  info: (m, e) => write('INFO', m, e),
  warn: (m, e) => write('WARNING', m, e),
  error: (m, e) => write('ERROR', m, e),
};
