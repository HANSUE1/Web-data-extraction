"""Multi-format storage: JSON, CSV, JSONL, databases, blob storage."""
import json
import csv
import os
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime


class JSONExporter:
    """Export to JSON format."""
    
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export(self, items: List[Dict[str, Any]], filename: str = None):
        if filename is None:
            filename = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = self.output_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        
        return str(filepath)


class JSONLExporter:
    """Export to JSONL format."""
    
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export(self, items: List[Dict[str, Any]], filename: str = None):
        if filename is None:
            filename = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        
        filepath = self.output_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        
        return str(filepath)


class CSVExporter:
    """Export to CSV format."""
    
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export(self, items: List[Dict[str, Any]], filename: str = None):
        if not items:
            return None
        
        if filename is None:
            filename = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        filepath = self.output_dir / filename
        
        # Get all unique keys
        all_keys = set()
        for item in items:
            all_keys.update(item.keys())
        fieldnames = sorted(list(all_keys))
        
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in items:
                writer.writerow(item)
        
        return str(filepath)


class DatabaseExporter:
    """Export to databases (SQLite, PostgreSQL, MongoDB)."""
    
    def __init__(self, db_url: str = None):
        self.db_url = db_url
    
    def export_sqlite(self, items: List[Dict[str, Any]], db_path: str, table_name: str):
        """Export to SQLite."""
        import sqlite3
        
        if not items:
            return
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create table with all columns
        all_keys = set()
        for item in items:
            all_keys.update(item.keys())
        columns = sorted(list(all_keys))
        
        cols_def = ', '.join([f'"{col}" TEXT' for col in columns])
        cursor.execute(f'CREATE TABLE IF NOT EXISTS {table_name} ({cols_def})')
        
        # Insert data
        placeholders = ', '.join(['?' for _ in columns])
        for item in items:
            values = [json.dumps(item.get(col)) if isinstance(item.get(col), (dict, list)) else item.get(col) for col in columns]
            cursor.execute(f'INSERT INTO {table_name} VALUES ({placeholders})', values)
        
        conn.commit()
        conn.close()
    
    def export_postgres(self, items: List[Dict[str, Any]], table_name: str):
        """Export to PostgreSQL (placeholder)."""
        try:
            import psycopg2
            # Implementation would go here
            raise NotImplementedError("PostgreSQL export requires psycopg2 configuration")
        except ImportError:
            raise Exception("psycopg2 not installed")
    
    def export_mongodb(self, items: List[Dict[str, Any]], collection_name: str):
        """Export to MongoDB (placeholder)."""
        try:
            import pymongo
            raise NotImplementedError("MongoDB export requires pymongo configuration")
        except ImportError:
            raise Exception("pymongo not installed")


class StorageManager:
    """Unified storage manager."""
    
    def __init__(self, config):
        self.config = config
        self.json_exporter = JSONExporter()
        self.jsonl_exporter = JSONLExporter()
        self.csv_exporter = CSVExporter()
        self.db_exporter = DatabaseExporter(config.storage.db_url)
    
    def export(self, items: List[Dict[str, Any]], formats: List = None):
        """Export to all configured formats."""
        if formats is None:
            formats = self.config.export_formats
        
        results = {}
        for fmt in formats:
            if fmt.value == 'json':
                results['json'] = self.json_exporter.export(items)
            elif fmt.value == 'jsonl':
                results['jsonl'] = self.jsonl_exporter.export(items)
            elif fmt.value == 'csv':
                results['csv'] = self.csv_exporter.export(items)
        
        return results
