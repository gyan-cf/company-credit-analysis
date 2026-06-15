"""
Anthropic Claude API client wrapper.
"""

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

from anthropic import Anthropic

from config.config import get_config


class ClaudeClient:
    """Anthropic Claude API client."""
    
    def __init__(self, api_key: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Claude client.
        
        Args:
            api_key: Anthropic API key (if None, will use config)
            config: Configuration dictionary (if None, will load from config)
        """
        if config is None:
            cfg = get_config()
            anthropic_config = cfg.get_anthropic_config()
            # Prioritize environment variable, then provided api_key, then config
            self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY') or anthropic_config.get('api_key', '')
            self.model = anthropic_config.get('model', 'claude-3-haiku-20240307')
            self.temperature = anthropic_config.get('temperature', 0.3)
            self.max_tokens = anthropic_config.get('max_tokens', 4000)
            self.timeout = anthropic_config.get('timeout', 60)
            self.analysis_config = cfg.get_analysis_config()
        else:
            self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY') or config.get('api_key', '')
            self.model = config.get('model', 'claude-3-haiku-20240307')
            self.temperature = config.get('temperature', 0.3)
            self.max_tokens = config.get('max_tokens', 4000)
            self.timeout = config.get('timeout', 60)
            self.analysis_config = config.get('analysis', {})
        
        if not self.api_key:
            raise ValueError("Anthropic API key not provided. Set ANTHROPIC_API_KEY environment variable or provide api_key parameter.")
        
        self.client = Anthropic(api_key=self.api_key, timeout=self.timeout)
    
    def analyze(self, prompt: str, max_retries: Optional[int] = None, 
               retry_delay: Optional[int] = None) -> Dict[str, Any]:
        """
        Call Claude API with prompt and return response.
        
        Args:
            prompt: Complete prompt string
            max_retries: Maximum number of retries (default from config)
            retry_delay: Delay between retries in seconds (default from config)
            
        Returns:
            Dictionary with 'content' (response text) and 'usage' (token usage)
        """
        max_retries = max_retries or self.analysis_config.get('max_retries', 3)
        retry_delay = retry_delay or self.analysis_config.get('retry_delay', 2)
        
        for attempt in range(max_retries + 1):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                )
                
                # Extract content
                content = ""
                if response.content:
                    if isinstance(response.content, list):
                        content = "".join([block.text for block in response.content if hasattr(block, 'text')])
                    else:
                        content = str(response.content)
                
                # Extract usage
                usage = {
                    'input_tokens': response.usage.input_tokens if hasattr(response, 'usage') else 0,
                    'output_tokens': response.usage.output_tokens if hasattr(response, 'usage') else 0
                }
                
                return {
                    'content': content,
                    'usage': usage,
                    'success': True
                }
            
            except Exception as e:
                if attempt < max_retries:
                    print(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    return {
                        'content': '',
                        'usage': {'input_tokens': 0, 'output_tokens': 0},
                        'success': False,
                        'error': str(e)
                    }
        
        return {
            'content': '',
            'usage': {'input_tokens': 0, 'output_tokens': 0},
            'success': False,
            'error': 'Max retries exceeded'
        }
    
    def stream_message(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        One streamed round-trip with optional tool-use.

        Yields events:
            {"type": "text_delta", "text": str}
            {"type": "message_complete",
             "stop_reason": str,
             "content": list,         # raw content blocks as dicts
             "tool_uses": list,       # convenience: [{id, name, input}]
             "text": str,             # concatenated assistant text
             "usage": {input_tokens, output_tokens}}
            {"type": "error", "error": str}

        This method does NOT loop on tool_use — the caller (agent loop)
        decides whether to dispatch tools and continue with a follow-up call.
        """
        kwargs: Dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            with self.client.messages.stream(**kwargs) as stream:
                for text_chunk in stream.text_stream:
                    if text_chunk:
                        yield {"type": "text_delta", "text": text_chunk}
                final = stream.get_final_message()
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
            return

        content_blocks: List[Dict[str, Any]] = []
        tool_uses: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        for block in (final.content or []):
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text = getattr(block, "text", "") or ""
                text_parts.append(text)
                content_blocks.append({"type": "text", "text": text})
            elif block_type == "tool_use":
                tu = {
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}) or {},
                }
                tool_uses.append(tu)
                content_blocks.append({"type": "tool_use", **tu})
            else:
                # Forward unknown blocks verbatim where possible.
                try:
                    content_blocks.append(block.model_dump())
                except Exception:
                    content_blocks.append({"type": block_type or "unknown"})

        usage = {}
        if getattr(final, "usage", None):
            usage = {
                "input_tokens": getattr(final.usage, "input_tokens", 0),
                "output_tokens": getattr(final.usage, "output_tokens", 0),
            }

        yield {
            "type": "message_complete",
            "stop_reason": getattr(final, "stop_reason", None),
            "content": content_blocks,
            "tool_uses": tool_uses,
            "text": "".join(text_parts),
            "usage": usage,
        }

    def parse_json_response(self, response_content: str) -> Dict[str, Any]:
        """
        Parse JSON from Claude response content.
        
        Args:
            response_content: Response content string
            
        Returns:
            Parsed JSON dictionary
        """
        # Store original content for error reporting
        original_content = response_content
        
        # Check if content is empty
        if not response_content or not response_content.strip():
            return {
                'error': 'Empty response content received',
                'raw_content': '',
                '_truncated': False
            }
        
        # Try to extract JSON from response
        # Claude may wrap JSON in markdown code blocks or add text
        
        # Remove markdown code blocks if present
        if '```json' in response_content:
            start = response_content.find('```json') + 7
            end = response_content.find('```', start)
            if end != -1:
                response_content = response_content[start:end].strip()
        elif '```' in response_content:
            start = response_content.find('```') + 3
            end = response_content.find('```', start)
            if end != -1:
                response_content = response_content[start:end].strip()
        
        # Try to find JSON object
        start_idx = response_content.find('{')
        end_idx = response_content.rfind('}')
        
        # Check if response might be truncated (ends abruptly without closing brace)
        is_truncated = False
        if start_idx != -1:
            # Count opening and closing braces to detect truncation
            open_braces = response_content.count('{')
            close_braces = response_content.count('}')
            if open_braces > close_braces:
                is_truncated = True
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = response_content[start_idx:end_idx + 1]
            try:
                parsed = json.loads(json_str)
                if is_truncated:
                    parsed['_truncated'] = True
                    parsed['_warning'] = 'Response may be truncated due to token limit'
                return parsed
            except json.JSONDecodeError as e:
                # If parsing fails, try to recover partial JSON
                if is_truncated:
                    return {
                        'error': f'Failed to parse JSON from truncated response: {str(e)}',
                        'raw_content': original_content[:2000],  # Show more content for debugging
                        '_truncated': True,
                        '_partial_content': response_content[:1000]
                    }
        
        # If no JSON found, try parsing entire content
        try:
            return json.loads(response_content)
        except json.JSONDecodeError as e:
            return {
                'error': f'Failed to parse JSON from response: {str(e)}',
                'raw_content': original_content[:2000],  # Show more content for debugging
                '_truncated': is_truncated,
                '_content_length': len(original_content)
            }


if __name__ == "__main__":
    # Test client initialization
    try:
        client = ClaudeClient()
        print("Claude client initialized successfully")
        print(f"Model: {client.model}")
        print(f"Temperature: {client.temperature}")
        print(f"Max tokens: {client.max_tokens}")
    except ValueError as e:
        print(f"Error: {e}")
        print("Set ANTHROPIC_API_KEY environment variable to test API calls")

