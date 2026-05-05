"""
Conversational RAG Agent using AWS Bedrock Knowledge Base (backed by Aurora PGVector).
Uses OpenAI GPT-4o for reasoning.
"""
import re
import asyncio
from typing import AsyncGenerator, Optional
from strands import Agent
from strands.models.openai import OpenAIModel
from app.core.config import get_settings
from app.services.session_manager import SessionManager
from app.core.logger import get_logger

# Import refactored logic
from app.core.prompts import SYSTEM_PROMPT, NON_UNDERWRITER_POLICY
from app.services.bedrock_retriever import search_manuals, get_last_retrieval_sources
from app.utils.hooks import RoleBasedOutputHook

logger = get_logger(__name__)


def _normalize_question(text: str) -> str:
    """Normalize question text for stable dedup checks."""
    if not text:
        return ""
    normalized = text.strip().lower()
    normalized = re.sub(r"^\d+\.\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return normalized


def _extract_followups_from_assistant_message(content: str) -> list[str]:
    """Extract historical follow-up questions from assistant responses."""
    if not content:
        return []
    fu_pattern = r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}"
    if not re.search(fu_pattern, content):
        return []
    section = re.split(fu_pattern, content, maxsplit=1)[1]
    matches = re.findall(r"\d+\.\s*(.+)", section)
    return [m.strip() for m in matches if m.strip()]


class BedrockKBAgent:
    """Strands Agent with OpenAI LLM and Managed AWS Bedrock Knowledge Base (Aurora)."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self.agents: dict[tuple[str, str], Agent] = {}
        self.settings = get_settings()
        if not self.settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required")
        logger.info("bedrock_kb_agent_initialized", kb_id=self.settings.bedrock_kb_id)

    def _build_agent(self, model: OpenAIModel, role_key: str) -> Agent:
        # URL/source blocking policy temporarily disabled for all roles.
        # role_policy = NON_UNDERWRITER_POLICY if role_key != "underwriter" else ""
        role_policy = ""
        prompt = f"{SYSTEM_PROMPT}\n\n{role_policy}".strip()

        # Build with hook provider when supported by installed Strands SDK.
        hook_provider: Optional[RoleBasedOutputHook] = None
        try:
            hook_provider = RoleBasedOutputHook(role_key)
            return Agent(
                model=model,
                system_prompt=prompt,
                tools=[search_manuals],
                hooks=[hook_provider],
            )
        except TypeError:
            logger.warning("agent_hooks_not_supported_fallback")
            return Agent(
                model=model,
                system_prompt=prompt,
                tools=[search_manuals],
            )

    def _get_or_create_agent(self, session_id: str, role: str) -> Agent:
        role_key = (role or "").strip().lower()
        cache_key = (session_id, role_key)
        if cache_key not in self.agents:
            # Initialize OpenAI model
            model = OpenAIModel(
                client_args={
                    "api_key": self.settings.openai_api_key,
                },
                model_id=self.settings.openai_chat_model,
                params={
                    "temperature": 0,
                    "max_tokens": 2048
                }
            )
            
            self.agents[cache_key] = self._build_agent(model, role_key)
        
        return self.agents[cache_key]

    async def query(
        self,
        session_id: str,
        query: str,
        role: str,
        top_k: int = 5
    ) -> AsyncGenerator[tuple[str, list[str], list[str]], None]:
        """Stream a query within a conversation session."""
        logger.info("processing_query_stream", session_id=session_id)

        try:
            # Initial state
            yield "🔍 Searching Coaction manuals...", [], []
            
            role_key = (role or "").strip().lower()
            agent = self._get_or_create_agent(session_id, role_key)
            
            # Simulate a small delay for retrieval start to ensure UI updates
            await asyncio.sleep(0.1)
            
            # Add user message to memory
            self.session_manager.add_message(session_id, "user", query)
            
            # Second state
            yield "📝 Analyzing manual content...", [], []
            
            # Execute agent synchronously (Strands call)
            response = agent(query)
            answer = str(response)
            
            # Debug: inspect the response object to find where follow-ups live
            logger.info("strands_response_debug",
                        type=str(type(response)),
                        str_len=len(answer),
                        has_message=hasattr(response, 'message'),
                        has_content=hasattr(response, 'content'),
                        has_text=hasattr(response, 'text'),
                        dir_keys=str([a for a in dir(response) if not a.startswith('_')]),
                        last_200_chars=repr(answer[-200:]) if len(answer) > 200 else repr(answer))
            
            # Extract and Split Follow-up Questions FIRST (before saving to session)
            follow_up_questions = []
            # Use flexible regex to catch formatting variations from the LLM
            fu_pattern = r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}"
            if re.search(fu_pattern, answer):
                parts = re.split(fu_pattern, answer, maxsplit=1)
                answer = parts[0].strip()
                fu_text = parts[1]
                matches = re.findall(r"\d+\.\s*(.+)", fu_text)
                raw_followups = [m.strip() for m in matches if m.strip()]

                # Prevent repeated follow-ups by filtering against user asks
                # and previously suggested follow-up questions in this session.
                history = self.session_manager.get_messages(session_id)
                historical_questions = set()

                for msg in history:
                    role = (msg.get("role") or "").strip().lower()
                    content = msg.get("content") or ""
                    if role == "user":
                        normalized_user_q = _normalize_question(content)
                        if normalized_user_q:
                            historical_questions.add(normalized_user_q)
                    elif role == "assistant":
                        for prev_fu in _extract_followups_from_assistant_message(content):
                            normalized_fu = _normalize_question(prev_fu)
                            if normalized_fu:
                                historical_questions.add(normalized_fu)

                seen_in_this_response = set()
                deduped_followups: list[str] = []
                for question in raw_followups:
                    normalized = _normalize_question(question)
                    if not normalized:
                        continue
                    if normalized in historical_questions or normalized in seen_in_this_response:
                        continue
                    seen_in_this_response.add(normalized)
                    deduped_followups.append(question)
                    if len(deduped_followups) == 3:
                        break

                follow_up_questions = deduped_followups

            # Save the cleaned answer (without follow-ups) to session history
            self.session_manager.add_message(session_id, "assistant", answer)

            # Get source URLs — only include ones the LLM actually cited in the answer
            retrieval_sources = get_last_retrieval_sources()
            all_urls = [s["url"] for s in retrieval_sources if s.get("url") and s["url"] != "N/A"]
            
            # Filter to only URLs that appear in the LLM's answer text
            cited_urls = [url for url in all_urls if url in answer]
            
            # If LLM didn't include any URLs, fall back to the top 3 retrieval sources
            sources = cited_urls if cited_urls else all_urls[:3]

            # Final yield
            logger.info("follow_up_debug", 
                        fu_count=len(follow_up_questions), 
                        follow_ups=follow_up_questions,
                        answer_ends_with=answer[-100:] if answer else "")
            yield answer, sources, follow_up_questions
            
        except Exception as e:
            logger.error("query_failed", session_id=session_id, error=str(e))
            yield f"Error: {str(e)}", [], []
