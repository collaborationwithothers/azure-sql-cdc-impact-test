package cdcimpact;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.security.auth.callback.Callback;
import javax.security.auth.callback.UnsupportedCallbackException;
import javax.security.auth.login.AppConfigurationEntry;
import org.apache.kafka.common.security.auth.AuthenticateCallbackHandler;
import org.apache.kafka.common.security.oauthbearer.OAuthBearerToken;
import org.apache.kafka.common.security.oauthbearer.OAuthBearerTokenCallback;

/**
 * Kafka SASL OAUTHBEARER login callback for the Event Hubs Kafka endpoint,
 * using the rig's managed identity. For Kafka clients the token resource is
 * https://NAMESPACE.servicebus.windows.net (Microsoft Learn,
 * "Authenticate an application with Microsoft Entra ID to access Event Hubs").
 */
public class EventHubsTokenHandler implements AuthenticateCallbackHandler {

    private String resource;

    public EventHubsTokenHandler() {
    }

    static String resourceFromBootstrap(String bootstrap) {
        String first = bootstrap.split(",")[0].trim();
        int colon = first.lastIndexOf(':');
        String host = colon > 0 ? first.substring(0, colon) : first;
        return "https://" + host;
    }

    @Override
    public void configure(Map<String, ?> configs, String saslMechanism, List<AppConfigurationEntry> jaasConfigEntries) {
        Object servers = configs.get("bootstrap.servers");
        String bootstrap = servers instanceof List<?> list ? String.valueOf(list.get(0)) : String.valueOf(servers);
        this.resource = resourceFromBootstrap(bootstrap);
    }

    @Override
    public void handle(Callback[] callbacks) throws UnsupportedCallbackException {
        for (Callback callback : callbacks) {
            if (callback instanceof OAuthBearerTokenCallback tokenCallback) {
                ManagedIdentityToken.Token token = ManagedIdentityToken.get(resource);
                long now = System.currentTimeMillis();
                tokenCallback.token(new OAuthBearerToken() {
                    @Override
                    public String value() {
                        return token.value;
                    }

                    @Override
                    public Set<String> scope() {
                        return Collections.emptySet();
                    }

                    @Override
                    public long lifetimeMs() {
                        return token.expiresOnEpochSeconds * 1000L;
                    }

                    @Override
                    public String principalName() {
                        return "rig";
                    }

                    @Override
                    public Long startTimeMs() {
                        return now;
                    }
                });
            } else {
                throw new UnsupportedCallbackException(callback);
            }
        }
    }

    @Override
    public void close() {
    }
}
