/**
 * Proveedor de identidad LOCAL (AUTH_MODE=dev): usuarios con correo y contraseña en la tabla
 * aegisvolt-identity-<env>. En AWS este papel lo cumple Amazon Cognito (AUTH_MODE=cognito).
 *
 * Reglas de negocio:
 *  - El rol sale del dominio del correo: @operador → operador de flota, @admin → administrador,
 *    @auditor → auditor (ej. sofia@operador.aegisvolt.co).
 *  - Solo los operadores de flota se registran solos. Las cuentas @admin y @auditor las crea un
 *    administrador desde la consola; al arrancar se asegura un administrador inicial.
 *  - Contraseñas con scrypt + sal aleatoria; la comparación es en tiempo constante.
 */
import { randomBytes, randomUUID, scrypt as scryptCb, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, GetCommand, PutCommand, ScanCommand } from '@aws-sdk/lib-dynamodb';
import { config } from './config.js';

const scrypt = promisify(scryptCb);
const KEY_LEN = 64;

export const ROLE_BY_DOMAIN = { operador: 'fleet-operator', admin: 'platform-admin', auditor: 'auditor' };
const EMAIL = /^[a-z0-9._%+-]{1,64}@(operador|admin|auditor)(\.[a-z0-9-]{1,63}){0,4}$/;

export class IdentityError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

export function normalizeEmail(raw) {
  return String(raw ?? '').trim().toLowerCase();
}

/** Rol que corresponde al correo, o null si el dominio no es @operador, @admin ni @auditor. */
export function roleForEmail(email) {
  const match = EMAIL.exec(normalizeEmail(email));
  return match ? ROLE_BY_DOMAIN[match[1]] : null;
}

export function validatePassword(password) {
  const p = String(password ?? '');
  if (p.length < 8 || p.length > 128) return 'La contraseña debe tener entre 8 y 128 caracteres.';
  if (!/[A-Za-z]/.test(p) || !/[0-9]/.test(p)) return 'La contraseña debe combinar letras y números.';
  return null;
}

export async function hashPassword(password) {
  const salt = randomBytes(16);
  const hash = await scrypt(String(password), salt, KEY_LEN);
  return `scrypt$${salt.toString('base64')}$${hash.toString('base64')}`;
}

export async function verifyPassword(password, stored) {
  const [alg, saltB64, hashB64] = String(stored ?? '').split('$');
  if (alg !== 'scrypt' || !saltB64 || !hashB64) return false;
  const expected = Buffer.from(hashB64, 'base64');
  const actual = await scrypt(String(password), Buffer.from(saltB64, 'base64'), expected.length);
  return timingSafeEqual(actual, expected);
}

// Hash fijo para comparar aunque el usuario no exista: misma duración → no revela qué correos existen.
const DUMMY_HASH = hashPassword(randomUUID());

let doc;
function db() {
  doc ??= DynamoDBDocumentClient.from(new DynamoDBClient({
    region: config.region,
    ...(config.dynamodbEndpoint ? { endpoint: config.dynamodbEndpoint } : {}),
  }), { marshallOptions: { removeUndefinedValues: true } });
  return doc;
}

const userKey = (email) => ({ pk: `USER#${email}`, sk: 'PROFILE' });

export function publicUser(u) {
  return { id: u.user_id, email: u.email, name: u.name, role: u.role, active: u.active !== false,
    createdAt: u.created_at, createdBy: u.created_by ?? null };
}

export async function findUser(email) {
  const res = await db().send(new GetCommand({ TableName: config.identityTable, Key: userKey(normalizeEmail(email)) }));
  return res.Item ?? null;
}

/**
 * Crea un usuario. `actor` es null en el autorregistro (solo permite @operador) o el administrador
 * que lo crea desde la consola (cualquiera de los tres dominios).
 */
export async function createUser({ email, name, password }, actor = null) {
  const normalized = normalizeEmail(email);
  const role = roleForEmail(normalized);
  if (!role) throw new IdentityError('El correo debe ser de un dominio @operador, @admin o @auditor.');
  if (!actor && role !== 'fleet-operator') {
    throw new IdentityError('Solo los operadores de flota se registran solos; las cuentas @admin y @auditor las crea un administrador.', 403);
  }
  const cleanName = String(name ?? '').trim();
  if (cleanName.length < 2 || cleanName.length > 80) throw new IdentityError('El nombre debe tener entre 2 y 80 caracteres.');
  const weak = validatePassword(password);
  if (weak) throw new IdentityError(weak);
  const user = {
    ...userKey(normalized), user_id: `u-${randomUUID()}`, email: normalized, name: cleanName, role,
    password_hash: await hashPassword(password), active: true, created_at: new Date().toISOString(),
    created_by: actor ?? 'autorregistro',
  };
  try {
    await db().send(new PutCommand({ TableName: config.identityTable, Item: user,
      ConditionExpression: 'attribute_not_exists(pk)' }));
  } catch (err) {
    if (err.name === 'ConditionalCheckFailedException') throw new IdentityError('Ya existe una cuenta con ese correo.', 409);
    throw err;
  }
  return publicUser(user);
}

export async function authenticateUser(email, password) {
  const user = await findUser(email);
  const ok = await verifyPassword(password, user?.password_hash ?? await DUMMY_HASH);
  if (!user || !ok || user.active === false) throw new IdentityError('Correo o contraseña incorrectos.', 401);
  return user;
}

export async function listUsers() {
  const items = [];
  let ExclusiveStartKey;
  do {
    const res = await db().send(new ScanCommand({ TableName: config.identityTable, ExclusiveStartKey,
      FilterExpression: 'begins_with(pk, :u)', ExpressionAttributeValues: { ':u': 'USER#' } }));
    items.push(...(res.Items ?? []));
    ExclusiveStartKey = res.LastEvaluatedKey;
  } while (ExclusiveStartKey);
  return items.map(publicUser).sort((a, b) => a.email.localeCompare(b.email));
}

/** Administrador inicial (idempotente). Sin él nadie podría crear cuentas @admin o @auditor. */
export async function ensureBootstrapAdmin() {
  const { email, password } = config.bootstrapAdmin;
  if (!email || !password) return false;
  try {
    await createUser({ email, name: 'Administrador inicial', password }, 'bootstrap');
    return true;
  } catch (err) {
    if (err instanceof IdentityError && err.status === 409) return false;
    throw err;
  }
}
