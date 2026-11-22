using System.Data;
using System.Reflection;
using System.Text.RegularExpressions;
using Microsoft.Data.SqlClient;

namespace CdcImpact.Common;

/// <summary>Builds connections. Azure runs use the managed identity; container tests use a template.</summary>
public sealed class SqlConnections(RunSettings settings)
{
    public string ConnectionString(string database, int? maxPool = null)
    {
        if (!string.IsNullOrEmpty(settings.ConnectionStringTemplate))
        {
            var b = new SqlConnectionStringBuilder(settings.ConnectionStringTemplate.Replace("{db}", database));
            if (maxPool is not null) b.MaxPoolSize = maxPool.Value;
            return b.ConnectionString;
        }
        var builder = new SqlConnectionStringBuilder
        {
            DataSource = $"tcp:{settings.SqlServer},1433",
            InitialCatalog = database,
            Authentication = SqlAuthenticationMethod.ActiveDirectoryManagedIdentity,
            UserID = settings.ClientId,
            Encrypt = SqlConnectionEncryptOption.Mandatory,
            ConnectTimeout = 60,
            MaxPoolSize = maxPool ?? settings.MaxPoolSize,
            MinPoolSize = 0,
            ApplicationName = "cdc-impact-rig",
        };
        return builder.ConnectionString;
    }

    public async Task<SqlConnection> OpenAsync(string database, CancellationToken ct, int? maxPool = null)
    {
        var conn = new SqlConnection(ConnectionString(database, maxPool));
        await conn.OpenAsync(ct);
        return conn;
    }
}

public static class SqlExtensions
{
    public static async Task<int> ExecAsync(this SqlConnection conn, string sql, CancellationToken ct, int timeoutSeconds = 600, params (string Name, object Value)[] parameters)
    {
        await using var cmd = new SqlCommand(sql, conn) { CommandTimeout = timeoutSeconds };
        foreach (var (name, value) in parameters) cmd.Parameters.AddWithValue(name, value);
        return await cmd.ExecuteNonQueryAsync(ct);
    }

    public static async Task<T?> ScalarAsync<T>(this SqlConnection conn, string sql, CancellationToken ct, int timeoutSeconds = 120)
    {
        await using var cmd = new SqlCommand(sql, conn) { CommandTimeout = timeoutSeconds };
        var value = await cmd.ExecuteScalarAsync(ct);
        return value is null or DBNull ? default : (T)Convert.ChangeType(value, Nullable.GetUnderlyingType(typeof(T)) ?? typeof(T));
    }

    /// <summary>Runs a query and returns each row as a name-to-value map. Binary values become hex strings.</summary>
    public static async Task<List<Dictionary<string, object?>>> RowsAsync(this SqlConnection conn, string sql, CancellationToken ct, int timeoutSeconds = 60)
    {
        await using var cmd = new SqlCommand(sql, conn) { CommandTimeout = timeoutSeconds };
        await using var reader = await cmd.ExecuteReaderAsync(ct);
        var rows = new List<Dictionary<string, object?>>();
        while (await reader.ReadAsync(ct))
        {
            var row = new Dictionary<string, object?>(reader.FieldCount);
            for (var i = 0; i < reader.FieldCount; i++)
            {
                var v = reader.IsDBNull(i) ? null : reader.GetValue(i);
                row[reader.GetName(i)] = v switch
                {
                    byte[] bytes => Convert.ToHexString(bytes),
                    DateTimeOffset dto => dto.UtcDateTime,
                    _ => v,
                };
            }
            rows.Add(row);
        }
        return rows;
    }
}

/// <summary>Loads the scripts in /sql, embedded at build time.</summary>
public static partial class SqlScripts
{
    public static string Load(string file)
    {
        var asm = typeof(SqlScripts).Assembly;
        using var stream = asm.GetManifestResourceStream($"sql/{file}") ?? throw new FileNotFoundException(file);
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    /// <summary>Returns the block that follows "-- name: NAME" up to the next such marker.</summary>
    public static string Block(string file, string name)
    {
        var text = Load(file);
        var parts = NameMarker().Split(text);
        // Split with a capture group yields: preamble, name1, body1, name2, body2, ...
        for (var i = 1; i + 1 < parts.Length; i += 2)
        {
            if (parts[i].Trim() == name) return parts[i + 1].Trim();
        }
        throw new KeyNotFoundException($"{file}: block {name}");
    }

    [GeneratedRegex(@"^--\s*name:\s*(\S+)\s*$", RegexOptions.Multiline)]
    private static partial Regex NameMarker();
}
