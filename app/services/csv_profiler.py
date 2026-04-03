"""CSV profiler service."""

import re
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.schemas.csv_inference import CsvColumnProfile, CsvProfile

logger = get_logger(__name__)


class CSVProfiler:
    """Service for profiling CSV columns and detecting semantic types."""
    
    # Regex patterns for common types
    EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
    PHONE_PATTERN = re.compile(r"^[\+]?[\d\s\-\(\)]{10,}$")
    DATE_PATTERN = re.compile(r"\d{1,4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,4}")
    CURRENCY_PATTERN = re.compile(r"^[$€£¥]?\s*[\d,]+\.?\d*\s*[KMB]?$", re.IGNORECASE)
    BOOLEAN_TRUE = {"true", "yes", "1", "y", "t"}
    BOOLEAN_FALSE = {"false", "no", "0", "n", "f"}
    
    @classmethod
    def profile_column(cls, df: pd.DataFrame, column: str) -> CsvColumnProfile:
        """Profile a single column."""
        series = df[column]
        total = len(series)
        
        # Basic stats
        null_count = series.isna().sum() + (series == "").sum() + (series == "nan").sum()
        null_percentage = (null_count / total) * 100 if total > 0 else 0
        unique_count = series.nunique()
        
        # Sample values (non-null)
        sample_values = (
            series[series.notna() & (series != "") & (series != "nan")]
            .drop_duplicates()
            .head(5)
            .tolist()
        )
        
        # Type detection
        non_null_values = series[series.notna() & (series != "")].astype(str)
        
        is_email = False
        is_url = False
        is_phone = False
        is_date = False
        is_boolean = False
        
        if len(non_null_values) > 0:
            # Check for email
            email_matches = non_null_values.apply(
                lambda x: bool(cls.EMAIL_PATTERN.match(str(x)))
            ).sum()
            is_email = email_matches / len(non_null_values) > 0.5
            
            # Check for URL
            url_matches = non_null_values.apply(
                lambda x: bool(cls.URL_PATTERN.match(str(x)))
            ).sum()
            is_url = url_matches / len(non_null_values) > 0.5
            
            # Check for phone
            phone_matches = non_null_values.apply(
                lambda x: bool(cls.PHONE_PATTERN.match(str(x).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")))
            ).sum()
            is_phone = phone_matches / len(non_null_values) > 0.5
            
            # Check for date
            date_matches = non_null_values.apply(
                lambda x: bool(cls.DATE_PATTERN.search(str(x)))
            ).sum()
            is_date = date_matches / len(non_null_values) > 0.3
            
            # Check for boolean
            bool_values = set(non_null_values.str.lower())
            is_boolean = bool_values.issubset(cls.BOOLEAN_TRUE | cls.BOOLEAN_FALSE)
        
        # Determine inferred type
        if is_email:
            inferred_type = "email"
        elif is_url:
            inferred_type = "url"
        elif is_phone:
            inferred_type = "phone"
        elif is_date:
            inferred_type = "date"
        elif is_boolean:
            inferred_type = "boolean"
        else:
            # Try numeric
            try:
                pd.to_numeric(non_null_values, errors="raise")
                inferred_type = "number"
            except:
                inferred_type = "string"
        
        return CsvColumnProfile(
            name=column,
            inferred_type=inferred_type,
            null_percentage=null_percentage,
            unique_count=unique_count,
            sample_values=sample_values,
            is_email=is_email,
            is_url=is_url,
            is_phone=is_phone,
            is_date=is_date,
            is_boolean=is_boolean,
        )
    
    @classmethod
    def profile_csv(cls, df: pd.DataFrame) -> CsvProfile:
        """Profile entire CSV file."""
        logger.info(f"Profiling CSV with {len(df)} rows and {len(df.columns)} columns")
        
        columns = []
        for col in df.columns:
            profile = cls.profile_column(df, col)
            columns.append(profile)
        
        return CsvProfile(
            total_rows=len(df),
            total_columns=len(df.columns),
            columns=columns,
            column_names=list(df.columns),
        )
    
    @classmethod
    def detect_email_column(cls, profile: CsvProfile) -> str | None:
        """Detect the primary email column."""
        # Priority order for email column detection
        email_candidates = [c for c in profile.columns if c.is_email]
        
        if not email_candidates:
            # Try name-based detection
            for col in profile.columns:
                if "email" in col.name.lower():
                    return col.name
            return None
        
        # Prefer columns named "email" or similar
        priority_names = ["email", "email_address", "e_mail", "contact_email"]
        for col in email_candidates:
            if any(name in col.name.lower() for name in priority_names):
                return col.name
        
        # Return first email column with lowest null percentage
        return min(email_candidates, key=lambda c: c.null_percentage).name
    
    @classmethod
    def detect_name_columns(cls, profile: CsvProfile) -> list[str]:
        """Detect name-related columns."""
        name_keywords = ["name", "first", "last", "full_name", "firstname", "lastname"]
        candidates = []
        
        for col in profile.columns:
            if any(kw in col.name.lower() for kw in name_keywords):
                candidates.append(col.name)
        
        return candidates
    
    @classmethod
    def detect_company_columns(cls, profile: CsvProfile) -> list[str]:
        """Detect company-related columns."""
        company_keywords = ["company", "organization", "org", "firm", "business", "employer"]
        domain_keywords = ["domain", "website", "url", "site"]
        candidates = []
        
        for col in profile.columns:
            name_lower = col.name.lower()
            if any(kw in name_lower for kw in company_keywords):
                candidates.append(col.name)
            elif col.is_url and any(kw in name_lower for kw in domain_keywords):
                candidates.append(col.name)
        
        return candidates
    
    @classmethod
    def detect_blocker_columns(cls, profile: CsvProfile) -> list[str]:
        """Detect potential blocker columns (opt-out, do-not-contact, etc.)."""
        blocker_keywords = [
            "opt_out", "optout", "unsubscribe", "do_not_contact", "donotcontact",
            "blocked", "excluded", "skip", "do_not_email", "unsubscribed",
            "bounced", "complaint", "suppress"
        ]
        candidates = []
        
        for col in profile.columns:
            if any(kw in col.name.lower() for kw in blocker_keywords):
                candidates.append(col.name)
        
        return candidates
    
    @classmethod
    def get_sample_rows(cls, df: pd.DataFrame, count: int = 5) -> list[dict]:
        """Get sample rows for analysis."""
        samples = []
        for i in range(min(count, len(df))):
            row = df.iloc[i].to_dict()
            # Convert to string and clean
            row = {k: str(v) if pd.notna(v) else "" for k, v in row.items()}
            samples.append(row)
        return samples
