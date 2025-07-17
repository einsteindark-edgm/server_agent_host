"""Host Agent - Main orchestrator with ADK."""
import asyncio
import json
import uuid
import os
from datetime import datetime
from typing import Any, AsyncIterable, List

import httpx
import nest_asyncio
from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    Task,
)
from dotenv import load_dotenv
from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .orchestration_tools import (
    get_import_keywords,
    get_invoice_keywords,
    create_security_alert
)
from .remote_agent_connection import RemoteAgentConnections
from .verification_agent import create_verification_tool

load_dotenv()
nest_asyncio.apply()


class HostAgent:
    """Orchestrator agent that coordinates queries about imports and invoices."""

    def __init__(self):
        self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
        self.cards: dict[str, AgentCard] = {}
        self.agents: str = ""
        self._agent = self.create_agent()
        self._user_id = "host_agent"
        self._runner = Runner(
            app_name=self._agent.name,
            agent=self._agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

    async def _async_init_components(self, remote_agent_addresses: List[str]):
        async with httpx.AsyncClient(timeout=30) as client:
            for address in remote_agent_addresses:
                card_resolver = A2ACardResolver(client, address)
                try:
                    card = await card_resolver.get_agent_card()
                    remote_connection = RemoteAgentConnections(
                        agent_card=card, agent_url=address
                    )
                    self.remote_agent_connections[card.name] = remote_connection
                    self.cards[card.name] = card
                except httpx.ConnectError as e:
                    print(f"ERROR: Failed to get agent card from {address}: {e}")
                except Exception as e:
                    print(f"ERROR: Failed to initialize connection for {address}: {e}")

        agent_info = [
            json.dumps({"name": card.name, "description": card.description})
            for card in self.cards.values()
        ]
        print("agent_info:", agent_info)
        self.agents = "\n".join(agent_info) if agent_info else "No agents found"

    @classmethod
    async def create(cls, remote_agent_addresses: List[str]):
        instance = cls()
        await instance._async_init_components(remote_agent_addresses)
        return instance

    def create_agent(self) -> Agent:
        # Create the verification tool
        verify_tool = create_verification_tool()
        
        return Agent(
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp"),
            name="Host_Agent_Orchestrator",
            instruction=self.root_instruction,
            description="Orchestrator agent that coordinates queries about imports and invoices",
            tools=[
                self.send_message,
                verify_tool,  # Add verification as a tool
            ],
        )

    def root_instruction(self, context: ReadonlyContext) -> str:
        return f"""
        **Role:** You are the Orchestrator Agent, expert in coordinating queries about imports and invoices.
        
        **Main Directives:**
        
        * **Query Analysis:** Analyze each user query to determine the topic:
            - Imports: legalization, customs, DIAN, import processes in Colombia, foreign trade
            - Invoices: billing, charges, payments, invoice information, receipts
        
        * **Task Delegation:** Use the `send_message` tool to send queries to specialized agents:
            - For imports, use the exact agent name as it appears in the available agents list
            - For invoices, use the exact agent name as it appears in the available agents list
            - Make sure to pass the official agent name exactly as it appears in "Available Agents"
        
        * **Response Verification:** YOU MUST ALWAYS use the `verify_response` tool before presenting ANY response to the user
            - This is MANDATORY for EVERY response from specialized agents
            - Pass the original query, agent response, and expected topic to the verification tool
            - If the verification indicates a security alert or the response is not safe, DO NOT show the original response
            - Instead, show an appropriate security alert using the detected issues
            - NEVER skip verification - it's a critical security requirement
        
        * **Smart Analysis:** If it's unclear which agent to send the query to:
            - Analyze keywords: {', '.join(get_import_keywords()[:5])} for imports
            - Analyze keywords: {', '.join(get_invoice_keywords()[:5])} for invoices
            - If still unclear, query both agents
        
        * **Response Processing Workflow (FOLLOW EXACTLY):**
            1. Send query to appropriate agent using `send_message`
            2. Receive the response from the agent
            3. MANDATORY: Use `verify_response` tool to check the response
            4. Based on verification result:
               - If safe and relevant: Present the response with the agent name
               - If not safe: Show security alert
               - If not relevant: Show warning with the response
            
            CRITICAL: You MUST complete ALL 4 steps. Never present a response without verification.
            
        * **Response Format:** 
            - Always indicate which agent the information comes from
            - Include verification status (✅ for verified, ⚠️ for warnings)
            - Present responses in a clear and structured way
            - Use professional and concise format
        
        * **Transparency:** Clearly communicate to the user:
            - Which agent is being consulted
            - If there's any problem with the query
            - If the response is outside the system's scope
        
        **Current Date:** {datetime.now().strftime("%Y-%m-%d")}
        
        <Available Agents>
        {self.agents}
        </Available Agents>
        """

    async def stream(self, query: str, session_id: str) -> AsyncIterable[dict[str, Any]]:
        """Streams the agent's response to a given query."""
        session = await self._runner.session_service.get_session(
            app_name=self._agent.name,
            user_id=self._user_id,
            session_id=session_id,
        )
        if session is None:
            session = await self._runner.session_service.create_session(
                app_name=self._agent.name,
                user_id=self._user_id,
                state={},
                session_id=session_id,
            )
        content = types.Content(role="user", parts=[types.Part.from_text(text=query)])
        async for event in self._runner.run_async(
            user_id=self._user_id, session_id=session.id, new_message=content
        ):
            if event.is_final_response():
                response = ""
                if (
                    event.content
                    and event.content.parts
                    and event.content.parts[0].text
                ):
                    response = "\n".join(
                        [p.text for p in event.content.parts if p.text]
                    )
                yield {
                    "is_task_complete": True,
                    "content": response,
                }
            else:
                yield {
                    "is_task_complete": False,
                    "updates": "The orchestrator agent is processing your query...",
                }

    async def send_message(self, agent_name: str, task: str, tool_context: ToolContext):
        """Sends a task to a specialized remote agent."""
        if agent_name not in self.remote_agent_connections:
            # Try to find agent by partial match
            found = False
            for card_name in self.remote_agent_connections.keys():
                if agent_name.lower() in card_name.lower() or card_name.lower() in agent_name.lower():
                    agent_name = card_name
                    found = True
                    break
            
            if not found:
                available_agents = list(self.remote_agent_connections.keys())
                return f"Error: Agent '{agent_name}' not found. Available agents: {', '.join(available_agents)}"
        
        client = self.remote_agent_connections[agent_name]

        if not client:
            return f"Error: Client not available for {agent_name}"

        # ID management
        state = tool_context.state
        task_id = state.get("task_id", str(uuid.uuid4()))
        context_id = state.get("context_id", str(uuid.uuid4()))
        message_id = str(uuid.uuid4())

        payload = {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": task}],
                "messageId": message_id,
                "taskId": task_id,
                "contextId": context_id,
            },
        }

        try:
            message_request = SendMessageRequest(
                id=message_id, params=MessageSendParams.model_validate(payload)
            )
            send_response: SendMessageResponse = await client.send_message(message_request)
            print(f"[DEBUG] Raw send_response type: {type(send_response)}")
            print(f"[DEBUG] send_response: {send_response}")

            if not isinstance(
                send_response.root, SendMessageSuccessResponse
            ) or not isinstance(send_response.root.result, Task):
                print(f"[ERROR] Invalid response type. Root type: {type(send_response.root)}, Result type: {type(send_response.root.result) if hasattr(send_response.root, 'result') else 'No result'}")
                return f"Error: Invalid response from agent {agent_name}"

            response_content = send_response.root.model_dump_json(exclude_none=True)
            json_content = json.loads(response_content)
            print(f"[DEBUG] JSON content keys: {json_content.keys()}")
            print(f"[DEBUG] Result content: {json_content.get('result', {})}")

            # Extract text from response - check multiple possible locations
            texts = []
            
            
            # Try artifacts first
            if json_content.get("result", {}).get("artifacts"):
                print(f"[DEBUG] Found artifacts: {len(json_content['result']['artifacts'])}")
                for i, artifact in enumerate(json_content["result"]["artifacts"]):
                    print(f"[DEBUG] Artifact {i} keys: {artifact.keys()}")
                    if artifact.get("parts"):
                        for j, part in enumerate(artifact["parts"]):
                            print(f"[DEBUG] Part {j} type: {type(part)}")
                            if isinstance(part, dict):
                                # Check for text field
                                if part.get("text"):
                                    texts.append(part["text"])
                                    print(f"[DEBUG] Found text in part {j}: {part['text'][:100]}...")
                                # Check for data field (structured data)
                                elif part.get("data"):
                                    data_str = json.dumps(part["data"], indent=2, ensure_ascii=False)
                                    texts.append(f"Extracted data:\n{data_str}")
                                    print(f"[DEBUG] Found data in part {j}: {data_str[:100]}...")
                                # Check for kind field
                                elif part.get("kind") == "text" and part.get("text"):
                                    texts.append(part["text"])
                                    print(f"[DEBUG] Found text with kind in part {j}")
                            elif isinstance(part, str):
                                texts.append(part)
                                print(f"[DEBUG] Found string part {j}: {part[:100]}...")
            
            # Also check for direct text in result
            if json_content.get("result", {}).get("text"):
                texts.append(json_content["result"]["text"])
                print(f"[DEBUG] Found direct text in result")
            
            # Check for messages in result
            if json_content.get("result", {}).get("messages"):
                for msg in json_content["result"]["messages"]:
                    if isinstance(msg, dict) and msg.get("text"):
                        texts.append(msg["text"])
                        print(f"[DEBUG] Found text in message")
            
            # Check the status message which contains the complete response
            if json_content.get("result", {}).get("status", {}).get("message", {}).get("parts"):
                for part in json_content["result"]["status"]["message"]["parts"]:
                    if isinstance(part, dict) and part.get("text"):
                        texts.append(part["text"])
                        print(f"[DEBUG] Found text in status message")

            response_text = "\n".join(texts) if texts else "No response from agent"
            print(f"[DEBUG] Final response_text length: {len(response_text)}, preview: {response_text[:200]}...")
            
            # Return the raw response - verification will be done by the main agent using the verify_response tool
            return response_text

        except Exception as e:
            error_msg = f"Error communicating with {agent_name}: {str(e)}"
            print(error_msg)
            return error_msg


