-- Schema for the CDC impact test. Idempotent. Runs in each test database.
-- Matter and Task model a legal SaaS task list. Outbox holds one JSON event
-- per task update (the transactional outbox pattern: the event is written in
-- the same transaction as the business change, and a separate reader, here
-- Debezium via CDC, publishes it).
-- The token {{PAYLOAD_TYPE}} is replaced with nvarchar(max) (decision D1) or
-- varchar(max).

IF OBJECT_ID(N'dbo.Matter', N'U') IS NULL
CREATE TABLE dbo.Matter
(
    MatterId  int           NOT NULL CONSTRAINT PK_Matter PRIMARY KEY CLUSTERED,
    Name      nvarchar(200) NOT NULL,
    CreatedAt datetime2(3)  NOT NULL
);

IF OBJECT_ID(N'dbo.Task', N'U') IS NULL
CREATE TABLE dbo.Task
(
    TaskId     bigint       NOT NULL CONSTRAINT PK_Task PRIMARY KEY CLUSTERED,
    MatterId   int          NOT NULL,
    Status     tinyint      NOT NULL,
    AssigneeId int          NOT NULL,
    DueDate    date         NOT NULL,
    CreatedAt  datetime2(3) NOT NULL,
    UpdatedAt  datetime2(3) NOT NULL
);

IF OBJECT_ID(N'dbo.Outbox', N'U') IS NULL
CREATE TABLE dbo.Outbox
(
    OutboxId    bigint        IDENTITY(1, 1) NOT NULL CONSTRAINT PK_Outbox PRIMARY KEY CLUSTERED,
    AggregateId bigint        NOT NULL,
    EventType   varchar(100)  NOT NULL,
    CreatedAt   datetime2(3)  NOT NULL,
    Payload     {{PAYLOAD_TYPE}} NOT NULL
);
