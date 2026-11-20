package co.aegisvolt.acl;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.apache.kafka.streams.TestInputTopic;
import org.apache.kafka.streams.TestOutputTopic;
import org.apache.kafka.streams.TopologyTestDriver;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AclTopologyTest {

    private static final ObjectMapper M = new ObjectMapper();
    private static final String FRAME = """
            [2,"ab12","MeterValues",{"connectorId":2,"transactionId":5001,"meterValue":[{"timestamp":"2026-11-19T17:00:00Z",
            "sampledValue":[{"value":"12500","measurand":"Energy.Active.Import.Register","unit":"Wh"},
            {"value":"143000","measurand":"Power.Active.Import","unit":"W"},{"value":"48.5","measurand":"SoC","unit":"Percent"}]}]}]""";

    private TopologyTestDriver driver;
    private TestInputTopic<String, String> in;
    private TestOutputTopic<String, String> out;
    private TestOutputTopic<String, String> dlq;

    @BeforeEach
    void setUp() {
        Properties p = new Properties();
        p.put("application.id", "acl-test");
        p.put("bootstrap.servers", "dummy:9092");
        driver = new TopologyTestDriver(AclApp.topology("test"), p);
        in = driver.createInputTopic(AclApp.SOURCE, new StringSerializer(), new StringSerializer());
        out = driver.createOutputTopic(AclApp.SINK, new StringDeserializer(), new StringDeserializer());
        dlq = driver.createOutputTopic(AclApp.DLQ, new StringDeserializer(), new StringDeserializer());
    }

    @AfterEach
    void tearDown() {
        driver.close();
    }

    @Test
    void traduceMeterValuesAlModeloDeDominio() throws Exception {
        in.pipeInput("EST-SIB-001", FRAME);
        JsonNode env = M.readTree(out.readValue());
        assertEquals("telemetry.MeterValuesRecorded", env.get("type").asText());
        assertEquals("test", env.get("environment").asText());
        JsonNode p = env.get("payload");
        assertEquals("C2", p.get("connector_id").asText());
        assertEquals(12.5, p.get("energy_kwh").asDouble());
        assertEquals(143.0, p.get("power_kw").asDouble());
        assertEquals(48.5, p.get("soc_percent").asDouble());
        assertTrue(dlq.isEmpty());
    }

    @Test
    void messageIdDeterministaParaIdempotencia() throws Exception {
        in.pipeInput("EST-SIB-001", FRAME);
        in.pipeInput("EST-SIB-001", FRAME);
        String a = M.readTree(out.readValue()).get("message_id").asText();
        String b = M.readTree(out.readValue()).get("message_id").asText();
        assertEquals(a, b);
    }

    @Test
    void framesInvalidosVanAlDeadLetter() throws Exception {
        in.pipeInput("EST-SIB-001", "{\"no\":\"es ocpp\"}");
        in.pipeInput("EST-SIB-001", "[2,\"x\",\"BootNotification\",{}]");
        in.pipeInput("est;drop", FRAME);
        assertEquals("NOT_AN_OCPP_CALL", M.readTree(dlq.readValue()).get("reason").asText());
        assertEquals("UNSUPPORTED_ACTION:BootNotification", M.readTree(dlq.readValue()).get("reason").asText());
        assertEquals("INVALID_CHARGE_POINT_ID", M.readTree(dlq.readValue()).get("reason").asText());
        assertTrue(out.isEmpty());
    }
}
