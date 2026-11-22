package cdcimpact;

import com.microsoft.sqlserver.jdbc.SQLServerAccessTokenCallback;
import com.microsoft.sqlserver.jdbc.SqlAuthenticationToken;

/**
 * Supplies Azure SQL tokens to the Microsoft JDBC driver through the
 * accessTokenCallbackClass connection property (driver 12.4 and later).
 * Debezium passes it with debezium.source.driver.accessTokenCallbackClass.
 */
public class SqlTokenCallback implements SQLServerAccessTokenCallback {

    static final String RESOURCE = "https://database.windows.net/";

    public SqlTokenCallback() {
    }

    @Override
    public SqlAuthenticationToken getAccessToken(String spn, String stsurl) {
        ManagedIdentityToken.Token token = ManagedIdentityToken.get(RESOURCE);
        // SqlAuthenticationToken(String, long) takes milliseconds since the epoch.
        return new SqlAuthenticationToken(token.value, token.expiresOnEpochSeconds * 1000L);
    }
}
