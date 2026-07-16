"""
Create-or-update the published Data Agent the Cloud Run demo chats against.

Run ONCE at deploy time (as an identity holding dataAgentCreator/Editor — e.g. the
deployer or the runtime SA). The published_context (datasource + system
instruction + one verified example query) mirrors agent_stateless/main.py. The
Cloud Run app then only *references* this agent via DataAgentContext, so the
per-request identity needs just dataAgentUser (get + chat).

Uses ambient ADC. Config via env / flags:
    BILLING_PROJECT (or --project)   required
    LOCATION        (or --location)  default: global
    AGENT_ID        (or --agent-id)  required
"""
import argparse
import os
import sys

from google.cloud import geminidataanalytics
from google.protobuf import field_mask_pb2

VERIFIED_NLQ = "Which species of tree is most prevalent?"
VERIFIED_SQL = (
    "SELECT species, COUNT(*) AS tree_count\n"
    "FROM `bigquery-public-data.san_francisco.street_trees`\n"
    "GROUP BY species\n"
    "ORDER BY tree_count DESC\n"
    "LIMIT 10"
)


def build_context():
    bq_ref = geminidataanalytics.BigQueryTableReference()
    bq_ref.project_id = "bigquery-public-data"
    bq_ref.dataset_id = "san_francisco"
    bq_ref.table_id = "street_trees"

    datasource_references = geminidataanalytics.DatasourceReferences()
    datasource_references.bq.table_references = [bq_ref]

    context = geminidataanalytics.Context()
    context.system_instruction = (
        "You are an expert urban forester in San Francisco. Analyze tree data accurately."
    )
    context.datasource_references = datasource_references
    context.example_queries = [
        geminidataanalytics.ExampleQuery(
            natural_language_question=VERIFIED_NLQ, sql_query=VERIFIED_SQL
        )
    ]
    return context


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("BILLING_PROJECT", ""))
    ap.add_argument("--location", default=os.environ.get("LOCATION", "global"))
    ap.add_argument("--agent-id", default=os.environ.get("AGENT_ID", ""))
    args = ap.parse_args()
    if not args.project or not args.agent_id:
        print("ERROR: --project/BILLING_PROJECT and --agent-id/AGENT_ID are required")
        return 2

    client = geminidataanalytics.DataAgentServiceClient()
    parent = f"projects/{args.project}/locations/{args.location}"
    agent_name = f"{parent}/dataAgents/{args.agent_id}"
    context = build_context()

    analytics_agent = geminidataanalytics.DataAnalyticsAgent()
    analytics_agent.published_context = context

    data_agent = geminidataanalytics.DataAgent()
    data_agent.display_name = "San Francisco Trees Analyst (Cloud Run demo)"
    data_agent.description = "Published agent the Cloud Run identity-model demo chats against"
    data_agent.data_analytics_agent = analytics_agent

    try:
        client.create_data_agent(
            request=geminidataanalytics.CreateDataAgentRequest(
                parent=parent, data_agent_id=args.agent_id, data_agent=data_agent
            )
        )
        print(f"Created agent: {agent_name}")
    except Exception as e:  # noqa: BLE001
        if "already exists" in str(e).lower():
            update_agent = geminidataanalytics.DataAgent()
            update_agent.name = agent_name
            update_agent.data_analytics_agent.published_context = context
            client.update_data_agent(
                request=geminidataanalytics.UpdateDataAgentRequest(
                    data_agent=update_agent,
                    update_mask=field_mask_pb2.FieldMask(
                        paths=["data_analytics_agent.published_context"]
                    ),
                )
            )
            print(f"Updated existing agent: {agent_name}")
        else:
            print(f"ERROR ensuring agent: {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
