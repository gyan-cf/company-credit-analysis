"""
OpenAI API client wrapper.
"""

import json
import os
import time
from typing import Dict, Any, Optional
from openai import OpenAI
from config.config import get_config


class OpenAIClient:
    """OpenAI API client."""
    
    def __init__(self, api_key: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        Initialize OpenAI client.
        
        Args:
            api_key: OpenAI API key (if None, will use config)
            config: Configuration dictionary (if None, will load from config)
        """
        if config is None:
            cfg = get_config()
            openai_config = cfg.get_openai_config()
            # Prioritize environment variable, then provided api_key, then config
            self.api_key = api_key or os.getenv('OPENAI_API_KEY') or openai_config.get('api_key', '')
            self.model = openai_config.get('model', 'gpt-4o')
            self.temperature = openai_config.get('temperature', 0.3)
            self.max_tokens = openai_config.get('max_tokens', 4000)
            self.timeout = openai_config.get('timeout', 60)
            self.analysis_config = cfg.get_analysis_config()
        else:
            self.api_key = api_key or os.getenv('OPENAI_API_KEY') or config.get('api_key', '')
            self.model = config.get('model', 'gpt-4o')
            self.temperature = config.get('temperature', 0.3)
            self.max_tokens = config.get('max_tokens', 4000)
            self.timeout = config.get('timeout', 60)
            self.analysis_config = config.get('analysis', {})
        
        if not self.api_key:
            raise ValueError("OpenAI API key not provided. Set OPENAI_API_KEY environment variable or provide api_key parameter.")
        
        self.client = OpenAI(api_key=self.api_key, timeout=self.timeout)
    
    def analyze(self, prompt: str, max_retries: Optional[int] = None, 
               retry_delay: Optional[int] = None) -> Dict[str, Any]:
        """
        Call OpenAI API with prompt and return response.
        
        Args:
            prompt: Complete prompt string
            max_retries: Maximum number of retries (default from config)
            retry_delay: Delay between retries in seconds (default from config)
            
        Returns:
            Dictionary with 'content' (response text) and 'usage' (token usage)
        """
        max_retries = max_retries or self.analysis_config.get('max_retries', 3)
        retry_delay = retry_delay or self.analysis_config.get('retry_delay', 2)
        
        # Check model type outside try block for use in error handling
        is_o3_model = "o3" in self.model.lower()
        use_max_completion_tokens = "gpt-5" in self.model.lower() or is_o3_model
        
        for attempt in range(max_retries + 1):
            try:
                # Check if prompt already contains JSON instructions
                # OpenAI JSON mode requires explicit JSON request in the prompt
                system_message = "You are a credit risk analysis assistant. Always respond with valid JSON only, no additional text."
                
                # Ensure prompt ends with JSON request if using JSON mode
                user_prompt = prompt
                if "Return ONLY valid JSON" not in prompt and "return the memo JSON" not in prompt.lower():
                    user_prompt = prompt + "\n\nIMPORTANT: Return ONLY valid JSON, no markdown, no additional text."
                
                create_params = {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_message
                        },
                        {
                            "role": "user",
                            "content": user_prompt
                        }
                    ],
                    "response_format": {"type": "json_object"}  # Force JSON response
                }
                
                # Only add temperature if model supports it (o3 models don't)
                if not is_o3_model:
                    create_params["temperature"] = self.temperature
                
                # Use appropriate parameter based on model
                if use_max_completion_tokens:
                    create_params["max_completion_tokens"] = self.max_tokens
                else:
                    create_params["max_tokens"] = self.max_tokens
                
                response = self.client.chat.completions.create(**create_params)
                
                # Extract content
                content = ""
                if response.choices and len(response.choices) > 0:
                    choice = response.choices[0]
                    if choice.message and choice.message.content:
                        content = choice.message.content
                
                # Extract usage
                usage = {
                    'input_tokens': response.usage.prompt_tokens if response.usage else 0,
                    'output_tokens': response.usage.completion_tokens if response.usage else 0,
                    'total_tokens': response.usage.total_tokens if response.usage else 0
                }
                
                return {
                    'content': content,
                    'usage': usage,
                    'success': True
                }
            
            except Exception as e:
                error_str = str(e)
                
                # Check for unsupported parameter errors (common with o3 models)
                if 'unsupported_parameter' in error_str.lower() or 'unsupported parameter' in error_str.lower():
                    # Try to extract the parameter name
                    import re
                    param_match = re.search(r"parameter[:\s]+['\"]?(\w+)['\"]?", error_str, re.IGNORECASE)
                    if param_match:
                        param_name = param_match.group(1)
                        print(f"Warning: Parameter '{param_name}' is not supported by model '{self.model}'. Removing it and retrying...")
                        # Remove the unsupported parameter and retry once
                        if param_name.lower() == 'temperature' and is_o3_model:
                            # Already handled, but log it
                            pass
                        # For other unsupported parameters, we'd need to handle them individually
                        # For now, just report the error
                
                if attempt < max_retries:
                    print(f"Attempt {attempt + 1} failed: {error_str}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    return {
                        'content': '',
                        'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0},
                        'success': False,
                        'error': error_str
                    }
        
        return {
            'content': '',
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0},
            'success': False,
            'error': 'Max retries exceeded'
        }
    
    def parse_json_response(self, response_content: str) -> Dict[str, Any]:
        """
        Parse JSON from OpenAI response content.
        
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
        # OpenAI may wrap JSON in markdown code blocks or add text
        
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
        client = OpenAIClient()
        print("OpenAI client initialized successfully")
        print(f"Model: {client.model}")
        print(f"Temperature: {client.temperature}")
        print(f"Max tokens: {client.max_tokens}")
    except ValueError as e:
        print(f"Error: {e}")
        print("Set OPENAI_API_KEY environment variable to test API calls")