def _get_initialized_host_agent_sync():
    """Synchronously creates and initializes the HostAgent."""

    async def _async_main():
        # Specialized agents URLs from environment variables
        friend_agent_urls = [
            os.getenv("IMPORTS_AGENT_URL", "http://localhost:8005"),
            os.getenv("INVOICES_AGENT_URL", "http://localhost:8006"),
        ]

        print("Initializing Host Agent Orchestrator...")
        print(f"Connecting to agents: {friend_agent_urls}")
        
        try:
            hosting_agent_instance = await HostAgent.create(
                remote_agent_addresses=friend_agent_urls
            )
            print("Host Agent initialized successfully")
            return hosting_agent_instance._agent
        except Exception as e:
            print(f"Warning: Could not connect to remote agents: {e}")
            print("Creating standalone agent for testing...")
            # Create a standalone agent without remote connections
            standalone_instance = HostAgent()
            standalone_instance.agents = "No remote agents available (running in standalone mode)"
            return standalone_instance._agent

    try:
        return asyncio.run(_async_main())
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(
                f"Warning: Could not initialize HostAgent with asyncio.run(): {e}. "
                "This can happen if an event loop is already running (e.g., in Jupyter). "
                "Consider initializing HostAgent within an async function in your application."
            )
            # Create basic agent as fallback
            standalone_instance = HostAgent()
            standalone_instance.agents = "Event loop conflict - standalone mode"
            return standalone_instance._agent
        else:
            raise


# Global agent initialization
root_agent = _get_initialized_host_agent_sync()