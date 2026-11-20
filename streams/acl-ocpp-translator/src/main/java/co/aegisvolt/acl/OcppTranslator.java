package co.aegisvolt.acl;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;

/**
 * Capa Anti-Corrupción: traduce frames OCPP-J 1.6 del sistema heredado
 * ({@code [2, "<uid>", "MeterValues", {...}]}) al evento de dominio {@code telemetry.MeterValuesRecorded}.
 *
 * <p>El modelo OCPP (conectores numéricos, Wh como texto, measurands) nunca entra al core.
 * Es una función pura: fácil de probar y sin estado.</p>
 */
public final class OcppTranslator {

    public static final int MAX_FRAME_BYTES = 32 * 1024;
    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** Resultado de la traducción: o un evento válido, o el motivo del rechazo (va al DLQ). */
    public record Result(String value, String error) {
        public boolean ok() { return error == null; }
        static Result ok(String v) { return new Result(v, null); }
        static Result fail(String e) { return new Result(null, e); }
    }

    private final String environment;

    public OcppTranslator(String environment) {
        this.environment = environment;
    }

    public Result translate(String chargePointId, String raw) {
        if (raw == null || raw.getBytes(StandardCharsets.UTF_8).length > MAX_FRAME_BYTES) {
            return Result.fail("FRAME_EMPTY_OR_TOO_LARGE");
        }
        if (chargePointId == null || !chargePointId.matches("^[A-Z0-9-]{3,64}$")) {
            return Result.fail("INVALID_CHARGE_POINT_ID");
        }
        final JsonNode frame;
        try {
            // Solo JSON; Jackson sin default typing → sin deserialización polimórfica insegura.
            frame = MAPPER.readTree(raw);
        } catch (Exception e) {
            return Result.fail("MALFORMED_JSON");
        }
        if (!frame.isArray() || frame.size() != 4 || frame.get(0).asInt() != 2) {
            return Result.fail("NOT_AN_OCPP_CALL");
        }
        if (!"MeterValues".equals(frame.get(2).asText())) {
            return Result.fail("UNSUPPORTED_ACTION:" + frame.get(2).asText());
        }
        JsonNode body = frame.get(3);
        int connector = body.path("connectorId").asInt(-1);
        JsonNode sample = body.path("meterValue").path(0);
        if (connector <= 0 || sample.isMissingNode()) {
            return Result.fail("MISSING_FIELDS");
        }

        Optional<Double> energyKwh = Optional.empty();
        Optional<Double> powerKw = Optional.empty();
        Optional<Double> soc = Optional.empty();
        for (JsonNode sv : sample.path("sampledValue")) {
            String measurand = sv.path("measurand").asText("Energy.Active.Import.Register");
            String unit = sv.path("unit").asText("Wh");
            double value;
            try {
                value = Double.parseDouble(sv.path("value").asText());
            } catch (NumberFormatException e) {
                return Result.fail("NON_NUMERIC_VALUE");
            }
            switch (measurand) {
                case "Energy.Active.Import.Register" -> energyKwh = Optional.of(unit.equals("kWh") ? value : value / 1000.0);
                case "Power.Active.Import" -> powerKw = Optional.of(unit.equals("kW") ? value : value / 1000.0);
                case "SoC" -> soc = Optional.of(value);
                default -> { /* measurands desconocidos se ignoran (tolerancia) */ }
            }
        }
        if (energyKwh.isEmpty() || powerKw.isEmpty()) {
            return Result.fail("MISSING_MEASURANDS");
        }

        String uid = frame.get(1).asText();
        String measuredAt = sample.path("timestamp").asText(Instant.now().toString());
        String connectorId = "C" + connector;
        long legacyTx = body.path("transactionId").asLong(0);

        ObjectNode payload = MAPPER.createObjectNode()
                .put("station_id", chargePointId)
                .put("connector_id", connectorId)
                .put("legacy_transaction_id", legacyTx)
                .put("energy_kwh", round(energyKwh.get()))
                .put("power_kw", round(powerKw.get()))
                .put("measured_at", measuredAt);
        soc.ifPresent(v -> payload.put("soc_percent", round(v)));

        ObjectNode envelope = MAPPER.createObjectNode()
                // message_id determinista → si Streams reprocesa, los consumidores lo detectan como duplicado.
                .put("message_id", UUID.nameUUIDFromBytes((chargePointId + "|" + uid).getBytes(StandardCharsets.UTF_8)).toString())
                .put("type", "telemetry.MeterValuesRecorded")
                .put("version", 1)
                .put("correlation_id", chargePointId + ":" + connectorId + ":" + legacyTx)
                .put("causation_id", uid)
                .put("occurred_at", measuredAt)
                .put("source", "acl-ocpp-translator")
                .put("environment", environment);
        envelope.set("payload", payload);
        return Result.ok(envelope.toString());
    }

    private static double round(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }
}
