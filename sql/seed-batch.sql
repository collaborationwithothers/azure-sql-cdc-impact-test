-- Inserts one batch of Task rows with TaskId in (@from, @to].
-- Rows are generated in the database, so no data crosses the network.
-- Azure SQL Database always logs inserts fully, so seeding is bound by the
-- log rate limit.
SET NOCOUNT ON;
WITH n AS
(
    SELECT TOP (@to - @from)
           ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i
    FROM sys.all_columns a CROSS JOIN sys.all_columns b CROSS JOIN sys.all_columns c
)
INSERT dbo.Task WITH (TABLOCK) (TaskId, MatterId, Status, AssigneeId, DueDate, CreatedAt, UpdatedAt)
SELECT @from + i,
       CAST(((@from + i) % @matters) + 1 AS int),
       CAST((@from + i) % 5 AS tinyint),
       CAST((@from + i) % 5000 AS int),
       DATEADD(day, CAST((@from + i) % 365 AS int), '2026-01-01'),
       '2026-01-01T00:00:00',
       '2026-01-01T00:00:00'
FROM n;
