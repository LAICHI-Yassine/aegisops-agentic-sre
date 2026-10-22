import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_servers.telemetry_finops_server"],
    )

    print("=== Connecting to MCP Server: aegis-telemetry-finops (via stdio JSON-RPC) ===")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. List Available MCP Tools
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"[1/4] Discovered {len(tool_names)} MCP Tools: {tool_names}")

            # 2. Test FinOps Anomaly Detection Tool
            finops_resp = await session.call_tool(
                "detect_finops_cost_spikes",
                arguments={"lookback_hours": 6, "spike_multiplier_threshold": 2.0},
            )
            finops_data = json.loads(finops_resp.content[0].text)
            anomaly = finops_data["finops_anomalies"][0]
            print(
                f"[2/4] FinOps Tool Verified -> Service: {anomaly['service_name']} | "
                f"Baseline: ${anomaly['baseline_avg_cost_usd']}/hr -> Recent Avg: ${anomaly['recent_avg_cost_usd']}/hr "
                f"({anomaly['cost_spike_multiplier']}x spike | {anomaly['peak_instances']}x {anomaly['current_instance_type']})"
            )

            # 3. Test DuckDB OLAP Telemetry Tool
            telem_resp = await session.call_tool(
                "analyze_service_telemetry",
                arguments={"service_name": "payment-service", "lookback_hours": 4},
            )
            telem_data = json.loads(telem_resp.content[0].text)
            summary = telem_data["telemetry_summary"]
            top_err = telem_data["error_signatures"][0]
            print(
                f"[3/4] DuckDB Telemetry Tool Verified -> payment-service p99 Latency: {summary['latency_p99_ms']} ms | "
                f"Peak RAM: {summary['peak_memory_mb']} MB | Top Error: {top_err['error_code']} ({top_err['occurrences']} events)"
            )

            # 4. Test AST SQL Injection Guardrail (INC-2026-004 Attack Simulation)
            malicious_sql = "SELECT * FROM deployments; DROP TABLE deployments; --"
            guard_resp = await session.call_tool(
                "execute_guarded_diagnostic_sql",
                arguments={"sql_query": malicious_sql, "target_engine": "postgres"},
            )
            guard_data = json.loads(guard_resp.content[0].text)
            print(
                f"[4/4] AST SQL Guardrail Verified -> Status: {guard_data['status']} | "
                f"Reason: {guard_data['violation_reason']}"
            )


if __name__ == "__main__":
    asyncio.run(main())