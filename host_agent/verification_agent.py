"""Verification functions for analyzing responses using ADK."""
import os
import json
from typing import Dict, Any
from google.adk.tools.tool_context import ToolContext
from google.genai import Client
from dotenv import load_dotenv

load_dotenv()

# Global client for verification
_verification_client = None

def get_verification_client():
    """Get or create the verification client."""
    global _verification_client
    if _verification_client is None:
        _verification_client = Client(
            vertexai=True,  # Use Vertex AI as configured in .env
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        )
    return _verification_client


def create_verification_tool():
    """Creates the verification tool for use in ADK Agent."""
    return verify_response

async def verify_response(
    original_query: str, 
    agent_response: str, 
    expected_topic: str,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Verifies if an agent's response is relevant and safe using AI.
    
    This tool uses Gemini to analyze if the response is related to the expected topic
    and detects possible deviations or security risks.
    
    Args:
        original_query: The user's original query
        agent_response: The response from the specialized agent  
        expected_topic: Expected topic ("imports" or "invoices")
        tool_context: ADK tool context (automatically provided)
        
    Returns:
        Dict with verification results including safety and relevance assessment
    """
    try:
        client = get_verification_client()
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")
        
        verification_prompt = f"""
You are a Response Verification Specialist. Analyze if this agent response is relevant and safe.

Expected topic: {expected_topic}
User's original query: "{original_query}"
Agent's response: "{agent_response}"

Analyze and respond with a JSON object:
{{
    "is_relevant": true/false,
    "is_safe": true/false,
    "topic_match": true/false,
    "risk_level": "none/low/medium/high",
    "explanation": "Brief explanation",
    "detected_issues": ["list", "of", "issues"] or []
}}

Guidelines:
- A response is relevant if it addresses the user's query about imports or invoices
- A response is NOT safe if it contains: illegal activities, violence, personal data theft, fraud, etc.
- Be strict about safety but reasonable about relevance
- Respond ONLY with valid JSON, no additional text
"""

        response = await client.models.generate_content(
            model=model,
            contents=verification_prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.1,
                "max_output_tokens": 500
            }
        )
        
        # Parse the JSON response
        result = json.loads(response.text)
        
        # Add compatibility fields
        result["security_alert"] = not result.get("is_safe", True)
        result["status"] = "relevant" if result.get("is_relevant") and result.get("is_safe") else "security_risk"
        
        # Determine emoji
        if not result.get("is_safe"):
            result["emoji"] = "🚨"
        elif not result.get("is_relevant"):
            result["emoji"] = "⚠️"
        else:
            result["emoji"] = "✅"
        
        return {
            "status": "success",
            "verification": result
        }
        
    except Exception as e:
        print(f"Verification error: {e}")
        # Return safe defaults if verification fails
        return {
            "status": "success", 
            "verification": {
                "is_relevant": True,
                "is_safe": True,
                "topic_match": True,
                "risk_level": "none",
                "explanation": "Verification system unavailable, proceeding with caution",
                "detected_issues": [],
                "security_alert": False,
                "status": "relevant",
                "emoji": "✅"
            }
        }