import { GraphQLError, Kind } from 'graphql';

/** Regla de validación: rechaza consultas anidadas más allá de `max` niveles (DoS por queries profundas). */
export function depthLimit(max) {
  return (context) => {
    const fragments = Object.fromEntries(
      context.getDocument().definitions
        .filter((d) => d.kind === Kind.FRAGMENT_DEFINITION)
        .map((d) => [d.name.value, d]),
    );
    const depthOf = (node, depth, seen = new Set()) => {
      if (!node.selectionSet) return depth;
      let deepest = depth;
      for (const sel of node.selectionSet.selections) {
        if (sel.kind === Kind.FRAGMENT_SPREAD) {
          if (seen.has(sel.name.value)) continue;
          seen.add(sel.name.value);
          deepest = Math.max(deepest, depthOf(fragments[sel.name.value], depth, seen));
        } else {
          deepest = Math.max(deepest, depthOf(sel, sel.kind === Kind.FIELD ? depth + 1 : depth, seen));
        }
      }
      return deepest;
    };
    return {
      OperationDefinition(node) {
        const depth = depthOf(node, 0);
        if (depth > max) context.reportError(new GraphQLError(`Profundidad ${depth} excede el máximo ${max}`));
      },
    };
  };
}
