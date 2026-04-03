"""Tests for unified LLM client."""

import pytest
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.services.llm_client import UnifiedLLMClient


class TestUnifiedLLMClient:
    """Tests for UnifiedLLMClient."""
    
    @patch("app.services.llm_client.settings")
    def test_init_fireworks_primary(self, mock_settings):
        """Test Fireworks as primary with OpenAI fallback."""
        mock_settings.LLM_PROVIDER = "fireworks"
        mock_settings.FIREWORKS_API_KEY = "fw-test-key"
        mock_settings.FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
        mock_settings.FIREWORKS_MODEL = "accounts/fireworks/routers/kimi-k2p5-turbo"
        mock_settings.OPENAI_API_KEY = "oa-test-key"
        mock_settings.OPENAI_MODEL = "gpt-4"
        
        with patch("app.services.llm_client.ChatOpenAI") as mock_chat:
            client = UnifiedLLMClient(temperature=0.7)
            
            # Should initialize Fireworks as primary
            assert client.provider == "fireworks"
            assert client.primary_client is not None
            assert client.fallback_client is not None
            
            # Verify Fireworks was called with correct params
            calls = mock_chat.call_args_list
            assert len(calls) >= 1
            
            # First call should be Fireworks
            fireworks_call = calls[0]
            assert fireworks_call.kwargs.get("api_key") == "fw-test-key"
            assert fireworks_call.kwargs.get("base_url") == "https://api.fireworks.ai/inference/v1"
            assert fireworks_call.kwargs.get("model") == "accounts/fireworks/routers/kimi-k2p5-turbo"
    
    @patch("app.services.llm_client.settings")
    def test_init_openai_primary(self, mock_settings):
        """Test OpenAI as primary without fallback."""
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.OPENAI_API_KEY = "oa-test-key"
        mock_settings.OPENAI_MODEL = "gpt-4"
        mock_settings.FIREWORKS_API_KEY = ""
        
        with patch("app.services.llm_client.ChatOpenAI") as mock_chat:
            client = UnifiedLLMClient(temperature=0.7)
            
            assert client.provider == "openai"
            assert client.primary_client is not None
            assert client.fallback_client is None  # No fallback for OpenAI
    
    @patch("app.services.llm_client.settings")
    def test_init_no_api_keys(self, mock_settings):
        """Test initialization with no API keys."""
        mock_settings.LLM_PROVIDER = "fireworks"
        mock_settings.FIREWORKS_API_KEY = ""
        mock_settings.OPENAI_API_KEY = ""
        
        client = UnifiedLLMClient(temperature=0.7)
        
        assert client.primary_client is None
        assert client.fallback_client is None
        assert not client.is_available()
    
    @patch("app.services.llm_client.settings")
    def test_get_provider_info_fireworks(self, mock_settings):
        """Test provider info with Fireworks config."""
        mock_settings.LLM_PROVIDER = "fireworks"
        mock_settings.FIREWORKS_API_KEY = "fw-key"
        mock_settings.FIREWORKS_MODEL = "accounts/fireworks/routers/kimi-k2p5-turbo"
        mock_settings.OPENAI_API_KEY = "oa-key"
        mock_settings.OPENAI_MODEL = "gpt-4"
        
        with patch("app.services.llm_client.ChatOpenAI"):
            client = UnifiedLLMClient(temperature=0.7)
            info = client.get_provider_info()
            
            assert info["provider"] == "fireworks"
            assert info["primary_model"] == "accounts/fireworks/routers/kimi-k2p5-turbo"
            assert info["fallback_model"] == "gpt-4"
    
    @pytest.mark.asyncio
    @patch("app.services.llm_client.settings")
    async def test_ainvoke_fallback_on_failure(self, mock_settings):
        """Test fallback to OpenAI when Fireworks fails."""
        mock_settings.LLM_PROVIDER = "fireworks"
        mock_settings.FIREWORKS_API_KEY = "fw-key"
        mock_settings.OPENAI_API_KEY = "oa-key"
        
        with patch("app.services.llm_client.ChatOpenAI") as mock_chat:
            # Setup mock clients
            mock_primary = MagicMock()
            mock_fallback = MagicMock()
            
            # Primary fails, fallback succeeds
            mock_primary.ainvoke.side_effect = Exception("Fireworks rate limit")
            mock_fallback.ainvoke.return_value = MagicMock(content="Fallback response")
            
            mock_chat.side_effect = [mock_primary, mock_fallback]
            
            client = UnifiedLLMClient(temperature=0.7)
            
            from langchain.schema import HumanMessage
            messages = [HumanMessage(content="Test prompt")]
            
            response = await client.ainvoke(messages)
            
            # Should get fallback response
            assert response.content == "Fallback response"
            
            # Verify fallback was called
            mock_fallback.ainvoke.assert_called_once()
