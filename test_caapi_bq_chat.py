from google.cloud import geminidataanalytics

# Configuration
billing_project = "kenly-dev-auto-240320261552"
location = "global"

# Clients
data_agent_client = geminidataanalytics.DataAgentServiceClient()
data_chat_client = geminidataanalytics.DataChatServiceClient()

# Data Source
bigquery_table_reference_1 = geminidataanalytics.BigQueryTableReference()
bigquery_table_reference_1.project_id = "bigquery-public-data"
bigquery_table_reference_1.dataset_id = "san_francisco"
bigquery_table_reference_1.table_id = "street_trees"

datasource_references = geminidataanalytics.DatasourceReferences()
datasource_references.bq.table_references = [bigquery_table_reference_1]

# Context (Stateless)
inline_context = geminidataanalytics.Context()
inline_context.system_instruction = "Help the user analyze their data."
inline_context.datasource_references = datasource_references

# Question
question = "Which species of tree is most prevalent?"
messages = [geminidataanalytics.Message()]
messages[0].user_message.text = question

# =====================================================================
# Chat API Section (Streaming Conversation)
# - Best for interactive, human-facing chat interfaces
# - Answers come as a stream of text/system messages
# - Supports multi-step reasoning: Can run multiple queries sequentially
#   to answer complex questions (e.g., "Find top 5 users, then find their orders").
# =====================================================================
try:
    print(f"Sending question: {question}")
    request = geminidataanalytics.ChatRequest(
        inline_context=inline_context,
        parent=f"projects/{billing_project}/locations/{location}",
        messages=messages,
    )
    stream = data_chat_client.chat(request=request)
    for response in stream:
        print("\n" + "="*40)
        print(f"Message ID: {response.message_id}")
        print("="*40)
        
        # Print the raw response for full detail (useful for auditing)
        print(f"Raw Response Dump:\n{response}")
except Exception as e:
    print(f"Error calling Conversational Analytics API: {e}")
