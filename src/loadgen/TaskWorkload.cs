using System.Data;
using System.Text;
using Microsoft.Data.SqlClient;

namespace CdcImpact.LoadGen;

/// <summary>The two transaction shapes. Configuration A uses UpdateOnly; B and C use UpdateAndOutbox.</summary>
public enum Transaction
{
    UpdateOnly,
    UpdateAndOutbox,
}

/// <summary>
/// The measured transaction: update one Task row and, optionally, insert a
/// JSON payload into the outbox, in one transaction and one round trip. This
/// is what the API would do. Whether CDC is on is invisible here.
/// </summary>
public sealed class TaskWorkload
{
    public const string UpdateOnlySql =
        "SET XACT_ABORT ON; BEGIN TRAN; " +
        "UPDATE dbo.Task SET Status = @status, UpdatedAt = SYSUTCDATETIME() WHERE TaskId = @id; " +
        "COMMIT;";

    public const string UpdateAndOutboxSql =
        "SET XACT_ABORT ON; BEGIN TRAN; " +
        "UPDATE dbo.Task SET Status = @status, UpdatedAt = SYSUTCDATETIME() WHERE TaskId = @id; " +
        "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) VALUES (@id, 'TaskUpdated', SYSUTCDATETIME(), @payload); " +
        "COMMIT;";

    private readonly string _connectionString;
    private readonly Transaction _transaction;
    private readonly long _maxTaskId;
    private readonly string _payloadTemplate;
    private readonly SqlDbType _payloadType;
    private readonly int _timeout;

    public TaskWorkload(string connectionString, Transaction transaction, long maxTaskId, int payloadChars, string payloadType, int commandTimeoutSeconds)
    {
        _connectionString = connectionString;
        _transaction = transaction;
        _maxTaskId = Math.Max(1, maxTaskId);
        _payloadTemplate = BuildPayloadTemplate(payloadChars);
        _payloadType = payloadType == "varchar(max)" ? SqlDbType.VarChar : SqlDbType.NVarChar;
        _timeout = commandTimeoutSeconds;
    }

    /// <summary>
    /// A JSON document of exactly <paramref name="chars"/> characters, with a
    /// fixed-width slot for the task ID. Every payload has the same length.
    /// </summary>
    public static string BuildPayloadTemplate(int chars)
    {
        const string head = "{\"type\":\"TaskUpdated\",\"taskId\":\"{{ID}}\",\"body\":\"";
        const string tail = "\"}";
        var fill = chars - (head.Length - "{{ID}}".Length + 20) - tail.Length;
        if (fill < 0) throw new ArgumentOutOfRangeException(nameof(chars), "Payload too small");
        return head + new string('x', fill) + tail;
    }

    public static string Payload(string template, long id) => template.Replace("{{ID}}", id.ToString("D20"));

    public string SqlText => _transaction == Transaction.UpdateOnly ? UpdateOnlySql : UpdateAndOutboxSql;

    public async Task ExecuteAsync(CancellationToken ct)
    {
        var id = Random.Shared.NextInt64(1, _maxTaskId + 1);
        await using var conn = new SqlConnection(_connectionString);
        await conn.OpenAsync(ct);
        await using var cmd = new SqlCommand(SqlText, conn) { CommandTimeout = _timeout };
        cmd.Parameters.Add("@id", SqlDbType.BigInt).Value = id;
        cmd.Parameters.Add("@status", SqlDbType.TinyInt).Value = (byte)(id % 5);
        if (_transaction == Transaction.UpdateAndOutbox)
        {
            cmd.Parameters.Add("@payload", _payloadType, -1).Value = Payload(_payloadTemplate, id);
        }
        await cmd.ExecuteNonQueryAsync(ct);
    }
}
