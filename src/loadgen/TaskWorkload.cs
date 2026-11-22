using System.Data;
using System.Text;
using CdcImpact.Common;
using Microsoft.Data.SqlClient;

namespace CdcImpact.LoadGen;

/// <summary>
/// The measured transaction: update one Task row and, for configurations B and
/// C, insert a JSON payload into the outbox, in one transaction and one round
/// trip. C runs the same SQL as B; the difference is CDC on the outbox.
/// </summary>
public sealed class TaskWorkload
{
    public const string UpdateOnly =
        "SET XACT_ABORT ON; BEGIN TRAN; " +
        "UPDATE dbo.Task SET Status = @status, UpdatedAt = SYSUTCDATETIME() WHERE TaskId = @id; " +
        "COMMIT;";

    public const string UpdateAndOutbox =
        "SET XACT_ABORT ON; BEGIN TRAN; " +
        "UPDATE dbo.Task SET Status = @status, UpdatedAt = SYSUTCDATETIME() WHERE TaskId = @id; " +
        "INSERT dbo.Outbox (AggregateId, EventType, CreatedAt, Payload) VALUES (@id, 'TaskUpdated', SYSUTCDATETIME(), @payload); " +
        "COMMIT;";

    private readonly string _connectionString;
    private readonly Configuration _configuration;
    private readonly long _maxTaskId;
    private readonly string _payloadTemplate;
    private readonly SqlDbType _payloadType;
    private readonly int _timeout;

    public TaskWorkload(string connectionString, Configuration configuration, long maxTaskId, int payloadChars, string payloadType, int commandTimeoutSeconds)
    {
        _connectionString = connectionString;
        _configuration = configuration;
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

    public string SqlText => _configuration == Configuration.A ? UpdateOnly : UpdateAndOutbox;

    public async Task ExecuteAsync(CancellationToken ct)
    {
        var id = Random.Shared.NextInt64(1, _maxTaskId + 1);
        await using var conn = new SqlConnection(_connectionString);
        await conn.OpenAsync(ct);
        await using var cmd = new SqlCommand(SqlText, conn) { CommandTimeout = _timeout };
        cmd.Parameters.Add("@id", SqlDbType.BigInt).Value = id;
        cmd.Parameters.Add("@status", SqlDbType.TinyInt).Value = (byte)(id % 5);
        if (_configuration != Configuration.A)
        {
            cmd.Parameters.Add("@payload", _payloadType, -1).Value = Payload(_payloadTemplate, id);
        }
        await cmd.ExecuteNonQueryAsync(ct);
    }
}
