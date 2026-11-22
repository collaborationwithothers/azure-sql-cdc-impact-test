-- CDC on the Outbox table only (design section 2). Run as the Entra admin
-- (db_owner). Each block is executed separately by the orchestrator.

-- name: enable
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE database_id = DB_ID() AND is_cdc_enabled = 1)
    EXEC sys.sp_cdc_enable_db;
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID(N'dbo.Outbox') AND is_tracked_by_cdc = 1)
    EXEC sys.sp_cdc_enable_table
        @source_schema = N'dbo',
        @source_name = N'Outbox',
        @role_name = NULL,
        @supports_net_changes = 0;

-- name: disable
IF EXISTS (SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID(N'dbo.Outbox') AND is_tracked_by_cdc = 1)
    EXEC sys.sp_cdc_disable_table @source_schema = N'dbo', @source_name = N'Outbox', @capture_instance = N'all';
IF EXISTS (SELECT 1 FROM sys.databases WHERE database_id = DB_ID() AND is_cdc_enabled = 1)
    EXEC sys.sp_cdc_disable_db;

-- name: lower-retention
-- Not documented as supported in Azure SQL Database. The orchestrator records
-- whether it succeeds.
EXEC sys.sp_cdc_change_job @job_type = N'cleanup', @retention = 1;

-- name: cleanup
-- Deletes change rows below the current maximum LSN.
DECLARE @lwm binary(10) = sys.fn_cdc_get_max_lsn();
EXEC sys.sp_cdc_cleanup_change_table
    @capture_instance = N'dbo_Outbox',
    @low_water_mark = @lwm,
    @threshold = 5000;
