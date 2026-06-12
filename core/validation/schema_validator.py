"""
JSON schema validation for input and output data.
"""

import json
import jsonschema
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


class SchemaValidator:
    """Validate JSON data against schemas."""
    
    def __init__(self):
        """Initialize validator with schema files."""
        # Schemas are in the root schemas/ directory
        self.schemas_dir = Path(__file__).parent.parent.parent / "schemas"
        self.input_schema = self._load_schema("input_schema.json")
        self.memo_schema = self._load_schema("memo_schema.json")
    
    def _load_schema(self, filename: str) -> Dict[str, Any]:
        """Load JSON schema from file."""
        schema_path = self.schemas_dir / filename
        with open(schema_path, 'r') as f:
            return json.load(f)
    
    def validate_input(self, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate input feature data against input schema.
        
        Args:
            data: Input data dictionary
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            jsonschema.validate(instance=data, schema=self.input_schema)
            return True, None
        except jsonschema.ValidationError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def validate_memo(self, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate memo output against memo schema.
        
        Args:
            data: Memo data dictionary
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            jsonschema.validate(instance=data, schema=self.memo_schema)
            return True, None
        except jsonschema.ValidationError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def validate_with_errors(self, data: Dict[str, Any], schema_type: str = "input") -> Dict[str, Any]:
        """
        Validate data and return detailed error information.
        
        Args:
            data: Data dictionary to validate
            schema_type: "input" or "memo"
            
        Returns:
            Dictionary with validation results
        """
        if schema_type == "input":
            is_valid, error = self.validate_input(data)
        elif schema_type == "memo":
            is_valid, error = self.validate_memo(data)
        else:
            return {
                "valid": False,
                "error": f"Unknown schema type: {schema_type}"
            }
        
        result = {
            "valid": is_valid,
            "error": error
        }
        
        if not is_valid and error:
            # Try to extract more details
            try:
                validator = jsonschema.Draft7Validator(
                    self.input_schema if schema_type == "input" else self.memo_schema
                )
                errors = list(validator.iter_errors(data))
                result["errors"] = [str(e) for e in errors]
            except:
                pass
        
        return result


if __name__ == "__main__":
    # Test validator
    validator = SchemaValidator()
    
    # Test with sample data
    sample_input = {
        "meta": {
            "borrower_name": "Test Borrower",
            "vintage_months": 12,
            "industry_hint": "generic",
            "bank_name": "Test Bank",
            "period_start": "2024-04",
            "period_end": "2025-03",
            "adb_floor_inr": 100000
        },
        "monthly": [
            {
                "month": "2024-04",
                "adb_m": 150000,
                "amct_m": 1000000,
                "credit_txn_count_m": 50
            }
        ],
        "aggregates": {}
    }
    
    is_valid, error = validator.validate_input(sample_input)
    print(f"Input validation: {'PASS' if is_valid else 'FAIL'}")
    if error:
        print(f"Error: {error}")

