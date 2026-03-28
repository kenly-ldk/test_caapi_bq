import sys
from google.cloud import geminidataanalytics

# Configuration
billing_project = "kenly-dev-auto-240320261552"
location = "global"
agent_id = "sf-trees-analyst-test"

# Question
question = "Which species of tree is most prevalent?"

print("="*40)
print("Persistent Agent Approach (vs Statless Chat API)")
print("="*40)

# Clients
data_agent_client = geminidataanalytics.DataAgentServiceClient()
data_chat_client = geminidataanalytics.DataChatServiceClient()

# Data Source Reference (Same as test_caapi.py)
bigquery_table_reference_1 = geminidataanalytics.BigQueryTableReference()
bigquery_table_reference_1.project_id = "bigquery-public-data"
bigquery_table_reference_1.dataset_id = "san_francisco"
bigquery_table_reference_1.table_id = "street_trees"

datasource_references = geminidataanalytics.DatasourceReferences()
datasource_references.bq.table_references = [bigquery_table_reference_1]

# Context Definition (Saved to the server)
context = geminidataanalytics.Context()
context.datasource_references = datasource_references
context.system_instruction = "You are an expert urban forester in San Francisco. Analyze tree data accurately."

# 1. Create Data Agent (Persistent Configuration)
try:
    print(f"\n--- Creating Persistent Data Agent: {agent_id} ---")
    
    analytics_agent = geminidataanalytics.DataAnalyticsAgent()
    analytics_agent.published_context = context # Save it as published context
    
    data_agent = geminidataanalytics.DataAgent()
    data_agent.display_name = "San Francisco Trees Analyst"
    data_agent.description = "Persistent agent for analyzing SF urban forest metrics"
    data_agent.data_analytics_agent = analytics_agent
    
    request = geminidataanalytics.CreateDataAgentRequest(
        parent=f"projects/{billing_project}/locations/{location}",
        data_agent_id=agent_id,
        data_agent=data_agent,
    )
    
    print("Calling create_data_agent...")
    operation = data_agent_client.create_data_agent(request=request)
    print("Agent created or checked. Operation running...")
    
    # The client might return the operation or the agent directly depending on sync/async in python SDK.
    # We will assume it's synchronous or we can use it directly if it's the object.
    agent_name = f"projects/{billing_project}/locations/{location}/dataAgents/{agent_id}"
    print(f"Agent Name: {agent_name}")

except Exception as e:
    # If it already exists, we will reuse it. That's the power of persistent agents!
    if "already exists" in str(e).lower():
        print(f"Agent {agent_id} already exists. Reusing it!")
        agent_name = f"projects/{billing_project}/locations/{location}/dataAgents/{agent_id}"
    else:
        print(f"Error creating agent: {e}")
        sys.exit(1)

# 2. Stateful Chat (Using the persistent Agent)
try:
    print(f"\n--- Chatting with Agent: {agent_id} ---")
    print(f"Question: {question}")
    
    # Define reference to the AGENT, not the datasource!
    agent_context = geminidataanalytics.DataAgentContext()
    agent_context.data_agent = agent_name
    agent_context.context_version = geminidataanalytics.DataAgentContext.ContextVersion.PUBLISHED
    
    # We send the request WITHOUT inline source references! The server uses the saved agent config.
    chat_request = geminidataanalytics.ChatRequest()
    chat_request.parent = f"projects/{billing_project}/locations/{location}"
    chat_request.data_agent_context = agent_context
    chat_request.messages = [geminidataanalytics.Message(
        user_message=geminidataanalytics.UserMessage(
            text=question
        )
    )]
    
    print("Calling chat with persistent agent...")
    stream = data_chat_client.chat(request=chat_request)
    
    for response in stream:
        print("\n" + "="*40)
        print("Message Received (From Persistent Agent):")
        print("="*40)
        print(f"Raw Response Dump:\n{response}")

except Exception as e:
    print(f"Error chatting with agent: {e}")

# =====================================================================
# Section 3: Fully Stateful Chat with Persistent History (ConversationReference)
# - The server manages your conversation history!
# - You will get a Conversation ID and Message IDs
# - Perfect for audit logs or shared context across complex workflows
# =====================================================================

try:
    print(f"\n--- Stateful Chat Using ConversationReference ---")
    
    convo_id = "sf-trees-convo-test"
    convo_name = f"projects/{billing_project}/locations/{location}/conversations/{convo_id}"
    
    # 1. Ensure Conversation exists
    try:
        convo_obj = geminidataanalytics.Conversation()
        convo_obj.agents = [agent_name] # agent_name was defined earlier
        
        create_req = geminidataanalytics.CreateConversationRequest(
            parent=f"projects/{billing_project}/locations/{location}",
            conversation_id=convo_id,
            conversation=convo_obj
        )
        data_chat_client.create_conversation(request=create_req)
        print(f"Created Conversation: {convo_name}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"Conversation {convo_id} already exists. Reusing it!")
        else:
            raise e

    # 2. Chat with ConversationReference
    print(f"Question (Stateful): {question}")
    
    convo_ref = geminidataanalytics.ConversationReference()
    convo_ref.conversation = convo_name
    convo_ref.data_agent_context = agent_context # agent_context was defined earlier

    chat_request_stateful = geminidataanalytics.ChatRequest()
    chat_request_stateful.parent = f"projects/{billing_project}/locations/{location}"
    chat_request_stateful.conversation_reference = convo_ref
    chat_request_stateful.messages = [geminidataanalytics.Message(
        user_message=geminidataanalytics.UserMessage(
            text=question
        )
    )]

    print("Calling chat with ConversationReference...")
    stream_stateful = data_chat_client.chat(request=chat_request_stateful)
    
    for response in stream_stateful:
        print("\n" + "="*40)
        print("Message Received (From Persistent Conversation):")
        print("="*40)
        # Here response.message_id SHOULD exist!
        print(f"Message ID: {response.message_id or 'Stateless/Ephemeral'}")
        print(f"System Text Response:\n{response.system_message.text.parts}")

except Exception as e:
    print(f"Error chatting with persistent conversation: {e}")
