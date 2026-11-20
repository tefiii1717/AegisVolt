package co.aegisvolt.acl;

import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.KafkaStreams;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.Topology;
import org.apache.kafka.streams.kstream.Branched;
import org.apache.kafka.streams.kstream.Consumed;
import org.apache.kafka.streams.kstream.KStream;
import org.apache.kafka.streams.kstream.Named;
import org.apache.kafka.streams.kstream.Produced;

import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CountDownLatch;

/**
 * Topología Kafka Streams de la ACL:
 *
 * <pre>
 *  legacy.ocpp.meter-values ──translate──┬─ ok ───▶ aegisvolt.telemetry.events
 *                                        └─ error ▶ aegisvolt.dead-letter
 * </pre>
 */
public final class AclApp {

    public static final String SOURCE = "legacy.ocpp.meter-values";
    public static final String SINK = "aegisvolt.telemetry.events";
    public static final String DLQ = "aegisvolt.dead-letter";

    private AclApp() {}

    public static Topology topology(String environment) {
        OcppTranslator translator = new OcppTranslator(environment);
        StreamsBuilder builder = new StreamsBuilder();
        KStream<String, OcppTranslator.Result> translated = builder
                .stream(SOURCE, Consumed.with(Serdes.String(), Serdes.String()))
                .mapValues((key, value) -> translator.translate(key, value), Named.as("ocpp-to-domain"));

        Map<String, KStream<String, OcppTranslator.Result>> branches = translated
                .split(Named.as("acl-"))
                .branch((k, r) -> r.ok(), Branched.as("ok"))
                .defaultBranch(Branched.as("error"));

        branches.get("acl-ok")
                .mapValues(OcppTranslator.Result::value)
                .to(SINK, Produced.with(Serdes.String(), Serdes.String()));

        branches.get("acl-error")
                .mapValues((k, r) -> "{\"source\":\"acl-ocpp-translator\",\"reason\":\"" + r.error() + "\",\"key\":\"" + k + "\"}")
                .to(DLQ, Produced.with(Serdes.String(), Serdes.String()));

        return builder.build();
    }

    public static void main(String[] args) throws InterruptedException {
        String env = System.getenv().getOrDefault("AEGIS_ENV", "local");
        Properties props = new Properties();
        props.put(StreamsConfig.APPLICATION_ID_CONFIG, "acl-ocpp-translator-" + env);
        props.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, System.getenv().getOrDefault("KAFKA_BOOTSTRAP", "localhost:9092"));
        props.put(StreamsConfig.PROCESSING_GUARANTEE_CONFIG, StreamsConfig.EXACTLY_ONCE_V2);
        props.put(StreamsConfig.REPLICATION_FACTOR_CONFIG, Integer.parseInt(System.getenv().getOrDefault("KAFKA_REPLICATION", "1")));

        KafkaStreams streams = new KafkaStreams(topology(env), props);
        CountDownLatch latch = new CountDownLatch(1);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            streams.close();
            latch.countDown();
        }));
        streams.setStateListener((now, before) ->
                System.out.printf("{\"service\":\"acl-ocpp-translator\",\"environment\":\"%s\",\"message\":\"state %s -> %s\"}%n",
                        env, before, now));
        streams.start();
        latch.await();
    }
}
