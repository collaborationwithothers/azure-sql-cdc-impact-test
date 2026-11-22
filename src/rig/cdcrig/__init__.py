"""Orchestrator and collector for the CDC impact test.

The .NET load generator holds the application logic (the transaction). This
package walks the test matrix, runs the SQL scripts, polls the DMVs, controls
Debezium through marker files and uploads results (decision 0010).
"""
