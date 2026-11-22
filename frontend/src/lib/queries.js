export const SHELL = /* GraphQL */ `
  query Shell { viewer { subject email name role scopes } environment { name version region authMode } }
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
    paymentMethods { id companyName accountLast4 creditLimitCop availableCop heldCop }
    featureFlags { name enabled }
  }
`;

const TX_FIELDS = `
  id state isTerminal outcome fleetId companyName requestedByEmail vehicleId stationId connectorId estimatedKwh powerKw amountCop surgeApplied
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

export const PAYMENT_METHODS = /* GraphQL */ `
  query PaymentMethods { paymentMethods { id companyName accountLast4 creditLimitCop availableCop heldCop capturedCop createdAt } }
`;

export const REGISTER_PAYMENT_METHOD = /* GraphQL */ `
  mutation RegisterPaymentMethod($input: RegisterPaymentMethodInput!) {
    registerPaymentMethod(input: $input) { id companyName accountLast4 availableCop }
  }
`;

export const INVOICES = /* GraphQL */ `
  query Invoices($limit: Int) {
    invoices(limit: $limit) {
      id sagaId companyName accountLast4 requestedByEmail stationId connectorId vehicleId energyKwh
      tariffCopPerKwh multiplier surgeApplied subtotalCop ivaCop totalCop issuedAt
    }
  }
`;

export const USERS = /* GraphQL */ `query Users { users { id email name role active createdAt createdBy } }`;

export const CREATE_USER = /* GraphQL */ `
  mutation CreateUser($input: CreateUserInput!) { createUser(input: $input) { id email name role } }
`;
