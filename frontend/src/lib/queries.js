export const SHELL = /* GraphQL */ `
  query Shell { viewer { subject role scopes } environment { name version region authMode } }
`;

export const STATIONS = /* GraphQL */ `
  query Stations {
    stations {
      id name city address mapX mapY emoji source availableConnectors
      connectors { id type powerKw tier status sagaId lastPowerKw energyKwh socPercent }
    }
    featureFlags { name enabled }
  }
`;

export const RESERVE_CONTEXT = /* GraphQL */ `
  query ReserveContext {
    stations { id name city source connectors { id type powerKw tier status } }
    fleets { id name availableCop heldCop }
    featureFlags { name enabled }
  }
`;

const TX_FIELDS = `
  id state isTerminal outcome fleetId vehicleId stationId connectorId estimatedKwh powerKw amountCop surgeApplied
  failureReason stuck createdAt updatedAt
  steps { step status at detail }
  compensations { action status at refundedCop }
`;

export const TRANSACTION = /* GraphQL */ `query Tx($id: ID!) { transaction(id: $id) { ${TX_FIELDS} } }`;
export const TRANSACTIONS = /* GraphQL */ `query Txs($limit: Int) { transactions(limit: $limit) { ${TX_FIELDS} } }`;

export const RESERVE = /* GraphQL */ `
  mutation Reserve($input: ReserveAndChargeInput!) {
    reserveAndCharge(input: $input) { accepted transaction { ${TX_FIELDS} } }
  }
`;

export const FLAGS = /* GraphQL */ `
  query Flags { environment { name } featureFlags { name enabled description environment prodLocked updatedAt updatedBy } }
`;

export const SET_FLAG = /* GraphQL */ `
  mutation SetFlag($name: String!, $enabled: Boolean!) { setFeatureFlag(name: $name, enabled: $enabled) { name enabled updatedAt updatedBy } }
`;

export const HEALTH = /* GraphQL */ `
  query Health { platformHealth { service status version circuitBreakers { name state failures rejected } } }
`;
