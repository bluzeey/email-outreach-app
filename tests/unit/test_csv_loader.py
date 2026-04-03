"""Unit tests for CSV loader."""

import os
import tempfile

import pytest

from app.services.csv_loader import CSVLoader


class TestCSVLoader:
    """Tests for CSVLoader."""
    
    def test_detect_encoding_utf8(self):
        """Test UTF-8 encoding detection."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write("name,email\n")
            f.write("John,john@example.com\n")
            temp_path = f.name
        
        try:
            encoding = CSVLoader.detect_encoding(temp_path)
            assert encoding.lower() in ['utf-8', 'utf-8-sig', 'ascii']
        finally:
            os.unlink(temp_path)
    
    def test_normalize_header(self):
        """Test header normalization."""
        assert CSVLoader.normalize_header("First Name") == "first_name"
        assert CSVLoader.normalize_header("Email-Address") == "email_address"
        assert CSVLoader.normalize_header("Company.Name") == "company_name"
        assert CSVLoader.normalize_header("  spaced  ") == "spaced"
    
    def test_load_csv_basic(self):
        """Test basic CSV loading."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("name,email\n")
            f.write("John Doe,john@example.com\n")
            f.write("Jane Smith,jane@example.com\n")
            temp_path = f.name
        
        try:
            df = CSVLoader.load_csv(temp_path)
            
            assert len(df) == 2
            assert list(df.columns) == ["name", "email"]
            assert df.iloc[0]["name"] == "John Doe"
            assert df.iloc[0]["email"] == "john@example.com"
        finally:
            os.unlink(temp_path)
    
    def test_load_csv_handles_nan(self):
        """Test that NaN values are handled."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("name,email,phone\n")
            f.write("John,john@example.com,\n")
            f.write("Jane,,555-1234\n")
            temp_path = f.name
        
        try:
            df = CSVLoader.load_csv(temp_path)
            
            # Empty values should be empty strings, not "nan"
            assert df.iloc[0]["phone"] == ""
            assert df.iloc[1]["email"] == ""
        finally:
            os.unlink(temp_path)
    
    def test_get_row_as_dict(self):
        """Test getting row as dictionary."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("name,email,company\n")
            f.write("John,john@example.com,Acme\n")
            temp_path = f.name
        
        try:
            df = CSVLoader.load_csv(temp_path)
            row = CSVLoader.get_row_as_dict(df, 0)
            
            assert row == {
                "name": "John",
                "email": "john@example.com",
                "company": "Acme",
            }
        finally:
            os.unlink(temp_path)
