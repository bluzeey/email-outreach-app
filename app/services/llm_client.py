"""Unified LLM client with Fireworks primary and OpenAI fallback."""

from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class UnifiedLLMClient:
    """
    Unified LLM client supporting Fireworks AI (primary) and OpenAI (fallback).
    
    Usage:
        client = UnifiedLLMClient(temperature=0.7)
        response = await client.ainvoke(messages)  # Auto-fallback on failure
    
    Configuration:
        - Set LLM_PROVIDER="fireworks" (default) to use Fireworks with OpenAI fallback
        - Set LLM_PROVIDER="openai" to use OpenAI directly
    """
    
    def __init__(self, temperature: float = 0.7):
        self.temperature = temperature
        self.primary_client = None
        self.fallback_client = None
        self.provider = settings.LLM_PROVIDER
        
        self._init_clients()
    
    def _init_clients(self):
        """Initialize clients based on LLM_PROVIDER setting."""
        logger.info(f"Initializing LLM clients with provider: {self.provider}")
        
        if self.provider == "fireworks":
            self._init_fireworks_primary()
            self._init_openai_fallback()
        elif self.provider == "openai":
            self._init_openai_primary()
        else:
            logger.warning(f"Unknown LLM_PROVIDER: {self.provider}, defaulting to OpenAI")
            self._init_openai_primary()
    
    def _init_fireworks_primary(self) -> bool:
        """Initialize Fireworks client using ChatOpenAI with custom base_url."""
        if not settings.FIREWORKS_API_KEY:
            logger.warning("FIREWORKS_API_KEY not set, cannot initialize Fireworks client")
            return False
        
        try:
            self.primary_client = ChatOpenAI(
                model=settings.FIREWORKS_MODEL,
                api_key=settings.FIREWORKS_API_KEY,
                base_url=settings.FIREWORKS_BASE_URL,
                temperature=self.temperature,
            )
            logger.info(
                f"Fireworks client initialized",
                model=settings.FIREWORKS_MODEL,
                base_url=settings.FIREWORKS_BASE_URL,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Fireworks client: {e}")
            return False
    
    def _init_openai_primary(self) -> bool:
        """Initialize OpenAI as primary client."""
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set, cannot initialize OpenAI client")
            return False
        
        try:
            self.primary_client = ChatOpenAI(
                model=settings.OPENAI_MODEL,
                api_key=settings.OPENAI_API_KEY,
                temperature=self.temperature,
            )
            logger.info(f"OpenAI client initialized (primary)", model=settings.OPENAI_MODEL)
            return True
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            return False
    
    def _init_openai_fallback(self) -> bool:
        """Initialize OpenAI as fallback client."""
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set, no fallback available")
            return False
        
        try:
            self.fallback_client = ChatOpenAI(
                model=settings.OPENAI_MODEL,
                api_key=settings.OPENAI_API_KEY,
                temperature=self.temperature,
            )
            logger.info(f"OpenAI client initialized (fallback)", model=settings.OPENAI_MODEL)
            return True
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI fallback: {e}")
            return False
    
    async def ainvoke(self, messages: list) -> Any:
        """
        Invoke LLM with automatic fallback to OpenAI on failure.
        
        Flow:
            1. Try primary client (Fireworks or OpenAI based on config)
            2. If fails and fallback available (when Fireworks is primary), try OpenAI
            3. If both fail, raise exception
        
        Args:
            messages: List of LangChain message objects
            
        Returns:
            LLM response
            
        Raises:
            RuntimeError: If no client available or both clients fail
        """
        # Try primary client first
        if self.primary_client:
            try:
                logger.debug(f"Calling primary LLM ({self.provider})")
                return await self.primary_client.ainvoke(messages)
            except Exception as e:
                logger.warning(
                    f"Primary LLM failed ({self.provider}): {e}",
                    error_type=type(e).__name__,
                )
        else:
            logger.warning(f"Primary client not initialized for provider: {self.provider}")
        
        # Try fallback for this specific request (only when Fireworks is primary)
        if self.fallback_client and self.provider == "fireworks":
            logger.info("Falling back to OpenAI for this request")
            try:
                return await self.fallback_client.ainvoke(messages)
            except Exception as e:
                logger.error(f"Fallback LLM (OpenAI) also failed: {e}")
                raise RuntimeError(f"Both primary (Fireworks) and fallback (OpenAI) failed: {e}")
        
        # No fallback available or primary was OpenAI and failed
        raise RuntimeError(f"LLM invocation failed and no fallback available")
    
    def is_available(self) -> bool:
        """Check if any LLM client is available."""
        return self.primary_client is not None or self.fallback_client is not None
    
    def get_provider_info(self) -> dict:
        """Get information about configured providers."""
        return {
            "provider": self.provider,
            "primary_available": self.primary_client is not None,
            "fallback_available": self.fallback_client is not None,
            "primary_model": settings.FIREWORKS_MODEL if self.provider == "fireworks" else settings.OPENAI_MODEL,
            "fallback_model": settings.OPENAI_MODEL if self.provider == "fireworks" else None,
        }
