"""CSV loader service."""

import chardet
import pandas as pd
from fastapi import UploadFile

from app.core.logging import get_logger

logger = get_logger(__name__)


class CSVLoader:
    """Service for loading and normalizing CSV files."""
    
    @staticmethod
    def detect_encoding(file_path: str) -> str:
        """Detect file encoding."""
        with open(file_path, "rb") as f:
            raw = f.read(10000)
            result = chardet.detect(raw)
            return result.get("encoding", "utf-8")
    
    @staticmethod
    def normalize_header(header: str) -> str:
        """Normalize column header."""
        # Convert to lowercase, strip whitespace, replace special chars
        normalized = header.lower().strip()
        normalized = normalized.replace(" ", "_")
        normalized = normalized.replace("-", "_")
        normalized = normalized.replace(".", "_")
        # Remove any non-alphanumeric characters except underscore
        normalized = "".join(c for c in normalized if c.isalnum() or c == "_")
        return normalized
    
    @classmethod
    def load_csv(cls, file_path: str) -> pd.DataFrame:
        """Load CSV file with encoding detection."""
        encoding = cls.detect_encoding(file_path)
        logger.info(f"Detected encoding: {encoding}")
        
        try:
            df = pd.read_csv(file_path, encoding=encoding)
        except UnicodeDecodeError:
            logger.warning(f"Failed to read with {encoding}, trying utf-8")
            df = pd.read_csv(file_path, encoding="utf-8")
        
        # Normalize headers
        df.columns = [cls.normalize_header(col) for col in df.columns]
        
        # Remove duplicate headers if any
        if df.columns.duplicated().any():
            logger.warning("Duplicate column headers found, deduplicating")
            seen = {}
            new_columns = []
            for col in df.columns:
                if col in seen:
                    seen[col] += 1
                    new_columns.append(f"{col}_{seen[col]}")
                else:
                    seen[col] = 0
                    new_columns.append(col)
            df.columns = new_columns
        
        # Convert all values to string for consistency
        df = df.astype(str)
        df = df.replace("nan", "")
        df = df.replace("None", "")
        
        return df
    
    @classmethod
    async def save_upload(cls, upload_file: UploadFile, dest_path: str) -> str:
        """Save uploaded file to disk."""
        import aiofiles
        
        async with aiofiles.open(dest_path, "wb") as f:
            content = await upload_file.read()
            await f.write(content)
        
        logger.info(f"Saved upload to: {dest_path}")
        return dest_path
    
    @classmethod
    def get_row_as_dict(cls, df: pd.DataFrame, row_index: int) -> dict:
        """Get a single row as dictionary."""
        row = df.iloc[row_index]
        return {col: str(val) if pd.notna(val) else "" for col, val in row.items()}
