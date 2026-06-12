"""
Configuration management for the analysis framework.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Configuration manager."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.
        
        Args:
            config_path: Path to config.yaml file (default: config.yaml in current directory)
        """
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._resolve_env_vars()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            # Return default config
            return self._get_default_config()
        
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        return config or self._get_default_config()
    
    def _resolve_env_vars(self):
        """Resolve environment variables in config."""
        # Resolve Anthropic API key
        api_key = self.config.get('anthropic', {}).get('api_key', '')
        if api_key.startswith('${') and api_key.endswith('}'):
            env_var = api_key[2:-1]
            self.config['anthropic']['api_key'] = os.getenv(env_var, '')
        
        # Resolve OpenAI API key
        openai_api_key = self.config.get('openai', {}).get('api_key', '')
        if openai_api_key.startswith('${') and openai_api_key.endswith('}'):
            env_var = openai_api_key[2:-1]
            self.config['openai']['api_key'] = os.getenv(env_var, '')
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            'llm_provider': 'openai',  # Default to OpenAI
            'anthropic': {
                'api_key': os.getenv('ANTHROPIC_API_KEY', ''),
                'model': 'claude-3-haiku-20240307',
                'temperature': 0.2,
                'max_tokens': 16000,
                'timeout': 60
            },
            'openai': {
                'api_key': os.getenv('OPENAI_API_KEY', ''),
                'model': 'gpt-4o',
                'temperature': 0.2,
                'max_tokens': 16000,
                'timeout': 60
            },
            'rbi_out_of_order_proxy': {
                'return_rate_alert': 0.05
            },
            'portfolio_norms': {
                'top3_high': 0.60,
                'cash_share_alert': 0.25,
                'adb_floor_inr': 100000,
                'credit_gap_days_alert': 30
            },
            'industry_context': 'generic',
            'default_meta': {
                'borrower_name': 'Unknown',
                'bank_name': 'Unknown',
                'industry_hint': 'generic',
                'location': ''
            },
            'analysis': {
                'enable_retry': True,
                'max_retries': 3,
                'retry_delay': 2,
                'validate_schema': True
            },
            'output': {
                'save_json': True,
                'save_markdown': True,
                'output_dir': 'output',
                'include_evidence_map': True
            }
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports dot notation)."""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value
    
    def get_anthropic_config(self) -> Dict[str, Any]:
        """Get Anthropic API configuration."""
        return self.config.get('anthropic', {})
    
    def get_openai_config(self) -> Dict[str, Any]:
        """Get OpenAI API configuration."""
        return self.config.get('openai', {})
    
    def get_policy_context(self) -> Dict[str, Any]:
        """Get policy context for prompts."""
        return {
            'rbi_out_of_order_proxy': self.config.get('rbi_out_of_order_proxy', {}),
            'portfolio_norms': self.config.get('portfolio_norms', {}),
            'industry_context': self.config.get('industry_context', 'generic')
        }
    
    def get_default_meta(self) -> Dict[str, Any]:
        """Get default metadata."""
        return self.config.get('default_meta', {})
    
    def get_analysis_config(self) -> Dict[str, Any]:
        """Get analysis configuration."""
        return self.config.get('analysis', {})
    
    def get_output_config(self) -> Dict[str, Any]:
        """Get output configuration."""
        return self.config.get('output', {})


# Global config instance
_config_instance: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """Get global config instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_path)
    return _config_instance


if __name__ == "__main__":
    # Test config loading
    config = get_config()
    print("Anthropic Config:", config.get_anthropic_config())
    print("Policy Context:", config.get_policy_context())
    print("API Key set:", bool(config.get('anthropic.api_key')))

