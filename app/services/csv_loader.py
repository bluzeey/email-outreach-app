"""Data file loader service - supports CSV and Excel files."""

import chardet
import pandas as pd
from fastapi import UploadFile

from app.core.logging import get_logger

logger = get_logger(__name__)


class DataLoader:
    """Service for loading and normalizing CSV and Excel files."""
    
    SUPPORTED_EXTENSIONS = {'.csv', '.xls', '.xlsx', '.xlsm'}
    
    @staticmethod
    def detect_encoding(file_path: str) -> str:
        """Detect file encoding (for CSV files)."""
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
    def get_file_extension(cls, filename: str) -> str:
        """Get lowercase file extension."""
        return filename.lower().split('.')[-1] if '.' in filename else ''
    
    @classmethod
    def is_supported_file(cls, filename: str) -> bool:
        """Check if file type is supported."""
        ext = cls.get_file_extension(filename)
        return f'.{ext}' in cls.SUPPORTED_EXTENSIONS or ext in {'csv', 'xls', 'xlsx', 'xlsm'}
    
    @classmethod
    def load_file(cls, file_path: str) -> pd.DataFrame:
        """Load CSV or Excel file with automatic format detection."""
        ext = cls.get_file_extension(file_path)
        
        if ext == 'csv':
            return cls._load_csv(file_path)
        elif ext in {'xls', 'xlsx', 'xlsm'}:
            return cls._load_excel(file_path)
        else:
            raise ValueError(f"Unsupported file format: .{ext}. Supported: CSV, XLS, XLSX")
    
    @classmethod
    def _load_csv(cls, file_path: str) -> pd.DataFrame:
        """Load CSV file with encoding detection."""
        encoding = cls.detect_encoding(file_path)
        logger.info(f"Detected encoding: {encoding}")
        
        try:
            df = pd.read_csv(file_path, encoding=encoding)
        except UnicodeDecodeError:
            logger.warning(f"Failed to read with {encoding}, trying utf-8")
            df = pd.read_csv(file_path, encoding="utf-8")
        
        return cls._normalize_dataframe(df)
    
    @classmethod
    def _load_excel(cls, file_path: str) -> pd.DataFrame:
        """Load Excel file (.xls, .xlsx, .xlsm)."""
        logger.info(f"Loading Excel file: {file_path}")
        
        try:
            # Read the first sheet by default
            df = pd.read_excel(file_path, sheet_name=0)
        except ImportError as e:
            if "openpyxl" in str(e) or "xlrd" in str(e):
                raise ImportError(
                    "Excel support requires additional dependencies. "
                    "Please install: pip install openpyxl xlrd"
                ) from e
            raise
        
        return cls._normalize_dataframe(df)
    
    @classmethod
    def _normalize_dataframe(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize dataframe headers and values."""
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


# Backwards compatibility - CSVLoader is now an alias for DataLoader
CSVLoader = DataLoader
