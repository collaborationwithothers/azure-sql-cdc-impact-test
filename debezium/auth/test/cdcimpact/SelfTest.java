package cdcimpact;

/** Plain checks run during the image build. Fails the build on any mismatch. */
public final class SelfTest {

    private static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError("SelfTest failed: " + what);
        }
        System.out.println("ok - " + what);
    }

    public static void main(String[] args) {
        String url = ManagedIdentityToken.endpointUrl("http://localhost:42356/msi/token", "https://database.windows.net/", "abc");
        check(url.equals("http://localhost:42356/msi/token?api-version=2019-08-01&resource=https%3A%2F%2Fdatabase.windows.net%2F&client_id=abc"), "endpoint url with scheme");
        check(ManagedIdentityToken.endpointUrl("localhost:1/t", "r", null).equals("http://localhost:1/t?api-version=2019-08-01&resource=r"), "endpoint url without scheme or client id");

        ManagedIdentityToken.Token t = ManagedIdentityToken.parse("{\"access_token\":\"eyJ.x.y\",\"expires_on\":\"1586984735\",\"resource\":\"r\"}");
        check(t.value.equals("eyJ.x.y") && t.expiresOnEpochSeconds == 1586984735L, "parse string expires_on");
        t = ManagedIdentityToken.parse("{\"expires_on\": 1700000000, \"access_token\": \"tok\"}");
        check(t.value.equals("tok") && t.expiresOnEpochSeconds == 1700000000L, "parse numeric expires_on");

        check(EventHubsTokenHandler.resourceFromBootstrap("ns.servicebus.windows.net:9093").equals("https://ns.servicebus.windows.net"), "event hubs resource");
        check(EventHubsTokenHandler.resourceFromBootstrap(" a.b:9093,c.d:9093").equals("https://a.b"), "first bootstrap server");
    }
}
