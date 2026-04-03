"""Unit tests for CSV profiler."""

import os
import tempfile

import pandas as pd
import pytest

from app.services.csv_loader import CSVLoader
from app.services.csv_profiler import CSVProfiler


class TestCSVProfiler:
    """Tests for CSVProfiler."""
    
    def test_profile_column_email(self):
        """Test email column profiling."""
        df = pd.DataFrame({
            "email": ["test@example.com", "user@domain.org", "invalid", ""]
        })
        
        profile = CSVProfiler.profile_column(df, "email")
        
        assert profile.name == "email"
        assert profile.is_email
        assert profile.inferred_type == "email"
    
    def test_profile_column_url(self):
        """Test URL column profiling."""
        df = pd.DataFrame({
            "website": ["https://example.com", "http://test.org", "not-a-url", ""]
        })
        
        profile = CSVProfiler.profile_column(df, "website")
        
        assert profile.is_url
        assert profile.inferred_type == "url"
    
    def test_profile_column_boolean(self):
        """Test boolean column profiling."""
        df = pd.DataFrame({
            "active": ["true", "false", "yes", "no"]
        })
        
        profile = CSVProfiler.profile_column(df, "active")
        
        assert profile.is_boolean
    
    def test_profile_csv(self):
        """Test full CSV profiling."""
        df = pd.DataFrame({
            "name": ["John", "Jane", "Bob"],
            "email": ["john@test.com", "jane@test.com", "bob@test.com"],
            "age": ["25", "30", "35"],
        })
        
        profile = CSVProfiler.profile_csv(df)
        
        assert profile.total_rows == 3
        assert profile.total_columns == 3
        assert len(profile.columns) == 3
    
    def test_detect_email_column(self):
        """Test email column detection."""
        profile = CSVProfiler.profile_csv(pd.DataFrame({
            "first_name": ["John", "Jane"],
            "email": ["john@test.com", "jane@test.com"],
            "company": ["Acme", "TechCorp"],
        }))
        
        email_col = CSVProfiler.detect_email_column(profile)
        assert email_col == "email"
    
    def test_detect_name_columns(self):
        """Test name column detection."""
        profile = CSVProfiler.profile_csv(pd.DataFrame({
            "first_name": ["John", "Jane"],
            "last_name": ["Doe", "Smith"],
            "email": ["john@test.com", "jane@test.com"],
        }))
        
        name_cols = CSVProfiler.detect_name_columns(profile)
        assert "first_name" in name_cols
        assert "last_name" in name_cols
    
    def test_detect_company_columns(self):
        """Test company column detection."""
        profile = CSVProfiler.profile_csv(pd.DataFrame({
            "name": ["John", "Jane"],
            "company": ["Acme", "TechCorp"],
            "website": ["https://acme.com", "https://techcorp.com"],
        }))
        
        company_cols = CSVProfiler.detect_company_columns(profile)
        assert "company" in company_cols
    
    def test_detect_blocker_columns(self):
        """Test blocker column detection."""
        profile = CSVProfiler.profile_csv(pd.DataFrame({
            "email": ["test@test.com"],
            "opt_out": ["false"],
            "do_not_contact": ["no"],
        }))
        
        blocker_cols = CSVProfiler.detect_blocker_columns(profile)
        assert "opt_out" in blocker_cols
        assert "do_not_contact" in blocker_cols
