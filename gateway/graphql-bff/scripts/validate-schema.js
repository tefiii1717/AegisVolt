// Valida en CI que el esquema GraphQL compila y que cada campo raíz tiene resolver.
import { readFileSync } from 'node:fs';
import { buildSchema } from 'graphql';

process.env.NODE_ENV = 'test';
const { resolvers } = await import('../src/resolvers.js');
const schema = buildSchema(readFileSync(new URL('../src/schema.graphql', import.meta.url), 'utf8'));
const missing = [];
for (const [typeName, type] of [['Query', schema.getQueryType()], ['Mutation', schema.getMutationType()],
  ['Subscription', schema.getSubscriptionType()]]) {
  for (const field of Object.keys(type.getFields())) {
    if (!resolvers[typeName]?.[field]) missing.push(`${typeName}.${field}`);
  }
}
if (missing.length) {
  console.error(`Campos sin resolver: ${missing.join(', ')}`);
  process.exit(1);
}
console.log(`Esquema válido: ${Object.keys(schema.getTypeMap()).filter((t) => !t.startsWith('__')).length} tipos`);
