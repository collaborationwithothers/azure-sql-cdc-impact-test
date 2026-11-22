package cdcimpact;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Fetches Microsoft Entra tokens for a user-assigned managed identity from the
 * Azure Container Apps identity endpoint. No Azure SDK dependency, so nothing
 * is added to Debezium Server's classpath except this jar.
 *
 * The endpoint and header come from IDENTITY_ENDPOINT and IDENTITY_HEADER,
 * which Container Apps injects. The identity is selected with AZURE_CLIENT_ID.
 * Request shape: https://learn.microsoft.com/azure/container-apps/managed-identity
 */
public final class ManagedIdentityToken {

    /** A token and its expiry in epoch seconds. */
    public static final class Token {
        public final String value;
        public final long expiresOnEpochSeconds;

        Token(String value, long expiresOnEpochSeconds) {
            this.value = value;
            this.expiresOnEpochSeconds = expiresOnEpochSeconds;
        }
    }

    private static final Map<String, Token> CACHE = new ConcurrentHashMap<>();
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10)).build();
    private static final Pattern ACCESS_TOKEN = Pattern.compile("\"access_token\"\\s*:\\s*\"([^\"]+)\"");
    private static final Pattern EXPIRES_ON = Pattern.compile("\"expires_on\"\\s*:\\s*\"?(\\d+)\"?");

    private ManagedIdentityToken() {
    }

    /** Returns a cached token for the resource, refreshed five minutes before expiry. */
    public static Token get(String resource) {
        long now = System.currentTimeMillis() / 1000;
        Token cached = CACHE.get(resource);
        if (cached != null && cached.expiresOnEpochSeconds - 300 > now) {
            return cached;
        }
        Token fresh = fetch(resource);
        CACHE.put(resource, fresh);
        return fresh;
    }

    static String endpointUrl(String endpoint, String resource, String clientId) {
        String base = endpoint.startsWith("http://") || endpoint.startsWith("https://") ? endpoint : "http://" + endpoint;
        StringBuilder url = new StringBuilder(base)
                .append(base.contains("?") ? "&" : "?")
                .append("api-version=2019-08-01&resource=")
                .append(URLEncoder.encode(resource, StandardCharsets.UTF_8));
        if (clientId != null && !clientId.isEmpty()) {
            url.append("&client_id=").append(URLEncoder.encode(clientId, StandardCharsets.UTF_8));
        }
        return url.toString();
    }

    static Token parse(String body) {
        Matcher token = ACCESS_TOKEN.matcher(body);
        Matcher expires = EXPIRES_ON.matcher(body);
        if (!token.find() || !expires.find()) {
            throw new IllegalStateException("Identity endpoint response has no access_token or expires_on");
        }
        return new Token(token.group(1), Long.parseLong(expires.group(1)));
    }

    private static Token fetch(String resource) {
        String endpoint = System.getenv("IDENTITY_ENDPOINT");
        String header = System.getenv("IDENTITY_HEADER");
        if (endpoint == null || header == null) {
            throw new IllegalStateException("IDENTITY_ENDPOINT or IDENTITY_HEADER is not set");
        }
        HttpRequest request = HttpRequest.newBuilder(URI.create(endpointUrl(endpoint, resource, System.getenv("AZURE_CLIENT_ID"))))
                .header("X-IDENTITY-HEADER", header)
                .timeout(Duration.ofSeconds(30))
                .GET().build();
        try {
            HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                throw new IllegalStateException("Identity endpoint returned HTTP " + response.statusCode());
            }
            return parse(response.body());
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Identity endpoint call failed", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while calling the identity endpoint", e);
        }
    }
}
